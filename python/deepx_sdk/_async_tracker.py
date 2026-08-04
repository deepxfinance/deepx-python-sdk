"""Submission tracking and block-scoped transaction result decoding."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from . import _native_py
from ._async_encoder import EncodedExtrinsic, ExtrinsicEncoder
from ._async_transport import AsyncRpcTransport, TransportRequestError
from ._errors import ChainError, RPCError
from ._pending_tx import PendingTransaction, TxStatus, TxTimeouts
from ._tx_diagnostics import (
    OutcomeCertainty,
    ReconciliationRequired,
    TransactionDropped,
    TransactionError,
    TransactionInvalid,
    TransactionUsurped,
    TxStage,
)


ResultT = TypeVar("ResultT")
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


@dataclass(frozen=True)
class ExpectedEvent:
    pallet: str
    event: str


@dataclass
class _TrackedTransaction(Generic[ResultT]):
    encoded: EncodedExtrinsic
    pending: PendingTransaction[ResultT]
    expected_event: ExpectedEvent
    result_decoder: Callable[[Mapping[str, Any], PendingTransaction[ResultT]], ResultT]
    submit_started_ns: int
    accepted_ns: int | None = None
    in_block_received_ns: int | None = None
    in_block_completed_ns: int | None = None
    subscription_id: str | None = None


@dataclass(frozen=True)
class _ResolvedBlock:
    extrinsic_indexes: dict[str, int]
    events: list[dict[str, Any]]
    event_decode_ms: float


class _TrackedChainError(ChainError):
    def __init__(
        self,
        decoded: ChainError,
        *,
        pending: PendingTransaction[Any],
        block_hash: str,
    ) -> None:
        super().__init__(
            code=decoded.code,
            name=decoded.name,
            pallet=decoded.pallet,
            message=decoded.message,
        )
        self.stage = TxStage.INCLUSION
        self.tx_hash = pending.tx_hash
        self.cloid = pending.cloid
        self.nonce = pending.nonce
        self.elapsed_ms = _elapsed_ms(pending)
        self.certainty = OutcomeCertainty.EXECUTED_FAILED
        self.retryable = False
        self.suggested_action = (
            "Inspect the decoded chain error; do not automatically resubmit."
        )
        self.pending = pending
        self.cause = decoded
        self.node_status = pending.node_status
        self.block_hash = block_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "pallet": self.pallet,
            "message": self.message,
            "stage": self.stage.value,
            "tx_hash": self.tx_hash,
            "cloid": self.cloid,
            "nonce": self.nonce,
            "block_hash": self.block_hash,
            "elapsed_ms": self.elapsed_ms,
            "certainty": self.certainty.value,
            "safe_to_retry": self.retryable,
            "suggested_action": self.suggested_action,
            "node_status": self.node_status,
        }


class TransactionTracker:
    def __init__(
        self,
        transport: AsyncRpcTransport,
        encoder: ExtrinsicEncoder,
        *,
        max_completed_transactions: int = 10_000,
        max_resolved_blocks: int = 256,
    ) -> None:
        self._transport = transport
        self._encoder = encoder
        self._max_completed_transactions = _non_negative_limit(
            max_completed_transactions,
            name="max_completed_transactions",
        )
        self._max_resolved_blocks = _non_negative_limit(
            max_resolved_blocks,
            name="max_resolved_blocks",
        )
        if self._max_resolved_blocks == 0:
            raise ValueError("max_resolved_blocks must be positive")
        self._transactions: dict[str, _TrackedTransaction[Any]] = {}
        self._active_transactions: dict[str, PendingTransaction[Any]] = {}
        self._completed_transactions: deque[
            tuple[str, _TrackedTransaction[Any]]
        ] = deque()
        self._block_transactions: dict[str, set[str]] = {}
        self._block_tasks: dict[str, asyncio.Task[_ResolvedBlock]] = {}
        self._applied_block_transactions: dict[str, set[str]] = {}
        self._block_fetch_count = 0
        self._background_tasks: set[asyncio.Task[Any]] = set()

    @property
    def block_fetch_count(self) -> int:
        return self._block_fetch_count

    @property
    def resolved_block_cache_size(self) -> int:
        return len(self._block_tasks)

    @property
    def pending_transactions(self) -> tuple[PendingTransaction[Any], ...]:
        return tuple(self._active_transactions.values())

    def pending_transaction(
        self,
        tx_hash: str,
    ) -> PendingTransaction[Any] | None:
        tracked = self._transactions.get(tx_hash)
        return tracked.pending if tracked is not None else None

    async def submit(
        self,
        *,
        encoded: EncodedExtrinsic,
        cloid: int | None,
        expected_event: ExpectedEvent,
        result_decoder: Callable[
            [Mapping[str, Any], PendingTransaction[ResultT]], ResultT
        ],
        timeouts: TxTimeouts,
        replacement_callback: Callable[
            [],
            Awaitable[PendingTransaction[Any]],
        ]
        | None = None,
        pending_callback: Callable[[PendingTransaction[ResultT]], None] | None = None,
    ) -> PendingTransaction[ResultT]:
        pending: PendingTransaction[ResultT] = PendingTransaction(
            tx_hash=encoded.tx_hash,
            nonce=encoded.nonce,
            cloid=cloid,
            timeouts=timeouts,
            replacement_callback=replacement_callback,
        )
        pending.timings.encode_ms = encoded.encode_ms
        pending.timings.sign_ms = encoded.sign_ms
        if pending_callback is not None:
            pending_callback(pending)
        tracked = _TrackedTransaction(
            encoded=encoded,
            pending=pending,
            expected_event=expected_event,
            result_decoder=result_decoder,
            submit_started_ns=time.perf_counter_ns(),
        )
        self._transactions[encoded.tx_hash] = tracked
        self._active_transactions[encoded.tx_hash] = pending
        pending.add_status_callback(
            lambda update, tx_hash=encoded.tx_hash, handle=pending: self._transaction_status_changed(
                tx_hash,
                handle,
                update.status,
            )
        )
        pending.mark_submitting()
        open_task = self._spawn(
            self._open_subscription(tracked),
            name=f"deepx-submit-watch-{encoded.tx_hash}",
        )
        submitted_task = asyncio.create_task(pending.wait_submitted())
        done, _pending_tasks = await asyncio.wait(
            {open_task, submitted_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if submitted_task in done:
            return submitted_task.result()
        try:
            open_task.result()
        except BaseException:
            submitted_task.cancel()
            await asyncio.gather(submitted_task, return_exceptions=True)
            raise
        return await submitted_task

    async def resolve_block(self, block_hash: str) -> None:
        task = self._block_tasks.get(block_hash)
        if task is None:
            task = asyncio.create_task(
                self._fetch_block(block_hash),
                name=f"deepx-resolve-block-{block_hash}",
            )
            self._block_tasks[block_hash] = task
            self._block_fetch_count += 1
        resolved = await asyncio.shield(task)
        applied = self._applied_block_transactions.setdefault(block_hash, set())
        for tx_hash in tuple(self._block_transactions.get(block_hash, ())):
            if tx_hash in applied:
                continue
            applied.add(tx_hash)
            tracked = self._transactions.get(tx_hash)
            extrinsic_index = resolved.extrinsic_indexes.get(tx_hash)
            if tracked is None or extrinsic_index is None:
                continue
            scoped = _native_py._filter_events_for_extrinsic(
                resolved.events,
                extrinsic_idx=extrinsic_index,
            )
            failed_attrs = _native_py._system_extrinsic_failed_attrs(scoped)
            if failed_attrs is not None:
                decoded_error = _native_py._chain_error_from_failed_attrs(
                    failed_attrs,
                    (
                        "submit extrinsic failed (block events): "
                        f"extrinsic_hash={tx_hash}, block_hash={block_hash}, "
                        f"extrinsic_idx={extrinsic_index}"
                    ),
                )
                if isinstance(decoded_error, ChainError):
                    error: Any = _TrackedChainError(
                        decoded_error,
                        pending=tracked.pending,
                        block_hash=block_hash,
                    )
                else:
                    error = TransactionError(
                        code="EXTRINSIC_FAILED_DECODE_ERROR",
                        stage=TxStage.INCLUSION,
                        tx_hash=tx_hash,
                        cloid=tracked.pending.cloid,
                        nonce=tracked.pending.nonce,
                        elapsed_ms=_elapsed_ms(tracked.pending),
                        certainty=OutcomeCertainty.EXECUTED_FAILED,
                        retryable=False,
                        suggested_action=(
                            "Inspect the raw dispatch error before taking another action."
                        ),
                        pending=tracked.pending,
                        cause=RuntimeError(failed_attrs),
                        node_status=tracked.pending.node_status,
                    )
                tracked.pending.mark_in_block_failed(error)
                continue
            matches = _native_py._filter_matching_events(
                scoped,
                pallet=tracked.expected_event.pallet,
                event=tracked.expected_event.event,
            )
            if not matches:
                tracked.pending.mark_in_block_failed(
                    TransactionError(
                        code="EXPECTED_EVENT_MISSING",
                        stage=TxStage.INCLUSION,
                        tx_hash=tx_hash,
                        cloid=tracked.pending.cloid,
                        nonce=tracked.pending.nonce,
                        elapsed_ms=_elapsed_ms(tracked.pending),
                        certainty=OutcomeCertainty.INCLUDED,
                        retryable=False,
                        suggested_action=(
                            "Inspect the scoped block events and decoder metadata; "
                            "do not resubmit the included transaction."
                        ),
                        pending=tracked.pending,
                        cause=RuntimeError(
                            {
                                "block_hash": block_hash,
                                "extrinsic_index": extrinsic_index,
                                "expected_event": (
                                    f"{tracked.expected_event.pallet}."
                                    f"{tracked.expected_event.event}"
                                ),
                            }
                        ),
                        node_status=tracked.pending.node_status,
                    )
                )
                continue
            fields = matches[0].get("attributes")
            if not isinstance(fields, Mapping):
                fields = {}
            tracked.pending.timings.event_decode_ms = resolved.event_decode_ms
            result = tracked.result_decoder(fields, tracked.pending)
            tracked.pending.mark_in_block_success(
                result=result,
                block_hash=block_hash,
                extrinsic_hash=tx_hash,
            )
            completed_ns = time.perf_counter_ns()
            tracked.in_block_completed_ns = completed_ns
            if tracked.in_block_received_ns is not None:
                tracked.pending.timings.in_block_dispatch_ms = (
                    completed_ns - tracked.in_block_received_ns
                ) / 1_000_000
        self._prune_resolved_blocks()

    def _transaction_status_changed(
        self,
        tx_hash: str,
        pending: PendingTransaction[Any],
        status: TxStatus,
    ) -> None:
        if status not in _TERMINAL_STATUSES:
            return
        if self._active_transactions.get(tx_hash) is pending:
            self._active_transactions.pop(tx_hash, None)
        tracked = self._transactions.get(tx_hash)
        if tracked is None or tracked.pending is not pending:
            return
        if tracked.subscription_id is not None:
            self._spawn(
                self._forget_subscription(tracked),
                name=f"deepx-forget-watch-{tx_hash}",
            )
        self._completed_transactions.append((tx_hash, tracked))
        while (
            len(self._completed_transactions)
            > self._max_completed_transactions
        ):
            expired_hash, expired = self._completed_transactions.popleft()
            if self._transactions.get(expired_hash) is expired:
                self._transactions.pop(expired_hash, None)

    def _prune_resolved_blocks(self) -> None:
        while len(self._block_tasks) > self._max_resolved_blocks:
            expired_hash = next(
                (
                    block_hash
                    for block_hash, task in self._block_tasks.items()
                    if task.done()
                ),
                None,
            )
            if expired_hash is None:
                return
            self._block_tasks.pop(expired_hash, None)
            self._block_transactions.pop(expired_hash, None)
            self._applied_block_transactions.pop(expired_hash, None)

    async def _forget_subscription(
        self,
        tracked: _TrackedTransaction[Any],
    ) -> None:
        subscription_id = tracked.subscription_id
        if subscription_id is None:
            return
        tracked.subscription_id = None
        forget = getattr(self._transport, "forget_subscription", None)
        if callable(forget):
            await forget(subscription_id)

    def prepare_recovery_block(self, block_hash: str) -> None:
        candidates = self._block_transactions.setdefault(block_hash, set())
        for tx_hash, pending in self._active_transactions.items():
            if pending.status in {
                TxStatus.SUBMITTING,
                TxStatus.SUBMITTED,
                TxStatus.RETRACTED,
            }:
                candidates.add(tx_hash)

    async def finalize_ancestor(
        self,
        pending: PendingTransaction[Any],
        finalized_hash: str,
    ) -> None:
        tracked = self._transactions.get(pending.tx_hash)
        if (
            tracked is not None
            and tracked.pending is pending
            and pending.status is TxStatus.IN_BLOCK_SUCCESS
        ):
            self._record_finalization(tracked)
            pending.mark_finalized(block_hash=finalized_hash)

    def mark_reconciliation_required(
        self,
        pending: PendingTransaction[Any],
        *,
        missing_start: int,
        missing_end: int,
        endpoint: str,
    ) -> None:
        tracked = self._transactions.get(pending.tx_hash)
        if (
            tracked is None
            or tracked.pending is not pending
            or pending.status
            not in {
                TxStatus.SUBMITTING,
                TxStatus.SUBMITTED,
                TxStatus.RETRACTED,
                TxStatus.IN_BLOCK_SUCCESS,
            }
        ):
            return
        error = ReconciliationRequired(
            code="RECONCILIATION_REQUIRED",
            stage=TxStage.RECOVERY,
            tx_hash=pending.tx_hash,
            cloid=pending.cloid,
            nonce=pending.nonce,
            elapsed_ms=_elapsed_ms(pending),
            certainty=(
                OutcomeCertainty.INCLUDED
                if pending.status is TxStatus.IN_BLOCK_SUCCESS
                else OutcomeCertainty.UNKNOWN
            ),
            retryable=False,
            suggested_action=(
                f"Query archive RPC or an indexer at {endpoint} for exact "
                f"missing heights {missing_start}-{missing_end}; reconcile by "
                "tx hash/cloid before any resubmission."
            ),
            pending=pending,
            cause=RuntimeError(
                {
                    "missing_start": missing_start,
                    "missing_end": missing_end,
                    "endpoint": endpoint,
                }
            ),
            node_status=pending.node_status,
        )
        error.missing_start = missing_start
        error.missing_end = missing_end
        error.endpoint = endpoint
        pending.mark_reconciliation_required(error)

    async def _open_subscription(
        self,
        tracked: _TrackedTransaction[Any],
    ) -> None:
        try:
            tracked.subscription_id = await self._transport.subscribe(
                "author_submitAndWatchExtrinsic",
                [tracked.encoded.data_hex],
                lambda update: self._handle_status(tracked, update),
            )
            if tracked.pending.status in _TERMINAL_STATUSES:
                await self._forget_subscription(tracked)
        except RPCError as exc:
            reason = _admission_reason(str(exc))
            if reason is None:
                may_have_been_sent = not isinstance(
                    exc, TransportRequestError
                ) or exc.may_have_been_sent
                if may_have_been_sent:
                    raise TransactionError(
                        code="TRANSPORT_AFTER_SEND",
                        stage=TxStage.SUBMISSION,
                        tx_hash=tracked.encoded.tx_hash,
                        cloid=tracked.pending.cloid,
                        nonce=tracked.encoded.nonce,
                        elapsed_ms=_elapsed_ms(tracked.pending),
                        certainty=OutcomeCertainty.UNKNOWN,
                        retryable=False,
                        suggested_action=(
                            "Keep this pending handle and reconcile by tx hash/cloid; "
                            "do not resubmit while the outcome is unknown."
                        ),
                        pending=tracked.pending,
                        cause=exc,
                        node_status=tracked.pending.node_status,
                    ) from exc
                raise TransactionError(
                    code="TRANSPORT_BEFORE_SEND",
                    stage=TxStage.SUBMISSION,
                    tx_hash=tracked.encoded.tx_hash,
                    cloid=tracked.pending.cloid,
                    nonce=tracked.encoded.nonce,
                    elapsed_ms=_elapsed_ms(tracked.pending),
                    certainty=OutcomeCertainty.NOT_SUBMITTED,
                    retryable=True,
                    suggested_action=(
                        "It is safe to reconnect and explicitly submit a newly "
                        "encoded transaction because no bytes were sent."
                    ),
                    pending=tracked.pending,
                    cause=exc,
                    node_status=tracked.pending.node_status,
                ) from exc
            tracked.pending.mark_invalid(
                TransactionInvalid.from_node_reason(
                    reason,
                    tx_hash=tracked.encoded.tx_hash,
                    cloid=tracked.pending.cloid,
                    nonce=tracked.encoded.nonce,
                    elapsed_ms=_elapsed_ms(tracked.pending),
                    pending=tracked.pending,
                    node_status="invalid",
                )
            )

    def _handle_status(
        self,
        tracked: _TrackedTransaction[Any],
        update: object,
    ) -> None:
        if not isinstance(update, dict) or len(update) != 1:
            return
        node_status, value = next(iter(update.items()))
        normalized = node_status.lower()
        if normalized in {"future", "ready", "broadcast"}:
            self._record_acceptance(tracked)
            tracked.pending.mark_submitted(node_status=node_status)
            return
        if normalized == "inblock" and isinstance(value, str):
            received_ns = time.perf_counter_ns()
            self._record_acceptance(tracked, received_ns=received_ns)
            if tracked.pending.status.value in {"created", "submitting"}:
                tracked.pending.mark_submitted(node_status=node_status)
            else:
                tracked.pending.node_status = node_status
            self._record_inclusion(tracked, received_ns)
            self._block_transactions.setdefault(value, set()).add(
                tracked.encoded.tx_hash
            )
            self._spawn(
                self.resolve_block(value),
                name=f"deepx-apply-block-{value}-{tracked.encoded.tx_hash}",
            )
            return
        if normalized == "finalized" and isinstance(value, str):
            received_ns = time.perf_counter_ns()
            self._record_acceptance(tracked, received_ns=received_ns)
            if tracked.pending.status in {TxStatus.CREATED, TxStatus.SUBMITTING}:
                tracked.pending.mark_submitted(node_status=node_status)
            else:
                tracked.pending.node_status = node_status
            self._record_inclusion(tracked, received_ns)
            self._block_transactions.setdefault(value, set()).add(
                tracked.encoded.tx_hash
            )
            self._spawn(
                self._finalize_transaction(tracked, value),
                name=f"deepx-finalize-{value}-{tracked.encoded.tx_hash}",
            )
            return
        if normalized == "finalitytimeout":
            tracked.pending.node_status = node_status
            return
        if normalized == "invalid":
            reason = value if isinstance(value, str) else "invalid"
            tracked.pending.mark_invalid(
                TransactionInvalid.from_node_reason(
                    reason,
                    tx_hash=tracked.encoded.tx_hash,
                    cloid=tracked.pending.cloid,
                    nonce=tracked.encoded.nonce,
                    elapsed_ms=_elapsed_ms(tracked.pending),
                    pending=tracked.pending,
                    node_status=node_status,
                )
            )
            return
        if normalized == "dropped":
            tracked.pending.mark_dropped(
                TransactionDropped(
                    code="TRANSACTION_DROPPED",
                    stage=TxStage.SUBMISSION,
                    tx_hash=tracked.encoded.tx_hash,
                    cloid=tracked.pending.cloid,
                    nonce=tracked.encoded.nonce,
                    elapsed_ms=_elapsed_ms(tracked.pending),
                    certainty=OutcomeCertainty.UNKNOWN,
                    retryable=False,
                    suggested_action=(
                        "Reconcile by tx hash/cloid before rebuilding or retrying."
                    ),
                    pending=tracked.pending,
                    node_status=node_status,
                )
            )
            return
        if normalized == "usurped":
            replacement_hash = value if isinstance(value, str) else None
            error = TransactionUsurped(
                code="TRANSACTION_USURPED",
                stage=TxStage.SUBMISSION,
                tx_hash=tracked.encoded.tx_hash,
                cloid=tracked.pending.cloid,
                nonce=tracked.encoded.nonce,
                elapsed_ms=_elapsed_ms(tracked.pending),
                certainty=OutcomeCertainty.REPLACED,
                retryable=False,
                suggested_action=(
                    "Track the replacement transaction; do not retry the old transaction."
                ),
                pending=tracked.pending,
                cause=RuntimeError({"replacement_hash": replacement_hash}),
                node_status=node_status,
            )
            error.replacement_hash = replacement_hash
            tracked.pending.mark_usurped(error)
            return
        if normalized == "retracted":
            tracked.pending.node_status = node_status
            tracked.pending.mark_retracted()

    async def _finalize_transaction(
        self,
        tracked: _TrackedTransaction[Any],
        block_hash: str,
    ) -> None:
        pending = tracked.pending
        if not (
            pending.status is TxStatus.IN_BLOCK_SUCCESS
            and pending.block_hash == block_hash
        ):
            await self.resolve_block(block_hash)
        if pending.status is TxStatus.IN_BLOCK_SUCCESS:
            self._record_finalization(tracked)
            pending.mark_finalized(block_hash=block_hash)

    async def _fetch_block(self, block_hash: str) -> _ResolvedBlock:
        block_response = await self._transport.request("chain_getBlock", [block_hash])
        extrinsics = _block_extrinsics(block_response)
        known_by_data = {
            self._transactions[tx_hash].encoded.data_hex.lower(): tx_hash
            for tx_hash in self._active_transactions
            if tx_hash in self._transactions
        }
        extrinsic_indexes: dict[str, int] = {}
        for index, raw_extrinsic in enumerate(extrinsics):
            normalized = raw_extrinsic.lower()
            tx_hash = known_by_data.get(normalized) or _hash_extrinsic(normalized)
            extrinsic_indexes[tx_hash] = index

        raw_events_batches = await self._fetch_events_map_batches(block_hash, block_response)
        decode_started_ns = time.perf_counter_ns()
        if raw_events_batches:
            # Multi-threaded runtime: events live in System.EventsMap(number, thread).
            decoded = await self._encoder.decode_system_events_map(raw_events_batches)
        else:
            # Legacy single-thread runtime: events live in System.Events.
            raw_events = await self._transport.request(
                "state_getStorage",
                [self._encoder.snapshot.system_events_storage_key, block_hash],
            )
            if not isinstance(raw_events, str):
                raw_events = "0x"
            decoded = await self._encoder.decode_system_events(raw_events)
        event_decode_ms = (
            time.perf_counter_ns() - decode_started_ns
        ) / 1_000_000
        events = [
            normalized
            for event in decoded
            if (normalized := _native_py._normalize_event_record_item(event))
            is not None
        ]
        return _ResolvedBlock(
            extrinsic_indexes=extrinsic_indexes,
            events=events,
            event_decode_ms=event_decode_ms,
        )

    async def _fetch_events_map_batches(
        self,
        block_hash: str,
        block_response: object,
    ) -> list[str]:
        """Fetch raw System.EventsMap values, one per thread, for this block.

        Returns [] when the runtime has no thread support (older runtimes) so the
        caller falls back to the System.Events single-key path.
        """
        number = _block_number_from_response(block_response)
        if number is None:
            return []
        thread_raw = await self._transport.request(
            "state_getStorage",
            [_native_py._system_threads_storage_key_hex(block_number=number), block_hash],
        )
        thread_count = 0
        if isinstance(thread_raw, str) and thread_raw:
            raw = _native_py._decode_hex_bytes(thread_raw)
            thread_count = raw[0] if raw else 0
        batches: list[str] = []
        for thread_id in range(max(0, thread_count) + 1):
            raw = await self._transport.request(
                "state_getStorage",
                [
                    _native_py._system_events_map_storage_key_hex(
                        block_number=number, thread_id=thread_id
                    ),
                    block_hash,
                ],
            )
            if isinstance(raw, str) and raw:
                batches.append(raw)
        return batches

    @staticmethod
    def _record_acceptance(
        tracked: _TrackedTransaction[Any],
        *,
        received_ns: int | None = None,
    ) -> None:
        if tracked.accepted_ns is not None:
            return
        accepted_ns = received_ns or time.perf_counter_ns()
        tracked.accepted_ns = accepted_ns
        tracked.pending.timings.rpc_submit_ms = (
            accepted_ns - tracked.submit_started_ns
        ) / 1_000_000

    @staticmethod
    def _record_inclusion(
        tracked: _TrackedTransaction[Any],
        received_ns: int,
    ) -> None:
        tracked.in_block_received_ns = received_ns
        if tracked.accepted_ns is None:
            return
        elapsed_ms = (received_ns - tracked.accepted_ns) / 1_000_000
        tracked.pending.timings.pool_wait_ms = elapsed_ms
        tracked.pending.timings.inclusion_ms = elapsed_ms

    @staticmethod
    def _record_finalization(tracked: _TrackedTransaction[Any]) -> None:
        if tracked.in_block_completed_ns is None:
            return
        tracked.pending.timings.finalization_ms = (
            time.perf_counter_ns() - tracked.in_block_completed_ns
        ) / 1_000_000

    def _spawn(self, awaitable: Any, *, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(awaitable, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(_consume_task_exception)
        return task


def _block_extrinsics(block_response: object) -> list[str]:
    if not isinstance(block_response, dict):
        return []
    block = block_response.get("block")
    if not isinstance(block, dict):
        return []
    extrinsics = block.get("extrinsics")
    if not isinstance(extrinsics, list):
        return []
    return [item for item in extrinsics if isinstance(item, str)]


def _block_number_from_response(block_response: object) -> int | None:
    if not isinstance(block_response, dict):
        return None
    block = block_response.get("block")
    if not isinstance(block, dict):
        return None
    header = block.get("header")
    if not isinstance(header, dict):
        return None
    number = header.get("number")
    if not isinstance(number, str):
        return None
    try:
        return int(number, 16)
    except ValueError:
        return None


def _hash_extrinsic(data_hex: str) -> str:
    payload = data_hex[2:] if data_hex.startswith("0x") else data_hex
    try:
        raw = bytes.fromhex(payload)
    except ValueError:
        raw = data_hex.encode()
    return "0x" + hashlib.blake2b(raw, digest_size=32).hexdigest()


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


def _elapsed_ms(pending: PendingTransaction[Any]) -> int:
    return int(pending.diagnostics()["elapsed_ms"])


def _non_negative_limit(value: int, *, name: str) -> int:
    limit = int(value)
    if limit < 0:
        raise ValueError(f"{name} must be non-negative")
    return limit


def _admission_reason(message: str) -> str | None:
    for marker in (
        "ExceedPoolLimit",
        "Payment",
        "TimeStale",
        "Future",
        "BadSigner",
    ):
        position = message.find(marker)
        if position >= 0:
            return message[position:]
    return None


__all__ = ["ExpectedEvent", "TransactionTracker"]
