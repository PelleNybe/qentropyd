"""Asynchronous optical QRNG API client (ANU / LfD / configurable JSON-hex)."""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any

import httpx

from qentropyd.sources.base import EntropySource, EntropySourceError

if TYPE_CHECKING:
    from qentropyd.config import AnuApiSourceConfig


class AnuApiEntropySource(EntropySource):
    """Production async HTTP/TLS client for public optical QRNG endpoints.

    Supports:
    - ``anu_v1``: ANU QRNG ``jsonI.php`` (``{"success": true, "data": [uint8, ...]}``)
    - ``lfd_hex``: LfD / generic hex-string JSON (``{"qrn": "..."}`` or ``{"data": ["hex..."]}``)
    - ``json_hex``: ``{"data": "<hex>"}`` or ``{"hex": "<hex>"}``
    - ``json_uint8``: ``{"data": [0..255, ...]}``
    """

    _ANU_MAX_CHUNK = 1024

    def __init__(
        self,
        config: AnuApiSourceConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        headers: dict[str, str] = {"Accept": "application/json"}
        if config.api_key:
            headers[config.api_key_header] = config.api_key
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_seconds),
            verify=config.verify_tls,
            headers=headers,
            follow_redirects=False,
        )

    @property
    def name(self) -> str:
        return self._config.name

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_bytes(self, num_bytes: int) -> bytes:
        if num_bytes <= 0:
            raise ValueError("num_bytes must be positive")

        collected = bytearray()
        while len(collected) < num_bytes:
            remaining = num_bytes - len(collected)
            chunk_size = min(remaining, self._ANU_MAX_CHUNK)
            chunk = await self._fetch_chunk_with_backoff(chunk_size)
            collected.extend(chunk[:remaining])

        result = bytes(collected[:num_bytes])
        if len(result) != num_bytes:
            raise EntropySourceError(
                f"{self.name}: expected {num_bytes} bytes, received {len(result)}"
            )
        return result

    async def _fetch_chunk_with_backoff(self, chunk_size: int) -> bytes:
        last_exc: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                return await self._fetch_once(chunk_size)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                last_exc = exc
                if attempt >= self._config.max_retries:
                    break
                delay = min(
                    self._config.max_backoff_seconds,
                    self._config.base_backoff_seconds * (2**attempt),
                )
                jitter = delay * random.uniform(0.0, 0.25)
                await asyncio.sleep(delay + jitter)
        raise EntropySourceError(f"{self.name} fetch failed after retries: {last_exc}") from last_exc

    async def _fetch_once(self, chunk_size: int) -> bytes:
        params: dict[str, str | int]
        if self._config.response_format == "anu_v1":
            params = {"length": chunk_size, "type": "uint8"}
        elif self._config.response_format == "lfd_hex":
            params = {"format": "HEX", "length": chunk_size}
        else:
            params = {"length": chunk_size}

        response = await self._client.get(self._config.endpoint_url, params=params)
        if response.status_code == 429:
            raise httpx.HTTPStatusError(
                "Rate limited (HTTP 429)",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        payload: Any = response.json()
        return self._parse_payload(payload, chunk_size)

    def _parse_payload(self, payload: Any, expected_len: int) -> bytes:
        if not isinstance(payload, dict):
            raise ValueError("QRNG JSON payload must be an object")

        fmt = self._config.response_format
        if fmt == "anu_v1":
            if not payload.get("success", False):
                raise ValueError("ANU QRNG API returned success=false")
            data = payload["data"]
            if not isinstance(data, list) or len(data) < expected_len:
                raise ValueError("ANU QRNG API returned insufficient uint8 data")
            return bytes(int(x) & 0xFF for x in data[:expected_len])

        if fmt == "json_uint8":
            data = payload["data"]
            if not isinstance(data, list) or len(data) < expected_len:
                raise ValueError("QRNG API returned insufficient uint8 data")
            return bytes(int(x) & 0xFF for x in data[:expected_len])

        if fmt in ("lfd_hex", "json_hex"):
            raw_hex: Any = payload.get("qrn") or payload.get("hex") or payload.get("data")
            if isinstance(raw_hex, list):
                raw_hex = "".join(str(item) for item in raw_hex)
            if not isinstance(raw_hex, str):
                raise ValueError("QRNG hex payload missing string field")
            cleaned_hex = raw_hex.strip().removeprefix("0x").removeprefix("0X")
            parsed = bytes.fromhex(cleaned_hex)
            if len(parsed) < expected_len:
                raise ValueError(
                    f"QRNG hex payload too short: {len(parsed)} < {expected_len}"
                )
            return parsed[:expected_len]

        raise ValueError(f"Unsupported response_format: {fmt}")
