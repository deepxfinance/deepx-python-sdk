from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

import pytest

import deepx_sdk as dx
from deepx_sdk._async_encoder import EncodedExtrinsic, TimestampNonceAllocator
from deepx_sdk._async_tracker import ExpectedEvent
from deepx_sdk._pending_tx import PendingTransaction, TxStatus, TxTimeouts
from deepx_sdk._tx_diagnostics import InclusionTimeout
from deepx_sdk.async_client import AsyncChainClient, AsyncComponents


class FakeTransport:
    def __init__(self) -> None:
        self.connect_calls = 0
        self.close_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class FakeEncoder:
    def __init__(self) -> None:
        self.bootstrap_calls = 0
        self.calls: list[tuple[str, str]] = []
        self.nonce_allocator = TimestampNonceAllocator(lambda: 1_000_000)

    async def bootstrap(self) -> None:
        self.bootstrap_calls += 1

    async def encode_pallet_call(
        self,
        *,
        call_module: str,
        call_function: str,
        call_params: dict[str, object],
        nonce: int | None = None,
        priority: bool = False,
    ) -> EncodedExtrinsic:
        del call_params, priority
        resolved_nonce = (
            self.nonce_allocator.next()
            if nonce is None
            else self.nonce_allocator.reserve(nonce)
        )
        self.calls.append((call_module, call_function))
        return EncodedExtrinsic(
            data_hex=f"0x{resolved_nonce:x}",
            tx_hash=f"0xtx{resolved_nonce}",
            nonce=resolved_nonce,
            runtime_version=1,
            encode_ms=0.1,
            sign_ms=0.1,
        )


class FakeTracker:
    def __init__(self) -> None:
        self.handles: list[PendingTransaction[Any]] = []
        self._decoders: dict[str, Callable[..., object]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def pending_transactions(self) -> tuple[PendingTransaction[Any], ...]:
        return tuple(
            handle
            for handle in self.handles
            if handle.status
            not in {
                TxStatus.FINALIZED,
                TxStatus.INVALID,
                TxStatus.DROPPED,
                TxStatus.USURPED,
                TxStatus.CLIENT_CLOSED,
            }
        )

    def pending_transaction(self, tx_hash: str) -> PendingTransaction[Any] | None:
        return next(
            (handle for handle in self.handles if handle.tx_hash == tx_hash),
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
        del expected_event
        self._loop = asyncio.get_running_loop()
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
        self._decoders[pending.tx_hash] = result_decoder
        return pending

    def execute(self, tx_hash: str, fields: Mapping[str, object]) -> object:
        assert self._loop is not None

        async def finish() -> object:
            pending = self.pending_transaction(tx_hash)
            assert pending is not None
            result = self._decoders[tx_hash](fields, pending)
            pending.mark_in_block_success(
                result=result,
                block_hash="0xblock",
                extrinsic_hash="0xextrinsic",
            )
            return result

        return asyncio.run_coroutine_threadsafe(finish(), self._loop).result()

    def finalize(self, tx_hash: str) -> None:
        assert self._loop is not None

        async def finish() -> None:
            pending = self.pending_transaction(tx_hash)
            assert pending is not None
            pending.mark_finalized(block_hash="0xfinal")

        asyncio.run_coroutine_threadsafe(finish(), self._loop).result()


class FakeRecovery:
    def __init__(self) -> None:
        self.start_calls = 0
        self.close_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


def fake_components() -> tuple[AsyncComponents, Callable[..., object]]:
    components = AsyncComponents(
        transport=FakeTransport(),
        encoder=FakeEncoder(),
        tracker=FakeTracker(),
        recovery=FakeRecovery(),
    )

    async def factory(_client: AsyncChainClient) -> AsyncComponents:
        return components

    return components, factory


def make_client() -> dx.ChainClient:
    return dx.ChainClient(
        substrate_ws="ws://node.test",
        private_key="0x" + "11" * 32,
        subaccount="0x" + "22" * 20,
    )


def test_sync_submit_apis_share_one_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    components, factory = fake_components()
    monkeypatch.setattr("deepx_sdk.async_client._production_components", factory)
    pair = "0x" + "33" * 32

    with make_client() as client:
        tickets = [
            client.perp_market.submit_order(
                market_id=3,
                side="buy",
                size=10,
                price=20,
                cloid=71,
            ),
            client.perp_market.submit_cancel(market_id=3, order_id=101),
            client.spot_market.submit_order(
                pair=pair,
                side="buy",
                quote_amount=20,
                base_amount=10,
                cloid=72,
            ),
            client.spot_market.submit_cancel(
                pair=pair,
                side="sell",
                order_id=102,
            ),
        ]

        assert all(ticket.state is dx.ExecutionState.ACCEPTED for ticket in tickets)
        assert [ticket.cloid for ticket in tickets] == [71, None, 72, None]
        assert components.transport.connect_calls == 1
        assert components.encoder.calls == [
            ("PerpMarket", "place_order"),
            ("PerpMarket", "cancel_order"),
            ("SpotMarket", "place_order"),
            ("SpotMarket", "cancel_order"),
        ]

    assert components.recovery.close_calls == 1
    assert components.transport.close_calls == 1


def test_sync_client_forwards_pool_limit_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _components, factory = fake_components()
    monkeypatch.setattr("deepx_sdk.async_client._production_components", factory)

    with dx.ChainClient(
        substrate_ws="ws://node.test",
        substrate_ws_endpoints=[
            "ws://node.test",
            "ws://backup.test",
        ],
        private_key="0x" + "11" * 32,
        subaccount="0x" + "22" * 20,
        node_pool_limit_per_account=10,
        max_pool_transactions_per_account=7,
        priority_pool_reserve=3,
    ) as client:
        ticket = client.perp_market.submit_order(
            market_id=3,
            side="buy",
            size=10,
            price=20,
        )
        async_client = client._ticket_runtime._client

        assert ticket.state is dx.ExecutionState.ACCEPTED
        assert async_client.node_pool_limit_per_account == 10
        assert async_client.max_pool_transactions_per_account == 7
        assert async_client.priority_pool_reserve == 3
        assert async_client.substrate_ws_endpoints == (
            "ws://node.test",
            "ws://backup.test",
        )
        assert client.active_rpc_endpoint == "ws://node.test"


def test_sync_ticket_waits_for_execution_and_finality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components, factory = fake_components()
    monkeypatch.setattr("deepx_sdk.async_client._production_components", factory)
    tracker = components.tracker

    with make_client() as client:
        ticket = client.perp_market.submit_order(
            market_id=3,
            side="buy",
            size=10,
            price=20,
            cloid=73,
        )
        expected = tracker.execute(ticket.tx_hash, {"order_id": 901})

        assert ticket.executed() == expected
        assert ticket.state is dx.ExecutionState.EXECUTED
        assert ticket.snapshot().block_hash == "0xblock"

        tracker.finalize(ticket.tx_hash)

        assert ticket.finalized() == expected
        assert ticket.state is dx.ExecutionState.FINALIZED


def test_sync_ticket_timeout_preserves_actionable_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _components, factory = fake_components()
    monkeypatch.setattr("deepx_sdk.async_client._production_components", factory)

    with make_client() as client:
        ticket = client.perp_market.submit_order(
            market_id=3,
            side="buy",
            size=10,
            price=20,
        )

        with pytest.raises(InclusionTimeout) as caught:
            ticket.executed(timeout=0)

        assert caught.value.code == "INCLUSION_TIMEOUT"
        assert caught.value.stage.value == "inclusion"
        assert caught.value.suggested_action
        assert ticket.state is dx.ExecutionState.ACCEPTED
