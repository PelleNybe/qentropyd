# `qentropyd` — Self-Verifying Quantum Entropy Daemon for Linux

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Linux Kernel](https://img.shields.io/badge/Linux-5.x%20%7C%206.x-FCC624.svg?logo=linux&logoColor=black)](https://kernel.org/)
[![NIST SP 800-22 & 800-90B](https://img.shields.io/badge/NIST-SP%20800--22%20%26%20800--90B-0A7E3A.svg)](docs/STATISTICAL_TESTS.md)
[![Prometheus Metrics](https://img.shields.io/badge/Prometheus-%2Fmetrics-E6522C.svg?logo=prometheus&logoColor=white)](docs/OPERATOR_MANUAL.md)
[![CI](https://img.shields.io/github/actions/workflow/status/CoraxCoLAB/qentropyd/ci.yml?label=CI)](.github/workflows/ci.yml)

**Zero-trust, self-verifying quantum entropy conditioning for Linux servers, HSMs, and edge nodes.**

`qentropyd` harvests raw bitstreams from independent quantum entropy sources, cryptographically XOR-mixes them, and gates every single batch through **NIST SP 800-90B** continuous health tests and **NIST SP 800-22** statistical tests before a single byte ever reaches `/dev/urandom`. If any test fails, the **fail-closed circuit breaker** immediately discards the batch, alerts `syslog`/`journald`, and exports Prometheus telemetry.

---

## Architecture & Dataflow

```mermaid
flowchart LR
    S1["Optical QRNG API\n(ANU / LfD TLS)"] --> M["Multi-Source\nXOR Mixer"]
    S2["Qiskit Hadamard QRNG\n(H^n Superposition)"] --> M
    M --> G1["NIST SP 800-90B\nRCT & APT Gate"]
    G1 -->|PASS| G2["NIST SP 800-22\n5-Test Suite Gate"]
    G1 -->|FAIL| CB["Fail-Closed\nCircuit Breaker"]
    G2 -->|FAIL| CB
    CB --> LOG["syslog / journald\n+ Rejection Metrics"]
    G2 -->|PASS| SINK["Linux RNG Sink\n(paranoid / ioctl)"]
    SINK --> URAND["/dev/urandom\nKernel CSPRNG"]
    G2 --> PROM["Prometheus\n/metrics"]
```

---

## Why `qentropyd`?

- **Cryptographic Defense-in-Depth:** Equal-length bitwise XOR mixing (`MultiSourceXorMixer`) guarantees that as long as *at least one* quantum source is genuine and uncompromised, the mixed stream remains unpredictable.
- **Mathematical Self-Verification:** Every batch is validated in real time using exact `scipy.special` (`erfc`, `gammaincc`, `binom`) and `numpy.fft` implementations of NIST SP 800-90B and SP 800-22.
- **Fail-Closed Guarantee:** Compromised, stuck-at-zero, periodic, or biased streams are **never** written to `/dev/urandom`.
- **Unprivileged by Default (`paranoid` mode):** Stirs `/dev/urandom` under `DynamicUser=yes` with an empty `CapabilityBoundingSet=`. Optional `credit` mode uses `ioctl(RNDADDENTROPY)` with strictly scoped `CAP_SYS_ADMIN`.
- **Production Observability:** Native Prometheus `/metrics` endpoint, `SIGHUP` hot-reload, and `systemd` journald integration.

---

## Quickstart

```bash
# 1. Install qentropyd
pip install .

# 2. Run the NIST SP 800-90B & SP 800-22 self-check using Qiskit Hadamard QRNG
qentropyd self-check --bytes 4096

# 3. Validate a production configuration
qentropyd -c systemd/qentropyd.example.yaml validate-config

# 4. Run the daemon
qentropyd -c systemd/qentropyd.example.yaml run
```

---

## Deep Dive

<details>
<summary><strong>Implemented NIST SP 800-22 &amp; SP 800-90B Tests &amp; Thresholds</strong></summary>

| Standard | Test | Purpose | Default Threshold |
| :--- | :--- | :--- | :--- |
| **SP 800-90B** | Repetition Count Test (RCT) | Stuck-at fault detection (byte + bit level) | $H = 4.0$, $\alpha = 2^{-20}$ ($C = 6$) |
| **SP 800-90B** | Adaptive Proportion Test (APT) | Symbol dominance over $W = 512$ window | Exact binomial cutoff ($\alpha = 2^{-20}$) |
| **SP 800-22** | Frequency (Monobit) Test | Global 0/1 balance | $P \ge 0.01$ |
| **SP 800-22** | Block Frequency Test | Local 0/1 balance ($M = 128$) | $P \ge 0.01$ |
| **SP 800-22** | Runs Test | Oscillation rate &amp; monobit prerequisite | $P \ge 0.01$ |
| **SP 800-22** | Longest Run of Ones | Block run-length distribution | $P \ge 0.01$ |
| **SP 800-22** | Discrete Fourier Transform (FFT) | Periodic / spectral anomaly detection | $P \ge 0.01$ |

</details>

<details>
<summary><strong>systemd Sandboxing &amp; Privilege Separation</strong></summary>

- **`systemd/qentropyd.service` (Default Paranoid Mode):**
  - `DynamicUser=yes`, `CapabilityBoundingSet=` (zero capabilities), `NoNewPrivileges=yes`
  - `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, `MemoryDenyWriteExecute=true`
  - `DevicePolicy=closed`, `DeviceAllow=/dev/urandom rw`
  - `SystemCallFilter=@system-service` &amp; `~@privileged @resources @mount @reboot @swap @obsolete`
- **`systemd/qentropyd-credit.service` (Kernel Credit Mode):**
  - Identical sandboxing with `CapabilityBoundingSet=CAP_SYS_ADMIN` and `AmbientCapabilities=CAP_SYS_ADMIN` strictly for `ioctl(RNDADDENTROPY)` (`0x40085203`).

</details>

<details>
<summary><strong>Sample Prometheus <code>/metrics</code> Output</strong></summary>

```prometheus
# HELP qentropyd_batches_accepted_total Total number of entropy batches passing all NIST SP 800-90B and 800-22 gates
# TYPE qentropyd_batches_accepted_total counter
qentropyd_batches_accepted_total 42.0
# HELP qentropyd_bytes_injected_total Total number of verified quantum entropy bytes injected into the Linux kernel RNG
# TYPE qentropyd_bytes_injected_total counter
qentropyd_bytes_injected_total 172032.0
# HELP qentropyd_nist_p_value Latest NIST SP 800-22 statistical test p-value
# TYPE qentropyd_nist_p_value gauge
qentropyd_nist_p_value{test_name="monobit"} 0.617075
qentropyd_nist_p_value{test_name="block_frequency"} 0.483921
qentropyd_nist_p_value{test_name="runs"} 0.724102
qentropyd_nist_p_value{test_name="longest_run"} 0.531894
qentropyd_nist_p_value{test_name="fft_spectral"} 0.812440
# HELP qentropyd_kernel_entropy_avail_bits Live Linux kernel entropy_avail in bits
# TYPE qentropyd_kernel_entropy_avail_bits gauge
qentropyd_kernel_entropy_avail_bits 256.0
```

</details>

---

## Documentation

- [Architecture Overview](docs/ARCHITECTURE.md)
- [Statistical &amp; Health Tests](docs/STATISTICAL_TESTS.md)
- [Security Hardening Guide](docs/SECURITY_HARDENING.md)
- [Operator Manual](docs/OPERATOR_MANUAL.md)
- [Security Policy](SECURITY.md) &bull; [Contributing](CONTRIBUTING.md) &bull; [Changelog](CHANGELOG.md)

---

## Creators &amp; Credits

- **Organization:** [Corax CoLAB](https://coraxcolab.com/) — Deep Tech, Edge AI &amp; Linux Security Engineering.
- **Lead Developer:** Pelle Nyberg ([GitHub: @PelleNybe](https://github.com/PelleNybe)).

Licensed under the [MIT License](LICENSE).
