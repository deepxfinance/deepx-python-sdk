from __future__ import annotations

import asyncio
import gc
import logging
import warnings
import weakref

import pytest

import deepx_sdk._pending_tx as pending_module
from deepx_sdk._pending_tx import PendingTransaction, TxStatus
from deepx_sdk._tx_diagnostics import (
    ClientNotConnected,
    FinalizationTimeout,
    InclusionTimeout,
    OutcomeCertainty,
    ReplacementUnsupported,
    TransactionDropped,
    TransactionInvalid,
    TransactionNotIncluded,
    TransactionUsurped,
    ReconciliationRequired,
    SubmissionTimeout,
    TxStage,
)


def test_wait_timeout_does_not_stop_background_tracking() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0x01",
            nonce=101,
            cloid=None,
        )
        pending.mark_submitted(node_status="ready")

        with pytest.raises(InclusionTimeout) as caught:
            await pending.wait_in_block(timeout=0.001)

        assert caught.value.pending is pending
        assert pending.status is TxStatus.SUBMITTED

        pending.mark_in_block_success(
            result=77,
            block_hash="0xblock",
            extrinsic_hash="0x01",
        )
        assert await pending.wait_in_block() == 77

    asyncio.run(run())


def test_legal_lifecycle_resolves_waiters_and_preserves_timings() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0x02", nonce=102, cloid=7
        )
        pending.mark_submitting()
        pending.mark_submitted(node_status="broadcast")
        assert await pending.wait_submitted() is pending

        pending.mark_in_block_success(
            result=12, block_hash="0xblock", extrinsic_hash="0x02"
        )
        pending.mark_finalized()

        assert pending.status is TxStatus.FINALIZED
        assert await pending.wait_in_block() == 12
        assert await pending.wait_finalized() == 12
        assert pending.timings.submitted_at is not None
        assert pending.timings.in_block_at is not None
        assert pending.timings.finalized_at is not None

    asyncio.run(run())


def test_submission_waiter_resolves_when_the_node_accepts_the_transaction() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xsubmitted-waiter", nonce=126, cloid=None
        )
        waiter = asyncio.create_task(pending.wait_submitted())
        await asyncio.sleep(0)
        pending.mark_submitted(node_status="ready")

        assert await waiter is pending

    asyncio.run(run())


def test_retraction_returns_handle_to_submitted_tracking() -> None:
    pending: PendingTransaction[int] = PendingTransaction(
        tx_hash="0x03", nonce=103, cloid=None
    )
    pending.mark_submitted(node_status="ready")
    pending.mark_in_block_success(result=5, block_hash="0xblock", extrinsic_hash="0x03")
    pending.mark_retracted()

    assert pending.status is TxStatus.RETRACTED
    pending.mark_submitted(node_status="broadcast")
    assert pending.status is TxStatus.SUBMITTED


def test_retraction_replaces_completed_waiters_for_the_new_best_chain_result() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xretraction", nonce=121, cloid=None
        )
        pending.mark_submitted(node_status="ready")
        waiter = asyncio.create_task(pending.wait_in_block())
        await asyncio.sleep(0)
        pending.mark_in_block_success(
            result=1, block_hash="0xold", extrinsic_hash="0xretraction"
        )
        assert await waiter == 1
        pending.mark_retracted()
        pending.mark_submitted(node_status="broadcast")
        pending.mark_in_block_success(
            result=2, block_hash="0xnew", extrinsic_hash="0xretraction"
        )

        assert await pending.wait_in_block() == 2

    asyncio.run(run())


def test_in_block_failure_propagates_the_typed_execution_error() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xin-block-failed", nonce=127, cloid=None
        )
        pending.mark_submitted(node_status="ready")
        error = TransactionDropped(
            code="IN_BLOCK_FAILED",
            stage=TxStage.INCLUSION,
            elapsed_ms=4,
            certainty=OutcomeCertainty.EXECUTED_FAILED,
            retryable=False,
            suggested_action="Inspect the in-block error before taking another action.",
        )
        pending.mark_in_block_failed(error)

        with pytest.raises(TransactionDropped) as caught:
            await pending.wait_in_block()
        assert caught.value is error

    asyncio.run(run())


def test_mark_finalized_requires_an_in_block_result() -> None:
    pending: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xno-result", nonce=122, cloid=None
    )
    pending.mark_submitted(node_status="ready")

    with pytest.raises(RuntimeError, match="without an in-block result"):
        pending.mark_finalized()


def test_repeated_submitted_status_keeps_the_latest_node_status() -> None:
    pending: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xrepeat", nonce=123, cloid=None
    )
    pending.mark_submitted(node_status="ready")
    pending.mark_submitted(node_status="broadcast")

    assert pending.node_status == "broadcast"


def test_illegal_finalized_to_submitted_transition_raises() -> None:
    pending: PendingTransaction[int] = PendingTransaction(
        tx_hash="0x04", nonce=104, cloid=None
    )
    pending.mark_submitted(node_status="ready")
    pending.mark_in_block_success(result=5, block_hash="0xblock", extrinsic_hash="0x04")
    pending.mark_finalized()

    with pytest.raises(RuntimeError, match="finalized.*submitted"):
        pending.mark_submitted(node_status="ready")


def test_failing_callback_is_logged_and_later_callbacks_run(caplog: pytest.LogCaptureFixture) -> None:
    pending: PendingTransaction[int] = PendingTransaction(
        tx_hash="0x05", nonce=105, cloid=None
    )
    received: list[TxStatus] = []

    def broken_callback(_update: object) -> None:
        raise ValueError("callback broke")

    def later_callback(update: object) -> None:
        received.append(update.status)  # type: ignore[attr-defined]

    pending.add_status_callback(broken_callback)
    pending.add_status_callback(later_callback)

    with caplog.at_level(logging.ERROR, logger="deepx_sdk._pending_tx"):
        pending.mark_submitted(node_status="ready")

    assert received == [TxStatus.SUBMITTED]
    assert "Transaction status callback failed" in caplog.text


def test_updates_yield_every_transition_in_order() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0x06", nonce=106, cloid=None
        )
        pending.mark_submitting()
        pending.mark_submitted(node_status="ready")
        pending.mark_in_block_success(result=8, block_hash="0xblock", extrinsic_hash="0x06")

        updates = [await anext(pending.updates()) for _ in range(3)]
        assert [update.status for update in updates] == [
            TxStatus.SUBMITTING,
            TxStatus.SUBMITTED,
            TxStatus.IN_BLOCK_SUCCESS,
        ]
        assert [update.previous_status for update in updates] == [
            TxStatus.CREATED,
            TxStatus.SUBMITTING,
            TxStatus.SUBMITTED,
        ]

    asyncio.run(run())


def test_diagnostics_never_exposes_a_private_key() -> None:
    pending: PendingTransaction[int] = PendingTransaction(
        tx_hash="0x07", nonce=107, cloid=None
    )
    pending.mark_submitted(node_status="ready")

    assert "private_key" not in pending.diagnostics()


def test_terminal_error_rejects_waiters_with_the_typed_error() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0x08", nonce=108, cloid=None
        )
        pending.mark_submitted(node_status="ready")
        error = TransactionDropped(
            code="TRANSACTION_DROPPED",
            stage=TxStage.SUBMISSION,
            elapsed_ms=3,
            certainty=OutcomeCertainty.UNKNOWN,
            retryable=False,
            suggested_action="Reconcile by tx hash before taking another action.",
            pending=pending,
        )
        pending.mark_dropped(error)

        with pytest.raises(TransactionDropped) as caught:
            await pending.wait_in_block()
        assert caught.value is error

    asyncio.run(run())


def test_cancelling_a_waiter_does_not_cancel_the_shared_tracking_future() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0x0c", nonce=112, cloid=None
        )
        pending.mark_submitted(node_status="ready")
        cancelled_waiter = asyncio.create_task(pending.wait_in_block())
        await asyncio.sleep(0)
        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter

        surviving_waiter = asyncio.create_task(pending.wait_in_block())
        await asyncio.sleep(0)
        assert not surviving_waiter.done()
        pending.mark_in_block_success(
            result=13, block_hash="0xblock", extrinsic_hash="0x0c"
        )
        assert await surviving_waiter == 13

    asyncio.run(run())


def test_mark_client_closed_generates_a_safe_terminal_diagnostic() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0x0d", nonce=113, cloid=None
        )
        pending.mark_submitted(node_status="ready")
        pending.mark_client_closed()

        assert pending.status is TxStatus.CLIENT_CLOSED
        with pytest.raises(Exception, match="Suggested action"):
            await pending.wait_in_block()

    asyncio.run(run())


def test_replace_requires_callback_then_delegates_explicitly() -> None:
    async def run() -> None:
        unsupported: PendingTransaction[int] = PendingTransaction(
            tx_hash="0x09", nonce=109, cloid=None
        )
        with pytest.raises(ReplacementUnsupported):
            await unsupported.replace()

        replacement: PendingTransaction[int] = PendingTransaction(
            tx_hash="0x0a", nonce=110, cloid=None
        )

        async def replace() -> PendingTransaction[int]:
            return replacement

        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0x0b", nonce=111, cloid=None, replacement_callback=replace
        )
        pending.mark_submitted(node_status="ready")
        assert await pending.replace() is replacement

    asyncio.run(run())


def test_illegal_terminal_transition_is_atomic_for_handle_error_and_waiters() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xatomic", nonce=114, cloid=14
        )
        pending.mark_submitted(node_status="ready")
        pending.mark_in_block_success(
            result=14, block_hash="0xblock", extrinsic_hash="0xatomic"
        )
        pending.mark_finalized()
        original_error = pending.error
        rejected_error = TransactionDropped(
            code="TRANSACTION_DROPPED",
            stage=TxStage.SUBMISSION,
            elapsed_ms=4,
            certainty=OutcomeCertainty.UNKNOWN,
            retryable=False,
            suggested_action="Reconcile by tx hash before taking another action.",
        )

        with pytest.raises(RuntimeError, match="finalized.*dropped"):
            pending.mark_dropped(rejected_error)

        assert pending.status is TxStatus.FINALIZED
        assert pending.error is original_error
        assert rejected_error.pending is None
        assert await pending.wait_in_block() == 14
        assert await pending.wait_finalized() == 14

    asyncio.run(run())


@pytest.mark.parametrize(
    ("error_type", "marker"),
    [
        (TransactionDropped, "mark_dropped"),
        (TransactionUsurped, "mark_usurped"),
        (ReconciliationRequired, "mark_reconciliation_required"),
    ],
)
def test_post_send_terminal_errors_inherit_pending_reconciliation_context(
    error_type: type[TransactionDropped], marker: str
) -> None:
    pending: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xcontext", nonce=115, cloid=15
    )
    pending.mark_submitted(node_status="broadcast")
    error = error_type(
        code="POST_SEND_ERROR",
        stage=TxStage.RECOVERY,
        elapsed_ms=4,
        certainty=OutcomeCertainty.UNKNOWN,
        retryable=False,
        suggested_action="",
    )

    getattr(pending, marker)(error)

    assert error.pending is pending
    assert error.tx_hash == "0xcontext"
    assert error.nonce == 115
    assert error.cloid == 15
    assert error.node_status == "broadcast"
    assert error.suggested_action
    assert error.to_dict()["safe_to_retry"] is False


def test_updates_is_a_single_consumer_stream_that_ends_after_final_update() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xupdates", nonce=116, cloid=None
        )
        pending.mark_submitted(node_status="ready")
        pending.mark_in_block_success(
            result=16, block_hash="0xblock", extrinsic_hash="0xupdates"
        )
        pending.mark_finalized()

        async with asyncio.timeout(0.1):
            updates = [update async for update in pending.updates()]
        assert [update.status for update in updates] == [
            TxStatus.SUBMITTED,
            TxStatus.IN_BLOCK_SUCCESS,
            TxStatus.FINALIZED,
        ]

    asyncio.run(run())


def test_updates_ends_after_the_terminal_error_update() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xupdates-error", nonce=117, cloid=None
        )
        pending.mark_submitted(node_status="ready")
        pending.mark_dropped(
            TransactionDropped(
                code="TRANSACTION_DROPPED",
                stage=TxStage.SUBMISSION,
                elapsed_ms=4,
                certainty=OutcomeCertainty.UNKNOWN,
                retryable=False,
                suggested_action="Reconcile by transaction hash before retrying.",
            )
        )

        async with asyncio.timeout(0.1):
            updates = [update async for update in pending.updates()]
        assert [update.status for update in updates] == [TxStatus.SUBMITTED, TxStatus.DROPPED]

    asyncio.run(run())


@pytest.mark.parametrize("wait_stage", ["submitted", "in_block", "finalized"])
def test_error_after_each_wait_timeout_does_not_report_unretrieved_future_exception(
    wait_stage: str,
) -> None:
    async def run() -> None:
        loop = asyncio.get_running_loop()
        reported: list[dict[str, object]] = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: reported.append(context))
        try:
            pending: PendingTransaction[int] = PendingTransaction(
                tx_hash="0xunretrieved", nonce=120, cloid=None
            )
            if wait_stage == "submitted":
                with pytest.raises(SubmissionTimeout):
                    await pending.wait_submitted(timeout=0.001)
                pending.mark_invalid(
                    TransactionInvalid(
                        code="TRANSACTION_INVALID",
                        stage=TxStage.SUBMISSION,
                        elapsed_ms=4,
                        certainty=OutcomeCertainty.REJECTED,
                        retryable=False,
                        suggested_action="Correct the invalid transaction before rebuilding.",
                    )
                )
            else:
                pending.mark_submitted(node_status="ready")
                if wait_stage == "in_block":
                    with pytest.raises(InclusionTimeout):
                        await pending.wait_in_block(timeout=0.001)
                else:
                    with pytest.raises(FinalizationTimeout):
                        await pending.wait_finalized(timeout=0.001)
                pending.mark_dropped(
                    TransactionDropped(
                        code="TRANSACTION_DROPPED",
                        stage=TxStage.SUBMISSION,
                        elapsed_ms=4,
                        certainty=OutcomeCertainty.UNKNOWN,
                        retryable=False,
                        suggested_action="Reconcile by transaction hash before retrying.",
                    )
                )
            del pending
            gc.collect()
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous_handler)
        assert reported == []

    asyncio.run(run())


def test_awaitable_callback_is_closed_and_never_scheduled_in_a_running_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xcallback-async", nonce=118, cloid=None
        )
        started = False

        async def callback(_update: object) -> None:
            nonlocal started
            started = True

        pending.add_status_callback(callback)
        with caplog.at_level(logging.WARNING, logger="deepx_sdk._pending_tx"):
            pending.mark_submitted(node_status="ready")
            await asyncio.sleep(0)
        assert not started
        assert "Transaction status callbacks must be synchronous" in caplog.text

    asyncio.run(run())


def test_waiter_receives_typed_terminal_error_before_its_timeout() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xobserver", nonce=125, cloid=None
        )
        pending.mark_submitted(node_status="ready")
        waiter = asyncio.create_task(pending.wait_in_block(timeout=1))
        await asyncio.sleep(0)
        error = TransactionDropped(
            code="TRANSACTION_DROPPED",
            stage=TxStage.SUBMISSION,
            elapsed_ms=4,
            certainty=OutcomeCertainty.UNKNOWN,
            retryable=False,
            suggested_action="Reconcile by transaction hash before retrying.",
        )
        pending.mark_dropped(error)

        with pytest.raises(TransactionDropped) as caught:
            await waiter
        assert caught.value is error

    asyncio.run(run())


def test_submitted_waiter_keeps_success_when_dropped_before_it_resumes() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xsubmitted-race", nonce=133, cloid=None
        )
        waiter = asyncio.create_task(pending.wait_submitted())
        await asyncio.sleep(0)
        pending.mark_submitted(node_status="ready")
        pending.mark_dropped(
            TransactionDropped(
                code="TRANSACTION_DROPPED",
                stage=TxStage.SUBMISSION,
                elapsed_ms=4,
                certainty=OutcomeCertainty.UNKNOWN,
                retryable=False,
                suggested_action="Reconcile by transaction hash before retrying.",
            )
        )

        assert await waiter is pending

    asyncio.run(run())


@pytest.mark.parametrize(
    ("marker", "error_type"),
    [
        ("mark_client_closed", ClientNotConnected),
        ("mark_reconciliation_required", ReconciliationRequired),
    ],
)
def test_in_block_waiter_keeps_result_when_later_error_arrives_before_it_resumes(
    marker: str,
    error_type: type[ClientNotConnected] | type[ReconciliationRequired],
) -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xin-block-race", nonce=134, cloid=None
        )
        pending.mark_submitted(node_status="ready")
        waiter = asyncio.create_task(pending.wait_in_block())
        await asyncio.sleep(0)
        pending.mark_in_block_success(
            result=34,
            block_hash="0xblock",
            extrinsic_hash="0xin-block-race",
        )
        getattr(pending, marker)(
            error_type(
                code="TRACKING_INTERRUPTED",
                stage=TxStage.CLIENT,
                elapsed_ms=4,
                certainty=OutcomeCertainty.UNKNOWN,
                retryable=False,
                suggested_action="Reconcile by transaction hash before retrying.",
            )
        )

        assert await waiter == 34

    asyncio.run(run())


@pytest.mark.parametrize("wait_stage", ["submitted", "in_block", "finalized"])
def test_unfinished_milestone_wakes_all_waiters_with_same_typed_error(
    wait_stage: str,
) -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash=f"0xconcurrent-{wait_stage}", nonce=135, cloid=None
        )
        if wait_stage != "submitted":
            pending.mark_submitted(node_status="ready")
        if wait_stage == "finalized":
            pending.mark_in_block_success(
                result=35,
                block_hash="0xblock",
                extrinsic_hash="0xconcurrent-finalized",
            )

        wait = getattr(pending, f"wait_{wait_stage}")
        waiters = [asyncio.create_task(wait()) for _ in range(3)]
        await asyncio.sleep(0)

        if wait_stage == "submitted":
            error = TransactionInvalid(
                code="TRANSACTION_INVALID",
                stage=TxStage.SUBMISSION,
                elapsed_ms=4,
                certainty=OutcomeCertainty.REJECTED,
                retryable=False,
                suggested_action="Correct the transaction before rebuilding.",
            )
            pending.mark_invalid(error)
        elif wait_stage == "in_block":
            error = TransactionDropped(
                code="TRANSACTION_DROPPED",
                stage=TxStage.INCLUSION,
                elapsed_ms=4,
                certainty=OutcomeCertainty.UNKNOWN,
                retryable=False,
                suggested_action="Reconcile by transaction hash before retrying.",
            )
            pending.mark_dropped(error)
        else:
            error = ReconciliationRequired(
                code="RECONCILIATION_REQUIRED",
                stage=TxStage.FINALIZATION,
                elapsed_ms=4,
                certainty=OutcomeCertainty.INCLUDED,
                retryable=False,
                suggested_action="Reconcile finality by block hash.",
            )
            pending.mark_reconciliation_required(error)

        outcomes = await asyncio.gather(*waiters, return_exceptions=True)
        assert all(outcome is error for outcome in outcomes)
        assert all(isinstance(outcome, type(error)) for outcome in outcomes)

    asyncio.run(run())


def test_async_callback_without_running_loop_is_closed_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pending: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xcallback-sync", nonce=119, cloid=None
    )

    async def callback(_update: object) -> None:
        return None

    pending.add_status_callback(callback)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with caplog.at_level(logging.WARNING, logger="deepx_sdk._pending_tx"):
            pending.mark_submitted(node_status="ready")
        gc.collect()

    assert not [warning for warning in caught if "was never awaited" in str(warning.message)]
    assert "Transaction status callbacks must be synchronous" in caplog.text


def test_task_returned_by_synchronous_callback_is_cancelled_without_leaking() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xcallback-task", nonce=128, cloid=None
        )
        never = asyncio.Event()
        task = asyncio.create_task(never.wait())
        reported: list[dict[str, object]] = []
        loop = asyncio.get_running_loop()
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: reported.append(context))
        try:
            pending.add_status_callback(lambda _update: task)
            pending.mark_submitted(node_status="ready")
            await asyncio.sleep(0)
            assert task.cancelled()
            assert task not in asyncio.all_tasks()
        finally:
            loop.set_exception_handler(old_handler)
        assert reported == []

    asyncio.run(run())


def test_completed_future_returned_by_callback_has_its_error_consumed() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xcallback-complete", nonce=130, cloid=None
        )
        completed = asyncio.get_running_loop().create_future()
        completed.set_exception(RuntimeError("callback future failed"))
        pending.add_status_callback(lambda _update: completed)

        pending.mark_submitted(node_status="ready")
        assert completed.done()
        assert completed.exception() is not None

    asyncio.run(run())


def test_internal_wait_without_a_timeout_awaits_the_shielded_shared_future() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xno-timeout", nonce=131, cloid=None
        )
        future = asyncio.get_running_loop().create_future()
        waiter = asyncio.create_task(pending._wait_for(future, None, pending._inclusion_timeout))
        await asyncio.sleep(0)
        future.set_result(31)

        assert await waiter == 31

    asyncio.run(run())


def test_missing_result_raises_a_clear_runtime_error() -> None:
    pending: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xmissing-result", nonce=132, cloid=None
    )

    with pytest.raises(RuntimeError, match="result is not available"):
        pending._result_or_raise()


def test_wait_timeouts_and_cancellation_cancel_shield_proxies_without_stopping_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xproxy", nonce=129, cloid=None
        )
        pending.mark_submitted(node_status="ready")
        original_shield = asyncio.shield
        proxies: list[asyncio.Future[object]] = []

        def record_shield(awaitable: object) -> asyncio.Future[object]:
            proxy = original_shield(awaitable)
            proxies.append(proxy)
            return proxy

        monkeypatch.setattr(pending_module.asyncio, "shield", record_shield)
        with pytest.raises(InclusionTimeout):
            await pending.wait_in_block(timeout=0.001)
        timed_out_proxy = proxies[-1]
        assert timed_out_proxy.cancelled()

        waiter = asyncio.create_task(pending.wait_in_block())
        await asyncio.sleep(0)
        cancelled_proxy = proxies[-1]
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert cancelled_proxy.cancelled()

        timed_out_ref = weakref.ref(timed_out_proxy)
        cancelled_ref = weakref.ref(cancelled_proxy)
        proxies.clear()
        del timed_out_proxy, cancelled_proxy, waiter
        gc.collect()
        await asyncio.sleep(0)
        assert timed_out_ref() is None
        assert cancelled_ref() is None

        pending.mark_in_block_success(result=29, block_hash="0xblock", extrinsic_hash="0xproxy")
        assert await pending.wait_in_block() == 29

    asyncio.run(run())


def test_repeated_timeout_and_cancellation_do_not_leave_waiter_tasks_behind() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xlong-submitted", nonce=124, cloid=None
        )
        pending.mark_submitted(node_status="ready")
        current = asyncio.current_task()
        before = {task for task in asyncio.all_tasks() if task is not current}

        for _ in range(3):
            with pytest.raises(InclusionTimeout):
                await pending.wait_in_block(timeout=0.001)
            waiter = asyncio.create_task(pending.wait_in_block())
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter

        await asyncio.sleep(0)
        after = {task for task in asyncio.all_tasks() if task is not current}
        assert after == before

        pending.mark_in_block_success(
            result=24, block_hash="0xblock", extrinsic_hash="0xlong-submitted"
        )
        assert await pending.wait_in_block() == 24

    asyncio.run(run())


def test_execution_state_and_convenience_waits_follow_the_success_lifecycle() -> None:
    async def run() -> None:
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xbusiness-state",
            nonce=201,
            cloid=301,
        )
        assert pending.execution_state is pending_module.ExecutionState.SUBMITTING

        pending.mark_submitting()
        pending.mark_submitted(node_status="ready")
        assert pending.execution_state is pending_module.ExecutionState.ACCEPTED

        pending.mark_in_block_success(
            result=41,
            block_hash="0xblock",
            extrinsic_hash="0xbusiness-state",
        )
        assert pending.execution_state is pending_module.ExecutionState.EXECUTED
        assert await pending.executed() == 41

        pending.mark_finalized(block_hash="0xblock")
        assert pending.execution_state is pending_module.ExecutionState.FINALIZED
        assert await pending.finalized() == 41

    asyncio.run(run())


def test_state_alias_matches_every_business_state() -> None:
    success: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xticket-state",
        nonce=211,
        cloid=311,
    )
    assert success.state is pending_module.ExecutionState.SUBMITTING
    success.mark_submitted(node_status="ready")
    assert success.state is pending_module.ExecutionState.ACCEPTED
    success.mark_in_block_success(
        result=51,
        block_hash="0xblock",
        extrinsic_hash=success.tx_hash,
    )
    assert success.state is pending_module.ExecutionState.EXECUTED
    success.mark_finalized(block_hash="0xblock")
    assert success.state is pending_module.ExecutionState.FINALIZED

    failed: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xticket-failed",
        nonce=212,
        cloid=312,
    )
    failed.mark_invalid(
        TransactionInvalid(
            code="TRANSACTION_INVALID",
            stage=TxStage.SUBMISSION,
            elapsed_ms=1,
            certainty=OutcomeCertainty.REJECTED,
            retryable=False,
            suggested_action="Correct the transaction.",
        )
    )
    assert failed.state is pending_module.ExecutionState.FAILED

    uncertain: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xticket-uncertain",
        nonce=213,
        cloid=313,
    )
    uncertain.mark_reconciliation_required(
        ReconciliationRequired(
            code="RECONCILIATION_REQUIRED",
            stage=TxStage.RECOVERY,
            elapsed_ms=1,
            certainty=OutcomeCertainty.UNKNOWN,
            retryable=False,
            suggested_action="Reconcile by transaction hash.",
        )
    )
    assert uncertain.state is pending_module.ExecutionState.ACTION_REQUIRED


def test_execution_state_maps_retracted_failure_and_action_required() -> None:
    retracted: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xretracted-state",
        nonce=202,
        cloid=None,
    )
    retracted.mark_submitted(node_status="ready")
    retracted.mark_in_block_success(
        result=1,
        block_hash="0xold",
        extrinsic_hash="0xretracted-state",
    )
    retracted.mark_retracted()
    assert retracted.execution_state is pending_module.ExecutionState.ACCEPTED

    failed: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xfailed-state",
        nonce=203,
        cloid=None,
    )
    failed.mark_submitted(node_status="ready")
    failed.mark_dropped(
        TransactionDropped(
            code="TRANSACTION_DROPPED",
            stage=TxStage.SUBMISSION,
            elapsed_ms=1,
            certainty=OutcomeCertainty.UNKNOWN,
            retryable=False,
            suggested_action="Reconcile before retrying.",
        )
    )
    assert failed.execution_state is pending_module.ExecutionState.FAILED

    not_included: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xnot-included",
        nonce=205,
        cloid=305,
    )
    not_included.mark_submitted(node_status="ready")
    not_included.mark_not_included(
        TransactionNotIncluded(
            code="TRANSACTION_NOT_INCLUDED",
            stage=TxStage.RECOVERY,
            elapsed_ms=1,
            certainty=OutcomeCertainty.NOT_INCLUDED,
            retryable=False,
            suggested_action="Check the indexer before rebuilding.",
        )
    )
    assert (
        not_included.execution_state
        is pending_module.ExecutionState.NOT_INCLUDED
    )

    uncertain: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xaction-required",
        nonce=204,
        cloid=None,
    )
    uncertain.mark_reconciliation_required(
        ReconciliationRequired(
            code="RECONCILIATION_REQUIRED",
            stage=TxStage.RECOVERY,
            elapsed_ms=1,
            certainty=OutcomeCertainty.UNKNOWN,
            retryable=False,
            suggested_action="Reconcile by transaction hash.",
        )
    )
    assert (
        uncertain.execution_state
        is pending_module.ExecutionState.ACTION_REQUIRED
    )


def test_transaction_snapshot_retry_and_replacement_are_conservative() -> None:
    async def replace() -> PendingTransaction[int]:
        return PendingTransaction(tx_hash="0xreplacement", nonce=205, cloid=None)

    replacement: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xreplaceable",
        nonce=205,
        cloid=305,
        replacement_callback=replace,
    )
    assert replacement.safe_to_retry is False
    assert replacement.replacement_allowed is False
    replacement.mark_submitted(node_status="ready")
    assert replacement.replacement_allowed is True

    rejected: PendingTransaction[int] = PendingTransaction(
        tx_hash="0xretryable",
        nonce=206,
        cloid=306,
    )
    rejected.mark_invalid(
        TransactionInvalid(
            code="CLIENT_REJECTED_BEFORE_SEND",
            stage=TxStage.CLIENT,
            elapsed_ms=2,
            certainty=OutcomeCertainty.NOT_SUBMITTED,
            retryable=True,
            suggested_action="Correct capacity and retry.",
        )
    )
    assert rejected.safe_to_retry is True
    snapshot = rejected.snapshot()
    rendered = snapshot.to_dict()
    assert rendered["execution_state"] == "failed"
    assert rendered["raw_status"] == "invalid"
    assert rendered["safe_to_retry"] is True
    assert rendered["tx_hash"] == "0xretryable"
    assert rendered["error"]["code"] == "CLIENT_REJECTED_BEFORE_SEND"
    assert rendered["recovery"]["scan_complete"] is None
