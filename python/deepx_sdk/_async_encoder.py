from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from . import _native_py
from ._errors import DeepXSDKError, ValidationError

try:
    from substrateinterface import SubstrateInterface as _SubstrateInterface
except Exception:  # pragma: no cover - exercised when the optional import is absent
    _SubstrateInterface = object  # type: ignore[assignment,misc]


_TIMESTAMP_WINDOW_MS = 60 * 60 * 1_000


@dataclass(frozen=True)
class EncodedExtrinsic:
    data_hex: str
    tx_hash: str
    nonce: int
    runtime_version: int
    encode_ms: float
    sign_ms: float


@dataclass(frozen=True)
class RuntimeSnapshot:
    substrate: Any
    keypair: Any
    system_events_storage_key: str
    chain_time_ms: int
    calibration_monotonic_ns: int
    runtime_version: int
    transaction_version: int
    runtime_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        compare=False,
        repr=False,
    )

    def estimated_chain_time_ms(self, monotonic_ns: int) -> int:
        elapsed_ns = max(0, int(monotonic_ns) - self.calibration_monotonic_ns)
        return self.chain_time_ms + elapsed_ns // 1_000_000


class TimestampNonceAllocator:
    """Process-local timestamp nonce allocation for one async chain client."""

    def __init__(self, estimated_chain_time_ms: Callable[[], int]) -> None:
        self._estimated_chain_time_ms = estimated_chain_time_ms
        self._high_water_mark: int | None = None
        self._reserved: set[int] = set()
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            estimated = int(self._estimated_chain_time_ms())
            nonce = max(
                estimated,
                estimated
                if self._high_water_mark is None
                else self._high_water_mark + 1,
            )
            while nonce in self._reserved:
                nonce += 1
            self._reserved.add(nonce)
            self._high_water_mark = nonce
            return nonce

    def observe(self, nonce: int) -> None:
        self.reserve(nonce)

    def reserve(self, nonce: int) -> int:
        explicit = _coerce_nonce(nonce)
        with self._lock:
            estimated = int(self._estimated_chain_time_ms())
            if abs(explicit - estimated) > _TIMESTAMP_WINDOW_MS:
                raise ValidationError(
                    "Explicit timestamp nonce is outside the chain's one-hour window."
                )
            if explicit in self._reserved:
                raise ValidationError(
                    f"Timestamp nonce {explicit} is already reserved by this client."
                )
            self._reserved.add(explicit)
            if self._high_water_mark is None or explicit > self._high_water_mark:
                self._high_water_mark = explicit
            return explicit

    def release(self, nonce: int) -> None:
        explicit = _coerce_nonce(nonce)
        with self._lock:
            self._reserved.discard(explicit)


class _FrozenSubstrateInterface(_SubstrateInterface):  # type: ignore[misc]
    """A metadata-only SubstrateInterface that cannot access a transport."""

    def __init__(self, initialized: Any, genesis_hash: str) -> None:
        self.metadata = initialized.metadata
        self.runtime_config = initialized.runtime_config
        self.runtime_version = initialized.runtime_version
        self.transaction_version = initialized.transaction_version
        self.config = dict(initialized.config)
        self.block_hash = initialized.block_hash
        self.block_id = getattr(initialized, "block_id", None)
        self._genesis_hash = genesis_hash

    def init_runtime(
        self,
        block_hash: str | None = None,
        block_id: int | None = None,
    ) -> None:
        _ = block_hash, block_id

    def get_block_hash(self, block_id: int | None = None) -> str:
        if block_id == 0:
            return self._genesis_hash
        raise DeepXSDKError(
            "Frozen encoder only has the cached genesis block hash."
        )

    def rpc_request(self, *_args: object, **_kwargs: object) -> object:
        raise DeepXSDKError("Network access is disabled for the frozen encoder.")

    def close(self) -> None:
        return None


SnapshotLoader = Callable[[], RuntimeSnapshot]


class _PriorityGate:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._busy = False
        self._priority_waiters = 0

    @asynccontextmanager
    async def slot(self, *, priority: bool) -> AsyncIterator[None]:
        acquired = False
        async with self._condition:
            if priority:
                self._priority_waiters += 1
            try:
                await self._condition.wait_for(
                    lambda: not self._busy
                    and (priority or self._priority_waiters == 0)
                )
                self._busy = True
                acquired = True
            finally:
                if priority:
                    self._priority_waiters -= 1
                    self._condition.notify_all()
        try:
            yield
        finally:
            if acquired:
                async with self._condition:
                    self._busy = False
                    self._condition.notify_all()


class ExtrinsicEncoder:
    def __init__(
        self,
        substrate_ws: str,
        private_key: str,
        *,
        timeout_ms: int | None = None,
        _snapshot_loader: SnapshotLoader | None = None,
        _endpoint_provider: Callable[[], str] | None = None,
        _monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._substrate_ws = substrate_ws
        self._private_key = private_key
        self._timeout_ms = timeout_ms
        self._snapshot_loader = _snapshot_loader
        self._endpoint_provider = _endpoint_provider
        self._monotonic_ns = _monotonic_ns
        self._snapshot: RuntimeSnapshot | None = None
        self._keypair: Any = None
        self._refresh_lock = asyncio.Lock()
        self._runtime_gate = _PriorityGate()
        self.nonce_allocator = TimestampNonceAllocator(
            estimated_chain_time_ms=self.estimated_chain_time_ms
        )

    @property
    def snapshot(self) -> RuntimeSnapshot:
        snapshot = self._snapshot
        if snapshot is None:
            raise DeepXSDKError("Extrinsic encoder has not been bootstrapped.")
        return snapshot

    def estimated_chain_time_ms(self) -> int:
        return self.snapshot.estimated_chain_time_ms(self._monotonic_ns())

    async def bootstrap(self) -> None:
        if self._snapshot is not None:
            return
        async with self._refresh_lock:
            if self._snapshot is None:
                self._snapshot = await asyncio.to_thread(self._load_snapshot)

    async def refresh(self) -> None:
        async with self._refresh_lock:
            self._snapshot = await asyncio.to_thread(self._load_snapshot)

    async def encode_pallet_call(
        self,
        *,
        call_module: str,
        call_function: str,
        call_params: dict[str, object],
        nonce: int | None = None,
        priority: bool = False,
    ) -> EncodedExtrinsic:
        snapshot = self.snapshot
        resolved_nonce = (
            self.nonce_allocator.next()
            if nonce is None
            else self.nonce_allocator.reserve(nonce)
        )
        try:
            async with self._runtime_gate.slot(priority=priority):
                async with snapshot.runtime_lock:
                    return await asyncio.to_thread(
                        _encode_pallet_call_sync,
                        snapshot,
                        call_module,
                        call_function,
                        call_params,
                        resolved_nonce,
                    )
        except BaseException:
            self.nonce_allocator.release(resolved_nonce)
            raise

    async def decode_system_events(
        self,
        raw_hex: str,
    ) -> list[dict[str, object]]:
        snapshot = self.snapshot
        async with self._runtime_gate.slot(priority=True):
            async with snapshot.runtime_lock:
                return await asyncio.to_thread(
                    _native_py._decode_system_events_offline,
                    substrate=snapshot.substrate,
                    raw_hex=raw_hex,
                )

    async def decode_system_events_map(
        self,
        raw_hex_values: list[str],
    ) -> list[dict[str, object]]:
        """Decode per-thread System.EventsMap batches (multi-threaded runtime)."""
        snapshot = self.snapshot
        async with self._runtime_gate.slot(priority=True):
            async with snapshot.runtime_lock:
                return await asyncio.to_thread(
                    _native_py._decode_events_map_offline,
                    substrate=snapshot.substrate,
                    raw_hex_values=raw_hex_values,
                )

    def _load_snapshot(self) -> RuntimeSnapshot:
        if self._snapshot_loader is not None:
            snapshot = self._snapshot_loader()
            if not isinstance(snapshot, RuntimeSnapshot):
                raise DeepXSDKError(
                    "Encoder snapshot loader returned an invalid RuntimeSnapshot."
                )
            return snapshot
        return self._load_default_snapshot()

    def _load_default_snapshot(self) -> RuntimeSnapshot:
        substrate: Any = None
        try:
            substrate_ws = (
                self._endpoint_provider()
                if self._endpoint_provider is not None
                else self._substrate_ws
            )
            substrate_cls = _native_py._get_substrate_interface_cls()
            substrate = _native_py._create_substrate(
                substrate_cls,
                substrate_ws,
                timeout_ms=self._timeout_ms,
            )
            substrate.init_runtime()
            _validate_initialized_substrate(substrate)

            genesis_hash = substrate.get_block_hash(0)
            if not isinstance(genesis_hash, str) or not genesis_hash:
                raise ValueError("missing genesis hash")

            timestamp = substrate.query(
                module="Timestamp",
                storage_function="Now",
                block_hash=substrate.block_hash,
            )
            chain_time_ms = _coerce_timestamp_value(timestamp)
            calibration_monotonic_ns = int(self._monotonic_ns())

            events_key = _create_system_events_storage_key(substrate)
            if self._keypair is None:
                self._keypair = _native_py._create_ecdsa_keypair(self._private_key)
            frozen = _freeze_substrate(substrate, genesis_hash)

            return RuntimeSnapshot(
                substrate=frozen,
                keypair=self._keypair,
                system_events_storage_key=events_key,
                chain_time_ms=chain_time_ms,
                calibration_monotonic_ns=calibration_monotonic_ns,
                runtime_version=int(substrate.runtime_version),
                transaction_version=int(substrate.transaction_version),
            )
        except DeepXSDKError:
            raise
        except ImportError as exc:
            raise DeepXSDKError(
                "Async encoder requires substrate-interface; install "
                "'substrate-interface'."
            ) from exc
        except Exception as exc:
            raise DeepXSDKError(
                "Extrinsic encoder bootstrap failed with "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            if substrate is not None:
                try:
                    substrate.close()
                except Exception:
                    pass


def _freeze_substrate(substrate: Any, genesis_hash: str) -> Any:
    return _FrozenSubstrateInterface(substrate, genesis_hash)


def _create_system_events_storage_key(substrate: Any) -> str:
    from substrateinterface.storage import StorageKey

    key = StorageKey.create_from_storage_function(
        "System",
        "Events",
        [],
        runtime_config=substrate.runtime_config,
        metadata=substrate.metadata,
    ).to_hex()
    if not isinstance(key, str) or not key:
        raise ValueError("missing System.Events storage key")
    return key


def _validate_initialized_substrate(substrate: Any) -> None:
    required = (
        "metadata",
        "runtime_config",
        "runtime_version",
        "transaction_version",
        "config",
        "block_hash",
    )
    missing = [name for name in required if getattr(substrate, name, None) is None]
    if missing:
        raise ValueError(
            "initialized substrate is missing fields: " + ", ".join(missing)
        )


def _coerce_timestamp_value(value: Any) -> int:
    current = getattr(value, "value", value)
    try:
        timestamp = int(current)
    except (TypeError, ValueError) as exc:
        raise ValueError("Timestamp.Now did not contain an integer") from exc
    if timestamp < 0:
        raise ValueError("Timestamp.Now cannot be negative")
    return timestamp


def _coerce_nonce(value: int) -> int:
    if isinstance(value, bool):
        raise ValidationError("Timestamp nonce must be an integer.")
    try:
        nonce = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Timestamp nonce must be an integer.") from exc
    if nonce < 0:
        raise ValidationError("Timestamp nonce cannot be negative.")
    return nonce


def _encode_pallet_call_sync(
    snapshot: RuntimeSnapshot,
    call_module: str,
    call_function: str,
    call_params: dict[str, object],
    nonce: int,
) -> EncodedExtrinsic:
    started_ns = time.perf_counter_ns()
    call = snapshot.substrate.compose_call(
        call_module=call_module,
        call_function=call_function,
        call_params=call_params,
        block_hash=snapshot.substrate.block_hash,
    )
    composed_ns = time.perf_counter_ns()
    extrinsic = snapshot.substrate.create_signed_extrinsic(
        call=call,
        keypair=snapshot.keypair,
        nonce=nonce,
    )
    signed_ns = time.perf_counter_ns()

    data_hex = _extrinsic_data_hex(extrinsic)
    tx_hash = _extrinsic_hash_hex(extrinsic, data_hex)
    return EncodedExtrinsic(
        data_hex=data_hex,
        tx_hash=tx_hash,
        nonce=nonce,
        runtime_version=snapshot.runtime_version,
        encode_ms=(composed_ns - started_ns) / 1_000_000,
        sign_ms=(signed_ns - composed_ns) / 1_000_000,
    )


def _extrinsic_data_hex(extrinsic: Any) -> str:
    raw = extrinsic.data.to_hex()
    if not isinstance(raw, str) or not raw:
        raise DeepXSDKError("Signed extrinsic did not contain encoded data.")
    return raw if raw.startswith("0x") else "0x" + raw


def _extrinsic_hash_hex(extrinsic: Any, _data_hex: str) -> str:
    raw_hash = getattr(extrinsic, "extrinsic_hash", None)
    if not isinstance(raw_hash, (bytes, bytearray)):
        raise DeepXSDKError("Signed extrinsic did not contain a transaction hash.")
    return "0x" + bytes(raw_hash).hex()


__all__ = [
    "EncodedExtrinsic",
    "ExtrinsicEncoder",
    "RuntimeSnapshot",
    "TimestampNonceAllocator",
]
