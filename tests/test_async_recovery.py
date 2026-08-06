from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from deepx_sdk._async_encoder import EncodedExtrinsic
from deepx_sdk._async_recovery import RecoveryConfig, RecoveryTracker
from deepx_sdk._async_tracker import ExpectedEvent, TransactionTracker
from deepx_sdk._async_transport import (
    AsyncRpcTransport,
    ConnectionState,
    TransportRequestError,
)
from deepx_sdk._pending_tx import PendingTransaction, TxStatus, TxTimeouts
from deepx_sdk._tx_diagnostics import (
    OutcomeCertainty,
    ReconciliationRequired,
    TransactionError,
    TxStage,
)


class _RecoveryEncoder:
    snapshot = SimpleNamespace(
        runtime_version=1,
        runtime_lock=None,
        system_events_storage_key="0xevents-key",
    )

    async def decode_system_events(self, raw_hex: str) -> list[dict[str, object]]:
        assert raw_hex == "0xevents"
        return [
            {
                "phase": {"ApplyExtrinsic": 0},
                "event": {
                    "module_id": "System",
                    "event_id": "ExtrinsicSuccess",
                    "attributes": {},
                },
            },
            {
                "phase": {"ApplyExtrinsic": 0},
                "event": {
                    "module_id": "PerpMarket",
                    "event_id": "OrderPlaced",
                    "attributes": {"order_id": 77},
                },
            },
        ]


class _RecoveryTransport:
    endpoint = "ws://archive.node.test"

    def __init__(self, *, best_number: int = 10, finalized_number: int = 9) -> None:
        self.best_number = best_number
        self.finalized_number = finalized_number
        self.connect_attempts = 1
        self.submission_count = 0
        self.block_fetch_count = 0
        self.blocks: dict[int, list[str]] = {}
        self._connection_callbacks: list[Any] = []
        self._subscription_handlers: dict[str, Any] = {}
        self._reconnect_enabled = False

    def enable_reconnect(
        self,
        *,
        initial_ms: int,
        max_ms: int,
    ) -> None:
        assert initial_ms == 100
        assert max_ms == 3_000
        self._reconnect_enabled = True

    def disable_reconnect(self) -> None:
        self._reconnect_enabled = False

    def add_connection_state_callback(self, callback: Any) -> None:
        self._connection_callbacks.append(callback)

    def remove_connection_state_callback(self, callback: Any) -> None:
        self._connection_callbacks.remove(callback)

    async def subscribe(self, method: str, params: list[object], handler: Any) -> str:
        if method == "author_submitAndWatchExtrinsic":
            self.submission_count += 1
            subscription_id = f"author-{self.submission_count}"
        else:
            subscription_id = method
        self._subscription_handlers[subscription_id] = handler
        return subscription_id

    async def unsubscribe(self, _method: str, subscription_id: str) -> None:
        self._subscription_handlers.pop(subscription_id, None)

    async def notify(self, subscription_id: str, update: object) -> None:
        while subscription_id not in self._subscription_handlers:
            await asyncio.sleep(0)
        result = self._subscription_handlers[subscription_id](update)
        if asyncio.iscoroutine(result):
            await result
        await asyncio.sleep(0)

    async def reconnect(self) -> None:
        assert self._reconnect_enabled
        self.connect_attempts += 1
        for callback in tuple(self._connection_callbacks):
            result = callback(ConnectionState.RECOVERING)
            if asyncio.iscoroutine(result):
                await result

    async def request(self, method: str, params: list[object]) -> object:
        if method == "chain_getBlockHash":
            number = self.best_number if not params else int(params[0])
            return f"0xblock-{number}"
        if method == "chain_getHeader":
            block_hash = str(params[0])
            return {"number": hex(int(block_hash.rsplit("-", 1)[1]))}
        if method == "chain_getFinalizedHead":
            return f"0xblock-{self.finalized_number}"
        if method == "chain_getBlock":
            self.block_fetch_count += 1
            number = int(str(params[0]).rsplit("-", 1)[1])
            return {"block": {"extrinsics": self.blocks.get(number, [])}}
        if method == "state_getStorage":
            return "0xevents"
        raise AssertionError(f"unexpected RPC method: {method}")


def _encoded() -> EncodedExtrinsic:
    return EncodedExtrinsic(
        data_hex="0x0102",
        tx_hash="0xtx",
        nonce=123,
        runtime_version=1,
        encode_ms=1.0,
        sign_ms=1.0,
    )


async def _submitted(
    tracker: TransactionTracker,
    transport: _RecoveryTransport,
) -> PendingTransaction[int]:
    submit = asyncio.create_task(
        tracker.submit(
            encoded=_encoded(),
            cloid=2**31,
            expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
            result_decoder=lambda fields, _pending: int(fields["order_id"]),
            timeouts=TxTimeouts(submit_ms=100),
        )
    )
    await transport.notify("author-1", {"ready": None})
    return await submit


def test_reconnect_recovers_original_pending_handle() -> None:
    async def run() -> None:
        transport = _RecoveryTransport()
        encoder = _RecoveryEncoder()
        tracker = TransactionTracker(transport, encoder)  # type: ignore[arg-type]
        pending = await _submitted(tracker, transport)
        recovery = RecoveryTracker(transport, tracker, encoder)  # type: ignore[arg-type]
        await recovery.start()

        transport.best_number = 13
        transport.finalized_number = 11
        transport.blocks[12] = ["0x0102"]
        await transport.reconnect()
        recovered = await recovery.reconcile(pending)

        assert recovered is pending
        assert await pending.wait_in_block() == 77
        assert transport.connect_attempts == 2
        assert tracker.block_fetch_count == 3
        assert transport.submission_count == 1
        await recovery.close()

    asyncio.run(run())


def test_recovery_same_block_keeps_one_tracker_fetch_task() -> None:
    async def run() -> None:
        transport = _RecoveryTransport()
        tracker = TransactionTracker(transport, _RecoveryEncoder())  # type: ignore[arg-type]
        pending = await _submitted(tracker, transport)
        transport.blocks[11] = ["0x0102"]
        tracker.prepare_recovery_block("0xblock-11")

        await asyncio.gather(
            tracker.resolve_block("0xblock-11"),
            tracker.resolve_block("0xblock-11"),
        )

        assert await pending.wait_in_block() == 77
        assert transport.block_fetch_count == 1
        assert tracker.block_fetch_count == 1

    asyncio.run(run())


class _ReconnectSocket:
    def __init__(self) -> None:
        self.inbound: asyncio.Queue[BaseException] = asyncio.Queue()
        self.close_calls = 0

    async def send(self, _message: str) -> None:
        return None

    async def recv(self) -> str:
        raise await self.inbound.get()

    async def close(self) -> None:
        self.close_calls += 1


def test_transport_reconnect_is_singleton_capped_and_callback_safe() -> None:
    async def run() -> None:
        first = _ReconnectSocket()
        replacement = _ReconnectSocket()
        attempts = 0
        delays: list[float] = []
        first_sleep_entered = asyncio.Event()
        release_first_sleep = asyncio.Event()
        connected = asyncio.Event()
        states: list[ConnectionState] = []

        async def connect_factory(_url: str, **_kwargs: object) -> _ReconnectSocket:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return first
            if attempts < 8:
                raise ConnectionError("not ready")
            return replacement

        async def sleep(delay: float) -> None:
            delays.append(delay)
            if len(delays) == 1:
                first_sleep_entered.set()
                await release_first_sleep.wait()
            await asyncio.sleep(0)

        def broken_callback(_state: ConnectionState) -> None:
            raise RuntimeError("callback failed")

        def record(state: ConnectionState) -> None:
            states.append(state)
            if state is ConnectionState.CONNECTED and attempts > 1:
                connected.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
            auto_reconnect=True,
            reconnect_jitter=lambda delay: delay,
            reconnect_sleep=sleep,
        )
        transport.add_connection_state_callback(broken_callback)
        transport.add_connection_state_callback(record)
        await transport.connect()

        disconnect = asyncio.create_task(
            transport._disconnect_connection("test disconnect")
        )
        await first_sleep_entered.wait()
        reconnect_task = transport._reconnect_task
        duplicate = asyncio.create_task(
            transport._disconnect_connection("duplicate disconnect")
        )
        await asyncio.sleep(0)
        assert transport._reconnect_task is reconnect_task
        release_first_sleep.set()
        await asyncio.gather(disconnect, duplicate)
        await connected.wait()

        assert delays == [0.1, 0.2, 0.4, 0.8, 1.6, 3.0, 3.0]
        assert states[-3:] == [
            ConnectionState.RECONNECTING,
            ConnectionState.RECOVERING,
            ConnectionState.CONNECTED,
        ]
        assert transport._reader_task is not None
        await transport.close()

    asyncio.run(run())


def test_transport_close_stops_sleeping_reconnect() -> None:
    async def run() -> None:
        socket = _ReconnectSocket()
        attempts = 0
        sleeping = asyncio.Event()
        never_release = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> _ReconnectSocket:
            nonlocal attempts
            attempts += 1
            return socket

        async def sleep(_delay: float) -> None:
            sleeping.set()
            await never_release.wait()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
            auto_reconnect=True,
            reconnect_jitter=lambda delay: delay,
            reconnect_sleep=sleep,
        )
        await transport.connect()
        disconnect = asyncio.create_task(transport._disconnect_connection("lost"))
        await sleeping.wait()
        await disconnect
        await transport.close()
        await asyncio.sleep(0)

        assert attempts == 1
        assert transport.state is ConnectionState.CLOSED
        assert transport._reconnect_task is None

    asyncio.run(run())


@pytest.mark.parametrize(
    ("may_have_been_sent", "certainty", "retryable", "action"),
    [
        (False, OutcomeCertainty.NOT_SUBMITTED, True, "safe"),
        (True, OutcomeCertainty.UNKNOWN, False, "do not resubmit"),
    ],
)
def test_submission_disconnect_reports_send_certainty_and_pending_guidance(
    may_have_been_sent: bool,
    certainty: OutcomeCertainty,
    retryable: bool,
    action: str,
) -> None:
    class FailingTransport:
        async def subscribe(
            self, _method: str, _params: list[object], _handler: Any
        ) -> str:
            raise TransportRequestError(
                "connection lost",
                may_have_been_sent=may_have_been_sent,
            )

    async def run() -> None:
        tracker = TransactionTracker(FailingTransport(), _RecoveryEncoder())  # type: ignore[arg-type]
        with pytest.raises(TransactionError) as caught:
            await tracker.submit(
                encoded=_encoded(),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda _fields, _pending: 77,
                timeouts=TxTimeouts(submit_ms=100),
            )

        error = caught.value
        assert error.certainty is certainty
        assert error.retryable is retryable
        assert error.pending is not None
        assert action in error.suggested_action.lower()
        if may_have_been_sent:
            assert tracker.pending_transaction("0xtx") is error.pending

    asyncio.run(run())


class _BoundaryTracker:
    def __init__(self, pending: PendingTransaction[int]) -> None:
        self.pending_transactions = (pending,)
        self.resolved: list[str] = []

    def prepare_recovery_block(self, _block_hash: str) -> None:
        return None

    async def resolve_block(self, block_hash: str) -> None:
        self.resolved.append(block_hash)

    def mark_reconciliation_required(
        self,
        pending: PendingTransaction[int],
        *,
        missing_start: int,
        missing_end: int,
        endpoint: str,
    ) -> None:
        error = ReconciliationRequired(
            code="RECONCILIATION_REQUIRED",
            stage=TxStage.RECOVERY,
            elapsed_ms=0,
            certainty=OutcomeCertainty.UNKNOWN,
            retryable=False,
            suggested_action=(
                f"Query archive endpoint {endpoint} for heights "
                f"{missing_start}-{missing_end}."
            ),
            pending=pending,
        )
        error.missing_start = missing_start
        error.missing_end = missing_end
        error.endpoint = endpoint
        pending.mark_reconciliation_required(error)

    async def reconcile_finalized(self, _hash: str, _number: int) -> None:
        return None


@pytest.mark.parametrize(("gap", "scanned"), [(256, True), (257, False)])
def test_recovery_window_boundary_is_exact(gap: int, scanned: bool) -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xwindow",
            nonce=1,
            cloid=None,
            # inclusion_ms=0 -> immediately overdue: gap overflow beyond
            # max_blocks only flags overdue transactions
            timeouts=TxTimeouts(inclusion_ms=0),
        )
        pending.mark_submitted(node_status="ready")
        transport = _RecoveryTransport(best_number=10, finalized_number=9)
        tracker = _BoundaryTracker(pending)
        recovery = RecoveryTracker(
            transport,
            tracker,  # type: ignore[arg-type]
            _RecoveryEncoder(),  # type: ignore[arg-type]
            config=RecoveryConfig(max_blocks=256),
        )
        await recovery.start()
        transport.best_number = 10 + gap
        await transport.reconnect()

        if scanned:
            assert len(tracker.resolved) == 256
            assert pending.status is TxStatus.SUBMITTED
        else:
            assert tracker.resolved == []
            assert pending.status is TxStatus.RECONCILIATION_REQUIRED
            assert isinstance(pending.error, ReconciliationRequired)
            assert pending.error.missing_start == 11
            assert pending.error.missing_end == 267
            assert pending.error.endpoint == "ws://archive.node.test"
            assert "11-267" in pending.error.suggested_action
        await recovery.close()

    asyncio.run(run())


class _FinalityTracker:
    def __init__(self, pending: PendingTransaction[int]) -> None:
        self.pending_transactions = (pending,)

    async def finalize_ancestor(
        self,
        pending: PendingTransaction[int],
        finalized_hash: str,
    ) -> None:
        pending.mark_finalized(block_hash=finalized_hash)


def test_finalized_ancestor_completes_after_finality_timeout() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xfinality",
            nonce=1,
            cloid=None,
        )
        pending.mark_submitted(node_status="ready")
        pending.mark_in_block_success(
            result=77,
            block_hash="0xblock-10",
            extrinsic_hash="0xfinality",
        )
        pending.node_status = "finalityTimeout"
        transport = _RecoveryTransport(best_number=12, finalized_number=9)
        tracker = _FinalityTracker(pending)
        recovery = RecoveryTracker(
            transport,
            tracker,  # type: ignore[arg-type]
            _RecoveryEncoder(),  # type: ignore[arg-type]
        )
        await recovery.start()

        transport.finalized_number = 12
        await transport.notify(
            "chain_subscribeFinalizedHeads",
            {"number": hex(12)},
        )

        assert await pending.wait_finalized() == 77
        assert pending.status is TxStatus.FINALIZED
        assert await recovery.reconcile(pending) is pending
        await recovery.close()

    asyncio.run(run())


class _RefreshingEncoder:
    def __init__(self) -> None:
        self.snapshot = SimpleNamespace(
            runtime_version=1,
            runtime_lock=asyncio.Lock(),
        )
        self.refresh_calls = 0

    async def refresh(self) -> None:
        self.refresh_calls += 1
        self.snapshot = SimpleNamespace(
            runtime_version=2,
            runtime_lock=asyncio.Lock(),
        )


def test_duplicate_runtime_version_refresh_waits_for_old_snapshot_once() -> None:
    async def run() -> None:
        transport = _RecoveryTransport()
        encoder = _RefreshingEncoder()
        tracker = _BoundaryTracker(
            PendingTransaction(tx_hash="0xunused", nonce=1, cloid=None)
        )
        recovery = RecoveryTracker(
            transport,
            tracker,  # type: ignore[arg-type]
            encoder,  # type: ignore[arg-type]
        )
        await recovery.start()
        old_snapshot = encoder.snapshot
        old_encode_started = asyncio.Event()
        release_old_encode = asyncio.Event()

        async def old_encode() -> int:
            async with old_snapshot.runtime_lock:
                old_encode_started.set()
                await release_old_encode.wait()
                return old_snapshot.runtime_version

        encode = asyncio.create_task(old_encode())
        await old_encode_started.wait()
        first = asyncio.create_task(
            transport.notify(
                "state_subscribeRuntimeVersion",
                {"specVersion": 2},
            )
        )
        duplicate = asyncio.create_task(
            transport.notify(
                "state_subscribeRuntimeVersion",
                {"specVersion": 2},
            )
        )
        await asyncio.sleep(0)
        assert encoder.refresh_calls == 0
        release_old_encode.set()
        assert await encode == 1
        await asyncio.gather(first, duplicate)

        assert encoder.refresh_calls == 1
        assert encoder.snapshot.runtime_version == 2
        await recovery.close()

    asyncio.run(run())


def test_recovery_gap_overflow_does_not_flag_fresh_pending() -> None:
    # On high-block-rate chains the fallback scan regularly falls behind
    # (gap > max_blocks) — freshly pending transactions must not be falsely
    # flagged RECONCILIATION_REQUIRED; the head just fast-forwards.
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xfresh",
            nonce=1,
            cloid=None,
            timeouts=TxTimeouts(inclusion_ms=60_000),  # fresh, not overdue
        )
        pending.mark_submitted(node_status="ready")
        transport = _RecoveryTransport(best_number=10, finalized_number=9)
        tracker = _BoundaryTracker(pending)
        recovery = RecoveryTracker(
            transport,
            tracker,  # type: ignore[arg-type]
            _RecoveryEncoder(),  # type: ignore[arg-type]
            config=RecoveryConfig(max_blocks=256),
        )
        await recovery.start()
        transport.best_number = 10 + 257
        await transport.reconnect()

        assert tracker.resolved == []  # no per-block scan happened
        assert pending.status is TxStatus.SUBMITTED  # not falsely flagged
        await recovery.close()

    asyncio.run(run())


def _watchdog_config(**overrides: object) -> RecoveryConfig:
    values: dict[str, object] = {
        "watchdog_interval_s": 0.01,
        "subscription_liveness_s": 0.03,
        "stale_ms": 0,
    }
    values.update(overrides)
    return RecoveryConfig(**values)  # type: ignore[arg-type]


async def _wait_until(predicate: Any, timeout_s: float = 2.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0.005)


def test_watchdog_scans_and_flags_stale_when_heads_silent() -> None:
    async def run() -> None:
        transport = _RecoveryTransport()
        encoder = _RecoveryEncoder()
        tracker = TransactionTracker(transport, encoder)  # type: ignore[arg-type]
        pending = await _submitted(tracker, transport)

        subscribe_calls: list[str] = []
        original_subscribe = transport.subscribe

        async def counting_subscribe(method: str, params: list[object], handler: Any) -> str:
            subscribe_calls.append(method)
            return await original_subscribe(method, params, handler)

        transport.subscribe = counting_subscribe  # type: ignore[method-assign]
        recovery = RecoveryTracker(
            transport, tracker, encoder, config=_watchdog_config()  # type: ignore[arg-type]
        )
        await recovery.start()
        assert subscribe_calls.count("chain_subscribeNewHeads") == 1

        # The chain advances but no head notifications arrive (the heads
        # subscription is dead): the watchdog must scan AND re-subscribe.
        transport.best_number = 12
        await _wait_until(lambda: pending.status is TxStatus.RECONCILIATION_REQUIRED)
        await _wait_until(
            lambda: subscribe_calls.count("chain_subscribeNewHeads") == 2
        )
        assert transport.block_fetch_count >= 2  # walked blocks 11 and 12
        await recovery.close()

    asyncio.run(run())


def test_watchdog_leaves_stalled_chain_alone() -> None:
    async def run() -> None:
        transport = _RecoveryTransport()
        encoder = _RecoveryEncoder()
        tracker = TransactionTracker(transport, encoder)  # type: ignore[arg-type]
        pending = await _submitted(tracker, transport)

        subscribe_calls: list[str] = []
        original_subscribe = transport.subscribe

        async def counting_subscribe(method: str, params: list[object], handler: Any) -> str:
            subscribe_calls.append(method)
            return await original_subscribe(method, params, handler)

        transport.subscribe = counting_subscribe  # type: ignore[method-assign]
        recovery = RecoveryTracker(
            transport, tracker, encoder, config=_watchdog_config()  # type: ignore[arg-type]
        )
        await recovery.start()
        fetches_after_start = transport.block_fetch_count

        # The chain is genuinely stalled: the best number never advances, so
        # no re-subscribe churn and no scan — nothing could have been missed.
        await asyncio.sleep(0.1)
        assert subscribe_calls.count("chain_subscribeNewHeads") == 1
        assert transport.block_fetch_count == fetches_after_start
        assert pending.status is TxStatus.SUBMITTED
        await recovery.close()

    asyncio.run(run())


def test_head_driven_scan_flags_stale_pending() -> None:
    async def run() -> None:
        transport = _RecoveryTransport()
        encoder = _RecoveryEncoder()
        tracker = TransactionTracker(transport, encoder)  # type: ignore[arg-type]
        pending = await _submitted(tracker, transport)
        recovery = RecoveryTracker(
            transport,
            tracker,
            encoder,  # type: ignore[arg-type]
            config=_watchdog_config(watchdog_interval_s=3600.0),
        )
        await recovery.start()

        # Healthy head flow: the tx is not in block 11, and with a zero stale
        # horizon it must be flagged right after the covering scan completes.
        transport.best_number = 11
        await transport.notify("chain_subscribeNewHeads", {"number": hex(11)})
        await _wait_until(lambda: pending.status is TxStatus.RECONCILIATION_REQUIRED)
        await recovery.close()

    asyncio.run(run())


def test_watchdog_idle_when_healthy_and_nothing_pending() -> None:
    async def run() -> None:
        transport = _RecoveryTransport()
        encoder = _RecoveryEncoder()
        tracker = TransactionTracker(transport, encoder)  # type: ignore[arg-type]

        request_calls: list[str] = []
        original_request = transport.request

        async def counting_request(method: str, params: list[object]) -> object:
            request_calls.append(method)
            return await original_request(method, params)

        transport.request = counting_request  # type: ignore[method-assign]
        recovery = RecoveryTracker(
            transport,
            tracker,
            encoder,  # type: ignore[arg-type]
            config=_watchdog_config(subscription_liveness_s=10.0),
        )
        await recovery.start()
        calls_after_start = len(request_calls)

        # Nothing pending and heads fresh: the watchdog costs zero RPCs.
        await asyncio.sleep(0.06)
        assert len(request_calls) == calls_after_start
        await recovery.close()

    asyncio.run(run())
