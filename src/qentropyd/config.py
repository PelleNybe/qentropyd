"""Configuration management for qentropyd using Pydantic v2 and YAML."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class AnuApiSourceConfig(BaseModel):
    """Configuration for HTTP/TLS optical QRNG API sources (ANU / LfD / custom JSON)."""

    enabled: bool = True
    name: str = "anu_optical_qrng"
    endpoint_url: str = "https://qrng.anu.edu.au/API/jsonI.php"
    response_format: Literal["anu_v1", "lfd_hex", "json_hex", "json_uint8"] = "anu_v1"
    api_key: str | None = None
    api_key_header: str = "x-api-key"
    timeout_seconds: float = Field(default=10.0, gt=0.1, le=120.0)
    verify_tls: bool = True
    max_retries: int = Field(default=3, ge=0, le=10)
    base_backoff_seconds: float = Field(default=1.0, gt=0.0, le=60.0)
    max_backoff_seconds: float = Field(default=60.0, gt=0.0, le=3600.0)

    @field_validator("endpoint_url")
    @classmethod
    def _validate_https(cls, v: str) -> str:
        if not v.startswith(("https://", "http://")):
            raise ValueError("endpoint_url must start with https:// or http://")
        return v


class QiskitQrngSourceConfig(BaseModel):
    """Configuration for Qiskit Hadamard-gate quantum circuit QRNG source."""

    enabled: bool = True
    name: str = "qiskit_hadamard_qrng"
    num_qubits: int = Field(default=16, ge=1, le=32)
    backend_type: Literal["aer_simulator", "remote_rest"] = "aer_simulator"
    remote_url: str | None = None
    api_token: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0.1, le=300.0)
    seed_simulator: int | None = None


class MixerConfig(BaseModel):
    """Configuration for multi-source cryptographic XOR mixing."""

    min_healthy_sources: int = Field(default=2, ge=1, le=16)
    batch_bytes: int = Field(default=4096, ge=256, le=1_048_576)

    @field_validator("batch_bytes")
    @classmethod
    def _validate_batch_bytes(cls, v: int) -> int:
        if v % 16 != 0:
            raise ValueError("batch_bytes must be a multiple of 16 bytes for block alignment")
        return v


class Nist80022Config(BaseModel):
    """Configuration for NIST SP 800-22 statistical test suite."""

    significance_level: float = Field(default=0.01, gt=0.0, lt=0.5)
    block_frequency_m: int = Field(default=128, ge=20, le=10000)


class Nist80090BConfig(BaseModel):
    """Configuration for NIST SP 800-90B continuous health tests."""

    min_entropy_bits_per_byte: float = Field(default=4.0, gt=0.1, le=8.0)
    alpha_90b: float = Field(default=2.0**-20, gt=0.0, lt=0.1)
    apt_window_size: int = Field(default=512, ge=64, le=4096)
    enable_bit_rct: bool = True


class SinkConfig(BaseModel):
    """Configuration for Linux kernel RNG injection sink."""

    mode: Literal["paranoid", "credit"] = "paranoid"
    device_path: str = "/dev/urandom"
    credit_bits_per_byte: float = Field(default=0.5, gt=0.0, le=8.0)


class MetricsConfig(BaseModel):
    """Configuration for Prometheus /metrics HTTP exporter."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = Field(default=9108, ge=1024, le=65535)


class DaemonConfig(BaseModel):
    """Configuration for main async daemon loop and circuit breaker."""

    interval_seconds: float = Field(default=10.0, gt=0.01, le=3600.0)
    low_entropy_threshold: int = Field(default=1024, ge=0, le=4096)
    circuit_breaker_threshold: int = Field(default=3, ge=1, le=100)
    circuit_breaker_cooldown_seconds: float = Field(default=60.0, gt=0.01, le=3600.0)
    log_to_syslog: bool = True
    max_cycles: int | None = None


class QEntropydConfig(BaseModel):
    """Root configuration model for qentropyd."""

    anu_api: AnuApiSourceConfig = Field(default_factory=AnuApiSourceConfig)
    qiskit_qrng: QiskitQrngSourceConfig = Field(default_factory=QiskitQrngSourceConfig)
    mixer: MixerConfig = Field(default_factory=MixerConfig)
    nist_800_22: Nist80022Config = Field(default_factory=Nist80022Config)
    nist_800_90b: Nist80090BConfig = Field(default_factory=Nist80090BConfig)
    sink: SinkConfig = Field(default_factory=SinkConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply environment variable overrides for common deployment parameters."""
    if mode := os.environ.get("QENTROPYD_SINK_MODE"):
        data.setdefault("sink", {})["mode"] = mode
    if port := os.environ.get("QENTROPYD_METRICS_PORT"):
        data.setdefault("metrics", {})["port"] = int(port)
    if host := os.environ.get("QENTROPYD_METRICS_HOST"):
        data.setdefault("metrics", {})["host"] = host
    if anu_key := os.environ.get("QENTROPYD_ANU_API_KEY"):
        data.setdefault("anu_api", {})["api_key"] = anu_key
    if min_src := os.environ.get("QENTROPYD_MIN_HEALTHY_SOURCES"):
        data.setdefault("mixer", {})["min_healthy_sources"] = int(min_src)
    return data


def load_config(path: str | Path | None = None) -> QEntropydConfig:
    """Load and validate qentropyd configuration from YAML file and environment."""
    raw: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path)
        if not config_path.is_file():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if loaded is not None:
            if not isinstance(loaded, dict):
                raise ValueError("Configuration root must be a YAML mapping")
            raw = loaded
    raw = _apply_env_overrides(raw)
    return QEntropydConfig.model_validate(raw)
