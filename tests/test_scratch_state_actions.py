from __future__ import annotations

import asyncio
import json

import pytest

from deepx_sdk import (
    ExecutionState,
    OutcomeCertainty,
    PendingTransaction,
    TransactionError,
    TransactionManager,
)
from deepx_sdk._tx_diagnostics import TxStage

# scratch/ is git-ignored local tooling; skip the whole module when absent.
_scratch_state_actions = pytest.importorskip(
    "scratch.test_async_state_actions",
    reason="requires git-ignored scratch/ scripts (not present in fresh clones)",
)
TransactionActionPrinter = _scratch_state_actions.TransactionActionPrinter


def _error(
    *,
    code: str,
    certainty: OutcomeCertainty,
    retryable: bool,
) -> TransactionError:
    return TransactionError(
        code=code,
        stage=TxStage.SUBMISSION,
        elapsed_ms=1,
        certainty=certainty,
        retryable=retryable,
        suggested_action="test action",
    )


def test_action_printer_handles_every_business_state(capsys) -> None:
    async def run() -> None:
        listener = TransactionActionPrinter()
        manager = TransactionManager(listener=listener)
        await manager.start()

        success = PendingTransaction[int](
            tx_hash="0xsuccess",
            nonce=1,
            cloid=101,
        )
        manager.register(success)
        success.mark_submitting()
        success.mark_submitted(node_status="ready")
        success.mark_in_block_success(
            result=7,
            block_hash="0xblock",
            extrinsic_hash=success.tx_hash,
        )
        success.mark_finalized(block_hash="0xblock")

        failed = PendingTransaction[int](
            tx_hash="0xfailed",
            nonce=2,
            cloid=102,
        )
        manager.register(failed)
        failed.mark_invalid(
            _error(
                code="NOT_SUBMITTED",
                certainty=OutcomeCertainty.NOT_SUBMITTED,
                retryable=True,
            )
        )

        uncertain = PendingTransaction[int](
            tx_hash="0xuncertain",
            nonce=3,
            cloid=103,
        )
        manager.register(uncertain)
        uncertain.mark_reconciliation_required(
            _error(
                code="UNKNOWN_OUTCOME",
                certainty=OutcomeCertainty.UNKNOWN,
                retryable=False,
            )
        )

        await manager.wait_idle()
        await manager.close()

    asyncio.run(run())
    actions = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert [item["execution_state"] for item in actions] == [
        ExecutionState.SUBMITTING.value,
        ExecutionState.ACCEPTED.value,
        ExecutionState.EXECUTED.value,
        ExecutionState.FINALIZED.value,
        ExecutionState.FAILED.value,
        ExecutionState.ACTION_REQUIRED.value,
    ]
    assert [item["action"] for item in actions] == [
        "start_submission_timer",
        "record_node_acceptance",
        "update_strategy_state",
        "persist_and_cleanup",
        "retry_allowed",
        "freeze_and_reconcile",
    ]
