"""Prometheus /metrics exporter for qentropyd observability."""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)


class QEntropydMetrics:
    """Encapsulates all Prometheus metrics for qentropyd."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()

        self.batches_accepted = Counter(
            "qentropyd_batches_accepted_total",
            "Total number of entropy batches passing all NIST SP 800-90B and 800-22 gates",
            registry=self.registry,
        )
        self.bytes_injected = Counter(
            "qentropyd_bytes_injected_total",
            "Total number of verified quantum entropy bytes injected into the Linux kernel RNG",
            registry=self.registry,
        )
        self.batches_rejected = Counter(
            "qentropyd_batches_rejected_total",
            "Total number of entropy batches rejected by the fail-closed circuit breaker",
            labelnames=("reason",),
            registry=self.registry,
        )
        self.nist_p_value = Gauge(
            "qentropyd_nist_p_value",
            "Latest NIST SP 800-22 statistical test p-value",
            labelnames=("test_name",),
            registry=self.registry,
        )
        self.source_fetch_latency = Histogram(
            "qentropyd_source_fetch_latency_seconds",
            "Latency of entropy source fetch operations in seconds",
            labelnames=("source",),
            registry=self.registry,
        )
        self.source_errors = Counter(
            "qentropyd_source_errors_total",
            "Total fetch errors per entropy source",
            labelnames=("source",),
            registry=self.registry,
        )
        self.kernel_entropy_avail = Gauge(
            "qentropyd_kernel_entropy_avail_bits",
            "Live Linux kernel entropy_avail in bits from /proc/sys/kernel/random/entropy_avail",
            registry=self.registry,
        )
        self.kernel_poolsize = Gauge(
            "qentropyd_kernel_poolsize_bits",
            "Linux kernel random poolsize in bits from /proc/sys/kernel/random/poolsize",
            registry=self.registry,
        )
        self.circuit_breaker_open = Gauge(
            "qentropyd_circuit_breaker_open",
            "1 if the fail-closed circuit breaker is currently in cooldown, 0 otherwise",
            registry=self.registry,
        )

    def start_server(self, host: str, port: int) -> None:
        """Start the Prometheus HTTP metrics server."""
        start_http_server(port=port, addr=host, registry=self.registry)

    def render_latest(self) -> bytes:
        """Render current metrics in Prometheus text exposition format."""
        return generate_latest(self.registry)
