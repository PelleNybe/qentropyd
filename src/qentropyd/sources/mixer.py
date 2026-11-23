"""Multi-source cryptographic XOR mixer with per-source health tracking."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import numpy as np

from qentropyd.sources.base import EntropySource, EntropySourceError, SourceHealth

if TYPE_CHECKING:
    from collections.abc import Sequence


def xor_bytes(streams: Sequence[bytes]) -> bytes:
    """Compute the bitwise XOR across one or more equal-length byte streams.

    By the one-time-pad / piling-up principle, if at least one input stream is
    uniformly random and independent of the others, the XOR output is uniform.
    """
    if not streams:
        raise ValueError("At least one byte stream is required for XOR mixing")
    expected_len = len(streams[0])
    if expected_len == 0:
        raise ValueError("Cannot XOR empty byte streams")
    for idx, stream in enumerate(streams[1:], start=1):
        if len(stream) != expected_len:
            raise ValueError(
                f"Stream {idx} length {len(stream)} does not match expected {expected_len}"
            )

    acc = np.frombuffer(streams[0], dtype=np.uint8).copy()
    for stream in streams[1:]:
        acc ^= np.frombuffer(stream, dtype=np.uint8)
    return acc.tobytes()


class MultiSourceXorMixer:
    """Fetches entropy concurrently from independent sources and XOR-mixes them."""

    def __init__(
        self,
        sources: Sequence[EntropySource],
        min_healthy_sources: int = 2,
    ) -> None:
        if not sources:
            raise ValueError("MultiSourceXorMixer requires at least one source")
        if min_healthy_sources < 1:
            raise ValueError("min_healthy_sources must be >= 1")
        if min_healthy_sources > len(sources):
            raise ValueError(
                f"min_healthy_sources ({min_healthy_sources}) exceeds configured sources ({len(sources)})"
            )
        self._sources = list(sources)
        self._min_healthy_sources = min_healthy_sources
        self._health: dict[str, SourceHealth] = {
            src.name: SourceHealth(name=src.name) for src in self._sources
        }

    @property
    def health_status(self) -> dict[str, SourceHealth]:
        """Return a snapshot mapping of source name to SourceHealth."""
        return dict(self._health)

    @property
    def sources(self) -> list[EntropySource]:
        """Return configured entropy sources."""
        return list(self._sources)

    async def _fetch_from_source(self, source: EntropySource, num_bytes: int) -> bytes | None:
        health = self._health[source.name]
        start = time.monotonic()
        try:
            data = await source.fetch_bytes(num_bytes)
            if len(data) != num_bytes:
                raise EntropySourceError(
                    f"{source.name} returned {len(data)} bytes, expected {num_bytes}"
                )
            health.consecutive_failures = 0
            health.total_bytes_fetched += len(data)
            health.last_latency_seconds = time.monotonic() - start
            health.last_error = None
            return data
        except Exception as exc:
            health.consecutive_failures += 1
            health.total_errors += 1
            health.last_latency_seconds = time.monotonic() - start
            health.last_error = str(exc)
            return None

    async def fetch_mixed_bytes(self, num_bytes: int) -> bytes:
        """Fetch ``num_bytes`` from all sources concurrently and XOR-mix valid outputs."""
        results = await asyncio.gather(
            *(self._fetch_from_source(src, num_bytes) for src in self._sources)
        )
        valid_streams = [chunk for chunk in results if chunk is not None]
        if len(valid_streams) < self._min_healthy_sources:
            errors = {
                name: h.last_error
                for name, h in self._health.items()
                if h.last_error is not None
            }
            raise EntropySourceError(
                f"Insufficient healthy sources: {len(valid_streams)} < "
                f"{self._min_healthy_sources}. Errors: {errors}"
            )
        return xor_bytes(valid_streams)

    async def close(self) -> None:
        """Close all underlying entropy sources."""
        await asyncio.gather(*(src.close() for src in self._sources))
