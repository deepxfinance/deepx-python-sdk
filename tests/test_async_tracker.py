from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from deepx_sdk._async_encoder import EncodedExtrinsic
from deepx_sdk._async_tracker import ExpectedEvent, TransactionTracker
from deepx_sdk._errors import ChainError, RPCError
from deepx_sdk._pending_tx import PendingTransaction, TxStatus, TxTimeouts
from deepx_sdk._tx_diagnostics import (
    InclusionTimeout,
    OutcomeCertainty,
    SubmissionTimeout,
    TransactionDropped,
    TransactionInvalid,
    TransactionError,
    TransactionUsurped,
    TxStage,
)


class FakeTransport:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.submissions: list[str] = []
        self.forgotten_subscriptions: list[str] = []
        self.requests: list[tuple[str, list[object]]] = []
        self.block_response: object = {
            "block": {
                "extrinsics": [
                    "0xaa",
                    "0xbb",
                    "0x0102",
                ]
            }
        }
        self.raw_events: object = "0xevents"

    async def subscribe(
        self,
        method: str,
        params: list[object],
        handler: Any,
    ) -> str:
        assert method == "author_submitAndWatchExtrinsic"
        assert len(params) == 1 and isinstance(params[0], str)
        self.submissions.append(params[0])
        subscription_id = f"sub-{len(self.submissions)}"
        self.handlers[subscription_id] = handler
        return subscription_id

    async def forget_subscription(self, subscription_id: str) -> None:
        self.forgotten_subscriptions.append(subscription_id)
        self.handlers.pop(subscription_id, None)

    async def notify(self, subscription_id: str, update: object) -> None:
        while subscription_id not in self.handlers:
            await asyncio.sleep(0)
        result = self.handlers[subscription_id](update)
        if asyncio.iscoroutine(result):
            await result
        await asyncio.sleep(0)

    async def request(self, method: str, params: list[object]) -> object:
        self.requests.append((method, params))
        if method == "chain_getBlock":
            return self.block_response
        if method == "state_getStorage":
            return self.raw_events
        raise AssertionError(f"unexpected RPC method: {method}")


class FakeEncoder:
    snapshot = SimpleNamespace(system_events_storage_key="0xevents-key")

    async def decode_system_events(
        self,
        raw_hex: str,
    ) -> list[dict[str, object]]:
        assert raw_hex == "0xevents"
        return [
            {
                "phase": {"ApplyExtrinsic": 2},
                "event": {
                    "module_id": "System",
                    "event_id": "ExtrinsicSuccess",
                    "attributes": {},
                },
            },
            {
                "phase": {"ApplyExtrinsic": 2},
                "event": {
                    "module_id": "PerpMarket",
                    "event_id": "OrderPlaced",
                    "attributes": {"order_id": 77},
                },
            },
        ]


class SlowFakeEncoder(FakeEncoder):
    def __init__(self) -> None:
        super().__init__()
        self.decode_started = asyncio.Event()
        self.allow_decode = asyncio.Event()

    async def decode_system_events(
        self,
        raw_hex: str,
    ) -> list[dict[str, object]]:
        self.decode_started.set()
        await self.allow_decode.wait()
        return await super().decode_system_events(raw_hex)


class RejectingTransport(FakeTransport):
    async def subscribe(
        self,
        method: str,
        params: list[object],
        handler: Any,
    ) -> str:
        self.submissions.append(str(params[0]))
        raise RPCError(
            "RPC method 'author_submitAndWatchExtrinsic' failed with code "
            "-32010: InvalidTransaction::Payment: CallType::Timestamp(1)"
        )


class FailedEventEncoder(FakeEncoder):
    async def decode_system_events(
        self,
        raw_hex: str,
    ) -> list[dict[str, object]]:
        return [
            {
                "phase": {"ApplyExtrinsic": 2},
                "event": {
                    "module_id": "System",
                    "event_id": "ExtrinsicFailed",
                    "attributes": {
                        "dispatch_error": {
                            "Module": {"index": 22, "error": "0x11000000"}
                        }
                    },
                },
            }
        ]


class MissingEventEncoder(FakeEncoder):
    async def decode_system_events(
        self,
        raw_hex: str,
    ) -> list[dict[str, object]]:
        return [
            {
                "phase": {"ApplyExtrinsic": 2},
                "event": {
                    "module_id": "System",
                    "event_id": "ExtrinsicSuccess",
                    "attributes": {},
                },
            }
        ]


class TwoTransactionEncoder(FakeEncoder):
    async def decode_system_events(
        self,
        raw_hex: str,
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        for index, order_id in ((1, 77), (2, 88)):
            events.extend(
                [
                    {
                        "phase": {"ApplyExtrinsic": index},
                        "event": {
                            "module_id": "System",
                            "event_id": "ExtrinsicSuccess",
                            "attributes": {},
                        },
                    },
                    {
                        "phase": {"ApplyExtrinsic": index},
                        "event": {
                            "module_id": "PerpMarket",
                            "event_id": "OrderPlaced",
                            "attributes": {"order_id": order_id},
                        },
                    },
                ]
            )
        return events


def _invalid_error() -> TransactionInvalid:
    return TransactionInvalid(
        code="TRANSACTION_INVALID",
        stage=TxStage.SUBMISSION,
        elapsed_ms=1,
        certainty=OutcomeCertainty.REJECTED,
        retryable=False,
        suggested_action="Correct the transaction.",
    )


def test_tracker_bounds_terminal_history_and_keeps_active_transactions() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(
            transport,
            FakeEncoder(),
            max_completed_transactions=2,
        )
        terminal: list[PendingTransaction[int]] = []

        for index in range(3):
            task = asyncio.create_task(
                tracker.submit(
                    encoded=EncodedExtrinsic(
                        data_hex=f"0x{index + 1:04x}",
                        tx_hash=f"0xterminal-{index}",
                        nonce=100 + index,
                        runtime_version=182,
                        encode_ms=1.0,
                        sign_ms=1.0,
                    ),
                    cloid=200 + index,
                    expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                    result_decoder=lambda fields, identity: int(
                        fields["order_id"]
                    ),
                    timeouts=TxTimeouts(),
                )
            )
            await transport.notify(f"sub-{index + 1}", {"ready": None})
            pending = await task
            pending.mark_invalid(_invalid_error())
            terminal.append(pending)
        await asyncio.sleep(0)

        active_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x9999",
                    tx_hash="0xactive",
                    nonce=999,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=999,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(),
            )
        )
        await transport.notify("sub-4", {"ready": None})
        active = await active_task

        assert tracker.pending_transaction("0xterminal-0") is None
        assert tracker.pending_transaction("0xterminal-1") is terminal[1]
        assert tracker.pending_transaction("0xterminal-2") is terminal[2]
        assert tracker.pending_transactions == (active,)
        assert terminal[0].status is TxStatus.INVALID
        assert transport.forgotten_subscriptions == ["sub-1", "sub-2", "sub-3"]

    asyncio.run(run())


def test_tracker_bounds_resolved_block_cache() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(
            transport,
            FakeEncoder(),
            max_resolved_blocks=2,
        )

        await tracker.resolve_block("0xblock-1")
        await tracker.resolve_block("0xblock-2")
        await tracker.resolve_block("0xblock-3")

        assert tracker.resolved_block_cache_size == 2

        await tracker.resolve_block("0xblock-1")

        assert tracker.resolved_block_cache_size == 2
        assert tracker.block_fetch_count == 4
        assert sum(
            method == "chain_getBlock" for method, _params in transport.requests
        ) == 4

    asyncio.run(run())


def test_submit_returns_on_ready_and_completes_in_block() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FakeEncoder())
        pending_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(),
            )
        )

        await transport.notify("sub-1", {"ready": None})
        pending = await pending_task
        assert pending.status is TxStatus.SUBMITTED

        await transport.notify("sub-1", {"inBlock": "0xblock"})
        assert await pending.wait_in_block() == 77

    asyncio.run(run())


def test_tracker_registers_pending_before_submitting() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FakeEncoder())
        registrations: list[tuple[PendingTransaction[int], TxStatus]] = []
        updates: list[TxStatus] = []

        def register(pending: PendingTransaction[int]) -> None:
            registrations.append((pending, pending.status))
            pending.add_status_callback(lambda update: updates.append(update.status))

        pending_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xregistered",
                    nonce=124,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31 + 1,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(),
                pending_callback=register,
            )
        )
        await transport.notify("sub-1", {"ready": None})
        pending = await pending_task

        assert registrations == [(pending, TxStatus.CREATED)]
        assert updates[:2] == [TxStatus.SUBMITTING, TxStatus.SUBMITTED]

    asyncio.run(run())


def test_submit_forwards_explicit_replacement_callback() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FakeEncoder())
        replacement = PendingTransaction[object](
            tx_hash="0xreplacement",
            nonce=123,
            cloid=None,
        )

        async def replace() -> PendingTransaction[object]:
            return replacement

        pending_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=None,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(),
                replacement_callback=replace,
            )
        )
        await transport.notify("sub-1", {"ready": None})
        pending = await pending_task

        assert await pending.replace() is replacement

    asyncio.run(run())


@pytest.mark.parametrize("node_status", ["FuTuRe", "broadcast"])
def test_submit_returns_on_all_pool_admission_statuses(node_status: str) -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FakeEncoder())
        pending_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(submit_ms=10),
            )
        )

        await transport.notify("sub-1", {node_status: None})
        pending = await pending_task

        assert pending.status is TxStatus.SUBMITTED
        assert pending.node_status == node_status

    asyncio.run(run())


def test_in_block_first_returns_submitted_handle_before_event_decode() -> None:
    async def run() -> None:
        transport = FakeTransport()
        encoder = SlowFakeEncoder()
        tracker = TransactionTracker(transport, encoder)
        pending_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(submit_ms=50),
            )
        )

        await transport.notify("sub-1", {"InBlOcK": "0xblock"})
        await encoder.decode_started.wait()
        pending = await asyncio.wait_for(pending_task, timeout=0.02)

        assert pending.status is TxStatus.SUBMITTED
        assert not encoder.allow_decode.is_set()

        encoder.allow_decode.set()
        assert await pending.wait_in_block() == 77

    asyncio.run(run())


@pytest.mark.parametrize(
    ("reason", "expected_text"),
    [
        ("ExceedPoolLimit", "50-transaction pool cap"),
        ("Payment: CallType::Timestamp(1)", "10-second quota-free interval"),
        ("TimeStale", "duplicate or older-than-retained timestamp nonce"),
        ("Future", "outside the allowed future range"),
        ("BadSigner", "inactive or frozen"),
    ],
)
def test_invalid_status_preserves_chain_specific_admission_diagnostics(
    reason: str,
    expected_text: str,
) -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FakeEncoder())
        submit_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(submit_ms=20),
            )
        )

        await transport.notify("sub-1", {"InVaLiD": reason})
        with pytest.raises(TransactionInvalid) as caught:
            await submit_task

        error = caught.value
        assert error.pending is not None
        assert error.tx_hash == "0xtx"
        assert error.nonce == 123
        assert error.cloid == 2**31
        assert error.node_status == "InVaLiD"
        assert error.invalid_reason == reason
        assert expected_text in str(error)

    asyncio.run(run())


def test_rpc_admission_error_uses_chain_specific_invalid_mapping() -> None:
    async def run() -> None:
        transport = RejectingTransport()
        tracker = TransactionTracker(transport, FakeEncoder())

        with pytest.raises(TransactionInvalid) as caught:
            await tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(submit_ms=50),
            )

        error = caught.value
        assert "10-second quota-free interval" in str(error)
        assert "Payment: CallType::Timestamp(1)" in error.invalid_reason
        assert error.pending is not None
        assert transport.submissions == ["0x0102"]

    asyncio.run(run())


def test_dropped_status_is_typed_and_keeps_unknown_certainty() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FakeEncoder())
        submit_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(),
            )
        )
        await transport.notify("sub-1", {"ready": None})
        pending = await submit_task

        await transport.notify("sub-1", {"DrOpPeD": None})
        with pytest.raises(TransactionDropped) as caught:
            await pending.wait_in_block()

        error = caught.value
        assert pending.status is TxStatus.DROPPED
        assert error.certainty is OutcomeCertainty.UNKNOWN
        assert error.pending is pending
        assert error.tx_hash == "0xtx"
        assert error.node_status == "DrOpPeD"

    asyncio.run(run())


def test_usurped_status_retains_replacement_hash() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FakeEncoder())
        submit_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(),
            )
        )
        await transport.notify("sub-1", {"ready": None})
        pending = await submit_task

        await transport.notify("sub-1", {"UsUrPeD": "0xreplacement"})
        with pytest.raises(TransactionUsurped) as caught:
            await pending.wait_in_block()

        error = caught.value
        assert pending.status is TxStatus.USURPED
        assert error.certainty is OutcomeCertainty.REPLACED
        assert error.pending is pending
        assert error.tx_hash == "0xtx"
        assert error.node_status == "UsUrPeD"
        assert error.replacement_hash == "0xreplacement"
        assert error.to_dict()["cause"] == {"replacement_hash": "0xreplacement"}

    asyncio.run(run())


def test_retracted_transaction_continues_to_a_new_in_block_result() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FakeEncoder())
        submit_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(),
            )
        )
        await transport.notify("sub-1", {"ready": None})
        pending = await submit_task
        await transport.notify("sub-1", {"inBlock": "0xold"})
        assert await pending.wait_in_block() == 77

        await transport.notify("sub-1", {"ReTrAcTeD": "0xold"})
        assert pending.status is TxStatus.RETRACTED
        assert pending.node_status == "ReTrAcTeD"

        await transport.notify("sub-1", {"inBlock": "0xnew"})
        assert await pending.wait_in_block() == 77

    asyncio.run(run())


def test_extrinsic_failed_raises_existing_chain_error_with_pending_context() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FailedEventEncoder())
        submit_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(inclusion_ms=20),
            )
        )
        await transport.notify("sub-1", {"ready": None})
        pending = await submit_task

        await transport.notify("sub-1", {"inBlock": "0xblock"})
        with pytest.raises(ChainError) as caught:
            await pending.wait_in_block()

        error = caught.value
        assert error.code == "22_17"
        assert error.pending is pending
        assert error.tx_hash == "0xtx"
        assert error.nonce == 123
        assert error.cloid == 2**31
        assert error.block_hash == "0xblock"
        assert error.stage is TxStage.INCLUSION
        assert error.certainty is OutcomeCertainty.EXECUTED_FAILED
        assert pending.status is TxStatus.IN_BLOCK_FAILED
        assert pending.diagnostics()["error"]["code"] == "22_17"

    asyncio.run(run())


def test_missing_expected_event_is_a_structured_decode_error() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, MissingEventEncoder())
        submit_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(inclusion_ms=20),
            )
        )
        await transport.notify("sub-1", {"ready": None})
        pending = await submit_task

        await transport.notify("sub-1", {"inBlock": "0xblock"})
        with pytest.raises(TransactionError) as caught:
            await pending.wait_in_block()

        error = caught.value
        assert error.code == "EXPECTED_EVENT_MISSING"
        assert error.stage is TxStage.INCLUSION
        assert error.pending is pending
        assert error.tx_hash == "0xtx"
        assert error.cause.args[0] == {
            "block_hash": "0xblock",
            "extrinsic_index": 2,
            "expected_event": "PerpMarket.OrderPlaced",
        }

    asyncio.run(run())


def test_two_pending_in_one_block_share_fetch_and_isolate_events_by_phase() -> None:
    async def run() -> None:
        transport = FakeTransport()
        transport.block_response = {
            "block": {"extrinsics": ["0xaa", "0x0102", "0x0304"]}
        }
        tracker = TransactionTracker(transport, TwoTransactionEncoder())
        first_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx-1",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(),
            )
        )
        second_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0304",
                    tx_hash="0xtx-2",
                    nonce=124,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31 + 1,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(),
            )
        )
        await asyncio.gather(
            transport.notify("sub-1", {"ready": None}),
            transport.notify("sub-2", {"ready": None}),
        )
        first, second = await asyncio.gather(first_task, second_task)

        await asyncio.gather(
            transport.notify("sub-1", {"inBlock": "0xshared"}),
            transport.notify("sub-2", {"inBlock": "0xshared"}),
        )

        assert await first.wait_in_block() == 77
        assert await second.wait_in_block() == 88
        assert transport.requests.count(("chain_getBlock", ["0xshared"])) == 1
        assert (
            transport.requests.count(
                ("state_getStorage", ["0xevents-key", "0xshared"])
            )
            == 1
        )
        assert transport.submissions == ["0x0102", "0x0304"]

    asyncio.run(run())


def test_submission_timeout_keeps_pending_and_background_tracking_continues() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FakeEncoder())

        with pytest.raises(SubmissionTimeout) as caught:
            await tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(submit_ms=5),
            )

        pending = caught.value.pending
        assert pending is not None
        assert pending.status is TxStatus.SUBMITTING
        assert transport.submissions == ["0x0102"]

        await transport.notify("sub-1", {"ready": None})
        assert await pending.wait_submitted() is pending
        await transport.notify("sub-1", {"inBlock": "0xblock"})
        assert await pending.wait_in_block() == 77
        assert transport.submissions == ["0x0102"]

    asyncio.run(run())


def test_inclusion_timeout_leaves_submitted_and_later_inclusion_completes() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FakeEncoder())
        submit_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(inclusion_ms=5),
            )
        )
        await transport.notify("sub-1", {"ready": None})
        pending = await submit_task

        with pytest.raises(InclusionTimeout) as caught:
            await pending.wait_in_block()
        assert caught.value.pending is pending
        assert pending.status is TxStatus.SUBMITTED

        await transport.notify("sub-1", {"inBlock": "0xblock"})
        assert await pending.wait_in_block() == 77

    asyncio.run(run())


def test_finalized_first_returns_before_decode_then_produces_typed_result() -> None:
    async def run() -> None:
        transport = FakeTransport()
        encoder = SlowFakeEncoder()
        tracker = TransactionTracker(transport, encoder)
        submit_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(submit_ms=50),
            )
        )

        await transport.notify("sub-1", {"FiNaLiZeD": "0xblock"})
        await encoder.decode_started.wait()
        pending = await asyncio.wait_for(submit_task, timeout=0.02)
        assert pending.status is TxStatus.SUBMITTED
        assert pending.node_status == "FiNaLiZeD"

        encoder.allow_decode.set()
        assert await pending.wait_finalized() == 77
        assert pending.status is TxStatus.FINALIZED

    asyncio.run(run())


def test_finality_timeout_keeps_result_and_later_finalized_completes() -> None:
    async def run() -> None:
        transport = FakeTransport()
        tracker = TransactionTracker(transport, FakeEncoder())
        submit_task = asyncio.create_task(
            tracker.submit(
                encoded=EncodedExtrinsic(
                    data_hex="0x0102",
                    tx_hash="0xtx",
                    nonce=123,
                    runtime_version=182,
                    encode_ms=1.0,
                    sign_ms=1.0,
                ),
                cloid=2**31,
                expected_event=ExpectedEvent("PerpMarket", "OrderPlaced"),
                result_decoder=lambda fields, identity: int(fields["order_id"]),
                timeouts=TxTimeouts(finalization_ms=20),
            )
        )
        await transport.notify("sub-1", {"ready": None})
        pending = await submit_task
        await transport.notify("sub-1", {"inBlock": "0xblock"})
        assert await pending.wait_in_block() == 77

        await transport.notify("sub-1", {"FiNaLiTyTiMeOuT": "0xblock"})
        assert pending.status is TxStatus.IN_BLOCK_SUCCESS
        assert pending.node_status == "FiNaLiTyTiMeOuT"
        assert pending.error is None

        await transport.notify("sub-1", {"FiNaLiZeD": "0xblock"})
        assert await pending.wait_finalized() == 77
        assert pending.status is TxStatus.FINALIZED

    asyncio.run(run())
