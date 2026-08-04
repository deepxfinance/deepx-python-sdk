from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from deepx_sdk._async_encoder import EncodedExtrinsic, TimestampNonceAllocator
from deepx_sdk._async_tracker import ExpectedEvent, TransactionTracker
from deepx_sdk._async_transport import AsyncRpcTransport
from deepx_sdk._pending_tx import PendingTransaction, TxStatus, TxTimings, TxTimeouts
from deepx_sdk._tx_diagnostics import (
    ClientBackpressure,
    OutcomeCertainty,
    TransactionDropped,
    TxStage,
)
from deepx_sdk.async_client import AsyncChainClient, AsyncComponents


_BASE_NONCE = 10_000_000


class LoadNode:
    def __init__(self) -> None:
        self.data_by_index: dict[int, str] = {}
        self.block_indices: dict[str, list[int]] = {}


class LoadTransport:
    def __init__(self, node: LoadNode) -> None:
        self.node = node
        self.connection_count = 0
        self.close_count = 0
        self.subscribe_calls: list[
            tuple[int, Callable[[object], object], asyncio.Future[str]]
        ] = []
        self.block_requests = 0
        self.event_requests = 0

    async def connect(self) -> None:
        self.connection_count += 1

    async def close(self) -> None:
        self.close_count += 1

    async def subscribe(
        self,
        method: str,
        params: list[object],
        handler: Callable[[object], object],
    ) -> str:
        assert method == "author_submitAndWatchExtrinsic"
        data_hex = str(params[0])
        index = next(
            index
            for index, candidate in self.node.data_by_index.items()
            if candidate == data_hex
        )
        response = asyncio.get_running_loop().create_future()
        self.subscribe_calls.append((index, handler, response))
        return await response

    async def wait_for_subscriptions(self, count: int) -> None:
        while len(self.subscribe_calls) < count:
            await asyncio.sleep(0)

    async def accept_all_in_reverse(self) -> None:
        for index, handler, response in reversed(self.subscribe_calls):
            response.set_result(f"sub-{index}")
            handler({"ready": None})
        await asyncio.sleep(0)

    async def include_all_in_reverse(self) -> None:
        by_index = {
            index: handler for index, handler, _response in self.subscribe_calls
        }
        for index in reversed(range(len(by_index))):
            by_index[index]({"inBlock": f"0xblock-{index // 10}"})
        await asyncio.sleep(0)

    async def request(self, method: str, params: list[object]) -> object:
        block_hash = str(params[-1])
        if method == "chain_getBlock":
            self.block_requests += 1
            return {
                "block": {
                    "extrinsics": [
                        self.node.data_by_index[index]
                        for index in self.node.block_indices[block_hash]
                    ]
                }
            }
        if method == "state_getStorage":
            self.event_requests += 1
            return f"events:{block_hash}"
        raise AssertionError(f"unexpected RPC method: {method}")


class LoadEncoder:
    snapshot = SimpleNamespace(system_events_storage_key="0xevents-key")

    def __init__(self, node: LoadNode) -> None:
        self.node = node
        self.bootstrap_count = 0
        self.encode_count = 0
        self.nonce_allocator = TimestampNonceAllocator(lambda: _BASE_NONCE)

    async def bootstrap(self) -> None:
        self.bootstrap_count += 1

    async def encode_pallet_call(
        self,
        *,
        call_module: str,
        call_function: str,
        call_params: dict[str, object],
        nonce: int | None = None,
        priority: bool = False,
    ) -> EncodedExtrinsic:
        _ = priority
        assert call_module == "PerpMarket"
        assert call_function == "place_order"
        resolved_nonce = (
            self.nonce_allocator.next()
            if nonce is None
            else self.nonce_allocator.reserve(nonce)
        )
        index = resolved_nonce - _BASE_NONCE
        data_hex = f"0x{index + 1:08x}"
        self.node.data_by_index[index] = data_hex
        self.encode_count += 1
        return EncodedExtrinsic(
            data_hex=data_hex,
            tx_hash=f"0xtx-{index}",
            nonce=resolved_nonce,
            runtime_version=182,
            encode_ms=0.1,
            sign_ms=0.1,
        )

    async def decode_system_events(
        self,
        raw_hex: str,
    ) -> list[dict[str, object]]:
        block_hash = raw_hex.removeprefix("events:")
        events: list[dict[str, object]] = []
        for extrinsic_index, result in enumerate(
            self.node.block_indices[block_hash]
        ):
            events.extend(
                [
                    {
                        "phase": {"ApplyExtrinsic": extrinsic_index},
                        "event": {
                            "module_id": "System",
                            "event_id": "ExtrinsicSuccess",
                            "attributes": {},
                        },
                    },
                    {
                        "phase": {"ApplyExtrinsic": extrinsic_index},
                        "event": {
                            "module_id": "PerpMarket",
                            "event_id": "OrderPlaced",
                            "attributes": {"order_id": result},
                        },
                    },
                ]
            )
        return events


class NoopRecovery:
    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


def test_100_concurrent_transactions_keep_identity_and_fetch_once_per_block() -> None:
    async def run() -> None:
        node = LoadNode()
        transport = LoadTransport(node)
        encoder = LoadEncoder(node)
        tracker = TransactionTracker(transport, encoder)
        components = AsyncComponents(
            transport=transport,
            encoder=encoder,
            tracker=tracker,
            recovery=NoopRecovery(),
        )
        factory_calls = 0

        async def factory(_client: AsyncChainClient) -> AsyncComponents:
            nonlocal factory_calls
            factory_calls += 1
            return components

        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="test-only-private-key",
            subaccount="0x" + "22" * 20,
            component_factory=factory,
            node_pool_limit_per_account=130,
            max_pool_transactions_per_account=128,
            max_outbound_queue=128,
        )
        await client.connect()

        encoded = await asyncio.gather(
            *(
                encoder.encode_pallet_call(
                    call_module="PerpMarket",
                    call_function="place_order",
                    call_params={"params": {"index": index}},
                    nonce=_BASE_NONCE + index,
                )
                for index in range(100)
            )
        )
        submissions = [
            asyncio.create_task(
                tracker.submit(
                    encoded=item,
                    cloid=None,
                    expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                    result_decoder=lambda fields, _pending: int(
                        fields["order_id"]
                    ),
                    timeouts=TxTimeouts(submit_ms=5_000),
                )
            )
            for item in encoded
        ]
        await transport.wait_for_subscriptions(100)
        await transport.accept_all_in_reverse()
        pending_transactions = await asyncio.gather(*submissions)

        for block_number in range(10):
            node.block_indices[f"0xblock-{block_number}"] = list(
                range(block_number * 10, block_number * 10 + 10)
            )
        await transport.include_all_in_reverse()
        results = await asyncio.gather(
            *(pending.wait_in_block() for pending in pending_transactions)
        )

        assert len({pending.tx_hash for pending in pending_transactions}) == 100
        assert results == list(range(100))
        assert factory_calls == 1
        assert transport.connection_count == 1
        assert encoder.bootstrap_count == 1
        assert tracker.block_fetch_count == 10
        assert transport.block_requests == 10
        assert transport.event_requests == 10
        await client.close()

    asyncio.run(run())


class ImmediateTransport:
    def __init__(self) -> None:
        self.connection_count = 0

    async def connect(self) -> None:
        self.connection_count += 1

    async def close(self) -> None:
        return None


class ImmediateEncoder:
    def __init__(self) -> None:
        self.bootstrap_count = 0
        self.encode_calls = 0
        self.nonce_allocator = TimestampNonceAllocator(lambda: _BASE_NONCE)

    async def bootstrap(self) -> None:
        self.bootstrap_count += 1

    async def encode_pallet_call(
        self,
        *,
        call_module: str,
        call_function: str,
        call_params: dict[str, object],
        nonce: int | None = None,
        priority: bool = False,
    ) -> EncodedExtrinsic:
        _ = call_module, call_function, call_params, priority
        resolved_nonce = (
            self.nonce_allocator.next()
            if nonce is None
            else self.nonce_allocator.reserve(nonce)
        )
        self.encode_calls += 1
        return EncodedExtrinsic(
            data_hex=f"0x{self.encode_calls:08x}",
            tx_hash=f"0ximmediate-{self.encode_calls}",
            nonce=resolved_nonce,
            runtime_version=182,
            encode_ms=0.1,
            sign_ms=0.1,
        )


class BlockingEncoder(ImmediateEncoder):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def encode_pallet_call(self, **kwargs: Any) -> EncodedExtrinsic:
        self.encode_calls += 1
        self.started.set()
        await self.release.wait()
        self.encode_calls -= 1
        return await super().encode_pallet_call(**kwargs)


class ImmediateTracker:
    def __init__(self) -> None:
        self.handles: list[PendingTransaction[Any]] = []
        self.submit_calls = 0

    @property
    def pending_transactions(self) -> tuple[PendingTransaction[Any], ...]:
        return tuple(
            pending
            for pending in self.handles
            if pending.status not in {TxStatus.FINALIZED, TxStatus.CLIENT_CLOSED}
        )

    def pending_transaction(self, tx_hash: str) -> PendingTransaction[Any] | None:
        return next(
            (pending for pending in self.handles if pending.tx_hash == tx_hash),
            None,
        )

    async def submit(
        self,
        *,
        encoded: EncodedExtrinsic,
        cloid: int | None,
        expected_event: ExpectedEvent,
        result_decoder: Callable[..., object],
        timeouts: TxTimeouts,
        replacement_callback: Callable[..., object] | None = None,
        pending_callback: Callable[[PendingTransaction[Any]], None] | None = None,
    ) -> PendingTransaction[Any]:
        _ = expected_event, result_decoder
        self.submit_calls += 1
        pending = PendingTransaction(
            tx_hash=encoded.tx_hash,
            nonce=encoded.nonce,
            cloid=cloid,
            timeouts=timeouts,
            replacement_callback=replacement_callback,
        )
        if pending_callback is not None:
            pending_callback(pending)
        pending.mark_submitting()
        pending.mark_submitted(node_status="ready")
        self.handles.append(pending)
        return pending


async def _immediate_client(
    *,
    encoder: ImmediateEncoder | None = None,
    **limits: int,
) -> tuple[AsyncChainClient, ImmediateEncoder, ImmediateTracker]:
    selected_encoder = encoder or ImmediateEncoder()
    tracker = ImmediateTracker()
    components = AsyncComponents(
        transport=ImmediateTransport(),
        encoder=selected_encoder,
        tracker=tracker,
        recovery=NoopRecovery(),
    )

    async def factory(_client: AsyncChainClient) -> AsyncComponents:
        return components

    client = AsyncChainClient(
        substrate_ws="ws://node.test",
        private_key="test-only-private-key",
        subaccount="0x" + "22" * 20,
        component_factory=factory,
        **limits,
    )
    await client.connect()
    return client, selected_encoder, tracker


def test_high_pending_pressure_is_thread_constant_and_rejects_before_send() -> None:
    async def submit(client: AsyncChainClient) -> PendingTransaction[Any]:
        return await client.perp_market.place_order(
            market_id=3,
            side="buy",
            size=1,
            price=1,
        )

    async def run() -> None:
        threads_before = threading.active_count()
        client, encoder, tracker = await _immediate_client()
        originals = [await submit(client) for _index in range(48)]

        with pytest.raises(ClientBackpressure):
            await submit(client)
        assert encoder.encode_calls == 48
        assert tracker.submit_calls == 48

        first_replacement = await originals[0].replace()
        second_replacement = await originals[1].replace()
        assert first_replacement.status is TxStatus.SUBMITTED
        assert second_replacement.status is TxStatus.SUBMITTED
        assert encoder.encode_calls == 50
        assert tracker.submit_calls == 50

        with pytest.raises(ClientBackpressure):
            await originals[2].replace()
        assert encoder.encode_calls == 50
        assert tracker.submit_calls == 50
        assert threading.active_count() <= threads_before + 1
        await client.close()

        tracked_client, tracked_encoder, tracked_tracker = await _immediate_client(
            max_tracked_transactions=1,
        )
        await submit(tracked_client)
        with pytest.raises(ClientBackpressure):
            await submit(tracked_client)
        assert tracked_encoder.encode_calls == 1
        assert tracked_tracker.submit_calls == 1
        await tracked_client.close()

        blocking = BlockingEncoder()
        outbound_client, outbound_encoder, outbound_tracker = await _immediate_client(
            encoder=blocking,
            max_tracked_transactions=10,
            max_pool_transactions_per_account=10,
            max_outbound_queue=1,
        )
        first = asyncio.create_task(submit(outbound_client))
        await blocking.started.wait()
        with pytest.raises(ClientBackpressure):
            await submit(outbound_client)
        assert outbound_encoder.encode_calls == 1
        assert outbound_tracker.submit_calls == 0
        blocking.release.set()
        await first
        await outbound_client.close()

    asyncio.run(run())


def test_nested_transport_secrets_are_redacted_everywhere(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_key = "private-test-value"
    raw_extrinsic = "0xraw-signed-test-value"
    nested_transport_error = RuntimeError(
        {
            "transport": ValueError(
                {
                    "private_key": private_key,
                    "nested": {"signed_extrinsic": raw_extrinsic},
                }
            )
        }
    )
    pending: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xsafe",
        nonce=123,
        cloid=None,
    )
    pending.mark_submitting()
    pending.mark_submitted(node_status="ready")
    pending.add_status_callback(
        lambda _update: (_ for _ in ()).throw(nested_transport_error)
    )
    error = TransactionDropped(
        code="TRANSPORT_DROPPED",
        stage=TxStage.SUBMISSION,
        tx_hash=pending.tx_hash,
        nonce=pending.nonce,
        elapsed_ms=1,
        certainty=OutcomeCertainty.UNKNOWN,
        retryable=False,
        suggested_action="Reconcile before retrying.",
        pending=pending,
        cause=nested_transport_error,
    )

    with caplog.at_level(logging.ERROR, logger="deepx_sdk"):
        pending.mark_dropped(error)

    rendered = "\n".join(
        (
            str(error),
            repr(error.to_dict()),
            repr(pending.diagnostics()),
            caplog.text,
        )
    )
    assert private_key not in rendered
    assert raw_extrinsic not in rendered
    assert "[REDACTED]" in repr(error.to_dict())


def test_benchmark_dry_run_and_required_instrumentation_contract() -> None:
    required_timings = {
        "encode_ms",
        "sign_ms",
        "rpc_submit_ms",
        "pool_wait_ms",
        "inclusion_ms",
        "event_decode_ms",
        "finalization_ms",
        "in_block_dispatch_ms",
    }
    assert required_timings <= set(TxTimings.__dataclass_fields__)
    assert isinstance(AsyncRpcTransport.connection_count, property)
    assert isinstance(AsyncChainClient.peak_tracked_transactions, property)
    assert isinstance(AsyncChainClient.peak_pool_transactions, property)

    script = Path(__file__).with_name("benchmark_async_chain.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--dry-run"],
        check=False,
        capture_output=True,
        text=True,
        env={},
    )
    assert completed.returncode == 0, completed.stderr
    assert "real_chain_executed: no" in completed.stdout
    assert "rolling_window: 48" in completed.stdout
    assert "configured_slot_duration_ms: 70" in completed.stdout
    assert "observed_finality: not measured" in completed.stdout
