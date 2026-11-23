# Security Hardening Guide

## Threat Model & Defenses

| Threat | Mitigation in `qentropyd` |
| :--- | :--- |
| **Compromised / Biased QRNG API** | Multi-source XOR mixing (`min_healthy_sources >= 2`) + NIST SP 800-90B/22 fail-closed gate. |
| **MITM on QRNG HTTPS Endpoint** | Strict TLS certificate verification (`verify_tls: true`), no HTTP redirects (`follow_redirects=False`). |
| **Low-Entropy / Stuck Stream Injection** | Fail-closed circuit breaker discards entire batch before `sink.inject()` is ever called. |
| **Privilege Escalation via Daemon** | Default `paranoid` mode runs under `DynamicUser=yes` with `CapabilityBoundingSet=` (zero capabilities). |
| **Filesystem / Syscall Escape** | `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, `MemoryDenyWriteExecute=true`, `SystemCallFilter=@system-service`. |

## Paranoid Mode vs. Credit Mode

- **`qentropyd.service` (Paranoid Default):** Writes verified entropy to `/dev/urandom` using standard `os.write()`. On Linux 5.10+, writing to `/dev/urandom` mixes input into the ChaCha20 CSPRNG state without crediting `entropy_avail`. Requires **no** Linux capabilities.
- **`qentropyd-credit.service` (Credit Mode):** Uses `ioctl(RNDADDENTROPY)` (`0x40085203`) with `CAP_SYS_ADMIN` only (via `CapabilityBoundingSet=CAP_SYS_ADMIN` and `AmbientCapabilities=CAP_SYS_ADMIN`). Recommended only for embedded/VM environments with starved early-boot entropy pools.
