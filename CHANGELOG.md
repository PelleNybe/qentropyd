# Changelog

All notable changes to `qentropyd` will be documented in this file.

## [1.0.0] - 2026-11-23

### Added
- **Multi-Source Quantum Entropy Ingestion:** Async optical QRNG client (`AnuApiEntropySource` supporting ANU/LfD/JSON) and Qiskit Hadamard-gate ($H^{\otimes n}$) quantum circuit QRNG (`QiskitQrngEntropySource`).
- **Cryptographic XOR Mixer:** Concurrent `MultiSourceXorMixer` with per-source health tracking and configurable `min_healthy_sources`.
- **NIST SP 800-90B Continuous Health Tests:** Repetition Count Test (byte + bit level) and Adaptive Proportion Test.
- **NIST SP 800-22 Statistical Suite:** Monobit, Block Frequency, Runs, Longest Run of Ones, and Discrete Fourier Transform (FFT) tests.
- **Linux Kernel RNG Sink:** `paranoid` unprivileged `/dev/urandom` write mode and `credit` `ioctl(RNDADDENTROPY)` mode.
- **Daemon & Hardening:** Fail-closed circuit breaker, `SIGTERM`/`SIGINT`/`SIGHUP` handling, `syslog`/`journald` alerting, Prometheus `/metrics` exporter, CLI (`run`, `self-check`, `test-sources`, `validate-config`), and hardened `systemd` units.
