"""Command-line interface for qentropyd."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from qentropyd.config import load_config
from qentropyd.daemon import QEntropyDaemon, build_sources
from qentropyd.sources.qiskit_qrng import QiskitQrngEntropySource
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

    test_src = sub.add_parser(
        "test-sources",
        help="Fetch a sample from configured sources and report health",
    )
    test_src.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output",
    )

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
    self_check.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output",
    )
    return parser


async def _cmd_test_sources(config_path: Path | None, json_output: bool) -> int:
    cfg = load_config(config_path)
    sources = build_sources(cfg)
    exit_code = 0
    results: list[dict[str, Any]] = []
    for src in sources:
        try:
            sample = await src.fetch_bytes(64)
            results.append(
                {
                    "name": src.name,
                    "status": "ok",
                    "bytes": len(sample),
                    "sample_prefix_hex": sample[:4].hex(),
                }
            )
            if not json_output:
                print(
                    f"[OK] {src.name}: fetched {len(sample)} bytes "
                    f"(prefix={sample[:4].hex()})"
                )
        except Exception as exc:
            exit_code = 1
            results.append({"name": src.name, "status": "error", "error": str(exc)})
            if not json_output:
                print(f"[FAIL] {src.name}: {exc}", file=sys.stderr)
        finally:
            await src.close()

    if json_output:
        print(json.dumps({"sources": results}, indent=2))
    return exit_code


async def _cmd_self_check(
    config_path: Path | None,
    num_bytes: int,
    json_output: bool,
) -> int:
    cfg = load_config(config_path)
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

    overall_pass = health.passed and nist.passed
    if json_output:
        payload = {
            "passed": overall_pass,
            "bytes": num_bytes,
            "sp800_90b": {
                "passed": health.passed,
                "tests": [
                    {
                        "name": h.name,
                        "passed": h.passed,
                        "cutoff": h.cutoff,
                        "observed_max": h.observed_max,
                        "details": h.details,
                    }
                    for h in health.results
                ],
            },
            "sp800_22": {
                "passed": nist.passed,
                "tests": [
                    {
                        "name": n.name,
                        "passed": n.passed,
                        "p_value": n.p_value,
                        "statistic": n.statistic,
                    }
                    for n in nist.results
                ],
            },
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"NIST SP 800-90B Health Tests: {'PASS' if health.passed else 'FAIL'}")
        for h_res in health.results:
            print(f"  - {h_res.name}: passed={h_res.passed} ({h_res.details})")

        print(f"NIST SP 800-22 Statistical Suite: {'PASS' if nist.passed else 'FAIL'}")
        for n_res in nist.results:
            print(f"  - {n_res.name}: p={n_res.p_value:.6f} passed={n_res.passed}")

    return 0 if overall_pass else 1


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
        return asyncio.run(_cmd_test_sources(args.config, args.json_output))

    if args.command == "self-check":
        return asyncio.run(_cmd_self_check(args.config, args.bytes, args.json_output))

    if args.command == "run":
        cfg = load_config(args.config)
        daemon = QEntropyDaemon(cfg, config_path=args.config)
        asyncio.run(daemon.run_forever())
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
