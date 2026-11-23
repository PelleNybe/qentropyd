"""Hardware TRNG/QRNG character device entropy source (e.g., /dev/hwrng, USB QRNG)."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from qentropyd.sources.base import EntropySource, EntropySourceError

if TYPE_CHECKING:
    from qentropyd.config import HwrngSourceConfig


class HardwareDeviceEntropySource(EntropySource):
    """Reads raw entropy from a Linux hardware RNG character device (e.g., /dev/hwrng)."""

    def __init__(self, config: HwrngSourceConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return self._config.name

    async def fetch_bytes(self, num_bytes: int) -> bytes:
        if num_bytes <= 0:
            raise ValueError("num_bytes must be positive")
        return await asyncio.wait_for(
            asyncio.to_thread(self._read_device_sync, num_bytes),
            timeout=self._config.timeout_seconds,
        )

    def _read_device_sync(self, num_bytes: int) -> bytes:
        buf = bytearray()
        try:
            fd = os.open(self._config.device_path, os.O_RDONLY)
            try:
                while len(buf) < num_bytes:
                    chunk = os.read(fd, num_bytes - len(buf))
                    if not chunk:
                        break
                    buf.extend(chunk)
            finally:
                os.close(fd)
        except OSError as exc:
            raise EntropySourceError(
                f"{self.name}: failed reading {self._config.device_path}: {exc}"
            ) from exc

        if len(buf) != num_bytes:
            raise EntropySourceError(
                f"{self.name}: short read from {self._config.device_path}: "
                f"{len(buf)} < {num_bytes}"
            )
        return bytes(buf)
