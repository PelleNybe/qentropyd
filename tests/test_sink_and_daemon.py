"""Tests for Linux RNG sink, Prometheus metrics, CLI, and fail-closed circuit breaker."""

from __future__ import annotations

import os
import struct
from typing import TYPE_CHECKING, Any

from prometheus_client import CollectorRegistry

from qentropyd.cli import main as cli_main
from qentropyd.config import (
    DaemonConfig,
    MetricsConfig,
    MixerConfig,
    QEntropydConfig,
    SinkConfig,
)
from qentropyd.daemon import QEntropyDaemon
from qentropyd.metrics import QEntropydMetrics
from qentropyd.sink.linux_rng import (
    RNDADDENTROPY,
    LinuxRngSink,
    pack_rand_pool_info,
    read_kernel_entropy_avail,
    read_kernel_poolsize,
)
from qentropyd.sources.base import EntropySource
from qentropyd.sources.mixer import MultiSourceXorMixer

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _StaticSource(EntropySource):
    def __init__(self, name: str, payload: bytes) -> None:
        self._name = name
        self._payload = payload

    @property
    def name(self) -> str:
        return self._name

    async def fetch_bytes(self, num_bytes: int) -> bytes:
        return self._payload[:num_bytes]


def test_pack_rand_pool_info_layout() -> None:
    data = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    packed = pack_rand_pool_info(data, entropy_bits=32)
    entropy_count, buf_size, buf = struct.unpack(f"@ii{len(data)}s", packed)
    assert entropy_count == 32
    assert buf_size == 8
    assert buf == data


def test_paranoid_sink_writes_bytes_without_ioctl(tmp_path: Path) -> None:
    target = tmp_path / "urandom_mock"
    target.write_bytes(b"")
    sink = LinuxRngSink(SinkConfig(mode="paranoid", device_path=str(target)))
    payload = os.urandom(256)
    written = sink.inject(payload)
    assert written == 256
    assert target.read_bytes() == payload


def test_credit_sink_calls_ioctl_rndaddentropy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "urandom_mock"
    target.write_bytes(b"")
    calls: list[tuple[int, bytes]] = []

    def fake_ioctl(fd: int, request: int, arg: Any) -> int:
        calls.append((request, bytes(arg)))
        return 0

    monkeypatch.setattr("qentropyd.sink.linux_rng.fcntl.ioctl", fake_ioctl)

    sink = LinuxRngSink(
        SinkConfig(mode="credit", device_path=str(target), credit_bits_per_byte=0.5)
    )
    payload = os.urandom(64)
    written = sink.inject(payload)
    assert written == 64
    assert len(calls) == 1
    req, packed = calls[0]
    assert req == RNDADDENTROPY
    entropy_bits, buf_size, buf = struct.unpack("@ii64s", packed)
    assert entropy_bits == 32
    assert buf_size == 64
    assert buf == payload


def test_read_kernel_procfs_entropy() -> None:
    avail = read_kernel_entropy_avail()
    pool = read_kernel_poolsize()
    assert avail >= 0
    assert pool > 0


async def test_circuit_breaker_discards_bad_entropy_and_never_injects(tmp_path: Path) -> None:
    target = tmp_path / "urandom_sink"
    target.write_bytes(b"")

    bad_source = _StaticSource("bad_qrng", b"\x00" * 4096)
    mixer = MultiSourceXorMixer([bad_source], min_healthy_sources=1)
    sink = LinuxRngSink(SinkConfig(mode="paranoid", device_path=str(target)))
    metrics = QEntropydMetrics(registry=CollectorRegistry())

    cfg = QEntropydConfig(
        mixer=MixerConfig(min_healthy_sources=1, batch_bytes=4096),
        sink=SinkConfig(mode="paranoid", device_path=str(target)),
        metrics=MetricsConfig(enabled=False),
        daemon=DaemonConfig(
            interval_seconds=0.1,
            circuit_breaker_threshold=2,
            circuit_breaker_cooldown_seconds=0.1,
            log_to_syslog=False,
        ),
    )
    daemon = QEntropyDaemon(cfg, mixer=mixer, sink=sink, metrics=metrics)

    assert await daemon.run_once() is False
    assert await daemon.run_once() is False
    assert daemon.consecutive_failures == 2
    # Fail-closed guarantee: zero bytes ever touched the sink
    assert target.read_bytes() == b""

    rendered = metrics.render_latest().decode("utf-8")
    assert "qentropyd_batches_rejected_total" in rendered
    assert 'reason="sp800_90b_rct"' in rendered


async def test_daemon_accepts_good_entropy_and_updates_metrics(tmp_path: Path) -> None:
    target = tmp_path / "urandom_sink"
    target.write_bytes(b"")

    good_payload = os.urandom(4096)
    good_source = _StaticSource("good_qrng", good_payload)
    mixer = MultiSourceXorMixer([good_source], min_healthy_sources=1)
    sink = LinuxRngSink(SinkConfig(mode="paranoid", device_path=str(target)))
    metrics = QEntropydMetrics(registry=CollectorRegistry())

    cfg = QEntropydConfig(
        mixer=MixerConfig(min_healthy_sources=1, batch_bytes=4096),
        sink=SinkConfig(mode="paranoid", device_path=str(target)),
        metrics=MetricsConfig(enabled=False),
        daemon=DaemonConfig(interval_seconds=0.1, log_to_syslog=False, max_cycles=1),
    )
    daemon = QEntropyDaemon(cfg, mixer=mixer, sink=sink, metrics=metrics)
    await daemon.run_forever(install_signals=False)

    assert target.read_bytes() == good_payload
    rendered = metrics.render_latest().decode("utf-8")
    assert "qentropyd_batches_accepted_total 1.0" in rendered
    assert "qentropyd_bytes_injected_total 4096.0" in rendered
    assert "qentropyd_nist_p_value" in rendered


def test_cli_validate_config_and_self_check(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    cfg_file = tmp_path / "test.yaml"
    cfg_file.write_text(
        "sink:\n  mode: paranoid\nqiskit_qrng:\n  seed_simulator: 42\n",
        encoding="utf-8",
    )
    assert cli_main(["-c", str(cfg_file), "validate-config"]) == 0
    _ = capsys.readouterr()
    assert cli_main(["-c", str(cfg_file), "self-check", "--bytes", "4096", "--json"]) == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["passed"] is True
    assert parsed["sp800_22"]["passed"] is True


async def test_daemon_sighup_reload_and_circuit_breaker_cooldown(tmp_path: Path) -> None:
    target = tmp_path / "urandom_sink"
    target.write_bytes(b"")
    cfg_file = tmp_path / "reload.yaml"
    cfg_file.write_text(
        f"anu_api:\n  enabled: false\nqiskit_qrng:\n  seed_simulator: 42\n"
        f"mixer:\n  min_healthy_sources: 1\n  batch_bytes: 4096\n"
        f"sink:\n  mode: paranoid\n  device_path: {target}\n"
        f"metrics:\n  enabled: false\n"
        f"daemon:\n  interval_seconds: 0.05\n  log_to_syslog: false\n  max_cycles: 2\n",
        encoding="utf-8",
    )

    bad_source = _StaticSource("bad_qrng", b"\x00" * 4096)
    mixer = MultiSourceXorMixer([bad_source], min_healthy_sources=1)
    sink = LinuxRngSink(SinkConfig(mode="paranoid", device_path=str(target)))
    metrics = QEntropydMetrics(registry=CollectorRegistry())

    cfg = QEntropydConfig(
        mixer=MixerConfig(min_healthy_sources=1, batch_bytes=4096),
        sink=SinkConfig(mode="paranoid", device_path=str(target)),
        metrics=MetricsConfig(enabled=False),
        daemon=DaemonConfig(
            interval_seconds=0.05,
            circuit_breaker_threshold=1,
            circuit_breaker_cooldown_seconds=0.05,
            log_to_syslog=False,
            max_cycles=2,
        ),
    )
    daemon = QEntropyDaemon(cfg, config_path=cfg_file, mixer=mixer, sink=sink, metrics=metrics)
    daemon.request_reload()
    await daemon.run_forever(install_signals=False)
    # After reload, qiskit_qrng (seed=42) replaced bad_source and injected 4096 bytes
    assert len(target.read_bytes()) == 4096
