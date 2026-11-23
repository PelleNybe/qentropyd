"""NIST SP 800-90B continuous health tests (Repetition Count & Adaptive Proportion)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import binom


@dataclass(frozen=True, slots=True)
class HealthTestResult:
    """Result of a single NIST SP 800-90B continuous health test."""

    name: str
    passed: bool
    cutoff: int
    observed_max: int
    details: str = ""


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """Aggregated result of NIST SP 800-90B continuous health tests."""

    passed: bool
    results: tuple[HealthTestResult, ...]
    failed_tests: tuple[str, ...]


def compute_rct_cutoff(min_entropy_h: float, alpha: float = 2.0**-20) -> int:
    """Compute NIST SP 800-90B §4.4.1 Repetition Count Test cutoff C.

    C = 1 + ceil(-log2(alpha) / H)
    """
    if min_entropy_h <= 0.0:
        raise ValueError("min_entropy_h must be > 0")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1)")
    return 1 + math.ceil(-math.log2(alpha) / min_entropy_h)


def compute_apt_cutoff(
    window_size: int,
    min_entropy_h: float,
    alpha: float = 2.0**-20,
) -> int:
    """Compute NIST SP 800-90B §4.4.2 Adaptive Proportion Test cutoff C.

    Finds the smallest integer C such that Pr[Binomial(W-1, 2^-H) >= C-1] <= alpha.
    """
    if window_size < 2:
        raise ValueError("window_size must be >= 2")
    if min_entropy_h <= 0.0:
        raise ValueError("min_entropy_h must be > 0")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1)")

    p_max = 2.0 ** (-min_entropy_h)
    # binom.isf(alpha, n, p) returns k such that sf(k) = Pr[X > k] <= alpha.
    # Since sf(k) = Pr[X >= k + 1], setting C - 1 = k + 1 => C = k + 2.
    k = int(binom.isf(alpha, window_size - 1, p_max))
    cutoff = k + 2
    while cutoff > 2 and float(binom.sf(cutoff - 2, window_size - 1, p_max)) <= alpha:
        cutoff -= 1
    while float(binom.sf(cutoff - 2, window_size - 1, p_max)) > alpha:
        cutoff += 1
    return min(cutoff, window_size)


def repetition_count_test(
    data: bytes,
    min_entropy_bits_per_byte: float = 4.0,
    alpha: float = 2.0**-20,
    check_bits: bool = True,
) -> HealthTestResult:
    """NIST SP 800-90B §4.4.1 Repetition Count Test (byte-level + optional bit-level)."""
    if not data:
        return HealthTestResult(
            name="rct",
            passed=False,
            cutoff=1,
            observed_max=0,
            details="Empty input buffer",
        )

    byte_cutoff = compute_rct_cutoff(min_entropy_bits_per_byte, alpha=alpha)
    max_byte_run = 1
    current_byte = data[0]
    run_len = 1
    for b in data[1:]:
        if b == current_byte:
            run_len += 1
            if run_len > max_byte_run:
                max_byte_run = run_len
        else:
            current_byte = b
            run_len = 1

    if max_byte_run >= byte_cutoff:
        return HealthTestResult(
            name="rct",
            passed=False,
            cutoff=byte_cutoff,
            observed_max=max_byte_run,
            details=f"Byte repetition {max_byte_run} >= cutoff {byte_cutoff}",
        )

    if check_bits:
        # Conservative bit-level RCT assuming H_bit = 0.5 bits/bit
        bit_cutoff = compute_rct_cutoff(0.5, alpha=alpha)
        max_bit_run = 1
        prev_bit = (data[0] >> 7) & 1
        bit_run = 0
        for byte in data:
            for shift in range(7, -1, -1):
                bit = (byte >> shift) & 1
                if bit == prev_bit:
                    bit_run += 1
                    if bit_run > max_bit_run:
                        max_bit_run = bit_run
                else:
                    prev_bit = bit
                    bit_run = 1
        if max_bit_run >= bit_cutoff:
            return HealthTestResult(
                name="rct",
                passed=False,
                cutoff=bit_cutoff,
                observed_max=max_bit_run,
                details=f"Bit repetition {max_bit_run} >= bit_cutoff {bit_cutoff}",
            )

    return HealthTestResult(
        name="rct",
        passed=True,
        cutoff=byte_cutoff,
        observed_max=max_byte_run,
        details=f"max_byte_run={max_byte_run} < {byte_cutoff}",
    )


def adaptive_proportion_test(
    data: bytes,
    window_size: int = 512,
    min_entropy_bits_per_byte: float = 4.0,
    alpha: float = 2.0**-20,
) -> HealthTestResult:
    """NIST SP 800-90B §4.4.2 Adaptive Proportion Test over non-overlapping windows."""
    if len(data) < window_size:
        return HealthTestResult(
            name="apt",
            passed=False,
            cutoff=window_size,
            observed_max=0,
            details=f"Buffer length {len(data)} < window_size {window_size}",
        )

    cutoff = compute_apt_cutoff(
        window_size=window_size,
        min_entropy_h=min_entropy_bits_per_byte,
        alpha=alpha,
    )
    observed_max = 0
    num_windows = len(data) // window_size
    for w_idx in range(num_windows):
        start = w_idx * window_size
        window = data[start : start + window_size]
        target = window[0]
        count = 1 + window[1:].count(target)
        if count > observed_max:
            observed_max = count
        if count >= cutoff:
            return HealthTestResult(
                name="apt",
                passed=False,
                cutoff=cutoff,
                observed_max=observed_max,
                details=f"Window {w_idx} symbol 0x{target:02x} count {count} >= cutoff {cutoff}",
            )

    return HealthTestResult(
        name="apt",
        passed=True,
        cutoff=cutoff,
        observed_max=observed_max,
        details=f"max_window_count={observed_max} < {cutoff}",
    )


def run_nist_800_90b_health_tests(
    data: bytes,
    min_entropy_bits_per_byte: float = 4.0,
    alpha: float = 2.0**-20,
    apt_window_size: int = 512,
    enable_bit_rct: bool = True,
) -> HealthCheckResult:
    """Run NIST SP 800-90B continuous health tests (RCT + APT) on a byte buffer."""
    rct = repetition_count_test(
        data,
        min_entropy_bits_per_byte=min_entropy_bits_per_byte,
        alpha=alpha,
        check_bits=enable_bit_rct,
    )
    apt = adaptive_proportion_test(
        data,
        window_size=apt_window_size,
        min_entropy_bits_per_byte=min_entropy_bits_per_byte,
        alpha=alpha,
    )
    results = (rct, apt)
    failed = tuple(r.name for r in results if not r.passed)
    return HealthCheckResult(
        passed=len(failed) == 0,
        results=results,
        failed_tests=failed,
    )
