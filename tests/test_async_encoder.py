from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import pytest

import deepx_sdk._async_encoder as encoder_module
from deepx_sdk._async_encoder import (
    ExtrinsicEncoder,
    RuntimeSnapshot,
    TimestampNonceAllocator,
)
from deepx_sdk._errors import DeepXSDKError, ValidationError


class _HexData:
    def __init__(self, raw_hex: str) -> None:
        self._raw_hex = raw_hex

    def to_hex(self) -> str:
        return self._raw_hex


class _FakeExtrinsic:
    def __init__(self, nonce: int, runtime_version: int) -> None:
        self.data = _HexData(f"0x{runtime_version:08x}{nonce:016x}")
        self.extrinsic_hash = runtime_version.to_bytes(4, "big") + nonce.to_bytes(
            28, "big"
        )


class _OfflineFrozen:
    def __init__(
        self,
        runtime_version: int,
        *,
        compose_started: threading.Event | None = None,
        compose_release: threading.Event | None = None,
        work_delay_s: float = 0.0,
    ) -> None:
        self.runtime_version = runtime_version
        self.transaction_version = 7
        self.block_hash = f"0xblock{runtime_version}"
        self.runtime_config = object()
        self.metadata = object()
        self.config = {"strict_scale_decode": True}
        self.thread_ids: list[int] = []
        self.call_functions: list[str] = []
        self.rpc_calls = 0
        self._compose_started = compose_started
        self._compose_release = compose_release
        self._work_delay_s = work_delay_s
        self._active = 0
        self.max_active = 0
        self._active_lock = threading.Lock()

    def rpc_request(self, *_args: object, **_kwargs: object) -> object:
        self.rpc_calls += 1
        raise AssertionError("hot path RPC")

    def compose_call(
        self,
        *,
        call_module: str,
        call_function: str,
        call_params: dict[str, object],
        block_hash: str,
    ) -> dict[str, object]:
        assert block_hash == self.block_hash
        self.thread_ids.append(threading.get_ident())
        self.call_functions.append(call_function)
        with self._active_lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        if self._compose_started is not None:
            self._compose_started.set()
        if self._compose_release is not None:
            assert self._compose_release.wait(timeout=2)
        if self._work_delay_s:
            time.sleep(self._work_delay_s)
        return {
            "module": call_module,
            "function": call_function,
            "params": call_params,
        }

    def create_signed_extrinsic(
        self,
        *,
        call: dict[str, object],
        keypair: object,
        nonce: int,
    ) -> _FakeExtrinsic:
        assert call["module"] == "PerpMarket"
        assert keypair == "keypair"
        self.thread_ids.append(threading.get_ident())
        if self._work_delay_s:
            time.sleep(self._work_delay_s)
        with self._active_lock:
            self._active -= 1
        return _FakeExtrinsic(nonce, self.runtime_version)


def _snapshot(
    frozen: object,
    *,
    chain_time_ms: int,
    calibration_monotonic_ns: int,
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        substrate=frozen,
        keypair="keypair",
        system_events_storage_key="0xevents",
        chain_time_ms=chain_time_ms,
        calibration_monotonic_ns=calibration_monotonic_ns,
        runtime_version=int(getattr(frozen, "runtime_version")),
        transaction_version=int(getattr(frozen, "transaction_version")),
    )


def test_timestamp_nonce_allocator_is_strictly_monotonic() -> None:
    allocator = TimestampNonceAllocator(estimated_chain_time_ms=lambda: 1_000_000)

    assert allocator.next() == 1_000_000
    assert allocator.next() == 1_000_001
    assert allocator.next() == 1_000_002


def test_explicit_nonce_advances_allocator_high_water_mark() -> None:
    allocator = TimestampNonceAllocator(estimated_chain_time_ms=lambda: 10)

    allocator.observe(100)

    assert allocator.next() == 101


def test_timestamp_nonce_allocator_concurrent_calls_are_unique() -> None:
    allocator = TimestampNonceAllocator(estimated_chain_time_ms=lambda: 50_000)

    with ThreadPoolExecutor(max_workers=16) as pool:
        nonces = list(pool.map(lambda _: allocator.next(), range(500)))

    assert len(set(nonces)) == 500
    assert min(nonces) == 50_000
    assert max(nonces) == 50_499


def test_explicit_nonce_is_reserved_until_released() -> None:
    allocator = TimestampNonceAllocator(estimated_chain_time_ms=lambda: 1_000)

    assert allocator.reserve(1_001) == 1_001
    with pytest.raises(ValidationError, match="already reserved"):
        allocator.reserve(1_001)

    allocator.release(1_001)
    assert allocator.reserve(1_001) == 1_001


@pytest.mark.parametrize(
    "explicit",
    [10_000_000 - 3_600_001, 10_000_000 + 3_600_001],
)
def test_explicit_nonce_outside_one_hour_window_is_rejected(explicit: int) -> None:
    allocator = TimestampNonceAllocator(estimated_chain_time_ms=lambda: 10_000_000)

    with pytest.raises(ValidationError, match="one-hour"):
        allocator.reserve(explicit)


def test_bootstrap_calibrates_timestamp_once_and_refresh_recalibrates() -> None:
    async def run() -> None:
        snapshots = [
            _snapshot(
                _OfflineFrozen(1),
                chain_time_ms=10_000,
                calibration_monotonic_ns=1_000_000_000,
            ),
            _snapshot(
                _OfflineFrozen(2),
                chain_time_ms=20_000,
                calibration_monotonic_ns=2_000_000_000,
            ),
        ]
        loads = 0

        def load() -> RuntimeSnapshot:
            nonlocal loads
            snapshot = snapshots[loads]
            loads += 1
            return snapshot

        monotonic_ns = 1_250_000_000
        encoder = ExtrinsicEncoder(
            "ws://node",
            "0x" + "11" * 32,
            _snapshot_loader=load,
            _monotonic_ns=lambda: monotonic_ns,
        )

        await asyncio.gather(encoder.bootstrap(), encoder.bootstrap())
        assert loads == 1
        assert encoder.estimated_chain_time_ms() == 10_250

        monotonic_ns = 2_125_000_000
        await encoder.refresh()
        assert loads == 2
        assert encoder.estimated_chain_time_ms() == 20_125

    asyncio.run(run())


def test_runtime_snapshot_is_frozen() -> None:
    snapshot = _snapshot(
        _OfflineFrozen(1),
        chain_time_ms=1,
        calibration_monotonic_ns=2,
    )

    with pytest.raises(FrozenInstanceError):
        snapshot.runtime_version = 9  # type: ignore[misc]


def test_warm_encode_is_offline_unique_and_reports_fields() -> None:
    async def run() -> None:
        main_thread = threading.get_ident()
        frozen = _OfflineFrozen(12)
        loads = 0

        def load() -> RuntimeSnapshot:
            nonlocal loads
            loads += 1
            return _snapshot(
                frozen,
                chain_time_ms=1_000_000,
                calibration_monotonic_ns=0,
            )

        encoder = ExtrinsicEncoder(
            "ws://node",
            "0x" + "11" * 32,
            _snapshot_loader=load,
            _monotonic_ns=lambda: 0,
        )
        await encoder.bootstrap()

        first = await encoder.encode_pallet_call(
            call_module="PerpMarket",
            call_function="place_order",
            call_params={"market_id": 1},
        )
        second = await encoder.encode_pallet_call(
            call_module="PerpMarket",
            call_function="place_order",
            call_params={"market_id": 1},
        )

        assert loads == 1
        assert frozen.rpc_calls == 0
        assert first.tx_hash != second.tx_hash
        assert first.data_hex == "0x0000000c00000000000f4240"
        assert first.tx_hash == "0x" + (
            (12).to_bytes(4, "big") + (1_000_000).to_bytes(28, "big")
        ).hex()
        assert first.nonce == 1_000_000
        assert second.nonce == 1_000_001
        assert first.runtime_version == 12
        assert first.encode_ms >= 0
        assert first.sign_ms >= 0
        assert frozen.thread_ids
        assert all(thread_id != main_thread for thread_id in frozen.thread_ids)

    asyncio.run(run())


def test_explicit_nonce_is_rejected_before_encoding() -> None:
    async def run() -> None:
        frozen = _OfflineFrozen(1)
        encoder = ExtrinsicEncoder(
            "ws://node",
            "0x" + "11" * 32,
            _snapshot_loader=lambda: _snapshot(
                frozen,
                chain_time_ms=5_000_000,
                calibration_monotonic_ns=0,
            ),
            _monotonic_ns=lambda: 0,
        )
        await encoder.bootstrap()

        with pytest.raises(ValidationError, match="one-hour"):
            await encoder.encode_pallet_call(
                call_module="PerpMarket",
                call_function="place_order",
                call_params={},
                nonce=9_000_000,
            )

        assert frozen.thread_ids == []

    asyncio.run(run())


def test_runtime_config_lock_serializes_concurrent_encodes() -> None:
    async def run() -> None:
        frozen = _OfflineFrozen(1, work_delay_s=0.01)
        encoder = ExtrinsicEncoder(
            "ws://node",
            "0x" + "11" * 32,
            _snapshot_loader=lambda: _snapshot(
                frozen,
                chain_time_ms=1_000,
                calibration_monotonic_ns=0,
            ),
            _monotonic_ns=lambda: 0,
        )
        await encoder.bootstrap()

        await asyncio.gather(
            *(
                encoder.encode_pallet_call(
                    call_module="PerpMarket",
                    call_function="place_order",
                    call_params={"i": i},
                )
                for i in range(8)
            )
        )

        assert frozen.max_active == 1

    asyncio.run(run())


def test_priority_encode_overtakes_queued_normal_encode() -> None:
    async def run() -> None:
        compose_started = threading.Event()
        compose_release = threading.Event()
        frozen = _OfflineFrozen(
            1,
            compose_started=compose_started,
            compose_release=compose_release,
        )
        encoder = ExtrinsicEncoder(
            "ws://node",
            "0x" + "11" * 32,
            _snapshot_loader=lambda: _snapshot(
                frozen,
                chain_time_ms=1_000,
                calibration_monotonic_ns=0,
            ),
            _monotonic_ns=lambda: 0,
        )
        await encoder.bootstrap()

        first = asyncio.create_task(
            encoder.encode_pallet_call(
                call_module="PerpMarket",
                call_function="first",
                call_params={},
            )
        )
        assert await asyncio.to_thread(compose_started.wait, 2)

        normal = asyncio.create_task(
            encoder.encode_pallet_call(
                call_module="PerpMarket",
                call_function="normal",
                call_params={},
            )
        )
        await asyncio.sleep(0)
        urgent = asyncio.create_task(
            encoder.encode_pallet_call(
                call_module="PerpMarket",
                call_function="urgent",
                call_params={},
                priority=True,
            )
        )
        await asyncio.sleep(0)
        compose_release.set()
        await asyncio.gather(first, normal, urgent)

        assert frozen.call_functions == ["first", "urgent", "normal"]
        assert frozen.max_active == 1

    asyncio.run(run())


def test_refresh_swaps_snapshot_without_changing_in_progress_encode() -> None:
    async def run() -> None:
        compose_started = threading.Event()
        compose_release = threading.Event()
        old_frozen = _OfflineFrozen(
            1,
            compose_started=compose_started,
            compose_release=compose_release,
        )
        new_frozen = _OfflineFrozen(2)
        snapshots = [
            _snapshot(old_frozen, chain_time_ms=1_000, calibration_monotonic_ns=0),
            _snapshot(new_frozen, chain_time_ms=2_000, calibration_monotonic_ns=0),
        ]
        loads = 0

        def load() -> RuntimeSnapshot:
            nonlocal loads
            snapshot = snapshots[loads]
            loads += 1
            return snapshot

        encoder = ExtrinsicEncoder(
            "ws://node",
            "0x" + "11" * 32,
            _snapshot_loader=load,
            _monotonic_ns=lambda: 0,
        )
        await encoder.bootstrap()

        old_encode = asyncio.create_task(
            encoder.encode_pallet_call(
                call_module="PerpMarket",
                call_function="place_order",
                call_params={},
            )
        )
        assert await asyncio.to_thread(compose_started.wait, 2)
        await encoder.refresh()
        compose_release.set()

        old_result = await old_encode
        new_result = await encoder.encode_pallet_call(
            call_module="PerpMarket",
            call_function="place_order",
            call_params={},
        )

        assert loads == 2
        assert old_result.runtime_version == 1
        assert old_result.nonce == 1_000
        assert new_result.runtime_version == 2
        assert new_result.nonce == 2_000

    asyncio.run(run())


def test_decode_system_events_uses_frozen_snapshot_without_rpc() -> None:
    class _ScaleObject:
        value = [
            {
                "phase": {"ApplyExtrinsic": 3},
                "event": {
                    "module_id": "System",
                    "event_id": "ExtrinsicSuccess",
                    "attributes": {},
                },
                "topics": [],
            }
        ]
        elements: list[object] = []

        def decode(self, *, check_remaining: bool) -> None:
            assert check_remaining is True

    class _RuntimeConfig:
        def create_scale_object(
            self,
            *,
            type_string: str,
            data: object,
            metadata: object,
        ) -> _ScaleObject:
            assert type_string == "Vec<EventRecord>"
            assert data is not None
            assert metadata is not None
            return _ScaleObject()

    class _StorageItem:
        @staticmethod
        def get_value_type_string() -> str:
            return "Vec<EventRecord>"

    class _Pallet:
        @staticmethod
        def get_storage_function(name: str) -> _StorageItem:
            assert name == "Events"
            return _StorageItem()

    class _Metadata:
        @staticmethod
        def get_metadata_pallet(name: str) -> _Pallet:
            assert name == "System"
            return _Pallet()

    class _DecodeFrozen(_OfflineFrozen):
        def __init__(self) -> None:
            super().__init__(3)
            self.runtime_config = _RuntimeConfig()
            self.metadata = _Metadata()
            self.init_runtime_calls = 0

        def init_runtime(self, *, block_hash: str | None = None) -> None:
            assert block_hash == self.block_hash
            self.init_runtime_calls += 1

    async def run() -> None:
        frozen = _DecodeFrozen()
        encoder = ExtrinsicEncoder(
            "ws://node",
            "0x" + "11" * 32,
            _snapshot_loader=lambda: _snapshot(
                frozen,
                chain_time_ms=1,
                calibration_monotonic_ns=0,
            ),
        )
        await encoder.bootstrap()

        events = await encoder.decode_system_events("0x00")

        assert events == [
            {
                "module_id": "System",
                "event_id": "ExtrinsicSuccess",
                "attributes": {},
                "phase": {"ApplyExtrinsic": 3},
                "extrinsic_idx": 3,
            }
        ]
        assert frozen.rpc_calls == 0
        assert frozen.init_runtime_calls == 1

    asyncio.run(run())


def test_default_bootstrap_loads_fields_calibrates_and_closes(monkeypatch) -> None:
    class _Source:
        metadata = "metadata"
        runtime_config = "runtime-config"
        runtime_version = 42
        transaction_version = 9
        config = {"strict_scale_decode": False}
        block_hash = "0xhead"

        def __init__(self) -> None:
            self.init_calls = 0
            self.timestamp_queries = 0
            self.close_calls = 0

        def init_runtime(self) -> None:
            self.init_calls += 1

        def get_block_hash(self, block_id: int) -> str:
            assert block_id == 0
            return "0xgenesis"

        def query(
            self,
            *,
            module: str,
            storage_function: str,
            block_hash: str,
        ) -> object:
            assert (module, storage_function, block_hash) == (
                "Timestamp",
                "Now",
                "0xhead",
            )
            self.timestamp_queries += 1
            return SimpleNamespace(value=123_456)

        def close(self) -> None:
            self.close_calls += 1

    source = _Source()
    monkeypatch.setattr(
        encoder_module._native_py,
        "_get_substrate_interface_cls",
        lambda: object,
    )
    created_endpoints: list[str] = []

    def create_substrate(
        _cls: object,
        ws: str,
        timeout_ms: int | None = None,
    ) -> object:
        assert timeout_ms == 321
        created_endpoints.append(ws)
        return source

    monkeypatch.setattr(
        encoder_module._native_py,
        "_create_substrate",
        create_substrate,
    )
    monkeypatch.setattr(
        encoder_module._native_py,
        "_create_ecdsa_keypair",
        lambda private_key: "keypair",
    )
    monkeypatch.setattr(
        encoder_module,
        "_create_system_events_storage_key",
        lambda substrate: "0xevents",
    )
    async def run() -> None:
        encoder = ExtrinsicEncoder(
            "ws://primary",
            "0x" + "11" * 32,
            timeout_ms=321,
            _endpoint_provider=lambda: "ws://active",
            _monotonic_ns=lambda: 987_000_000,
        )
        await encoder.bootstrap()

        assert created_endpoints == ["ws://active"]
        assert encoder.snapshot.chain_time_ms == 123_456
        assert encoder.snapshot.calibration_monotonic_ns == 987_000_000
        assert encoder.snapshot.system_events_storage_key == "0xevents"
        assert encoder.snapshot.runtime_version == 42
        assert encoder.snapshot.transaction_version == 9
        assert encoder.snapshot.substrate.get_block_hash(0) == "0xgenesis"
        encoder.snapshot.substrate.init_runtime(block_hash="0xhead")
        with pytest.raises(DeepXSDKError, match="Network access is disabled"):
            encoder.snapshot.substrate.rpc_request("state_getStorage", [])
        with pytest.raises(DeepXSDKError, match="cached genesis"):
            encoder.snapshot.substrate.get_block_hash(1)
        encoder.snapshot.substrate.close()
        assert source.init_calls == 1
        assert source.timestamp_queries == 1
        assert source.close_calls == 1

    asyncio.run(run())


def test_default_bootstrap_closes_transport_when_initialization_fails(
    monkeypatch,
) -> None:
    class _BrokenSource:
        close_calls = 0

        def init_runtime(self) -> None:
            return None

        def close(self) -> None:
            self.close_calls += 1

    source = _BrokenSource()
    monkeypatch.setattr(
        encoder_module._native_py,
        "_get_substrate_interface_cls",
        lambda: object,
    )
    monkeypatch.setattr(
        encoder_module._native_py,
        "_create_substrate",
        lambda *_args, **_kwargs: source,
    )

    async def run() -> None:
        encoder = ExtrinsicEncoder("ws://node", "0x" + "11" * 32)
        with pytest.raises(DeepXSDKError, match="bootstrap.*ValueError"):
            await encoder.bootstrap()
        assert source.close_calls == 1

    asyncio.run(run())


def test_missing_substrate_interface_is_reported_as_sdk_error(monkeypatch) -> None:
    def missing() -> object:
        raise ImportError("install substrate-interface")

    monkeypatch.setattr(
        encoder_module._native_py,
        "_get_substrate_interface_cls",
        missing,
    )

    async def run() -> None:
        encoder = ExtrinsicEncoder("ws://node", "0x" + "11" * 32)
        with pytest.raises(DeepXSDKError, match="substrate-interface"):
            await encoder.bootstrap()

    asyncio.run(run())
