from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from deepx_sdk._async_encoder import EncodedExtrinsic, TimestampNonceAllocator
from deepx_sdk._async_tracker import ExpectedEvent
from deepx_sdk._pending_tx import (
    ExecutionState,
    PendingTransaction,
    TxStatus,
    TxTimeouts,
)
from deepx_sdk._tx_diagnostics import (
    ClientBackpressure,
    ClientNotConnected,
    InclusionTimeout,
    OutcomeCertainty,
    ReplacementUnsupported,
    TransactionError,
    TransactionInvalid,
    TransactionUsurped,
    TxStage,
)
from deepx_sdk._types import SpotCancelOrderResult, SpotPlaceOrderResult
from deepx_sdk.async_client import (
    AsyncChainClient,
    AsyncComponents,
    _production_components,
)


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
        self.encode_calls: list[dict[str, object]] = []
        self.encode_priorities: list[bool] = []
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
        resolved_nonce = (
            self.nonce_allocator.next()
            if nonce is None
            else self.nonce_allocator.reserve(nonce)
        )
        self.encode_calls.append(
            {
                "call_module": call_module,
                "call_function": call_function,
                "call_params": call_params,
                "nonce": resolved_nonce,
            }
        )
        self.encode_priorities.append(priority)
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
        self.submissions: list[dict[str, object]] = []
        self.next_error: TransactionError | None = None

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
                TxStatus.NOT_INCLUDED,
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
        if self.next_error is not None:
            error = self.next_error
            self.next_error = None
            raise error
        self.submissions.append(
            {
                "encoded": encoded,
                "cloid": cloid,
                "expected_event": expected_event,
                "result_decoder": result_decoder,
                "replacement_callback": replacement_callback,
            }
        )
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


class FakeRecovery:
    def __init__(self) -> None:
        self.start_calls = 0
        self.close_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def reconcile(
        self,
        pending: PendingTransaction[Any],
    ) -> PendingTransaction[Any]:
        return pending


def fake_component_bundle() -> tuple[
    AsyncComponents,
    Callable[..., object],
    Callable[[], int],
]:
    components = AsyncComponents(
        transport=FakeTransport(),
        encoder=FakeEncoder(),
        tracker=FakeTracker(),
        recovery=FakeRecovery(),
    )
    factory_calls = 0

    async def factory(_client: AsyncChainClient) -> AsyncComponents:
        nonlocal factory_calls
        factory_calls += 1
        return components

    def calls() -> int:
        return factory_calls

    return components, factory, calls


def test_async_client_requires_connect_and_closes_pending() -> None:
    async def run() -> None:
        components, factory, factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            component_factory=factory,
        )

        with pytest.raises(ClientNotConnected):
            await client.perp_market.place_order(
                market_id=3,
                side="buy",
                size=10,
                price=20,
            )

        await client.connect()
        await client.connect()
        pending = await client.perp_market.place_order(
            market_id=3,
            side="buy",
            size=10,
            price=20,
        )
        await client.close()

        assert factory_calls() == 1
        assert components.transport.connect_calls == 1
        assert components.encoder.bootstrap_calls == 1
        assert components.recovery.start_calls == 1
        assert components.recovery.close_calls == 1
        assert components.transport.close_calls == 1
        assert pending.status is TxStatus.CLIENT_CLOSED

    asyncio.run(run())


def test_async_client_connects_and_closes_dedicated_recovery_transport() -> None:
    async def run() -> None:
        submission_transport = FakeTransport()
        recovery_transport = FakeTransport()
        components = AsyncComponents(
            transport=submission_transport,
            encoder=FakeEncoder(),
            tracker=FakeTracker(),
            recovery=FakeRecovery(),
            recovery_transport=recovery_transport,
        )

        async def factory(_client: AsyncChainClient) -> AsyncComponents:
            return components

        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            component_factory=factory,
        )
        await client.connect()
        await client.close()

        assert submission_transport.connect_calls == 1
        assert recovery_transport.connect_calls == 1
        assert recovery_transport.close_calls == 1
        assert submission_transport.close_calls == 1

    asyncio.run(run())


def test_production_components_route_recovery_to_configured_endpoints() -> None:
    async def run() -> None:
        client = AsyncChainClient(
            substrate_ws_endpoints=["ws://submission.test"],
            recovery_substrate_ws_endpoints=["ws://recovery.test"],
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
        )
        components = await _production_components(client)

        assert components.transport.connection_url == "ws://submission.test"
        assert components.recovery_transport.connection_url == "ws://recovery.test"
        assert components.recovery._pool_transport is components.transport

        await components.recovery_transport.close()
        await components.transport.close()

    asyncio.run(run())


def test_async_client_pool_limit_defaults_and_validation() -> None:
    client = AsyncChainClient(
        substrate_ws="ws://node.test",
        private_key="0x" + "11" * 32,
        subaccount="0x" + "22" * 20,
    )
    assert client.node_pool_limit_per_account == 50
    assert client.max_pool_transactions_per_account == 48
    assert client.priority_pool_reserve == 2

    with pytest.raises(ValueError, match="node_pool_limit_per_account"):
        AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            node_pool_limit_per_account=0,
        )
    with pytest.raises(ValueError, match="exceeds"):
        AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            node_pool_limit_per_account=10,
            max_pool_transactions_per_account=9,
            priority_pool_reserve=2,
        )


def test_async_client_accepts_ordered_rpc_endpoints() -> None:
    client = AsyncChainClient(
        substrate_ws_endpoints=[
            "ws://primary.test",
            "ws://backup.test",
            "ws://primary.test",
        ],
        recovery_substrate_ws_endpoints=[
            "ws://recovery.test",
            "ws://recovery-backup.test",
            "ws://recovery.test",
        ],
        private_key="0x" + "11" * 32,
        subaccount="0x" + "22" * 20,
    )

    assert client.substrate_ws == "ws://primary.test"
    assert client.substrate_ws_endpoints == (
        "ws://primary.test",
        "ws://backup.test",
    )
    assert client.active_rpc_endpoint == "ws://primary.test"
    assert client.recovery_substrate_ws_endpoints == (
        "ws://recovery.test",
        "ws://recovery-backup.test",
    )

    with pytest.raises(ValueError, match="substrate_ws_endpoints"):
        AsyncChainClient(
            substrate_ws_endpoints=[],
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
        )


def test_custom_priority_pool_reserve_stops_at_configured_limit() -> None:
    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            max_tracked_transactions=10,
            node_pool_limit_per_account=5,
            max_pool_transactions_per_account=3,
            priority_pool_reserve=2,
            component_factory=factory,
        )
        await client.connect()
        for _ in range(3):
            await client.perp_market.place_order(
                market_id=3,
                side="buy",
                size=10,
                price=20,
            )

        for order_id in (41, 42):
            await client.perp_market.cancel_order(
                market_id=3,
                order_id=order_id,
                fast_cancel=True,
            )
        with pytest.raises(ClientBackpressure) as caught:
            await client.perp_market.cancel_order(
                market_id=3,
                order_id=43,
                fast_cancel=True,
            )

        assert "configured priority pool limit of 5" in str(caught.value)
        assert client.peak_pool_transactions == 5
        await client.close()

    asyncio.run(run())


def test_client_transaction_manager_tracks_every_state_and_identifier() -> None:
    async def run() -> None:
        events = []
        _components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            component_factory=factory,
            transaction_listener=events.append,
        )
        await client.connect()

        pending = await client.perp_market.place_order(
            market_id=3,
            side="buy",
            size=10,
            price=20,
            cloid=77,
        )
        await client.transactions.wait_idle()

        assert pending.state is ExecutionState.ACCEPTED
        assert client.transactions.get(pending.tx_hash) is pending
        assert client.transactions.get_by_cloid(77) is pending
        assert [event.execution_state for event in events] == [
            ExecutionState.SUBMITTING,
            ExecutionState.ACCEPTED,
        ]

        await client.close()
        assert events[-1].execution_state is ExecutionState.ACTION_REQUIRED

    asyncio.run(run())


def test_market_and_wait_helpers_return_executed_results() -> None:
    async def complete_next(
        task: asyncio.Task[Any],
        components: AsyncComponents,
        result: object,
    ) -> None:
        expected_count = len(components.tracker.handles) + 1
        while len(components.tracker.handles) < expected_count:
            await asyncio.sleep(0)
        pending = components.tracker.handles[-1]
        pending.mark_in_block_success(
            result=result,
            block_hash="0xblock",
            extrinsic_hash=pending.tx_hash,
        )
        assert await task is result

    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            component_factory=factory,
        )
        await client.connect()
        pair = "0x" + "33" * 32

        calls = [
            client.perp_market.place_order_and_wait(
                market_id=3,
                side="buy",
                size=10,
                price=20,
            ),
            client.perp_market.cancel_order_and_wait(
                market_id=3,
                order_id=41,
            ),
            client.spot_market.place_order_and_wait(
                side="buy",
                pair=pair,
                quote_amount=100,
                base_amount=10,
            ),
            client.spot_market.cancel_order_and_wait(
                side="buy",
                pair=pair,
                order_id=42,
            ),
        ]
        for index, call in enumerate(calls):
            result = object()
            task = asyncio.create_task(call)
            await complete_next(task, components, result)

        await client.close()

    asyncio.run(run())


def test_place_order_shapes_ready_return_and_timestamp_nonces() -> None:
    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            component_factory=factory,
        )
        await client.connect()

        limit = await client.perp_market.place_order(
            market_id=3,
            side="buy",
            size=10,
            price=20,
        )
        market = await client.perp_market.place_order(
            market_id=4,
            side=False,
            size=11,
            order_type="market",
            slippage=5,
        )
        ioc = await client.perp_market.place_order(
            market_id=5,
            side="long",
            size=12,
            price=22,
            order_type="ioc",
            cloid=77,
            nonce_ms=1_000_005,
        )

        assert limit.status is TxStatus.SUBMITTED
        assert limit.nonce == 1_000_000
        assert market.nonce == 1_000_001
        assert ioc.nonce == 1_000_005
        assert components.encoder.encode_calls == [
            {
                "call_module": "PerpMarket",
                "call_function": "place_order",
                "call_params": {
                    "params": {
                        "subaccount": "0x" + "22" * 20,
                        "market_id": 3,
                        "is_long": True,
                        "size": 10,
                        "price": 20,
                        "order_type": {"Limit": "GTC"},
                        "take_profit": None,
                        "stop_loss": None,
                        "reduce_only": False,
                        "post_only": "None",
                        "cloid": None,
                    }
                },
                "nonce": 1_000_000,
            },
            {
                "call_module": "PerpMarket",
                "call_function": "place_order",
                "call_params": {
                    "params": {
                        "subaccount": "0x" + "22" * 20,
                        "market_id": 4,
                        "is_long": False,
                        "size": 11,
                        "price": 0,
                        "order_type": {"Market": 5},
                        "take_profit": None,
                        "stop_loss": None,
                        "reduce_only": False,
                        "post_only": "None",
                        "cloid": None,
                    }
                },
                "nonce": 1_000_001,
            },
            {
                "call_module": "PerpMarket",
                "call_function": "place_order",
                "call_params": {
                    "params": {
                        "subaccount": "0x" + "22" * 20,
                        "market_id": 5,
                        "is_long": True,
                        "size": 12,
                        "price": 22,
                        "order_type": {"Limit": "IOC"},
                        "take_profit": None,
                        "stop_loss": None,
                        "reduce_only": False,
                        "post_only": "None",
                        "cloid": 77,
                    }
                },
                "nonce": 1_000_005,
            },
        ]
        await client.close()

    asyncio.run(run())


def test_cancel_order_normal_and_fast_shapes() -> None:
    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            component_factory=factory,
        )
        await client.connect()

        await client.perp_market.cancel_order(
            market_id=3,
            order_id=41,
        )
        await client.perp_market.cancel_order(
            market_id=4,
            order_id=42,
            fast_cancel=True,
        )

        expected_base = {
            "subaccount": "0x" + "22" * 20,
            "cancel_reason": "UserCanceled",
        }
        assert components.encoder.encode_calls[0]["call_params"] == {
            "params": {
                **expected_base,
                "order_id": 41,
                "market_id": 3,
                "fast_cancel": False,
            }
        }
        assert components.encoder.encode_calls[1]["call_params"] == {
            "params": {
                **expected_base,
                "order_id": 42,
                "market_id": 4,
                "fast_cancel": True,
            }
        }
        assert components.tracker.submissions[0]["expected_event"] == ExpectedEvent(
            "PerpMarket",
            "OrderCancelled",
        )
        assert components.tracker.submissions[1]["expected_event"] == ExpectedEvent(
            "System",
            "ExtrinsicSuccess",
        )
        await client.close()

    asyncio.run(run())


def test_async_spot_place_order_exact_shapes_and_decoders() -> None:
    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            component_factory=factory,
        )
        await client.connect()
        pair = "0x" + "33" * 32

        with pytest.raises(TypeError, match="pair"):
            await client.spot_market.place_order(
                side="buy",
                quote_amount=1,
                base_amount=2,
            )

        buy_limit = await client.spot_market.place_order(
            side="buy",
            pair=pair,
            quote_amount=1000,
            base_amount=10,
            post_only=1,
        )
        sell_limit = await client.spot_market.place_order(
            side=False,
            pair=pair,
            quote_amount=2000,
            base_amount=20,
            post_only=2,
            reduce_only=True,
        )
        buy_market = await client.spot_market.place_order(
            side=True,
            pair=pair,
            quote_amount=3000,
            base_amount=30,
            order_type="market",
            reduce_only=True,
        )
        sell_market = await client.spot_market.place_order(
            side="sell",
            pair=pair,
            quote_amount=4000,
            base_amount=40,
            order_type=1,
            slippage=9,
        )
        buy_ioc = await client.spot_market.place_order(
            side="bid",
            pair=pair,
            quote_amount=5000,
            base_amount=50,
            order_type="ioc",
            reduce_only=True,
        )
        sell_ioc = await client.spot_market.place_order(
            side="ask",
            pair=pair,
            quote_amount=6000,
            base_amount=60,
            order_type=3,
            cloid=2**31,
            nonce_ms=1_000_010,
        )

        assert all(
            pending.status is TxStatus.SUBMITTED
            for pending in (
                buy_limit,
                sell_limit,
                buy_market,
                sell_market,
                buy_ioc,
                sell_ioc,
            )
        )
        assert components.encoder.encode_calls == [
            {
                "call_module": "SpotMarket",
                "call_function": "place_order",
                "call_params": {
                    "params": {
                        "subaccount": "0x" + "22" * 20,
                        "pair": pair,
                        "is_buy": True,
                        "quote_amount": 1000,
                        "base_amount": 10,
                        "order_type": {"Limit": "GTC"},
                        "post_only": "MustPostOnly",
                        "reduce_only": False,
                        "cloid": None,
                    }
                },
                "nonce": 1_000_000,
            },
            {
                "call_module": "SpotMarket",
                "call_function": "place_order",
                "call_params": {
                    "params": {
                        "subaccount": "0x" + "22" * 20,
                        "pair": pair,
                        "is_buy": False,
                        "quote_amount": 2000,
                        "base_amount": 20,
                        "order_type": {"Limit": "GTC"},
                        "post_only": "Adaptive",
                        "reduce_only": True,
                        "cloid": None,
                    }
                },
                "nonce": 1_000_001,
            },
            {
                "call_module": "SpotMarket",
                "call_function": "place_order",
                "call_params": {
                    "params": {
                        "subaccount": "0x" + "22" * 20,
                        "pair": pair,
                        "is_buy": True,
                        "quote_amount": 3000,
                        "base_amount": 30,
                        "order_type": {"Market": None},
                        "post_only": "None",
                        "reduce_only": True,
                        "cloid": None,
                    }
                },
                "nonce": 1_000_002,
            },
            {
                "call_module": "SpotMarket",
                "call_function": "place_order",
                "call_params": {
                    "params": {
                        "subaccount": "0x" + "22" * 20,
                        "pair": pair,
                        "is_buy": False,
                        "quote_amount": 4000,
                        "base_amount": 40,
                        "order_type": {"Market": 9},
                        "post_only": "None",
                        "reduce_only": False,
                        "cloid": None,
                    }
                },
                "nonce": 1_000_003,
            },
            {
                "call_module": "SpotMarket",
                "call_function": "place_order",
                "call_params": {
                    "params": {
                        "subaccount": "0x" + "22" * 20,
                        "pair": pair,
                        "is_buy": True,
                        "quote_amount": 5000,
                        "base_amount": 50,
                        "order_type": {"Limit": "IOC"},
                        "post_only": "None",
                        "reduce_only": True,
                        "cloid": None,
                    }
                },
                "nonce": 1_000_004,
            },
            {
                "call_module": "SpotMarket",
                "call_function": "place_order",
                "call_params": {
                    "params": {
                        "subaccount": "0x" + "22" * 20,
                        "pair": pair,
                        "is_buy": False,
                        "quote_amount": 6000,
                        "base_amount": 60,
                        "order_type": {"Limit": "IOC"},
                        "post_only": "None",
                        "reduce_only": False,
                        "cloid": 2**31,
                    }
                },
                "nonce": 1_000_010,
            },
        ]
        assert [
            submission["expected_event"]
            for submission in components.tracker.submissions
        ] == [
            ExpectedEvent("SpotMarket", "StateOrderBuy"),
            ExpectedEvent("SpotMarket", "StateOrderSell"),
            ExpectedEvent("SpotMarket", "StateOrderBuy"),
            ExpectedEvent("SpotMarket", "StateOrderSell"),
            ExpectedEvent("SpotMarket", "StateOrderBuy"),
            ExpectedEvent("SpotMarket", "StateOrderSell"),
        ]

        buy_decoder = components.tracker.submissions[0]["result_decoder"]
        sell_decoder = components.tracker.submissions[5]["result_decoder"]
        assert callable(buy_decoder)
        assert callable(sell_decoder)
        assert buy_decoder({"order_id": "0x7b"}, buy_limit) == SpotPlaceOrderResult(
            order_id=123,
            tx_hash=buy_limit.tx_hash,
            extrinsic_hash=buy_limit.tx_hash,
        )
        assert sell_decoder(
            {"order": {"id": {"value": "124"}}},
            sell_ioc,
        ) == SpotPlaceOrderResult(
            order_id=124,
            tx_hash=sell_ioc.tx_hash,
            extrinsic_hash=sell_ioc.tx_hash,
        )
        await client.close()

    asyncio.run(run())


def test_async_spot_cancel_order_exact_shapes_and_decoders() -> None:
    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            component_factory=factory,
        )
        await client.connect()
        pair = "0x" + "33" * 32

        normal = await client.spot_market.cancel_order(
            side="buy",
            pair=pair,
            order_id=99,
        )
        fast = await client.spot_market.cancel_order(
            side=False,
            pair=pair,
            order_id=100,
            fast_cancel=True,
            nonce_ms=1_000_010,
        )

        assert normal.status is TxStatus.SUBMITTED
        assert fast.status is TxStatus.SUBMITTED
        assert components.encoder.encode_calls == [
            {
                "call_module": "SpotMarket",
                "call_function": "cancel_order",
                "call_params": {
                    "params": {
                        "subaccount": "0x" + "22" * 20,
                        "pair": pair,
                        "order_id": 99,
                        "is_buy": True,
                        "cancel_reason": "UserCanceled",
                        "fast_cancel": False,
                    }
                },
                "nonce": 1_000_000,
            },
            {
                "call_module": "SpotMarket",
                "call_function": "cancel_order",
                "call_params": {
                    "params": {
                        "subaccount": "0x" + "22" * 20,
                        "pair": pair,
                        "order_id": 100,
                        "is_buy": False,
                        "cancel_reason": "UserCanceled",
                        "fast_cancel": True,
                    }
                },
                "nonce": 1_000_010,
            },
        ]
        assert [
            submission["expected_event"]
            for submission in components.tracker.submissions
        ] == [
            ExpectedEvent("SpotMarket", "OrderCancelled"),
            ExpectedEvent("System", "ExtrinsicSuccess"),
        ]

        normal_decoder = components.tracker.submissions[0]["result_decoder"]
        fast_decoder = components.tracker.submissions[1]["result_decoder"]
        assert callable(normal_decoder)
        assert callable(fast_decoder)
        assert normal_decoder({"id": "123"}, normal) == SpotCancelOrderResult(
            order_id=123,
            tx_hash=normal.tx_hash,
            extrinsic_hash=normal.tx_hash,
        )
        assert fast_decoder({}, fast) == SpotCancelOrderResult(
            order_id=100,
            tx_hash=fast.tx_hash,
            extrinsic_hash=fast.tx_hash,
        )
        await client.close()

    asyncio.run(run())


def test_explicit_replacement_uses_same_nonce_no_op_and_reserved_pool_slot() -> None:
    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            max_pool_transactions_per_account=1,
            component_factory=factory,
        )
        await client.connect()
        original = await client.perp_market.place_order(
            market_id=3,
            side="buy",
            size=10,
            price=20,
        )

        with pytest.raises(ClientBackpressure):
            await client.perp_market.cancel_order(market_id=3, order_id=41)

        replacement = await original.replace()

        assert replacement.status is TxStatus.SUBMITTED
        assert replacement.nonce == original.nonce
        assert components.encoder.encode_calls[-1] == {
            "call_module": "Subaccount",
            "call_function": "no_op",
            "call_params": {},
            "nonce": original.nonce,
        }
        assert components.tracker.submissions[-1]["expected_event"] == ExpectedEvent(
            "System",
            "ExtrinsicSuccess",
        )

        original.mark_usurped(
            TransactionUsurped(
                code="TEST_USURPED",
                stage=TxStage.SUBMISSION,
                elapsed_ms=0,
                certainty=OutcomeCertainty.REPLACED,
                retryable=False,
                suggested_action="Track replacement.",
            )
        )
        await client.close()

    asyncio.run(run())


def test_replacement_is_never_automatic_and_is_rejected_after_in_block() -> None:
    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            component_factory=factory,
        )
        await client.connect()
        original = await client.perp_market.cancel_order(
            market_id=3,
            order_id=41,
        )

        with pytest.raises(InclusionTimeout):
            await original.wait_in_block(timeout=0.001)
        assert len(components.encoder.encode_calls) == 1

        original.mark_in_block_success(
            result=object(),
            block_hash="0xblock",
            extrinsic_hash=original.tx_hash,
        )
        with pytest.raises(ReplacementUnsupported):
            await original.replace()
        assert len(components.encoder.encode_calls) == 1
        await client.close()

    asyncio.run(run())


def test_replacement_node_rejection_preserves_original_tracking() -> None:
    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            component_factory=factory,
        )
        await client.connect()
        original = await client.perp_market.place_order(
            market_id=3,
            side="buy",
            size=10,
            price=20,
        )
        components.tracker.next_error = TransactionInvalid.from_node_reason(
            "Payment: CallType::Timestamp(1)",
            tx_hash="0xrejected",
            nonce=original.nonce,
        )

        with pytest.raises(TransactionInvalid) as caught:
            await original.replace()

        assert "Quota" in str(caught.value)
        assert original.status is TxStatus.SUBMITTED
        assert client.pending_transaction(original.tx_hash) is original

        replacement = await original.replace()
        assert replacement.status is TxStatus.SUBMITTED
        await client.close()

    asyncio.run(run())


def test_replacement_never_exceeds_configured_priority_pool_limit() -> None:
    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            max_tracked_transactions=100,
            max_pool_transactions_per_account=48,
            component_factory=factory,
        )
        await client.connect()
        originals = [
            await client.perp_market.place_order(
                market_id=3,
                side="buy",
                size=10,
                price=20,
            )
            for _ in range(48)
        ]

        await originals[0].replace()
        await originals[1].replace()
        with pytest.raises(ClientBackpressure) as caught:
            await originals[2].replace()

        assert "configured priority pool limit of 50" in str(caught.value)
        assert len(components.encoder.encode_calls) == 50
        assert originals[2].status is TxStatus.SUBMITTED
        await client.close()

    asyncio.run(run())


def test_pool_backpressure_happens_before_encoding_and_waits_for_capacity() -> None:
    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            max_tracked_transactions=2,
            max_pool_transactions_per_account=1,
            max_outbound_queue=1,
            component_factory=factory,
        )
        await client.connect()
        first = await client.perp_market.place_order(
            market_id=3,
            side="buy",
            size=10,
            price=20,
        )

        with pytest.raises(ClientBackpressure) as caught:
            await client.perp_market.place_order(
                market_id=3,
                side="sell",
                size=11,
                price=21,
            )
        assert caught.value.certainty is OutcomeCertainty.NOT_SUBMITTED
        assert len(components.encoder.encode_calls) == 1

        writable = asyncio.create_task(client.wait_writable())
        await asyncio.sleep(0)
        assert not writable.done()

        first.mark_in_block_success(
            result=object(),
            block_hash="0xblock",
            extrinsic_hash=first.tx_hash,
        )
        await asyncio.wait_for(writable, timeout=0.1)
        await client.close()

    asyncio.run(run())


def test_fast_cancel_uses_reserved_pool_capacity() -> None:
    async def run() -> None:
        components, factory, _factory_calls = fake_component_bundle()
        client = AsyncChainClient(
            substrate_ws="ws://node.test",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "22" * 20,
            max_tracked_transactions=4,
            max_pool_transactions_per_account=1,
            max_outbound_queue=4,
            component_factory=factory,
        )
        await client.connect()
        await client.perp_market.place_order(
            market_id=3,
            side="buy",
            size=10,
            price=20,
        )

        with pytest.raises(ClientBackpressure):
            await client.perp_market.cancel_order(
                market_id=3,
                order_id=41,
            )

        urgent = await client.perp_market.cancel_order(
            market_id=3,
            order_id=41,
            fast_cancel=True,
        )

        assert urgent.status is TxStatus.SUBMITTED
        assert client.peak_pool_transactions == 2
        assert components.encoder.encode_priorities == [False, True]
        await client.close()

    asyncio.run(run())
