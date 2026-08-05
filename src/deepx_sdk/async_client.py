from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ._abi import normalize_address, normalize_bytes32
from ._async_encoder import ExtrinsicEncoder
from ._async_recovery import RecoveryConfig, RecoveryTracker
from ._async_tracker import ExpectedEvent, TransactionTracker
from ._async_transport import AsyncRpcTransport, _safe_url
from ._errors import ValidationError
from ._network import (
    network_config,
    normalize_net,
    resolve_substrate_ws_endpoints,
)
from ._pending_tx import PendingTransaction, TxStatus, TxTimeouts
from ._perp_market import (
    _parse_int_field as _parse_perp_int_field,
    _perp_place_params,
)
from ._spot_market import (
    _parse_int_field as _parse_spot_int_field,
    _post_only_param,
    _spot_order_type_param,
    _spot_place_params,
)
from ._transaction_manager import TransactionListener, TransactionManager
from ._types import (
    CancelOrderResult,
    PlaceOrderResult,
    SpotCancelOrderResult,
    SpotPlaceOrderResult,
    TxResult,
)
from ._tx_diagnostics import (
    ClientBackpressure,
    ClientNotConnected,
    OutcomeCertainty,
    ReplacementUnsupported,
    TransactionError,
    TxStage,
)
from .client import (
    _normalize_order_type,
    _normalize_perp_side,
    _normalize_spot_side,
)


@dataclass(frozen=True)
class AsyncComponents:
    transport: Any
    encoder: Any
    tracker: Any
    recovery: Any


@dataclass
class _CapacityReservation:
    tracked: bool = True
    pool: bool = True
    outbound: bool = True


class AsyncPerpMarketClient:
    def __init__(self, client: AsyncChainClient) -> None:
        self._client = client

    async def place_order(
        self,
        *,
        side: str | bool,
        size: int,
        market_id: int,
        order_type: str | int = "limit",
        price: int | None = None,
        slippage: int | None = None,
        take_profit: int | None = None,
        stop_loss: int | None = None,
        reduce_only: bool = False,
        post_only: int = 0,
        cloid: int | None = None,
        nonce_ms: int | None = None,
    ) -> PendingTransaction[PlaceOrderResult]:
        order_type_id = {
            "limit": 0,
            "market": 1,
            "stop": 2,
            "ioc": 3,
        }[_normalize_order_type(order_type)]
        call_params = {
            "params": _perp_place_params(
                subaccount=self._client.subaccount,
                market_id=market_id,
                is_long=_normalize_perp_side(side),
                size=size,
                price=0 if price is None else price,
                order_type=order_type_id,
                slippage=slippage,
                take_profit=take_profit,
                stop_loss=stop_loss,
                reduce_only=reduce_only,
                post_only=post_only,
                cloid=cloid,
            )
        }

        def decode(
            fields: Mapping[str, Any],
            pending: PendingTransaction[PlaceOrderResult],
        ) -> PlaceOrderResult:
            return PlaceOrderResult(
                order_id=_parse_perp_int_field(fields, "order_id"),
                tx_hash=pending.tx_hash,
                extrinsic_hash=pending.extrinsic_hash or pending.tx_hash,
            )

        return await self._client._submit(
            call_module="PerpMarket",
            call_function="place_order",
            call_params=call_params,
            nonce_ms=nonce_ms,
            cloid=cloid,
            expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
            result_decoder=decode,
        )

    async def cancel_order(
        self,
        *,
        market_id: int,
        order_id: int,
        fast_cancel: bool = False,
        nonce_ms: int | None = None,
    ) -> PendingTransaction[CancelOrderResult]:
        call_params = {
            "params": {
                "subaccount": normalize_address(self._client.subaccount),
                "order_id": int(order_id),
                "market_id": int(market_id),
                "cancel_reason": "UserCanceled",
                "fast_cancel": bool(fast_cancel),
            }
        }

        def decode(
            fields: Mapping[str, Any],
            pending: PendingTransaction[CancelOrderResult],
        ) -> CancelOrderResult:
            resolved_order_id = (
                int(order_id)
                if fast_cancel
                else _parse_perp_int_field(fields, "order_id")
            )
            return CancelOrderResult(
                order_id=resolved_order_id,
                tx_hash=pending.tx_hash,
                extrinsic_hash=pending.extrinsic_hash or pending.tx_hash,
            )

        expected_event = (
            ExpectedEvent("System", "ExtrinsicSuccess")
            if fast_cancel
            else ExpectedEvent("PerpMarket", "OrderCancelled")
        )
        return await self._client._submit(
            call_module="PerpMarket",
            call_function="cancel_order",
            call_params=call_params,
            nonce_ms=nonce_ms,
            cloid=None,
            expected_event=expected_event,
            result_decoder=decode,
            priority=fast_cancel,
        )

    async def place_order_and_wait(self, **kwargs: Any) -> PlaceOrderResult:
        pending = await self.place_order(**kwargs)
        return await pending.executed()

    async def cancel_order_and_wait(self, **kwargs: Any) -> CancelOrderResult:
        pending = await self.cancel_order(**kwargs)
        return await pending.executed()


class AsyncSpotMarketClient:
    def __init__(self, client: AsyncChainClient) -> None:
        self._client = client

    async def place_order(
        self,
        *,
        side: str | bool,
        pair: str,
        quote_amount: int,
        base_amount: int,
        order_type: str | int = "limit",
        slippage: int | None = None,
        post_only: int = 0,
        reduce_only: bool = False,
        cloid: int | None = None,
        nonce_ms: int | None = None,
    ) -> PendingTransaction[SpotPlaceOrderResult]:
        normalized_side = _normalize_spot_side(side)
        normalized_order_type = _normalize_order_type(order_type)
        order_type_id = {
            "limit": 0,
            "market": 1,
            "stop": 2,
            "ioc": 3,
        }[normalized_order_type]
        call_params = _spot_place_params(
            subaccount=self._client.subaccount,
            pair=pair,
            is_buy=normalized_side == "buy",
            quote_amount=quote_amount,
            base_amount=base_amount,
            order_type=_spot_order_type_param(order_type_id, slippage),
            post_only=_post_only_param(
                post_only if normalized_order_type == "limit" else 0
            ),
            reduce_only=reduce_only,
            cloid=cloid,
        )

        def decode(
            fields: Mapping[str, Any],
            pending: PendingTransaction[SpotPlaceOrderResult],
        ) -> SpotPlaceOrderResult:
            return SpotPlaceOrderResult(
                order_id=_parse_spot_int_field(fields, "order_id"),
                tx_hash=pending.tx_hash,
                extrinsic_hash=pending.extrinsic_hash or pending.tx_hash,
            )

        return await self._client._submit(
            call_module="SpotMarket",
            call_function="place_order",
            call_params=call_params,
            nonce_ms=nonce_ms,
            cloid=cloid,
            expected_event=ExpectedEvent(
                "SpotMarket",
                "StateOrderBuy"
                if normalized_side == "buy"
                else "StateOrderSell",
            ),
            result_decoder=decode,
        )

    async def cancel_order(
        self,
        *,
        side: str | bool,
        pair: str,
        order_id: int,
        fast_cancel: bool = False,
        nonce_ms: int | None = None,
    ) -> PendingTransaction[SpotCancelOrderResult]:
        is_buy = _normalize_spot_side(side) == "buy"
        call_params = {
            "params": {
                "subaccount": normalize_address(self._client.subaccount),
                "pair": "0x" + normalize_bytes32(pair).hex(),
                "order_id": int(order_id),
                "is_buy": is_buy,
                "cancel_reason": "UserCanceled",
                "fast_cancel": bool(fast_cancel),
            }
        }

        def decode(
            fields: Mapping[str, Any],
            pending: PendingTransaction[SpotCancelOrderResult],
        ) -> SpotCancelOrderResult:
            resolved_order_id = (
                int(order_id)
                if fast_cancel
                else _parse_spot_int_field(fields, "order_id")
            )
            return SpotCancelOrderResult(
                order_id=resolved_order_id,
                tx_hash=pending.tx_hash,
                extrinsic_hash=pending.extrinsic_hash or pending.tx_hash,
            )

        expected_event = (
            ExpectedEvent("System", "ExtrinsicSuccess")
            if fast_cancel
            else ExpectedEvent("SpotMarket", "OrderCancelled")
        )
        return await self._client._submit(
            call_module="SpotMarket",
            call_function="cancel_order",
            call_params=call_params,
            nonce_ms=nonce_ms,
            cloid=None,
            expected_event=expected_event,
            result_decoder=decode,
            priority=fast_cancel,
        )

    async def place_order_and_wait(self, **kwargs: Any) -> SpotPlaceOrderResult:
        pending = await self.place_order(**kwargs)
        return await pending.executed()

    async def cancel_order_and_wait(self, **kwargs: Any) -> SpotCancelOrderResult:
        pending = await self.cancel_order(**kwargs)
        return await pending.executed()


ComponentFactory = Callable[["AsyncChainClient"], Awaitable[AsyncComponents]]


class AsyncChainClient:
    def __init__(
        self,
        *,
        substrate_ws: str = "",
        substrate_ws_endpoints: Sequence[str] | None = None,
        private_key: str,
        subaccount: str,
        net: str = "devnet",
        timeouts: TxTimeouts | None = None,
        recovery_config: RecoveryConfig | None = None,
        max_tracked_transactions: int = 1024,
        max_completed_transactions: int = 10_000,
        max_resolved_blocks: int = 256,
        node_pool_limit_per_account: int = 50,
        max_pool_transactions_per_account: int = 48,
        priority_pool_reserve: int = 2,
        max_outbound_queue: int = 64,
        print_state: bool = False,
        transaction_listener: TransactionListener | None = None,
        component_factory: ComponentFactory | None = None,
    ) -> None:
        config = network_config(normalize_net(net))
        self.net = normalize_net(net)
        self.substrate_ws_endpoints = resolve_substrate_ws_endpoints(
            substrate_ws,
            substrate_ws_endpoints,
            default=config.substrate_ws,
        )
        self.substrate_ws = self.substrate_ws_endpoints[0]
        self.private_key = private_key
        self.subaccount = subaccount
        self.timeouts = timeouts or TxTimeouts()
        self.recovery_config = recovery_config or RecoveryConfig()
        self.max_tracked_transactions = int(max_tracked_transactions)
        self.max_completed_transactions = int(max_completed_transactions)
        self.max_resolved_blocks = int(max_resolved_blocks)
        if self.max_completed_transactions < 0:
            raise ValueError("max_completed_transactions must be non-negative")
        if self.max_resolved_blocks <= 0:
            raise ValueError("max_resolved_blocks must be positive")
        self.node_pool_limit_per_account = int(node_pool_limit_per_account)
        self.max_pool_transactions_per_account = int(max_pool_transactions_per_account)
        self.priority_pool_reserve = int(priority_pool_reserve)
        if self.node_pool_limit_per_account <= 0:
            raise ValueError("node_pool_limit_per_account must be positive")
        if self.max_pool_transactions_per_account <= 0:
            raise ValueError(
                "max_pool_transactions_per_account must be positive"
            )
        if self.priority_pool_reserve < 0:
            raise ValueError("priority_pool_reserve must be non-negative")
        if (
            self.max_pool_transactions_per_account + self.priority_pool_reserve
            > self.node_pool_limit_per_account
        ):
            raise ValueError(
                "max_pool_transactions_per_account "
                f"({self.max_pool_transactions_per_account}) + "
                f"priority_pool_reserve ({self.priority_pool_reserve}) exceeds "
                "node_pool_limit_per_account "
                f"({self.node_pool_limit_per_account})"
            )
        self.priority_pool_limit = (
            self.max_pool_transactions_per_account + self.priority_pool_reserve
        )
        self.max_outbound_queue = int(max_outbound_queue)
        self._component_factory = component_factory or _production_components
        self.transactions = TransactionManager(
            listener=transaction_listener,
            print_state=print_state,
            max_tracked_transactions=self.max_tracked_transactions,
            max_completed_transactions=self.max_completed_transactions,
        )
        self.perp_market = AsyncPerpMarketClient(self)
        self.spot_market = AsyncSpotMarketClient(self)
        self._components: AsyncComponents | None = None
        self._connected = False
        self._closed = False
        self._connect_lock = asyncio.Lock()
        self._capacity = asyncio.Condition()
        self._tracked_count = 0
        self._pool_count = 0
        self._outbound_count = 0
        self._peak_tracked_count = 0
        self._peak_pool_count = 0
        self._reservations: dict[
            PendingTransaction[Any],
            _CapacityReservation,
        ] = {}
        self._nonce_pool_refs: dict[int, int] = {}

    async def __aenter__(self) -> AsyncChainClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        await self.close()

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._connected:
                return
            if self._closed:
                self._raise_not_connected("Client is closed and cannot reconnect.")
            components = await self._component_factory(self)
            self._components = components
            await components.transport.connect()
            await components.encoder.bootstrap()
            await components.recovery.start()
            await self.transactions.start()
            self._connected = True

    async def close(self) -> None:
        async with self._connect_lock:
            if self._closed:
                return
            self._closed = True
            self._connected = False
            components = self._components
            if components is None:
                await self.transactions.close()
                await self._notify_capacity()
                return
            await components.recovery.close()
            for pending in tuple(components.tracker.pending_transactions):
                if pending.status not in _TERMINAL_STATUSES:
                    pending.mark_client_closed()
            await components.transport.close()
            await self.transactions.close()
            await self._notify_capacity()

    async def wait_writable(self) -> None:
        self._require_connected()
        async with self._capacity:
            await self._capacity.wait_for(
                lambda: self._closed or self._has_normal_capacity()
            )
        self._require_connected()

    def pending_transaction(self, tx_hash: str) -> PendingTransaction[Any] | None:
        self._require_connected()
        assert self._components is not None
        return self._components.tracker.pending_transaction(tx_hash)

    @property
    def peak_tracked_transactions(self) -> int:
        return self._peak_tracked_count

    @property
    def peak_pool_transactions(self) -> int:
        return self._peak_pool_count

    @property
    def active_rpc_endpoint(self) -> str:
        components = self._components
        if components is None:
            return _safe_url(self.substrate_ws)
        return str(
            getattr(components.transport, "endpoint", _safe_url(self.substrate_ws))
        )

    def _require_connected(self) -> None:
        if self._connected and not self._closed:
            return
        self._raise_not_connected(
            "Call await client.connect() before submitting."
            if not self._closed
            else "Client is closed and does not accept new submissions."
        )

    @staticmethod
    def _raise_not_connected(action: str) -> None:
        raise ClientNotConnected(
            code="CLIENT_NOT_CONNECTED",
            stage=TxStage.CLIENT,
            elapsed_ms=0,
            certainty=OutcomeCertainty.NOT_SUBMITTED,
            retryable=False,
            suggested_action=action,
        )

    async def _submit(
        self,
        *,
        call_module: str,
        call_function: str,
        call_params: dict[str, object],
        nonce_ms: int | None,
        cloid: int | None,
        expected_event: ExpectedEvent,
        result_decoder: Callable[
            [Mapping[str, Any], PendingTransaction[Any]],
            Any,
        ],
        priority: bool = False,
    ) -> PendingTransaction[Any]:
        self._require_connected()
        assert self._components is not None
        await self._acquire_capacity(priority=priority)
        encoded: Any = None
        pending: PendingTransaction[Any] | None = None
        try:
            encoded = await self._components.encoder.encode_pallet_call(
                call_module=call_module,
                call_function=call_function,
                call_params=call_params,
                nonce=nonce_ms,
                priority=priority,
            )
            pending = await self._components.tracker.submit(
                encoded=encoded,
                cloid=cloid,
                expected_event=expected_event,
                result_decoder=result_decoder,
                timeouts=self.timeouts,
                replacement_callback=lambda: self._replace_by_hash(
                    encoded.tx_hash
                ),
                pending_callback=self.transactions.register,
            )
        except BaseException as exc:
            error_pending = (
                exc.pending
                if isinstance(exc, TransactionError)
                and isinstance(exc.pending, PendingTransaction)
                else None
            )
            if error_pending is not None:
                reservation = self._register_pending(error_pending)
                self._release_outbound(reservation)
                if exc.certainty is OutcomeCertainty.NOT_SUBMITTED:
                    self._release_pool(error_pending, reservation)
                    self._release_tracked(reservation)
                else:
                    self._release_for_status(
                        error_pending,
                        error_pending.status,
                    )
                await self._notify_capacity()
            else:
                self._release_unbound_capacity()
                if encoded is not None:
                    self._release_nonce(encoded.nonce)
                await self._notify_capacity()
            raise

        reservation = self._register_pending(pending)
        self._release_outbound(reservation)
        self._release_for_status(pending, pending.status)
        await self._notify_capacity()
        return pending

    async def _replace_by_hash(
        self,
        tx_hash: str,
    ) -> PendingTransaction[TxResult]:
        self._require_connected()
        assert self._components is not None
        original = self._components.tracker.pending_transaction(tx_hash)
        if original is None or original.status is not TxStatus.SUBMITTED:
            raise ReplacementUnsupported(
                code="REPLACEMENT_UNSUPPORTED",
                stage=TxStage.CLIENT,
                tx_hash=tx_hash,
                cloid=original.cloid if original is not None else None,
                nonce=original.nonce if original is not None else None,
                elapsed_ms=0,
                certainty=OutcomeCertainty.NOT_SUBMITTED,
                retryable=False,
                suggested_action=(
                    "Replacement is only allowed while the original timestamp-"
                    "nonce transaction is pool-tracked in SUBMITTED state."
                ),
                pending=original,
            )
        return await self._submit_replacement(original)

    async def _submit_replacement(
        self,
        original: PendingTransaction[Any],
    ) -> PendingTransaction[TxResult]:
        # Verified chain policy: normal perp calls have priority 0, fast
        # cancels 100, and Subaccount.no_op 200 for same-nonce replacement.
        self._require_connected()
        assert self._components is not None
        await self._acquire_capacity(priority=True)
        encoder = self._components.encoder
        allocator = getattr(encoder, "nonce_allocator", None)
        release = getattr(allocator, "release", None)
        if callable(release):
            release(original.nonce)

        encoded: Any = None
        try:
            encoded = await encoder.encode_pallet_call(
                call_module="Subaccount",
                call_function="no_op",
                call_params={},
                nonce=original.nonce,
                priority=True,
            )

            def decode(
                _fields: Mapping[str, Any],
                pending: PendingTransaction[TxResult],
            ) -> TxResult:
                return TxResult(tx_hash=pending.tx_hash, event=None)

            replacement = await self._components.tracker.submit(
                encoded=encoded,
                cloid=None,
                expected_event=ExpectedEvent("System", "ExtrinsicSuccess"),
                result_decoder=decode,
                timeouts=self.timeouts,
                replacement_callback=None,
                pending_callback=self.transactions.register,
            )
        except BaseException as exc:
            error_pending = (
                exc.pending
                if isinstance(exc, TransactionError)
                and isinstance(exc.pending, PendingTransaction)
                else None
            )
            if error_pending is not None:
                reservation = self._register_pending(error_pending)
                self._release_outbound(reservation)
                self._release_for_status(error_pending, error_pending.status)
            else:
                self._release_unbound_capacity()
                self._ensure_nonce_reserved(original.nonce)
            await self._notify_capacity()
            raise

        reservation = self._register_pending(replacement)
        self._release_outbound(reservation)
        self._release_for_status(replacement, replacement.status)
        await self._notify_capacity()
        return replacement

    async def _acquire_capacity(self, *, priority: bool) -> None:
        async with self._capacity:
            self._require_connected()
            has_capacity = (
                self._has_priority_capacity()
                if priority
                else self._has_normal_capacity()
            )
            if not has_capacity:
                constraint = (
                    "the configured priority pool limit of "
                    f"{self.priority_pool_limit} transactions per account"
                    if priority
                    else (
                        "a local tracked, outbound, or per-account pool "
                        f"capacity limit ({self.max_tracked_transactions}/"
                        f"{self.max_outbound_queue}/"
                        f"{self.max_pool_transactions_per_account})"
                    )
                )
                raise ClientBackpressure(
                    code="CLIENT_BACKPRESSURE",
                    stage=TxStage.CLIENT,
                    elapsed_ms=0,
                    certainty=OutcomeCertainty.NOT_SUBMITTED,
                    retryable=True,
                    suggested_action=(
                        f"Submission was blocked by {constraint}. Wait for "
                        "capacity before submitting."
                    ),
                )
            self._tracked_count += 1
            self._pool_count += 1
            self._outbound_count += 1
            self._peak_tracked_count = max(
                self._peak_tracked_count,
                self._tracked_count,
            )
            self._peak_pool_count = max(
                self._peak_pool_count,
                self._pool_count,
            )

    def _has_normal_capacity(self) -> bool:
        return (
            self._tracked_count < self.max_tracked_transactions
            and self._pool_count < self.max_pool_transactions_per_account
            and self._outbound_count < self.max_outbound_queue
        )

    def _has_priority_capacity(self) -> bool:
        return (
            self._tracked_count < self.max_tracked_transactions
            and self._pool_count < self.priority_pool_limit
            and self._outbound_count < self.max_outbound_queue
        )

    def _register_pending(
        self,
        pending: PendingTransaction[Any],
    ) -> _CapacityReservation:
        reservation = _CapacityReservation()
        self._reservations[pending] = reservation
        self._nonce_pool_refs[pending.nonce] = (
            self._nonce_pool_refs.get(pending.nonce, 0) + 1
        )
        pending.add_status_callback(
            lambda update: self._pending_status_changed(
                pending,
                update.status,
            )
        )
        return reservation

    def _pending_status_changed(
        self,
        pending: PendingTransaction[Any],
        status: TxStatus,
    ) -> None:
        self._release_for_status(pending, status)
        try:
            asyncio.get_running_loop().create_task(self._notify_capacity())
        except RuntimeError:
            pass

    def _release_for_status(
        self,
        pending: PendingTransaction[Any],
        status: TxStatus,
    ) -> None:
        reservation = self._reservations.get(pending)
        if reservation is None:
            return
        if status in _POOL_EXIT_STATUSES:
            self._release_pool(pending, reservation)
        if status in _TERMINAL_STATUSES:
            self._release_tracked(reservation)
        if not reservation.tracked and not reservation.pool:
            self._reservations.pop(pending, None)

    def _release_outbound(self, reservation: _CapacityReservation) -> None:
        if reservation.outbound:
            reservation.outbound = False
            self._outbound_count -= 1

    def _release_pool(
        self,
        pending: PendingTransaction[Any],
        reservation: _CapacityReservation,
    ) -> None:
        if reservation.pool:
            reservation.pool = False
            self._pool_count -= 1
            refs = self._nonce_pool_refs.get(pending.nonce, 0)
            if refs <= 1:
                self._nonce_pool_refs.pop(pending.nonce, None)
                self._release_nonce(pending.nonce)
            else:
                self._nonce_pool_refs[pending.nonce] = refs - 1

    def _release_tracked(self, reservation: _CapacityReservation) -> None:
        if reservation.tracked:
            reservation.tracked = False
            self._tracked_count -= 1

    def _release_unbound_capacity(self) -> None:
        self._tracked_count -= 1
        self._pool_count -= 1
        self._outbound_count -= 1

    def _release_nonce(self, nonce: int) -> None:
        components = self._components
        allocator = (
            getattr(components.encoder, "nonce_allocator", None)
            if components is not None
            else None
        )
        release = getattr(allocator, "release", None)
        if callable(release):
            release(nonce)

    def _ensure_nonce_reserved(self, nonce: int) -> None:
        components = self._components
        allocator = (
            getattr(components.encoder, "nonce_allocator", None)
            if components is not None
            else None
        )
        reserve = getattr(allocator, "reserve", None)
        if callable(reserve):
            try:
                reserve(nonce)
            except ValidationError:
                pass

    async def _notify_capacity(self) -> None:
        async with self._capacity:
            self._capacity.notify_all()


_TERMINAL_STATUSES = frozenset(
    {
        TxStatus.IN_BLOCK_FAILED,
        TxStatus.FINALIZED,
        TxStatus.INVALID,
        TxStatus.DROPPED,
        TxStatus.USURPED,
        TxStatus.RECONCILIATION_REQUIRED,
        TxStatus.CLIENT_CLOSED,
    }
)
_POOL_EXIT_STATUSES = _TERMINAL_STATUSES | {
    TxStatus.IN_BLOCK_SUCCESS,
}


async def _production_components(client: AsyncChainClient) -> AsyncComponents:
    transport = AsyncRpcTransport(client.substrate_ws_endpoints)
    encoder = ExtrinsicEncoder(
        client.substrate_ws,
        client.private_key,
        _endpoint_provider=lambda: transport.connection_url,
    )
    tracker = TransactionTracker(
        transport,
        encoder,
        max_completed_transactions=client.max_completed_transactions,
        max_resolved_blocks=client.max_resolved_blocks,
    )
    recovery = RecoveryTracker(
        transport,
        tracker,
        encoder,
        config=client.recovery_config,
    )
    return AsyncComponents(
        transport=transport,
        encoder=encoder,
        tracker=tracker,
        recovery=recovery,
    )


__all__ = ["AsyncChainClient", "AsyncComponents", "AsyncPerpMarketClient"]
