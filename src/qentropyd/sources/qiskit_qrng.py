"""Qiskit Hadamard-gate (H^n) quantum circuit QRNG entropy source."""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING, Any

import httpx
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

from qentropyd.sources.base import EntropySource, EntropySourceError

if TYPE_CHECKING:
    from qentropyd.config import QiskitQrngSourceConfig


class QiskitQrngEntropySource(EntropySource):
    """Authentic quantum circuit QRNG using Hadamard superposition and measurement.

    Constructs an ``n``-qubit circuit applying ``H`` to every qubit (creating an
    equal superposition ``|+>^n``) followed by computational-basis measurement.
    Each shot yields ``n`` uniformly distributed quantum bits.

    Supports:
    - ``aer_simulator``: Local Qiskit Aer quantum simulator backend.
    - ``remote_rest``: Configurable remote quantum execution REST endpoint.
    """

    def __init__(
        self,
        config: QiskitQrngSourceConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_client = http_client is None
        self._http_client = http_client
        self._simulator = AerSimulator(seed_simulator=config.seed_simulator)
        self._circuit = self._build_hadamard_circuit(config.num_qubits)

    @property
    def name(self) -> str:
        return self._config.name

    @staticmethod
    def _build_hadamard_circuit(num_qubits: int) -> QuantumCircuit:
        qc = QuantumCircuit(num_qubits, num_qubits)
        qc.h(range(num_qubits))
        qc.measure(range(num_qubits), range(num_qubits))
        return qc

    async def close(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()

    async def fetch_bytes(self, num_bytes: int) -> bytes:
        if num_bytes <= 0:
            raise ValueError("num_bytes must be positive")

        if self._config.backend_type == "aer_simulator":
            return await asyncio.to_thread(self._sample_aer_sync, num_bytes)
        if self._config.backend_type == "remote_rest":
            return await self._sample_remote_rest(num_bytes)
        raise EntropySourceError(f"Unknown backend_type: {self._config.backend_type}")

    def _sample_aer_sync(self, num_bytes: int) -> bytes:
        total_bits_needed = num_bytes * 8
        shots = math.ceil(total_bits_needed / self._config.num_qubits)
        try:
            job = self._simulator.run(self._circuit, shots=shots, memory=True)
            result = job.result()
            bitstrings: list[str] = result.get_memory(self._circuit)
        except Exception as exc:
            raise EntropySourceError(f"{self.name} Aer execution failed: {exc}") from exc

        return self._pack_bitstrings(bitstrings, num_bytes)

    async def _sample_remote_rest(self, num_bytes: int) -> bytes:
        if not self._config.remote_url:
            raise EntropySourceError(f"{self.name}: remote_url is required for remote_rest")
        if self._http_client is None:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self._config.api_token:
                headers["Authorization"] = f"Bearer {self._config.api_token}"
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.timeout_seconds),
                headers=headers,
            )

        total_bits_needed = num_bytes * 8
        shots = math.ceil(total_bits_needed / self._config.num_qubits)
        try:
            resp = await self._http_client.post(
                self._config.remote_url,
                json={"num_qubits": self._config.num_qubits, "shots": shots},
            )
            resp.raise_for_status()
            payload: Any = resp.json()
            memory = payload["memory"]
            if not isinstance(memory, list):
                raise ValueError("Remote quantum backend 'memory' field must be a list")
            return self._pack_bitstrings([str(b) for b in memory], num_bytes)
        except Exception as exc:
            raise EntropySourceError(f"{self.name} remote execution failed: {exc}") from exc

    @staticmethod
    def _pack_bitstrings(bitstrings: list[str], num_bytes: int) -> bytes:
        joined = "".join(bitstrings)
        needed_bits = num_bytes * 8
        if len(joined) < needed_bits:
            raise EntropySourceError(
                f"Insufficient quantum measurement bits: {len(joined)} < {needed_bits}"
            )
        bit_slice = joined[:needed_bits]
        out = bytearray(num_bytes)
        for i in range(num_bytes):
            out[i] = int(bit_slice[i * 8 : (i + 1) * 8], 2)
        return bytes(out)
