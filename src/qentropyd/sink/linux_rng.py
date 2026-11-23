"""Linux kernel CSPRNG injection sink (/dev/urandom & ioctl RNDADDENTROPY)."""

from __future__ import annotations

import fcntl
import os
import struct
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qentropyd.config import SinkConfig

# Linux <linux/random.h>: _IOW('R', 0x03, int[2]) == 0x40085203
RNDADDENTROPY: int = 0x40085203

_PROC_ENTROPY_AVAIL = Path("/proc/sys/kernel/random/entropy_avail")
_PROC_POOLSIZE = Path("/proc/sys/kernel/random/poolsize")


class RngSinkError(Exception):
    """Raised when injecting entropy into the Linux kernel RNG fails."""


def pack_rand_pool_info(data: bytes, entropy_bits: int) -> bytes:
    """Pack bytes into Linux ``struct rand_pool_info`` for ``ioctl(RNDADDENTROPY)``.

    Layout in ``<linux/random.h>``:
        struct rand_pool_info {
            int entropy_count; /* in bits */
            int buf_size;      /* in bytes */
            __u32 buf[0];
        };
    """
    if not data:
        raise ValueError("Cannot pack empty entropy buffer")
    if entropy_bits < 0 or entropy_bits > len(data) * 8:
        raise ValueError(
            f"entropy_bits ({entropy_bits}) must be between 0 and {len(data) * 8}"
        )
    pad_len = (-len(data)) % 4
    padded = data + (b"\x00" * pad_len)
    return struct.pack(f"@ii{len(padded)}s", entropy_bits, len(data), padded)


def read_kernel_entropy_avail(proc_path: Path = _PROC_ENTROPY_AVAIL) -> int:
    """Read live kernel entropy_avail (bits) from procfs."""
    try:
        return int(proc_path.read_text(encoding="utf-8").strip())
    except OSError as exc:
        raise RngSinkError(f"Failed to read {proc_path}: {exc}") from exc


def read_kernel_poolsize(proc_path: Path = _PROC_POOLSIZE) -> int:
    """Read kernel random poolsize (bits) from procfs."""
    try:
        return int(proc_path.read_text(encoding="utf-8").strip())
    except OSError as exc:
        raise RngSinkError(f"Failed to read {proc_path}: {exc}") from exc


class LinuxRngSink:
    """Injects verified quantum entropy into the Linux kernel CSPRNG.

    Modes:
    - ``paranoid`` (default): Writes bytes directly to ``/dev/urandom`` without
      crediting the kernel entropy counter. Requires zero elevated privileges.
    - ``credit``: Uses ``ioctl(RNDADDENTROPY)`` to mix bytes and conservatively
      credit kernel entropy. Requires ``CAP_SYS_ADMIN``.
    """

    def __init__(self, config: SinkConfig) -> None:
        self._config = config

    @property
    def mode(self) -> str:
        return self._config.mode

    @property
    def device_path(self) -> str:
        return self._config.device_path

    def inject(self, data: bytes) -> int:
        """Inject verified entropy bytes into the configured kernel RNG device.

        Returns:
            Number of bytes written/mixed into the kernel pool.
        """
        if not data:
            raise ValueError("Refusing to inject empty entropy buffer")

        if self._config.mode == "paranoid":
            return self._inject_paranoid(data)
        if self._config.mode == "credit":
            return self._inject_credit(data)
        raise RngSinkError(f"Unsupported sink mode: {self._config.mode}")

    def _inject_paranoid(self, data: bytes) -> int:
        try:
            fd = os.open(self._config.device_path, os.O_WRONLY)
            try:
                written = os.write(fd, data)
            finally:
                os.close(fd)
        except OSError as exc:
            raise RngSinkError(
                f"Paranoid write to {self._config.device_path} failed: {exc}"
            ) from exc

        if written != len(data):
            raise RngSinkError(
                f"Short write to {self._config.device_path}: {written} != {len(data)}"
            )
        return written

    def _inject_credit(self, data: bytes) -> int:
        entropy_bits = int(len(data) * self._config.credit_bits_per_byte)
        payload = pack_rand_pool_info(data, entropy_bits=entropy_bits)
        try:
            fd = os.open(self._config.device_path, os.O_WRONLY)
            try:
                fcntl.ioctl(fd, RNDADDENTROPY, payload)
            finally:
                os.close(fd)
        except OSError as exc:
            raise RngSinkError(
                f"ioctl(RNDADDENTROPY) on {self._config.device_path} failed: {exc}"
            ) from exc
        return len(data)
