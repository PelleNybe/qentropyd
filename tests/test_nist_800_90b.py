"""Tests for NIST SP 800-90B continuous health tests (RCT & APT)."""

from __future__ import annotations

import os

from qentropyd.validators.health_check import (
    adaptive_proportion_test,
    compute_apt_cutoff,
    compute_rct_cutoff,
    repetition_count_test,
    run_nist_800_90b_health_tests,
)


def test_rct_and_apt_cutoff_values() -> None:
    # For H=4.0 bits/byte and alpha=2^-20: C_RCT = 1 + ceil(20/4) = 6
    assert compute_rct_cutoff(4.0, alpha=2.0**-20) == 6
    # For H=1.0 bits/sample and alpha=2^-20: C_RCT = 21
    assert compute_rct_cutoff(1.0, alpha=2.0**-20) == 21
    # APT cutoff for W=512, H=4.0 (p=1/16, mean~32) should be around 60-80
    apt_c = compute_apt_cutoff(512, 4.0, alpha=2.0**-20)
    assert 55 <= apt_c <= 85


def test_health_tests_pass_csprng() -> None:
    data = os.urandom(4096)
    res = run_nist_800_90b_health_tests(data)
    assert res.passed is True
    assert res.failed_tests == ()


def test_rct_rejects_stuck_at_zero_and_stuck_at_one() -> None:
    assert repetition_count_test(b"\x00" * 512).passed is False
    assert repetition_count_test(b"\xff" * 512).passed is False


def test_rct_bit_level_catches_alternating_or_long_bit_runs() -> None:
    # Byte values differ (0x00, 0x01, 0x00, ...), but contains long runs of 0 bits (15+ bits)
    # With 8 consecutive 0x00 bytes -> 64 consecutive zero bits > bit_cutoff (41 for H_bit=0.5)
    payload = b"\x00" * 8 + os.urandom(512)
    res = repetition_count_test(payload, min_entropy_bits_per_byte=4.0, check_bits=True)
    assert res.passed is False


def test_apt_rejects_dominant_symbol_in_window() -> None:
    # 512-byte window where 200 bytes are 0x42 (far above cutoff ~68 for H=4.0)
    base = bytearray(os.urandom(512))
    for i in range(0, 400, 2):
        base[i] = 0x42
    res = adaptive_proportion_test(bytes(base), window_size=512, min_entropy_bits_per_byte=4.0)
    assert res.passed is False
