# Architecture of `qentropyd`

`qentropyd` is designed around a **zero-trust, fail-closed** entropy conditioning pipeline for Linux hosts.

## Component Architecture

1. **Asynchronous Entropy Sources (`src/qentropyd/sources/`)**
   - `AnuApiEntropySource`: Async `httpx` client supporting TLS-verified optical QRNG endpoints (ANU `jsonI.php`, LfD hex, generic JSON hex/uint8) with exponential backoff and jitter on HTTP 429/5xx or transport errors. Multiple endpoints can be configured via `extra_http_sources`.
   - `QiskitQrngEntropySource`: Constructs an $n$-qubit Hadamard circuit ($H^{\otimes n}$) followed by computational-basis measurement using `qiskit` and `qiskit-aer` (or a configurable remote quantum REST endpoint).
   - `HardwareDeviceEntropySource`: Async reader for Linux hardware TRNG/QRNG character devices (`/dev/hwrng`, USB QRNGs) with bounded read timeouts.
2. **Multi-Source Cryptographic XOR Mixer (`src/qentropyd/sources/mixer.py`)**
   - Concurrently fetches equal-length byte streams via `asyncio.gather`.
   - Computes $Y = S_1 \oplus S_2 \oplus \dots \oplus S_k$ using `numpy` uint8 vectorized bitwise XOR.
   - Enforces `min_healthy_sources` (default `2`) so that a single compromised, stuck, or biased source cannot degrade output unpredictability as long as at least one independent source remains genuine.
3. **Two-Stage Validation Gate (`src/qentropyd/validators/`)**
   - **Stage 1 — NIST SP 800-90B Continuous Health Tests (`health_check.py`)**: Repetition Count Test (byte + bit level) and Adaptive Proportion Test.
   - **Stage 2 — NIST SP 800-22 Statistical Suite (`nist_tests.py`)**: Monobit, Block Frequency, Runs, Longest Run of Ones, and Discrete Fourier Transform (FFT).
4. **Fail-Closed Circuit Breaker (`src/qentropyd/daemon.py`)**
   - Any failure in Stage 1 or Stage 2 immediately discards the entire candidate batch, never touches `/dev/urandom`, increments `qentropyd_batches_rejected_total`, and logs a `LOG_WARNING` to `syslog`/`journald`.
   - Repeated consecutive failures (`circuit_breaker_threshold`) open the circuit breaker, emit `LOG_CRIT`, and enter a cooldown period.
5. **Linux Kernel RNG Sink (`src/qentropyd/sink/linux_rng.py`)**
   - `paranoid` mode (default): Unprivileged `write()` to `/dev/urandom` (stirs kernel ChaCha20 pool without crediting entropy bits).
   - `credit` mode: `ioctl(fd, RNDADDENTROPY, struct rand_pool_info)` (`0x40085203`) with conservative bit crediting (`CAP_SYS_ADMIN` required).
6. **Observability (`src/qentropyd/metrics.py`)**
   - Prometheus `/metrics` HTTP endpoint exposing batch acceptance/rejection counters, per-test $P$-values, source fetch latency/errors, and live `/proc/sys/kernel/random/entropy_avail`.
