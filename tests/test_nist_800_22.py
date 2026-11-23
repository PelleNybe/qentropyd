"""Tests for NIST SP 800-22 Rev. 1a statistical test suite."""

from __future__ import annotations

import os

import numpy as np
import pytest

from qentropyd.validators.nist_tests import (
    block_frequency_test,
    bytes_to_bits,
    discrete_fourier_transform_test,
    frequency_monobit_test,
    longest_run_of_ones_test,
    run_nist_800_22_suite,
    runs_test,
)


def _bitstring_to_array(bitstring: str) -> np.ndarray:
    return np.array([int(ch) for ch in bitstring], dtype=np.uint8)


def test_nist_sp800_22_monobit_reference_vector() -> None:
    # NIST SP 800-22 §2.1.8 example (100 bits, S_100 = -16, P = 0.109599)
    vec = (
        "11001001000011111101101010100010001000010110100011"
        "00001000110100110001001100011001100010100010111000"
    )
    bits = _bitstring_to_array(vec)
    res = frequency_monobit_test(bits, alpha=0.01)
    assert res.passed is True
    assert res.p_value == pytest.approx(0.109599, abs=1e-5)


def test_nist_sp800_22_runs_reference_vector() -> None:
    # NIST SP 800-22 §2.3.8 example (100 bits)
    vec = (
        "11001001000011111101101010100010001000010110100011"
        "00001000110100110001001100011001100010100010111000"
    )
    bits = _bitstring_to_array(vec)
    res = runs_test(bits, alpha=0.01)
    assert res.passed is True
    assert res.p_value == pytest.approx(0.500798, abs=1e-4)


def test_nist_sp800_22_longest_run_reference_vector() -> None:
    # NIST SP 800-22 §2.4.8 example (128 bits, M=8, K=3)
    vec = (
        "1100110000010101011011000100110011100000000000100100110101010001"
        "0001001111010110100000001101011111001100111001101101100010110010"
    )
    bits = _bitstring_to_array(vec)
    res = longest_run_of_ones_test(bits, alpha=0.01)
    assert res.passed is True
    assert 0.01 <= res.p_value <= 1.0


def test_nist_sp800_22_passes_csprng_batch() -> None:
    # Deterministic 8192-byte (65536-bit) SHA-256 CTR CSPRNG stream
    import hashlib

    buf = bytearray()
    for counter in range(256):
        buf.extend(hashlib.sha256(counter.to_bytes(4, "big")).digest())
    suite = run_nist_800_22_suite(bytes(buf), alpha=0.01, block_frequency_m=128)
    assert suite.passed is True, f"Failed tests: {suite.failed_tests}, results: {suite.results}"
    assert len(suite.results) == 5


@pytest.mark.parametrize(
    "bad_data",
    [
        b"\x00" * 4096,  # Stuck-at-zero
        b"\xff" * 4096,  # Stuck-at-one
        b"\xaa" * 4096,  # Alternating 10101010
        (b"\x0f\xf0\xaa\x55" * 1024),  # Periodic 4-byte pattern
    ],
)
def test_nist_sp800_22_rejects_degenerate_streams(bad_data: bytes) -> None:
    suite = run_nist_800_22_suite(bad_data, alpha=0.01, block_frequency_m=128)
    assert suite.passed is False
    assert len(suite.failed_tests) >= 1


def test_nist_sp800_22_rejects_biased_stream() -> None:
    rng = np.random.default_rng(seed=42)
    # 70% ones, 30% zeros -> strongly biased
    biased_bits = (rng.random(32768) < 0.70).astype(np.uint8)
    biased_bytes = np.packbits(biased_bits, bitorder="big").tobytes()
    suite = run_nist_800_22_suite(biased_bytes, alpha=0.01)
    assert suite.passed is False
    assert "monobit" in suite.failed_tests
    assert "runs" in suite.failed_tests


def test_fft_detects_periodic_signal() -> None:
    # 4096 bytes of 16-byte repeating random-looking block -> periodic in frequency domain
    block = os.urandom(16)
    periodic = block * 256
    bits = bytes_to_bits(periodic)
    res = discrete_fourier_transform_test(bits, alpha=0.01)
    assert res.passed is False


def test_block_frequency_rejects_low_m() -> None:
    bits = bytes_to_bits(os.urandom(256))
    with pytest.raises(ValueError, match="block_size"):
        block_frequency_test(bits, block_size=8)
