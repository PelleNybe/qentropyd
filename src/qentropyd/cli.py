"""Command-line interface for qentropyd."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from qentropyd.config import load_config
from qentropyd.daemon import QEntropyDaemon, build_sources
from qentropyd.validators.health_check import run_nist_800_90b_health_tests
from qentropyd.validators.nist_tests import run_nist_800_22_suite


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qentropyd",
        description="Self-verifying Quantum Entropy Daemon for Linux",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Start the quantum entropy daemon")
    sub.add_parser("validate-config", help="Validate YAML configuration and exit")
    sub.add_parser("test-sources", help="Fetch a sample from configured sources and report health")

    self_check = sub.add_parser(
        "self-check",
        help="Run NIST SP 800-90B and SP 800-22 self-check against Qiskit QRNG",
    )
    self_check.add_argument(
        "--bytes",
        type=int,
        default=4096,
        help="Number of bytes to generate and validate (default: 4096)",
    )
    return parser


async def _cmd_test_sources(config_path: Path | None) -> int:
    cfg = load_config(config_path)
    sources = build_sources(cfg)
    exit_code = 0
    for src in sources:
        try:
            sample = await src.fetch_bytes(64)
            print(f"[OK] {src.name}: fetched {len(sample)} bytes (sha256_prefix={sample[:4].hex()})")
        except Exception as exc:
            print(f"[FAIL] {src.name}: {exc}", file=sys.stderr)
            exit_code = 1
        finally:
            await src.close()
    return exit_code


async def _cmd_self_check(config_path: Path | None, num_bytes: int) -> int:
    cfg = load_config(config_path)
    from qentropyd.sources.qiskit_qrng import QiskitQrngEntropySource

    source = QiskitQrngEntropySource(cfg.qiskit_qrng)
    try:
        sample = await source.fetch_bytes(num_bytes)
    finally:
        await source.close()

    health = run_nist_800_90b_health_tests(
        sample,
        min_entropy_bits_per_byte=cfg.nist_800_90b.min_entropy_bits_per_byte,
        alpha=cfg.nist_800_90b.alpha_90b,
        apt_window_size=cfg.nist_800_90b.apt_window_size,
        enable_bit_rct=cfg.nist_800_90b.enable_bit_rct,
    )
    nist = run_nist_800_22_suite(
        sample,
        alpha=cfg.nist_800_22.significance_level,
        block_frequency_m=cfg.nist_800_22.block_frequency_m,
    )

    print(f"NIST SP 800-90B Health Tests: {'PASS' if health.passed else 'FAIL'}")
    for h_res in health.results:
        print(f"  - {h_res.name}: passed={h_res.passed} ({h_res.details})")

    print(f"NIST SP 800-22 Statistical Suite: {'PASS' if nist.passed else 'FAIL'}")
    for n_res in nist.results:
        print(f"  - {n_res.name}: p={n_res.p_value:.6f} passed={n_res.passed}")

    return 0 if (health.passed and nist.passed) else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for qentropyd."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    if args.command == "validate-config":
        cfg = load_config(args.config)
        print(
            f"Configuration valid: mode={cfg.sink.mode}, "
            f"batch_bytes={cfg.mixer.batch_bytes}, "
            f"min_healthy_sources={cfg.mixer.min_healthy_sources}"
        )
        return 0

    if args.command == "test-sources":
        return asyncio.run(_cmd_test_sources(args.config))

    if args.command == "self-check":
        return asyncio.run(_cmd_self_check(args.config, args.bytes))

    if args.command == "run":
        cfg = load_config(args.config)
        daemon = QEntropyDaemon(cfg, config_path=args.config)
        asyncio.run(daemon.run_forever())
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
