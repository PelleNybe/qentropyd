"""Exact NIST SP 800-22 Rev. 1a statistical test suite implementation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from scipy.special import erfc, gammaincc

if TYPE_CHECKING:
    from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class NistTestResult:
    """Result of a single NIST SP 800-22 statistical test."""

    name: str
    p_value: float
    passed: bool
    statistic: float
    details: str = ""


@dataclass(frozen=True, slots=True)
class NistSuiteResult:
    """Aggregated result of the NIST SP 800-22 test suite."""

    passed: bool
    results: tuple[NistTestResult, ...]
    failed_tests: tuple[str, ...]


def bytes_to_bits(data: bytes) -> NDArray[np.uint8]:
    """Convert raw bytes to a 1-D MSB-first binary numpy array of {0, 1}."""
    if not data:
        return np.empty(0, dtype=np.uint8)
    arr = np.frombuffer(data, dtype=np.uint8)
    return np.unpackbits(arr, bitorder="big")


def frequency_monobit_test(
    bits: NDArray[np.uint8],
    alpha: float = 0.01,
) -> NistTestResult:
    """NIST SP 800-22 §2.1: Frequency (Monobit) Test."""
    n = int(bits.size)
    if n < 100:
        return NistTestResult(
            name="monobit",
            p_value=0.0,
            passed=False,
            statistic=0.0,
            details=f"Insufficient bits ({n} < 100)",
        )
    s_n = int(np.sum(2 * bits.astype(np.int64) - 1))
    s_obs = abs(s_n) / math.sqrt(n)
    p_value = float(erfc(s_obs / math.sqrt(2.0)))
    return NistTestResult(
        name="monobit",
        p_value=p_value,
        passed=p_value >= alpha,
        statistic=s_obs,
        details=f"S_n={s_n}, n={n}",
    )


def block_frequency_test(
    bits: NDArray[np.uint8],
    block_size: int = 128,
    alpha: float = 0.01,
) -> NistTestResult:
    """NIST SP 800-22 §2.2: Frequency Test within a Block."""
    n = int(bits.size)
    if block_size < 20:
        raise ValueError("block_size M must be >= 20 per NIST SP 800-22 §2.2")
    num_blocks = n // block_size
    if num_blocks < 1:
        return NistTestResult(
            name="block_frequency",
            p_value=0.0,
            passed=False,
            statistic=0.0,
            details=f"Insufficient bits ({n}) for block_size={block_size}",
        )
    usable = bits[: num_blocks * block_size].reshape(num_blocks, block_size)
    pi = np.mean(usable, axis=1, dtype=np.float64)
    chi_sq = 4.0 * block_size * float(np.sum((pi - 0.5) ** 2))
    p_value = float(gammaincc(num_blocks / 2.0, chi_sq / 2.0))
    return NistTestResult(
        name="block_frequency",
        p_value=p_value,
        passed=p_value >= alpha,
        statistic=chi_sq,
        details=f"N={num_blocks}, M={block_size}",
    )


def runs_test(
    bits: NDArray[np.uint8],
    alpha: float = 0.01,
) -> NistTestResult:
    """NIST SP 800-22 §2.3: Runs Test."""
    n = int(bits.size)
    if n < 100:
        return NistTestResult(
            name="runs",
            p_value=0.0,
            passed=False,
            statistic=0.0,
            details=f"Insufficient bits ({n} < 100)",
        )
    pi = float(np.mean(bits, dtype=np.float64))
    tau = 2.0 / math.sqrt(n)
    if abs(pi - 0.5) >= tau:
        return NistTestResult(
            name="runs",
            p_value=0.0,
            passed=False,
            statistic=0.0,
            details=f"Failed monobit prerequisite: |pi - 0.5|={abs(pi - 0.5):.6f} >= tau={tau:.6f}",
        )

    v_obs = 1 + int(np.sum(bits[1:] != bits[:-1]))
    denom = 2.0 * math.sqrt(2.0 * n) * pi * (1.0 - pi)
    p_value = 0.0 if denom == 0.0 else float(erfc(abs(v_obs - 2.0 * n * pi * (1.0 - pi)) / denom))

    return NistTestResult(
        name="runs",
        p_value=p_value,
        passed=p_value >= alpha,
        statistic=float(v_obs),
        details=f"V_obs={v_obs}, pi={pi:.6f}",
    )


def _longest_run_in_block(block: NDArray[np.uint8]) -> int:
    """Compute the length of the longest consecutive run of 1s in a 1-D bit array."""
    max_run = 0
    current = 0
    for bit in block:
        if bit:
            current += 1
            if current > max_run:
                max_run = current
        else:
            current = 0
    return max_run


def longest_run_of_ones_test(
    bits: NDArray[np.uint8],
    alpha: float = 0.01,
) -> NistTestResult:
    """NIST SP 800-22 §2.4: Test for the Longest Run of Ones in a Block."""
    n = int(bits.size)
    if n < 128:
        return NistTestResult(
            name="longest_run",
            p_value=0.0,
            passed=False,
            statistic=0.0,
            details=f"Insufficient bits ({n} < 128)",
        )

    pi: tuple[float, ...]
    if n < 6272:
        m = 8
        k = 3
        min_val = 1
        max_val = 4
        pi = (0.2148, 0.3672, 0.2305, 0.1875)
    elif n < 750000:
        m = 128
        k = 5
        min_val = 4
        max_val = 9
        pi = (0.1174, 0.2430, 0.2493, 0.1752, 0.1027, 0.1124)
    else:
        m = 10000
        k = 6
        min_val = 10
        max_val = 16
        pi = (0.0882, 0.2092, 0.2483, 0.1933, 0.1208, 0.0675, 0.0727)

    num_blocks = n // m
    usable = bits[: num_blocks * m].reshape(num_blocks, m)
    counts = [0] * (k + 1)

    for idx in range(num_blocks):
        run = _longest_run_in_block(usable[idx])
        if run <= min_val:
            counts[0] += 1
        elif run >= max_val:
            counts[k] += 1
        else:
            counts[run - min_val] += 1

    chi_sq = sum(
        ((counts[i] - num_blocks * pi[i]) ** 2) / (num_blocks * pi[i])
        for i in range(k + 1)
    )
    p_value = float(gammaincc(k / 2.0, chi_sq / 2.0))
    return NistTestResult(
        name="longest_run",
        p_value=p_value,
        passed=p_value >= alpha,
        statistic=chi_sq,
        details=f"N={num_blocks}, M={m}, counts={counts}",
    )


def discrete_fourier_transform_test(
    bits: NDArray[np.uint8],
    alpha: float = 0.01,
) -> NistTestResult:
    """NIST SP 800-22 §2.6: Discrete Fourier Transform (Spectral) Test."""
    n = int(bits.size)
    if n < 1000:
        return NistTestResult(
            name="fft_spectral",
            p_value=0.0,
            passed=False,
            statistic=0.0,
            details=f"Insufficient bits ({n} < 1000)",
        )

    x = 2.0 * bits.astype(np.float64) - 1.0
    spectrum = np.fft.fft(x)
    magnitudes = np.abs(spectrum[: n // 2])
    threshold = math.sqrt(math.log(1.0 / 0.05) * n)
    n_0 = 0.95 * (n / 2.0)
    n_1 = int(np.sum(magnitudes < threshold))
    d = (n_1 - n_0) / math.sqrt(n * 0.95 * 0.05 / 4.0)
    p_value = float(erfc(abs(d) / math.sqrt(2.0)))
    return NistTestResult(
        name="fft_spectral",
        p_value=p_value,
        passed=p_value >= alpha,
        statistic=d,
        details=f"N1={n_1}, N0={n_0:.2f}, T={threshold:.2f}",
    )


def run_nist_800_22_suite(
    data: bytes,
    alpha: float = 0.01,
    block_frequency_m: int = 128,
) -> NistSuiteResult:
    """Run all 5 NIST SP 800-22 statistical tests on a candidate byte buffer."""
    bits = bytes_to_bits(data)
    results = (
        frequency_monobit_test(bits, alpha=alpha),
        block_frequency_test(bits, block_size=block_frequency_m, alpha=alpha),
        runs_test(bits, alpha=alpha),
        longest_run_of_ones_test(bits, alpha=alpha),
        discrete_fourier_transform_test(bits, alpha=alpha),
    )
    failed = tuple(r.name for r in results if not r.passed)
    return NistSuiteResult(
        passed=len(failed) == 0,
        results=results,
        failed_tests=failed,
    )
