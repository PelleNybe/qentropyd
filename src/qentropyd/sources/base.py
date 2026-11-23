"""Abstract base interface for asynchronous quantum entropy sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class EntropySourceError(Exception):
    """Raised when an entropy source fails to deliver valid entropy bytes."""


@dataclass(slots=True)
class SourceHealth:
    """Tracks operational health and backoff state for an entropy source."""

    name: str
    consecutive_failures: int = 0
    total_bytes_fetched: int = 0
    total_errors: int = 0
    last_latency_seconds: float = 0.0
    last_error: str | None = None

    @property
    def is_healthy(self) -> bool:
        """Return True if the source has not hit consecutive failure limits."""
        return self.consecutive_failures == 0


class EntropySource(ABC):
    """Abstract asynchronous interface for a hardware or quantum entropy source."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this entropy source."""

    @abstractmethod
    async def fetch_bytes(self, num_bytes: int) -> bytes:
        """Fetch exactly ``num_bytes`` of raw entropy asynchronously.

        Raises:
            EntropySourceError: If the source cannot provide ``num_bytes`` safely.
        """

    async def close(self) -> None:
        """Release any underlying network or backend resources."""
        return None
