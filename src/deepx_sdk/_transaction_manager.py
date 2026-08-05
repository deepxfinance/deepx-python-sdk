"""Client-owned transaction event routing and operational state output."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ._pending_tx import (
    ExecutionState,
    PendingTransaction,
    TransactionSnapshot,
    TxStatus,
    TxUpdate,
)


logger = logging.getLogger(__name__)
_STOP = object()
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

TransactionListener = Callable[["TransactionEvent"], Any]

_STATUS_MESSAGES: dict[TxStatus, str] = {
    TxStatus.CREATED: "Transaction created",
    TxStatus.SUBMITTING: "Submission in progress",
    TxStatus.SUBMITTED: "Node accepted the transaction; waiting for inclusion",
    TxStatus.IN_BLOCK_SUCCESS: "Transaction included and executed successfully",
    TxStatus.IN_BLOCK_FAILED: "Transaction included but execution failed",
    TxStatus.FINALIZED: "Transaction finalized",
    TxStatus.INVALID: "Transaction invalid",
    TxStatus.DROPPED: "Transaction dropped from the transaction pool",
    TxStatus.USURPED: "Transaction replaced by another transaction with the same nonce",
    TxStatus.RETRACTED: "Containing block retracted; waiting for reinclusion",
    TxStatus.RECONCILIATION_REQUIRED: "Outcome uncertain; reconciliation required",
    TxStatus.CLIENT_CLOSED: "Client closed; reconcile the transaction outcome",
}


@dataclass(frozen=True)
class TransactionEvent:
    snapshot: TransactionSnapshot
    previous_status: TxStatus
    timestamp: datetime
    message: str

    @property
    def execution_state(self) -> ExecutionState:
        return self.snapshot.execution_state

    @property
    def raw_status(self) -> TxStatus:
        return self.snapshot.raw_status

    def to_dict(self) -> dict[str, Any]:
        rendered = self.snapshot.to_dict()
        rendered.update(
            {
                "previous_status": self.previous_status.value,
                "timestamp": self.timestamp.isoformat(),
                "message": self.message,
            }
        )
        return rendered


class TransactionManager:
    def __init__(
        self,
        *,
        listener: TransactionListener | None = None,
        print_state: bool = False,
        max_tracked_transactions: int = 1024,
        max_completed_transactions: int = 10_000,
    ) -> None:
        self._listener = listener
        self._print_state = bool(print_state)
        self._max_completed_transactions = int(max_completed_transactions)
        if self._max_completed_transactions < 0:
            raise ValueError("max_completed_transactions must be non-negative")
        self._queue: asyncio.Queue[TransactionEvent | object] = asyncio.Queue(
            maxsize=max(64, int(max_tracked_transactions) * 8)
        )
        self._by_hash: dict[str, PendingTransaction[Any]] = {}
        self._by_cloid: dict[int, PendingTransaction[Any]] = {}
        self._registered: set[int] = set()
        self._completed: deque[
            tuple[str, PendingTransaction[Any]]
        ] = deque()
        self._worker: asyncio.Task[None] | None = None
        self._accepting = False
        self._overflow_warned = False

    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._accepting = True
        self._worker = asyncio.create_task(
            self._run(),
            name="deepx-transaction-manager",
        )

    def register(self, pending: PendingTransaction[Any]) -> None:
        self._by_hash[pending.tx_hash] = pending
        if pending.cloid is not None:
            self._by_cloid[pending.cloid] = pending
        identity = id(pending)
        if identity in self._registered:
            return
        self._registered.add(identity)
        pending.add_status_callback(
            lambda update, handle=pending: self._on_update(handle, update)
        )

    def get(self, tx_hash: str) -> PendingTransaction[Any] | None:
        return self._by_hash.get(tx_hash)

    def get_by_cloid(self, cloid: int) -> PendingTransaction[Any] | None:
        return self._by_cloid.get(int(cloid))

    def snapshots(self) -> tuple[TransactionSnapshot, ...]:
        return tuple(pending.snapshot() for pending in self._by_hash.values())

    async def wait_idle(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        worker = self._worker
        if worker is None:
            self._accepting = False
            return
        self._accepting = False
        await self._queue.join()
        await self._queue.put(_STOP)
        await worker
        self._worker = None

    def _on_update(
        self,
        pending: PendingTransaction[Any],
        update: TxUpdate,
    ) -> None:
        if update.status in _TERMINAL_STATUSES:
            self._retain_completed(pending)
        if not self._accepting:
            return
        event = TransactionEvent(
            snapshot=pending.snapshot(),
            previous_status=update.previous_status,
            timestamp=update.timestamp,
            message=_STATUS_MESSAGES[update.status],
        )
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            if not self._overflow_warned:
                self._overflow_warned = True
                logger.warning(
                    "Transaction manager queue capacity exceeded; "
                    "read authoritative snapshots from client.transactions"
                )

    def _retain_completed(self, pending: PendingTransaction[Any]) -> None:
        self._completed.append((pending.tx_hash, pending))
        while len(self._completed) > self._max_completed_transactions:
            tx_hash, expired = self._completed.popleft()
            if self._by_hash.get(tx_hash) is expired:
                self._by_hash.pop(tx_hash, None)
            if (
                expired.cloid is not None
                and self._by_cloid.get(expired.cloid) is expired
            ):
                self._by_cloid.pop(expired.cloid, None)
            self._registered.discard(id(expired))

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                event = item
                if self._print_state:
                    self._print_event(event)
                if self._listener is not None:
                    await self._notify_listener(event)
            finally:
                self._queue.task_done()

    @staticmethod
    def _print_event(event: TransactionEvent) -> None:
        try:
            print(
                json.dumps(
                    event.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
        except Exception as exc:
            logger.error(
                "Transaction state output failed: %s",
                type(exc).__name__,
            )

    async def _notify_listener(self, event: TransactionEvent) -> None:
        try:
            result = self._listener(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.error(
                "Transaction listener failed: %s",
                type(exc).__name__,
            )


__all__ = [
    "TransactionEvent",
    "TransactionListener",
    "TransactionManager",
]
