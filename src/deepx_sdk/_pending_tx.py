"""The stateful handle returned while a transaction is still being tracked."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from ._tx_diagnostics import (
    FinalizationTimeout,
    InclusionTimeout,
    OutcomeCertainty,
    ReplacementUnsupported,
    SubmissionTimeout,
    TransactionError,
    TxStage,
)


ResultT = TypeVar("ResultT")
logger = logging.getLogger(__name__)
_UPDATE_END = object()


@dataclass(frozen=True)
class _TerminalWake:
    error: TransactionError


class TxStatus(str, Enum):
    CREATED = "created"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    IN_BLOCK_SUCCESS = "in_block_success"
    IN_BLOCK_FAILED = "in_block_failed"
    FINALIZED = "finalized"
    INVALID = "invalid"
    DROPPED = "dropped"
    USURPED = "usurped"
    RETRACTED = "retracted"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    CLIENT_CLOSED = "client_closed"


class ExecutionState(str, Enum):
    SUBMITTING = "submitting"
    ACCEPTED = "accepted"
    EXECUTED = "executed"
    FINALIZED = "finalized"
    FAILED = "failed"
    ACTION_REQUIRED = "action_required"


@dataclass(frozen=True)
class TxTimeouts:
    submit_ms: int = 1_000
    inclusion_ms: int = 5_000
    finalization_ms: int = 30_000


@dataclass
class TxTimings:
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    submitting_at: datetime | None = None
    submitted_at: datetime | None = None
    in_block_at: datetime | None = None
    finalized_at: datetime | None = None
    last_message_at: datetime | None = None
    encode_ms: float | None = None
    sign_ms: float | None = None
    rpc_submit_ms: float | None = None
    pool_wait_ms: float | None = None
    inclusion_ms: float | None = None
    event_decode_ms: float | None = None
    finalization_ms: float | None = None
    in_block_dispatch_ms: float | None = None


@dataclass(frozen=True)
class TxUpdate:
    status: TxStatus
    previous_status: TxStatus
    timestamp: datetime
    node_status: str | None = None
    block_hash: str | None = None
    extrinsic_hash: str | None = None
    error: TransactionError | None = None


@dataclass(frozen=True)
class TransactionSnapshot:
    execution_state: ExecutionState
    raw_status: TxStatus
    tx_hash: str
    cloid: int | None
    nonce: int
    node_status: str | None
    block_hash: str | None
    extrinsic_hash: str | None
    safe_to_retry: bool
    replacement_allowed: bool
    error: dict[str, Any] | None
    timestamps: dict[str, str | None]
    timings: dict[str, float | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_state": self.execution_state.value,
            "raw_status": self.raw_status.value,
            "tx_hash": self.tx_hash,
            "cloid": self.cloid,
            "nonce": self.nonce,
            "node_status": self.node_status,
            "block_hash": self.block_hash,
            "extrinsic_hash": self.extrinsic_hash,
            "safe_to_retry": self.safe_to_retry,
            "replacement_allowed": self.replacement_allowed,
            "error": self.error,
            "timestamps": dict(self.timestamps),
            "timings": dict(self.timings),
        }


_LEGAL_TRANSITIONS: dict[TxStatus, frozenset[TxStatus]] = {
    TxStatus.CREATED: frozenset(
        {
            TxStatus.SUBMITTING,
            TxStatus.SUBMITTED,
            TxStatus.INVALID,
            TxStatus.RECONCILIATION_REQUIRED,
            TxStatus.CLIENT_CLOSED,
        }
    ),
    TxStatus.SUBMITTING: frozenset(
        {
            TxStatus.SUBMITTED,
            TxStatus.INVALID,
            TxStatus.RECONCILIATION_REQUIRED,
            TxStatus.CLIENT_CLOSED,
        }
    ),
    TxStatus.SUBMITTED: frozenset(
        {
            TxStatus.IN_BLOCK_SUCCESS,
            TxStatus.IN_BLOCK_FAILED,
            TxStatus.INVALID,
            TxStatus.DROPPED,
            TxStatus.USURPED,
            TxStatus.CLIENT_CLOSED,
            TxStatus.RECONCILIATION_REQUIRED,
        }
    ),
    TxStatus.IN_BLOCK_SUCCESS: frozenset(
        {
            TxStatus.FINALIZED,
            TxStatus.RETRACTED,
            TxStatus.CLIENT_CLOSED,
            TxStatus.RECONCILIATION_REQUIRED,
        }
    ),
    TxStatus.RETRACTED: frozenset(
        {
            TxStatus.SUBMITTED,
            TxStatus.IN_BLOCK_SUCCESS,
            TxStatus.IN_BLOCK_FAILED,
            TxStatus.INVALID,
            TxStatus.DROPPED,
            TxStatus.USURPED,
            TxStatus.CLIENT_CLOSED,
            TxStatus.RECONCILIATION_REQUIRED,
        }
    ),
    TxStatus.IN_BLOCK_FAILED: frozenset(),
    TxStatus.FINALIZED: frozenset(),
    TxStatus.INVALID: frozenset(),
    TxStatus.DROPPED: frozenset(),
    TxStatus.USURPED: frozenset(),
    TxStatus.RECONCILIATION_REQUIRED: frozenset(),
    TxStatus.CLIENT_CLOSED: frozenset(),
}

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


class PendingTransaction(Generic[ResultT]):
    """Tracks one transaction without ever changing its lifecycle on a wait timeout."""

    def __init__(
        self,
        *,
        tx_hash: str,
        nonce: int,
        cloid: int | None,
        timeouts: TxTimeouts | None = None,
        replacement_callback: Callable[[], Awaitable[PendingTransaction[Any]]] | None = None,
    ) -> None:
        self.tx_hash = tx_hash
        self.nonce = nonce
        self.cloid = cloid
        self.timeouts = timeouts or TxTimeouts()
        self.status = TxStatus.CREATED
        self.timings = TxTimings()
        self.block_hash: str | None = None
        self.extrinsic_hash: str | None = None
        self.node_status: str | None = None
        self.result: ResultT | None = None
        self.error: TransactionError | None = None
        self._created_monotonic = time.monotonic()
        self._submitted_future: asyncio.Future[PendingTransaction[ResultT]] | None = None
        self._in_block_future: asyncio.Future[ResultT] | None = None
        self._finalized_future: asyncio.Future[ResultT] | None = None
        self._updates: asyncio.Queue[TxUpdate | object] = asyncio.Queue()
        self._callbacks: list[Callable[[TxUpdate], Any]] = []
        self._replacement_callback = replacement_callback

    @property
    def execution_state(self) -> ExecutionState:
        if self.status in {TxStatus.CREATED, TxStatus.SUBMITTING}:
            return ExecutionState.SUBMITTING
        if self.status in {TxStatus.SUBMITTED, TxStatus.RETRACTED}:
            return ExecutionState.ACCEPTED
        if self.status is TxStatus.IN_BLOCK_SUCCESS:
            return ExecutionState.EXECUTED
        if self.status is TxStatus.FINALIZED:
            return ExecutionState.FINALIZED
        if self.status in {
            TxStatus.IN_BLOCK_FAILED,
            TxStatus.INVALID,
            TxStatus.DROPPED,
            TxStatus.USURPED,
        }:
            return ExecutionState.FAILED
        return ExecutionState.ACTION_REQUIRED

    @property
    def state(self) -> ExecutionState:
        """Return the concise business state used by transaction-ticket callers."""
        return self.execution_state

    @property
    def safe_to_retry(self) -> bool:
        return bool(
            self.error is not None
            and self.error.certainty is OutcomeCertainty.NOT_SUBMITTED
            and self.error.retryable
        )

    @property
    def replacement_allowed(self) -> bool:
        return (
            self.status is TxStatus.SUBMITTED
            and self._replacement_callback is not None
        )

    def mark_submitting(self) -> None:
        self._transition(TxStatus.SUBMITTING)

    def mark_submitted(self, *, node_status: str | None = None) -> None:
        if self.status is TxStatus.SUBMITTED:
            self.node_status = node_status or self.node_status
            self.timings.last_message_at = datetime.now(UTC)
            return
        self._transition(TxStatus.SUBMITTED, node_status=node_status)
        self._resolve_submitted()

    def mark_in_block_success(
        self,
        *,
        result: ResultT,
        block_hash: str,
        extrinsic_hash: str,
    ) -> None:
        self._transition(
            TxStatus.IN_BLOCK_SUCCESS,
            block_hash=block_hash,
            extrinsic_hash=extrinsic_hash,
        )
        self.result = result
        self._resolve_submitted()
        self._resolve_result(self._in_block_future, result)

    def mark_in_block_failed(self, error: TransactionError) -> None:
        self._finish_with_error(TxStatus.IN_BLOCK_FAILED, error)

    def mark_finalized(self, *, block_hash: str | None = None) -> None:
        if self.result is None:
            raise RuntimeError("cannot finalize a transaction without an in-block result")
        self._transition(TxStatus.FINALIZED, block_hash=block_hash)
        self._resolve_result(self._finalized_future, self.result)

    def mark_retracted(self) -> None:
        self._transition(TxStatus.RETRACTED)
        self.result = None
        self.block_hash = None
        self.extrinsic_hash = None
        self._replace_completed_result_futures()

    def mark_invalid(self, error: TransactionError) -> None:
        self._finish_with_error(TxStatus.INVALID, error)

    def mark_dropped(self, error: TransactionError) -> None:
        self._finish_with_error(TxStatus.DROPPED, error)

    def mark_usurped(self, error: TransactionError) -> None:
        self._finish_with_error(TxStatus.USURPED, error)

    def mark_reconciliation_required(self, error: TransactionError) -> None:
        self._finish_with_error(TxStatus.RECONCILIATION_REQUIRED, error)

    def mark_client_closed(self, error: TransactionError | None = None) -> None:
        if error is None:
            error = TransactionError(
                code="CLIENT_CLOSED",
                stage=TxStage.CLIENT,
                tx_hash=self.tx_hash,
                cloid=self.cloid,
                nonce=self.nonce,
                elapsed_ms=self._elapsed_ms(),
                certainty=OutcomeCertainty.UNKNOWN,
                retryable=False,
                suggested_action="Reconcile by tx hash/cloid after reconnecting; do not resubmit blindly.",
                pending=self,
                node_status=self.node_status,
            )
        self._finish_with_error(TxStatus.CLIENT_CLOSED, error)

    def add_status_callback(self, callback: Callable[[TxUpdate], Any]) -> None:
        self._callbacks.append(callback)

    async def updates(self) -> AsyncIterator[TxUpdate]:
        """Yield ordered status changes to one consumer, ending after a terminal update."""
        while True:
            update = await self._updates.get()
            if update is _UPDATE_END:
                return
            yield update  # type: ignore[misc]

    async def wait_submitted(self, timeout: float | None = None) -> PendingTransaction[ResultT]:
        if self.status in {
            TxStatus.SUBMITTED,
            TxStatus.IN_BLOCK_SUCCESS,
            TxStatus.FINALIZED,
            TxStatus.RETRACTED,
        }:
            return self
        self._raise_if_terminal_error()
        future = self._submitted_future_for_current_loop()
        return await self._wait_for(
            future,
            timeout if timeout is not None else self.timeouts.submit_ms / 1_000,
            self._submission_timeout,
        )

    async def wait_in_block(self, timeout: float | None = None) -> ResultT:
        if self.status in {TxStatus.IN_BLOCK_SUCCESS, TxStatus.FINALIZED}:
            return self._result_or_raise()
        self._raise_if_terminal_error()
        future = self._in_block_future_for_current_loop()
        return await self._wait_for(
            future,
            timeout if timeout is not None else self.timeouts.inclusion_ms / 1_000,
            self._inclusion_timeout,
        )

    async def wait_finalized(self, timeout: float | None = None) -> ResultT:
        if self.status is TxStatus.FINALIZED:
            return self._result_or_raise()
        self._raise_if_terminal_error()
        future = self._finalized_future_for_current_loop()
        return await self._wait_for(
            future,
            timeout if timeout is not None else self.timeouts.finalization_ms / 1_000,
            self._finalization_timeout,
        )

    async def executed(self, timeout: float | None = None) -> ResultT:
        return await self.wait_in_block(timeout)

    async def finalized(self, timeout: float | None = None) -> ResultT:
        return await self.wait_finalized(timeout)

    async def replace(self) -> PendingTransaction[Any]:
        if self._replacement_callback is None or self.status is not TxStatus.SUBMITTED:
            raise ReplacementUnsupported(
                code="REPLACEMENT_UNSUPPORTED",
                stage=TxStage.CLIENT,
                tx_hash=self.tx_hash,
                cloid=self.cloid,
                nonce=self.nonce,
                elapsed_ms=self._elapsed_ms(),
                certainty=OutcomeCertainty.NOT_SUBMITTED,
                retryable=False,
                suggested_action=(
                    "Use explicit replacement only after node pool acceptance and only "
                    "for transaction types that support the same-nonce replacement rule."
                ),
                pending=self,
            )
        return await self._replacement_callback()

    def diagnostics(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "tx_hash": self.tx_hash,
            "cloid": self.cloid,
            "nonce": self.nonce,
            "created_at": self.timings.created_at.isoformat(),
            "submitted_at": self._isoformat(self.timings.submitted_at),
            "in_block_at": self._isoformat(self.timings.in_block_at),
            "finalized_at": self._isoformat(self.timings.finalized_at),
            "elapsed_ms": self._elapsed_ms(),
            "last_node_status": self.node_status,
            "last_message_at": self._isoformat(self.timings.last_message_at),
            "block_hash": self.block_hash,
            "extrinsic_hash": self.extrinsic_hash,
            "error": self.error.to_dict() if self.error is not None else None,
        }

    def snapshot(self) -> TransactionSnapshot:
        return TransactionSnapshot(
            execution_state=self.execution_state,
            raw_status=self.status,
            tx_hash=self.tx_hash,
            cloid=self.cloid,
            nonce=self.nonce,
            node_status=self.node_status,
            block_hash=self.block_hash,
            extrinsic_hash=self.extrinsic_hash,
            safe_to_retry=self.safe_to_retry,
            replacement_allowed=self.replacement_allowed,
            error=self.error.to_dict() if self.error is not None else None,
            timestamps={
                "created_at": self.timings.created_at.isoformat(),
                "submitting_at": self._isoformat(self.timings.submitting_at),
                "submitted_at": self._isoformat(self.timings.submitted_at),
                "in_block_at": self._isoformat(self.timings.in_block_at),
                "finalized_at": self._isoformat(self.timings.finalized_at),
                "last_message_at": self._isoformat(self.timings.last_message_at),
            },
            timings={
                "encode_ms": self.timings.encode_ms,
                "sign_ms": self.timings.sign_ms,
                "rpc_submit_ms": self.timings.rpc_submit_ms,
                "pool_wait_ms": self.timings.pool_wait_ms,
                "inclusion_ms": self.timings.inclusion_ms,
                "event_decode_ms": self.timings.event_decode_ms,
                "finalization_ms": self.timings.finalization_ms,
                "in_block_dispatch_ms": self.timings.in_block_dispatch_ms,
            },
        )

    def _transition(
        self,
        target: TxStatus,
        *,
        node_status: str | None = None,
        block_hash: str | None = None,
        extrinsic_hash: str | None = None,
        error: TransactionError | None = None,
    ) -> None:
        previous = self.status
        self._assert_legal_transition(target)
        now = datetime.now(UTC)
        self.status = target
        self.node_status = node_status or self.node_status
        self.block_hash = block_hash or self.block_hash
        self.extrinsic_hash = extrinsic_hash or self.extrinsic_hash
        self.timings.last_message_at = now
        if target is TxStatus.SUBMITTING:
            self.timings.submitting_at = now
        elif target is TxStatus.SUBMITTED:
            self.timings.submitted_at = now
        elif target is TxStatus.IN_BLOCK_SUCCESS:
            self.timings.in_block_at = now
        elif target is TxStatus.FINALIZED:
            self.timings.finalized_at = now
        update = TxUpdate(
            status=target,
            previous_status=previous,
            timestamp=now,
            node_status=self.node_status,
            block_hash=self.block_hash,
            extrinsic_hash=self.extrinsic_hash,
            error=error,
        )
        self._updates.put_nowait(update)
        self._notify_callbacks(update)
        if target in _TERMINAL_STATUSES:
            self._updates.put_nowait(_UPDATE_END)

    def _finish_with_error(self, status: TxStatus, error: TransactionError) -> None:
        self._assert_legal_transition(status)
        if error.pending is None:
            error.pending = self
        if error.tx_hash is None:
            error.tx_hash = self.tx_hash
        if error.nonce is None:
            error.nonce = self.nonce
        if error.cloid is None:
            error.cloid = self.cloid
        if error.node_status is None:
            error.node_status = self.node_status
        if not error.suggested_action:
            error.suggested_action = "Reconcile by tx hash/cloid before taking another action."
        self.error = error
        self._transition(status, error=error)
        self._reject(self._submitted_future, error)
        self._reject(self._in_block_future, error)
        self._reject(self._finalized_future, error)

    def _notify_callbacks(self, update: TxUpdate) -> None:
        for callback in self._callbacks:
            try:
                callback_result = callback(update)
                if inspect.isawaitable(callback_result):
                    self._close_awaitable_callback(callback_result)
            except Exception:
                logger.error("Transaction status callback failed")

    @staticmethod
    def _close_awaitable_callback(awaitable: Awaitable[Any]) -> None:
        if isinstance(awaitable, asyncio.Future):
            awaitable.cancel()
            if awaitable.done() and not awaitable.cancelled():
                awaitable.exception()
        else:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
        logger.warning("Transaction status callbacks must be synchronous")

    async def _wait_for(
        self,
        future: asyncio.Future[Any],
        timeout: float | None,
        timeout_error: Callable[[], TransactionError],
    ) -> Any:
        shielded = asyncio.shield(future)
        shielded.add_done_callback(self._consume_future_exception)
        try:
            if timeout is None:
                result = await shielded
            else:
                async with asyncio.timeout(timeout):
                    result = await shielded
            if isinstance(result, _TerminalWake):
                raise result.error
            return result
        except TimeoutError as exc:
            raise timeout_error() from exc
        finally:
            if not shielded.done():
                shielded.cancel()

    def _submitted_future_for_current_loop(self) -> asyncio.Future[PendingTransaction[ResultT]]:
        if self._submitted_future is None:
            self._submitted_future = asyncio.get_running_loop().create_future()
            self._submitted_future.add_done_callback(self._consume_future_exception)
        return self._submitted_future

    def _in_block_future_for_current_loop(self) -> asyncio.Future[ResultT]:
        if self._in_block_future is None:
            self._in_block_future = asyncio.get_running_loop().create_future()
            self._in_block_future.add_done_callback(self._consume_future_exception)
        return self._in_block_future

    def _finalized_future_for_current_loop(self) -> asyncio.Future[ResultT]:
        if self._finalized_future is None:
            self._finalized_future = asyncio.get_running_loop().create_future()
            self._finalized_future.add_done_callback(self._consume_future_exception)
        return self._finalized_future

    def _replace_completed_result_futures(self) -> None:
        if self._in_block_future is not None and self._in_block_future.done():
            self._in_block_future = asyncio.get_running_loop().create_future()
            self._in_block_future.add_done_callback(self._consume_future_exception)
        if self._finalized_future is not None and self._finalized_future.done():
            self._finalized_future = asyncio.get_running_loop().create_future()
            self._finalized_future.add_done_callback(self._consume_future_exception)

    def _resolve_submitted(self) -> None:
        if self._submitted_future is not None and not self._submitted_future.done():
            self._submitted_future.set_result(self)

    @staticmethod
    def _resolve_result(future: asyncio.Future[ResultT] | None, result: ResultT) -> None:
        if future is not None and not future.done():
            future.set_result(result)

    @staticmethod
    def _reject(future: asyncio.Future[Any] | None, error: TransactionError) -> None:
        if future is not None and not future.done():
            future.set_result(_TerminalWake(error))

    @staticmethod
    def _consume_future_exception(future: asyncio.Future[Any]) -> None:
        if not future.cancelled():
            future.exception()

    def _assert_legal_transition(self, target: TxStatus) -> None:
        if target not in _LEGAL_TRANSITIONS[self.status]:
            raise RuntimeError(
                f"illegal transaction transition: {self.status.value} -> {target.value}"
            )

    def _raise_if_terminal_error(self) -> None:
        if self.error is not None:
            raise self.error

    def _result_or_raise(self) -> ResultT:
        self._raise_if_terminal_error()
        if self.result is None:
            raise RuntimeError("transaction result is not available")
        return self.result

    def _submission_timeout(self) -> SubmissionTimeout:
        return SubmissionTimeout(
            code="SUBMISSION_TIMEOUT",
            stage=TxStage.SUBMISSION,
            tx_hash=self.tx_hash,
            cloid=self.cloid,
            nonce=self.nonce,
            elapsed_ms=self._elapsed_ms(),
            certainty=OutcomeCertainty.UNKNOWN,
            retryable=False,
            suggested_action="Continue waiting or reconcile by tx hash/cloid before retrying.",
            pending=self,
            node_status=self.node_status,
        )

    def _inclusion_timeout(self) -> InclusionTimeout:
        return InclusionTimeout(
            code="INCLUSION_TIMEOUT",
            stage=TxStage.INCLUSION,
            tx_hash=self.tx_hash,
            cloid=self.cloid,
            nonce=self.nonce,
            elapsed_ms=self._elapsed_ms(),
            certainty=OutcomeCertainty.UNKNOWN,
            retryable=False,
            suggested_action="Continue waiting or reconcile by tx hash/cloid before retrying.",
            pending=self,
            node_status=self.node_status,
        )

    def _finalization_timeout(self) -> FinalizationTimeout:
        return FinalizationTimeout(
            code="FINALIZATION_TIMEOUT",
            stage=TxStage.FINALIZATION,
            tx_hash=self.tx_hash,
            cloid=self.cloid,
            nonce=self.nonce,
            elapsed_ms=self._elapsed_ms(),
            certainty=OutcomeCertainty.INCLUDED,
            retryable=False,
            suggested_action="Continue waiting or reconcile finality by tx hash/block hash; do not resubmit.",
            pending=self,
            node_status=self.node_status,
        )

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._created_monotonic) * 1_000)

    @staticmethod
    def _isoformat(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None


__all__ = ["PendingTransaction", "TxStatus", "TxTimings", "TxTimeouts", "TxUpdate"]
