"""Main async daemon loop, fail-closed circuit breaker, syslog & signal handling."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import syslog
from typing import TYPE_CHECKING

from qentropyd.config import QEntropydConfig, load_config
from qentropyd.metrics import QEntropydMetrics
from qentropyd.sink.linux_rng import (
    LinuxRngSink,
    RngSinkError,
    read_kernel_entropy_avail,
    read_kernel_poolsize,
)
from qentropyd.sources.anu_api import AnuApiEntropySource
from qentropyd.sources.base import EntropySource, EntropySourceError
from qentropyd.sources.mixer import MultiSourceXorMixer
from qentropyd.sources.qiskit_qrng import QiskitQrngEntropySource
from qentropyd.validators.health_check import run_nist_800_90b_health_tests
from qentropyd.validators.nist_tests import run_nist_800_22_suite

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("qentropyd")


def build_sources(config: QEntropydConfig) -> list[EntropySource]:
    """Instantiate enabled quantum entropy sources from configuration."""
    sources: list[EntropySource] = []
    if config.anu_api.enabled:
        sources.append(AnuApiEntropySource(config.anu_api))
    if config.qiskit_qrng.enabled:
        sources.append(QiskitQrngEntropySource(config.qiskit_qrng))
    return sources


class QEntropyDaemon:
    """Self-verifying Quantum Entropy Daemon with fail-closed circuit breaker."""

    def __init__(
        self,
        config: QEntropydConfig,
        config_path: Path | None = None,
        mixer: MultiSourceXorMixer | None = None,
        sink: LinuxRngSink | None = None,
        metrics: QEntropydMetrics | None = None,
    ) -> None:
        self._config = config
        self._config_path = config_path
        self._metrics = metrics or QEntropydMetrics()
        self._sink = sink or LinuxRngSink(config.sink)
        self._mixer = mixer or MultiSourceXorMixer(
            build_sources(config),
            min_healthy_sources=config.mixer.min_healthy_sources,
        )
        self._stop_event = asyncio.Event()
        self._reload_requested = False
        self._consecutive_failures = 0
        self._syslog_opened = False

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def _emit_syslog(self, priority: int, message: str) -> None:
        if self._config.daemon.log_to_syslog:
            if not self._syslog_opened:
                syslog.openlog("qentropyd", syslog.LOG_PID, syslog.LOG_DAEMON)
                self._syslog_opened = True
            syslog.syslog(priority, message)

    def request_shutdown(self) -> None:
        """Signal the daemon loop to terminate gracefully."""
        logger.info("Shutdown requested")
        self._emit_syslog(syslog.LOG_NOTICE, "Shutdown requested")
        self._stop_event.set()

    def request_reload(self) -> None:
        """Signal the daemon loop to reload YAML configuration on next cycle."""
        logger.info("SIGHUP received: scheduling configuration reload")
        self._emit_syslog(syslog.LOG_NOTICE, "SIGHUP received: scheduling configuration reload")
        self._reload_requested = True

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_shutdown)
        loop.add_signal_handler(signal.SIGHUP, self.request_reload)

    async def _reload_config(self) -> None:
        self._reload_requested = False
        if self._config_path is None:
            logger.warning("Reload requested but no config_path was provided; ignoring")
            return
        try:
            new_cfg = load_config(self._config_path)
            new_sources = build_sources(new_cfg)
            new_mixer = MultiSourceXorMixer(
                new_sources,
                min_healthy_sources=new_cfg.mixer.min_healthy_sources,
            )
            await self._mixer.close()
            self._config = new_cfg
            self._mixer = new_mixer
            self._sink = LinuxRngSink(new_cfg.sink)
            logger.info("Configuration reloaded from %s", self._config_path)
            self._emit_syslog(
                syslog.LOG_INFO,
                f"Configuration reloaded from {self._config_path}",
            )
        except Exception as exc:
            logger.error("Failed to reload configuration: %s", exc)
            self._emit_syslog(syslog.LOG_ERR, f"Configuration reload failed: {exc}")

    def _update_kernel_metrics(self) -> None:
        try:
            avail = read_kernel_entropy_avail()
            poolsize = read_kernel_poolsize()
            self._metrics.kernel_entropy_avail.set(avail)
            self._metrics.kernel_poolsize.set(poolsize)
        except RngSinkError:
            pass

    def _record_source_metrics(self) -> None:
        for name, health in self._mixer.health_status.items():
            if health.last_latency_seconds > 0.0:
                self._metrics.source_fetch_latency.labels(source=name).observe(
                    health.last_latency_seconds
                )
            if not health.is_healthy:
                self._metrics.source_errors.labels(source=name).inc()

    async def run_once(self) -> bool:
        """Execute a single harvest -> mix -> validate -> inject cycle.

        Returns:
            True if a verified batch was injected, False if rejected or failed.
        """
        self._update_kernel_metrics()

        try:
            candidate = await self._mixer.fetch_mixed_bytes(self._config.mixer.batch_bytes)
        except EntropySourceError as exc:
            self._record_source_metrics()
            self._consecutive_failures += 1
            self._metrics.batches_rejected.labels(reason="source_unavailable").inc()
            logger.warning("Entropy source acquisition failed: %s", exc)
            self._emit_syslog(syslog.LOG_WARNING, f"Source acquisition failed: {exc}")
            return False

        self._record_source_metrics()

        # Gate 1: NIST SP 800-90B continuous health tests (RCT + APT)
        health_res = run_nist_800_90b_health_tests(
            candidate,
            min_entropy_bits_per_byte=self._config.nist_800_90b.min_entropy_bits_per_byte,
            alpha=self._config.nist_800_90b.alpha_90b,
            apt_window_size=self._config.nist_800_90b.apt_window_size,
            enable_bit_rct=self._config.nist_800_90b.enable_bit_rct,
        )
        if not health_res.passed:
            self._consecutive_failures += 1
            for failed_name in health_res.failed_tests:
                self._metrics.batches_rejected.labels(reason=f"sp800_90b_{failed_name}").inc()
            msg = (
                "Circuit breaker REJECTED batch on NIST SP 800-90B: "
                f"{', '.join(health_res.failed_tests)}"
            )
            logger.warning(msg)
            self._emit_syslog(syslog.LOG_WARNING, msg)
            del candidate
            return False

        # Gate 2: NIST SP 800-22 statistical test suite
        nist_res = run_nist_800_22_suite(
            candidate,
            alpha=self._config.nist_800_22.significance_level,
            block_frequency_m=self._config.nist_800_22.block_frequency_m,
        )
        for test_result in nist_res.results:
            self._metrics.nist_p_value.labels(test_name=test_result.name).set(
                test_result.p_value
            )

        if not nist_res.passed:
            self._consecutive_failures += 1
            for failed_name in nist_res.failed_tests:
                self._metrics.batches_rejected.labels(reason=f"sp800_22_{failed_name}").inc()
            msg = (
                "Circuit breaker REJECTED batch on NIST SP 800-22: "
                f"{', '.join(nist_res.failed_tests)}"
            )
            logger.warning(msg)
            self._emit_syslog(syslog.LOG_WARNING, msg)
            del candidate
            return False

        # Gate 3: Inject into Linux kernel CSPRNG
        try:
            injected = self._sink.inject(candidate)
        except RngSinkError as exc:
            self._consecutive_failures += 1
            self._metrics.batches_rejected.labels(reason="sink_error").inc()
            logger.error("Kernel RNG injection failed: %s", exc)
            self._emit_syslog(syslog.LOG_ERR, f"Kernel RNG injection failed: {exc}")
            return False

        self._consecutive_failures = 0
        self._metrics.circuit_breaker_open.set(0)
        self._metrics.batches_accepted.inc()
        self._metrics.bytes_injected.inc(injected)
        self._update_kernel_metrics()
        logger.debug("Injected %d verified quantum entropy bytes (%s mode)", injected, self._sink.mode)
        return True

    async def run_forever(self, install_signals: bool = True) -> None:
        """Run the main daemon loop until stopped or max_cycles reached."""
        if install_signals:
            self._install_signal_handlers()

        if self._config.metrics.enabled:
            self._metrics.start_server(
                host=self._config.metrics.host,
                port=self._config.metrics.port,
            )
            logger.info(
                "Prometheus /metrics server listening on %s:%d",
                self._config.metrics.host,
                self._config.metrics.port,
            )

        self._emit_syslog(
            syslog.LOG_INFO,
            f"qentropyd started in {self._sink.mode} mode (batch={self._config.mixer.batch_bytes}B)",
        )

        cycles = 0
        try:
            while not self._stop_event.is_set():
                if self._reload_requested:
                    await self._reload_config()

                await self.run_once()
                cycles += 1
                if (
                    self._config.daemon.max_cycles is not None
                    and cycles >= self._config.daemon.max_cycles
                ):
                    break

                if self._consecutive_failures >= self._config.daemon.circuit_breaker_threshold:
                    self._metrics.circuit_breaker_open.set(1)
                    cooldown = self._config.daemon.circuit_breaker_cooldown_seconds
                    crit_msg = (
                        f"Circuit breaker OPEN after {self._consecutive_failures} consecutive "
                        f"failures; cooling down for {cooldown:.1f}s"
                    )
                    logger.critical(crit_msg)
                    self._emit_syslog(syslog.LOG_CRIT, crit_msg)
                    wait_time = cooldown
                else:
                    wait_time = self._config.daemon.interval_seconds

                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=wait_time)
        finally:
            await self._mixer.close()
            if self._syslog_opened:
                syslog.closelog()
                self._syslog_opened = False
