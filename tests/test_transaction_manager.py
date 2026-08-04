from __future__ import annotations

import asyncio
import json
import logging

from deepx_sdk._pending_tx import (
    ExecutionState,
    PendingTransaction,
)
from deepx_sdk._transaction_manager import TransactionManager
from deepx_sdk._tx_diagnostics import (
    OutcomeCertainty,
    TransactionInvalid,
    TxStage,
)


def test_manager_bounds_terminal_hash_and_cloid_history() -> None:
    async def run() -> None:
        manager = TransactionManager(
            max_tracked_transactions=8,
            max_completed_transactions=2,
        )
        await manager.start()
        handles = [
            PendingTransaction[int](
                tx_hash=f"0xhistory-{index}",
                nonce=index,
                cloid=100 + index,
            )
            for index in range(3)
        ]
        for pending in handles:
            manager.register(pending)
            pending.mark_invalid(
                TransactionInvalid(
                    code="TRANSACTION_INVALID",
                    stage=TxStage.SUBMISSION,
                    elapsed_ms=1,
                    certainty=OutcomeCertainty.REJECTED,
                    retryable=False,
                    suggested_action="Correct the transaction.",
                )
            )
        await manager.wait_idle()

        assert manager.get("0xhistory-0") is None
        assert manager.get_by_cloid(100) is None
        assert manager.get("0xhistory-1") is handles[1]
        assert manager.get_by_cloid(102) is handles[2]
        assert len(manager.snapshots()) == 2
        assert handles[0].state is ExecutionState.FAILED
        await manager.close()

    asyncio.run(run())


def test_manager_delivers_ordered_events_and_indexes_handles(
    capsys,
) -> None:
    async def run() -> None:
        events = []

        async def listener(event) -> None:
            events.append(event)

        manager = TransactionManager(
            listener=listener,
            print_state=False,
            max_tracked_transactions=8,
        )
        await manager.start()
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xmanaged",
            nonce=1,
            cloid=101,
        )
        manager.register(pending)
        pending.mark_submitting()
        pending.mark_submitted(node_status="ready")
        pending.mark_in_block_success(
            result=9,
            block_hash="0xblock",
            extrinsic_hash="0xmanaged",
        )
        pending.mark_finalized(block_hash="0xblock")
        await manager.wait_idle()

        assert [event.execution_state for event in events] == [
            ExecutionState.SUBMITTING,
            ExecutionState.ACCEPTED,
            ExecutionState.EXECUTED,
            ExecutionState.FINALIZED,
        ]
        assert manager.get("0xmanaged") is pending
        assert manager.get_by_cloid(101) is pending
        assert manager.snapshots() == (pending.snapshot(),)
        assert capsys.readouterr().out == ""
        await manager.close()

    asyncio.run(run())


def test_listener_failure_isolated_and_manager_close_stops_delivery(
    caplog,
) -> None:
    async def run() -> None:
        calls: list[str] = []

        def listener(event) -> None:
            calls.append(event.raw_status.value)
            if len(calls) == 1:
                raise ValueError("secret listener detail")

        manager = TransactionManager(
            listener=listener,
            max_tracked_transactions=8,
        )
        await manager.start()
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xlistener",
            nonce=2,
            cloid=None,
        )
        manager.register(pending)
        pending.mark_submitting()
        pending.mark_submitted(node_status="broadcast")
        await manager.wait_idle()

        assert calls == ["submitting", "submitted"]
        assert "ValueError" in caplog.text
        assert "secret listener detail" not in caplog.text

        await manager.close()
        pending.mark_in_block_success(
            result=2,
            block_hash="0xblock",
            extrinsic_hash="0xlistener",
        )
        await asyncio.sleep(0)
        assert calls == ["submitting", "submitted"]

    with caplog.at_level(logging.ERROR):
        asyncio.run(run())


def test_print_state_outputs_redacted_structured_json(capsys) -> None:
    async def run() -> None:
        private_key = "0x" + "ab" * 32
        signed_extrinsic = "0xdeadbeefcafebabe"
        manager = TransactionManager(
            print_state=True,
            max_tracked_transactions=8,
        )
        await manager.start()
        pending: PendingTransaction[int] = PendingTransaction(
            tx_hash="0xprinted",
            nonce=3,
            cloid=103,
        )
        manager.register(pending)
        pending.mark_invalid(
            TransactionInvalid(
                code="TRANSACTION_INVALID",
                stage=TxStage.SUBMISSION,
                elapsed_ms=1,
                certainty=OutcomeCertainty.REJECTED,
                retryable=False,
                suggested_action="Correct the transaction.",
                cause=RuntimeError(
                    {
                        "private_key": private_key,
                        "signed_extrinsic": signed_extrinsic,
                    }
                ),
            )
        )
        await manager.wait_idle()
        await manager.close()

        output = capsys.readouterr().out
        rendered = json.loads(output)
        assert rendered["execution_state"] == "failed"
        assert rendered["raw_status"] == "invalid"
        assert rendered["tx_hash"] == "0xprinted"
        assert private_key not in output
        assert signed_extrinsic not in output

    asyncio.run(run())


def test_queue_overflow_warns_without_losing_authoritative_snapshots(
    caplog,
) -> None:
    async def run() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_listener(_event) -> None:
            entered.set()
            await release.wait()

        manager = TransactionManager(
            listener=slow_listener,
            max_tracked_transactions=1,
        )
        await manager.start()
        handles = [
            PendingTransaction[int](
                tx_hash=f"0xoverflow-{index}",
                nonce=1000 + index,
                cloid=None,
            )
            for index in range(70)
        ]
        manager.register(handles[0])
        handles[0].mark_submitting()
        await entered.wait()
        for pending in handles[1:]:
            manager.register(pending)
            pending.mark_submitting()

        assert len(manager.snapshots()) == 70
        assert "queue capacity" in caplog.text
        release.set()
        await manager.wait_idle()
        await manager.close()

    with caplog.at_level(logging.WARNING):
        asyncio.run(run())
