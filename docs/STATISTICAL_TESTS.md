# Statistical & Continuous Health Tests

Every mixed entropy batch must pass **both** NIST SP 800-90B continuous health tests and **all 5** NIST SP 800-22 Rev. 1a statistical tests at significance level $\alpha = 0.01$ before kernel injection.

## 1. NIST SP 800-90B Continuous Health Tests

### Repetition Count Test (RCT — §4.4.1)
- **Purpose:** Rapidly detects catastrophic stuck-at faults (e.g., all-zeros, all-ones, frozen hardware).
- **Cutoff:** $C = 1 + \lceil -\log_2(\alpha_{90B}) / H \rceil$. With default $H = 4.0$ bits/byte and $\alpha_{90B} = 2^{-20}$, $C = 6$ consecutive identical bytes.
- **Bit-level guard:** Also runs a conservative bit-level RCT ($H_{\text{bit}} = 0.5$) to catch long runs of identical bits across byte boundaries.

### Adaptive Proportion Test (APT — §4.4.2)
- **Purpose:** Detects gradual loss of entropy or symbol bias over a sliding/non-overlapping window of $W = 512$ bytes.
- **Cutoff:** Exact binomial upper bound computed via `scipy.stats.binom.isf` such that $\Pr[\text{Binomial}(W-1, 2^{-H}) \ge C - 1] \le \alpha_{90B}$.

## 2. NIST SP 800-22 Statistical Test Suite

1. **Frequency (Monobit) Test (§2.1):** Verifies global 0/1 balance via $P = \operatorname{erfc}(|S_n| / \sqrt{2n})$.
2. **Frequency Test within a Block (§2.2):** Verifies local 0/1 balance across $N = \lfloor n / M \rfloor$ blocks of $M = 128$ bits via $P = \operatorname{gammaincc}(N/2, \chi^2_{\text{obs}}/2)$.
3. **Runs Test (§2.3):** Checks oscillation rate between 0-runs and 1-runs, enforcing the prerequisite $|\pi - 0.5| < 2/\sqrt{n}$.
4. **Longest Run of Ones in a Block (§2.4):** Checks distribution of maximum 1-runs per block against NIST's exact theoretical $\pi_i$ tables for $M \in \{8, 128, 10000\}$.
5. **Discrete Fourier Transform / Spectral Test (§2.6):** Uses `numpy.fft.fft` to detect periodic patterns by comparing the number of spectral peaks below $T = \sqrt{\ln(1/0.05)n}$ against the expected $0.95 \cdot n/2$.
