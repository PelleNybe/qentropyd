"""Tests for entropy sources (ANU API, Qiskit Hadamard QRNG, and XOR mixer)."""

from __future__ import annotations

import os

import httpx
import pytest

from qentropyd.config import AnuApiSourceConfig, HwrngSourceConfig, QiskitQrngSourceConfig
from qentropyd.sources.anu_api import AnuApiEntropySource
from qentropyd.sources.base import EntropySource, EntropySourceError
from qentropyd.sources.hwrng import HardwareDeviceEntropySource
from qentropyd.sources.mixer import MultiSourceXorMixer, xor_bytes
from qentropyd.sources.qiskit_qrng import QiskitQrngEntropySource
from qentropyd.validators.health_check import run_nist_800_90b_health_tests
from qentropyd.validators.nist_tests import run_nist_800_22_suite


class _StuckZeroSource(EntropySource):
    """Adversarial/compromised source returning all-zero bytes."""

    @property
    def name(self) -> str:
        return "stuck_zero"

    async def fetch_bytes(self, num_bytes: int) -> bytes:
        return b"\x00" * num_bytes


class _FailingSource(EntropySource):
    @property
    def name(self) -> str:
        return "failing_source"

    async def fetch_bytes(self, num_bytes: int) -> bytes:
        raise EntropySourceError("Simulated outage")


async def test_anu_api_source_uint8_and_pagination() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        length = int(request.url.params["length"])
        return httpx.Response(
            200,
            json={"type": "uint8", "length": length, "data": list(os.urandom(length)), "success": True},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        cfg = AnuApiSourceConfig(response_format="anu_v1", max_retries=0)
        src = AnuApiEntropySource(cfg, client=client)
        # 1500 bytes forces 2 chunked requests (1024 + 476)
        data = await src.fetch_bytes(1500)
        assert len(data) == 1500
        assert calls == 2


async def test_anu_api_source_lfd_hex_and_backoff_on_429() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"error": "rate limit"})
        length = int(request.url.params["length"])
        return httpx.Response(200, json={"qrn": os.urandom(length).hex()})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        cfg = AnuApiSourceConfig(
            response_format="lfd_hex",
            max_retries=2,
            base_backoff_seconds=0.01,
            max_backoff_seconds=0.05,
        )
        src = AnuApiEntropySource(cfg, client=client)
        data = await src.fetch_bytes(64)
        assert len(data) == 64
        assert attempts == 2


async def test_qiskit_hadamard_qrng_passes_nist_validation() -> None:
    cfg = QiskitQrngSourceConfig(num_qubits=16, backend_type="aer_simulator")
    src = QiskitQrngEntropySource(cfg)
    try:
        data = await src.fetch_bytes(4096)
    finally:
        await src.close()

    assert len(data) == 4096
    health = run_nist_800_90b_health_tests(data)
    nist = run_nist_800_22_suite(data)
    assert health.passed is True
    assert nist.passed is True


async def test_xor_mixer_preserves_unpredictability_with_compromised_source() -> None:
    qiskit_src = QiskitQrngEntropySource(QiskitQrngSourceConfig(num_qubits=16))
    stuck_src = _StuckZeroSource()
    mixer = MultiSourceXorMixer([qiskit_src, stuck_src], min_healthy_sources=2)
    try:
        mixed = await mixer.fetch_mixed_bytes(4096)
    finally:
        await mixer.close()

    assert len(mixed) == 4096
    assert run_nist_800_90b_health_tests(mixed).passed is True
    assert run_nist_800_22_suite(mixed).passed is True


async def test_xor_mixer_enforces_min_healthy_sources() -> None:
    mixer = MultiSourceXorMixer(
        [_StuckZeroSource(), _FailingSource()],
        min_healthy_sources=2,
    )
    with pytest.raises(EntropySourceError, match="Insufficient healthy sources"):
        await mixer.fetch_mixed_bytes(64)


def test_xor_bytes_validates_lengths() -> None:
    assert xor_bytes([b"\xaa\x55", b"\xff\xff"]) == b"\x55\xaa"
    with pytest.raises(ValueError, match="length"):
        xor_bytes([b"\x00\x01", b"\x00"])


async def test_anu_api_json_hex_with_0x_and_json_uint8() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "hex" in str(request.url):
            return httpx.Response(200, json={"hex": "0x" + os.urandom(32).hex()})
        return httpx.Response(200, json={"data": list(os.urandom(32))})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        hex_src = AnuApiEntropySource(
            AnuApiSourceConfig(
                endpoint_url="https://example.test/hex",
                response_format="json_hex",
                max_retries=0,
            ),
            client=client,
        )
        assert len(await hex_src.fetch_bytes(32)) == 32

        u8_src = AnuApiEntropySource(
            AnuApiSourceConfig(
                endpoint_url="https://example.test/uint8",
                response_format="json_uint8",
                max_retries=0,
            ),
            client=client,
        )
        assert len(await u8_src.fetch_bytes(32)) == 32


async def test_qiskit_remote_rest_backend() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 16 qubits * 4 shots = 64 bits = 8 bytes
        return httpx.Response(200, json={"memory": ["1010110011001010"] * 4})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        cfg = QiskitQrngSourceConfig(
            num_qubits=16,
            backend_type="remote_rest",
            remote_url="https://quantum.example.test/run",
        )
        src = QiskitQrngEntropySource(cfg, http_client=client)
        data = await src.fetch_bytes(8)
        assert len(data) == 8


async def test_hwrng_device_source_and_errors(tmp_path: os.PathLike[str]) -> None:
    from pathlib import Path

    dev = Path(tmp_path) / "hwrng_mock"
    payload = os.urandom(128)
    dev.write_bytes(payload)

    src = HardwareDeviceEntropySource(HwrngSourceConfig(enabled=True, device_path=str(dev)))
    assert await src.fetch_bytes(64) == payload[:64]

    with pytest.raises(EntropySourceError, match="short read"):
        await src.fetch_bytes(256)

    missing = HardwareDeviceEntropySource(
        HwrngSourceConfig(enabled=True, device_path=str(Path(tmp_path) / "nonexistent"))
    )
    with pytest.raises(EntropySourceError, match="failed reading"):
        await missing.fetch_bytes(16)
