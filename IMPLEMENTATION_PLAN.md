# `qentropyd` — Architecture & Implementation Plan

**Organization:** Corax CoLAB  
**Target Platform:** Linux (x86_64 / ARM64, Kernel 5.x/6.x+, `systemd`)  
**Language & Runtime:** Python 3.11+ (`asyncio`, strict `mypy` typing)  
**License:** MIT  

---

## 1. System Overview & Entropy Ingestion Pipeline

`qentropyd` is a self-verifying, fail-closed Quantum Entropy Daemon for Linux. It continuously harvests raw random bitstreams from multiple independent quantum entropy sources, cryptographically mixes them, subjects the combined stream to rigorous NIST SP 800-90B continuous health tests and NIST SP 800-22 statistical tests, and injects only verified high-entropy bytes into the Linux kernel CSPRNG (`/dev/urandom`).

### Pipeline Dataflow

```text
+-----------------------+       +-----------------------+
|   ANU / LfD QRNG API  |       |  Qiskit Hadamard QRNG |
|  (Optical Vacuum /    |       |  (H^n Superposition   |
|   Configurable HTTPS) |       |   & Measurement)      |
+-----------+-----------+       +-----------+-----------+
            | async fetch                   | async fetch
            v                               v
   [Source Health Check]           [Source Health Check]
            |                               |
            +---------------+---------------+
                            |
                            v
              +---------------------------+
              | Multi-Source XOR Mixer    |
              | (Equal-length bitwise XOR |
              |  with source tracking)    |
              +-------------+-------------+
                            |
                            v
              +---------------------------+
              |  NIST SP 800-90B Gate     |
              |  - Repetition Count (RCT) |
              |  - Adaptive Prop. (APT)   |
              +-------------+-------------+
                            | PASS
                            v
              +---------------------------+
              |  NIST SP 800-22 Gate      |
              |  - Monobit Frequency      |
              |  - Block Frequency        |
              |  - Runs Test              |
              |  - Longest Run of Ones    |
              |  - Spectral (DFT / FFT)   |
              +-------------+-------------+
                            | PASS (all p >= alpha)
                            v
              +---------------------------+
              |   Fail-Closed Sink Gate   |
              |  - paranoid: write()      |
              |  - credit: ioctl()        |
              +-------------+-------------+
                            |
            +---------------+---------------+
            |                               |
            v                               v
   /dev/urandom                    Prometheus /metrics
   (Kernel CSPRNG)                 & syslog / journald
```

### Multi-Source Cryptographic XOR Mixing
- `sources/mixer.py` fetches equal-length byte chunks ($L$ bytes, default $L = 4096$ bytes = $32,768$ bits per batch, configurable) concurrently via `asyncio.gather` from all enabled & healthy `EntropySource` instances.
- Each source is independently checked for non-empty, exact-length output and basic liveness before mixing.
- Mixing computes $Y = S_1 \oplus S_2 \oplus \dots \oplus S_k$. By the Piling-Up Lemma / one-time-pad property, as long as **at least one** independent source $S_i$ is genuinely uniform and independent of the others, the XOR output $Y$ is computationally and information-theoretically indistinguishable from uniform random, even if all other sources are deterministic, biased, or adversarially controlled.
- Minimum active source requirement (`min_healthy_sources`, default `2` in production multi-source mode, configurable to `1` for single-source degraded fallback if explicitly allowed by operator policy). If fewer than `min_healthy_sources` succeed, no batch is produced and the circuit breaker holds off with exponential backoff.

---

## 2. Exact Mathematical Formulations

Let $\varepsilon = \varepsilon_1 \varepsilon_2 \dots \varepsilon_n \in \{0, 1\}^n$ be the bit sequence derived from the candidate byte batch (MSB-first per byte), with $n = 8L$ bits. All NIST SP 800-22 tests evaluate a $P$-value against significance level $\alpha$ (default $\alpha = 0.01$; a test passes iff $P \ge \alpha$).

### 2.1 NIST SP 800-22 Statistical Test Suite (`validators/nist_tests.py`)

1. **Frequency (Monobit) Test**
   - Map bits to $X_i = 2\varepsilon_i - 1 \in \{-1, +1\}$.
   - Compute partial sum $S_n = \sum_{i=1}^n X_i$ and test statistic:
     $$s_{\text{obs}} = \frac{|S_n|}{\sqrt{n}}$$
   - Compute $P$-value using the complementary error function (`scipy.special.erfc`):
     $$P = \operatorname{erfc}\left(\frac{s_{\text{obs}}}{\sqrt{2}}\right)$$

2. **Frequency Test within a Block**
   - Partition the $n$ bits into $N = \lfloor n / M \rfloor$ non-overlapping blocks of length $M$ bits (default $M = 128$, ensuring $M \ge 20$, $M > 0.01 n$ or $N < 100$ per NIST SP 800-22 §2.2 recommendations).
   - For each block $i \in \{1, \dots, N\}$, compute the proportion of ones:
     $$\pi_i = \frac{1}{M} \sum_{j=1}^M \varepsilon_{(i-1)M + j}$$
   - Compute the $\chi^2$ statistic:
     $$\chi^2_{\text{obs}} = 4M \sum_{i=1}^N \left(\pi_i - \frac{1}{2}\right)^2$$
   - Compute $P$-value using the regularized upper incomplete gamma function (`scipy.special.gammaincc`):
     $$P = \operatorname{gammaincc}\left(\frac{N}{2}, \frac{\chi^2_{\text{obs}}}{2}\right)$$

3. **Runs Test**
   - Compute overall proportion of ones $\pi = \frac{1}{n} \sum_{i=1}^n \varepsilon_i$.
   - Pre-test prerequisite: if $|\pi - \frac{1}{2}| \ge \tau$ where $\tau = \frac{2}{\sqrt{n}}$, the Runs Test is immediately failed ($P = 0.0$) per NIST SP 800-22 §2.3.
   - Count total runs $V_n(\text{obs}) = 1 + \sum_{k=1}^{n-1} r(k)$, where $r(k) = 0$ if $\varepsilon_k = \varepsilon_{k+1}$ and $1$ otherwise.
   - Compute $P$-value:
     $$P = \operatorname{erfc}\left(\frac{\left|V_n(\text{obs}) - 2n\pi(1 - \pi)\right|}{2\sqrt{2n}\,\pi(1 - \pi)}\right)$$

4. **Test for the Longest Run of Ones in a Block**
   - Partition into $N$ blocks of size $M$ according to NIST SP 800-22 §2.4 parameters:
     - $n \ge 128$ and $< 6272$: $M = 8$, $K = 3$, $N = \lfloor n / 8 \rfloor$ (max $16$ or all blocks), categories $\{\le 1, 2, 3, \ge 4\}$, $\pi = [0.2148, 0.3672, 0.2305, 0.1875]$.
     - $6272 \le n < 750000$: $M = 128$, $K = 5$, $N = \lfloor n / 128 \rfloor$, categories $\{\le 4, 5, 6, 7, 8, \ge 9\}$, $\pi = [0.1174, 0.2430, 0.2493, 0.1752, 0.1027, 0.1124]$.
     - $n \ge 750000$: $M = 10000$, $K = 6$, $N = \lfloor n / 10000 \rfloor$, categories $\{\le 10, 11, 12, 13, 14, 15, \ge 16\}$, $\pi = [0.0882, 0.2092, 0.2483, 0.1933, 0.1208, 0.0675, 0.0727]$.
   - Tally frequencies $\nu_0, \dots, \nu_K$ of the longest run of ones in each block.
   - Compute $\chi^2_{\text{obs}} = \sum_{i=0}^K \frac{(\nu_i - N \pi_i)^2}{N \pi_i}$ and:
     $$P = \operatorname{gammaincc}\left(\frac{K}{2}, \frac{\chi^2_{\text{obs}}}{2}\right)$$

5. **Discrete Fourier Transform (Spectral / FFT) Test**
   - Map bits to $X_i = 2\varepsilon_i - 1 \in \{-1, +1\}$.
   - Compute DFT $S = \operatorname{FFT}(X)$ via `numpy.fft.fft` and take magnitudes $M_j = |S_j|$ for $j = 0, \dots, \frac{n}{2} - 1$.
   - Compute the 95% peak height threshold:
     $$T = \sqrt{\ln\left(\frac{1}{0.05}\right) n} = \sqrt{2.995732273553991\, n}$$
   - Expected count below threshold: $N_0 = 0.95 \cdot \frac{n}{2}$.
   - Observed count strictly less than $T$: $N_1 = \#\{j \in [0, n/2) : M_j < T\}$.
   - Normalized deviation and $P$-value:
     $$d = \frac{N_1 - N_0}{\sqrt{n \cdot 0.95 \cdot 0.05 / 4}}, \qquad P = \operatorname{erfc}\left(\frac{|d|}{\sqrt{2}}\right)$$

### 2.2 NIST SP 800-90B Continuous Health Tests (`validators/health_check.py`)

Applied at the byte/symbol level ($8$-bit symbols, $H_{\min}$ configurable, default conservative assumption $H = 4.0$ bits/byte or $H = 1.0$ bits/bit; for byte-level RCT/APT we parameterize by min-entropy per sample $H$ and false-positive probability $\alpha_{\text{90B}} = 2^{-20}$ or $2^{-30}$):

1. **Repetition Count Test (RCT) — NIST SP 800-90B §4.4.1**
   - Cutoff: $C = 1 + \left\lceil \frac{-\log_2 \alpha_{\text{90B}}}{H} \right\rceil$.
   - Tracks current symbol $A$ and consecutive repetition count $B$.
   - If any symbol repeats $B \ge C$ consecutive times, the test immediately fails.
   - Also supports bit-level RCT ($H_{\text{bit}} \le 1$) to catch stuck-at-0 / stuck-at-1 bitstreams regardless of byte alignment.

2. **Adaptive Proportion Test (APT) — NIST SP 800-90B §4.4.2**
   - Window size $W = 512$ (non-binary byte symbols, $2^8 = 256$ alphabet) or $W = 1024$ (binary).
   - Cutoff $C$ computed from the binomial survival function (`scipy.stats.binom.sf`) such that $\Pr[\text{Binomial}(W-1, 2^{-H}) \ge C - 1] \le \alpha_{\text{90B}}$ (or exact NIST SP 800-90B Table 1 / binomial upper bound).
   - Within each window of $W$ samples starting with $A = x_0$, count $B = \#\{i \in [1, W-1] : x_i = A\} + 1$. If $B \ge C$, the test fails.

---

## 3. Fail-Closed Circuit Breaker, Backoff & Alerting

- **Fail-Closed Guarantee:** Every batch must pass **both** NIST SP 800-90B (RCT + APT) and **all 5** NIST SP 800-22 tests before `linux_rng.py` is ever invoked. Any single test failure immediately discards the entire buffer, zeroes sensitive references, increments `qentropyd_batches_rejected_total{reason="<test_name>"}`, and logs a `LOG_WARNING` / `LOG_ERR` event to `syslog` (and `stderr` for `journald`).
- **Source Resilience & Exponential Backoff:**
  - Individual source failures (HTTP 429/5xx, TLS errors, timeouts, Qiskit backend errors) trigger per-source exponential backoff: $t_{\text{wait}} = \min(t_{\max}, t_{\text{base}} \cdot 2^{f-1}) \cdot (1 + \text{jitter})$.
  - Consecutive validation failures trip the circuit breaker into a `COOLDOWN` state (default backoff up to `max_backoff_seconds`, e.g., 300s) and emit a `LOG_CRIT` alert if consecutive failures exceed `circuit_breaker_threshold`.
  - The daemon never falls back to PRNG/urandom-reading to feed `/dev/urandom` (zero circular entropy).

---

## 4. Linux Privilege Separation & `systemd` Sandboxing

| Feature | `qentropyd.service` (Paranoid Default) | `qentropyd-credit.service` (Credit Mode) |
| :--- | :--- | :--- |
| **Kernel Sink** | `write()` to `/dev/urandom` | `ioctl(fd, RNDADDENTROPY, ...)` (`0x40085203`) |
| **Entropy Credit** | `0` bits (stirs pool without crediting) | Conservative credit (e.g., `0.5`–`1.0` bits/byte, capped) |
| **User / Group** | `DynamicUser=yes` (unprivileged) | `DynamicUser=yes` + `AmbientCapabilities=CAP_SYS_ADMIN` |
| **Capabilities** | `CapabilityBoundingSet=` (empty) | `CapabilityBoundingSet=CAP_SYS_ADMIN` |
| **Filesystem** | `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true` | Same + `DeviceAllow=/dev/urandom rw` |
| **Syscall / Memory** | `NoNewPrivileges=yes`, `MemoryDenyWriteExecute=true`, `SystemCallFilter=@system-service` | Same + `ioctl` allowed on `/dev/urandom` |

---

## 5. 4-Phase Milestone Breakdown & Verification Criteria

| Phase | Deliverables | Verification Criteria |
| :--- | :--- | :--- |
| **Phase 1: Sources & Validation Engine** | `pyproject.toml`, `config.py`, `sources/base.py`, `sources/anu_api.py`, `sources/qiskit_qrng.py`, `validators/nist_tests.py`, `validators/health_check.py` | `pytest` passes on NIST SP 800-22 reference vectors, genuine Qiskit/OS CSPRNG streams, and adversarial vectors (all-zeros, alternating `0xAA`/`0x55`, biased 70/30, periodic). |
| **Phase 2: Kernel Injection & Circuit Breaker** | `sink/linux_rng.py`, circuit breaker logic in `daemon.py`, `tests/test_sink.py` | Verified `struct rand_pool_info` packing (`2I` + payload), `/proc/sys/kernel/random/{entropy_avail,poolsize}` parsing, paranoid write path, and fail-closed rejection tests. |
| **Phase 3: Daemonization, Hardening & Metrics** | `daemon.py` (signals: `SIGTERM`, `SIGINT`, `SIGHUP`), `metrics.py`, `cli.py`, `systemd/qentropyd.service`, `systemd/qentropyd-credit.service`, `systemd/qentropyd.example.yaml` | `prometheus-client` `/metrics` scrape verified; signal reload/shutdown tested; `ruff`, `mypy --strict`, `pytest` green. |
| **Phase 4: XOR Mixer, Docs & Release Prep** | `sources/mixer.py`, `docs/*.md`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `LICENSE`, `README.md`, `.github/workflows/ci.yml` | Multi-source XOR mixing property tests (1 compromised + 1 uniform = passes); full `ruff`, `mypy`, `pytest` suite passes with zero warnings/errors. |
