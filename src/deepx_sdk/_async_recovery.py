"""Reconnect recovery and runtime-version coordination."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from ._async_encoder import ExtrinsicEncoder
from ._async_tracker import TransactionTracker
from ._async_transport import AsyncRpcTransport, ConnectionState
from ._pending_tx import PendingTransaction, TxStatus
from ._tx_diagnostics import (
    OutcomeCertainty,
    TransactionNotIncluded,
    TxStage,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoveryConfig:
    max_blocks: int = 256
    reconnect_initial_ms: int = 100
    reconnect_max_ms: int = 3_000
    # Watchdog: drives catch-up scans on a timer so reconciliation keeps
    # working when the heads subscription silently dies (dropped after a
    # notification-queue overflow, or evicted server-side during a stall).
    watchdog_interval_s: float = 5.0
    # If RPC shows the chain advancing but no head notification arrived for
    # this long, the heads subscription is presumed dead and re-established.
    subscription_liveness_s: float = 15.0
    # A pending transaction that stays unresolved this long — with full
    # block visibility, i.e. the scanner could have seen its inclusion — is
    # checked against the submission node's pool after a complete finalized
    # scan. Deliberately much larger than the
    # inclusion wait timeout: slow-but-successful inclusions (degraded
    # the dev deployment has shown 30-50s) must not be falsely flagged.
    stale_ms: int = 60_000
    scan_concurrency: int = 8


class RecoveryTracker:
    def __init__(
        self,
        transport: AsyncRpcTransport,
        tracker: TransactionTracker,
        encoder: ExtrinsicEncoder,
        *,
        scan_transport: AsyncRpcTransport | None = None,
        pool_transport: AsyncRpcTransport | None = None,
        config: RecoveryConfig | None = None,
    ) -> None:
        self._transport = transport
        self._scan_transport = scan_transport or transport
        self._tracker = tracker
        self._encoder = encoder
        self._pool_transport = pool_transport or transport
        self._config = config or RecoveryConfig()
        if self._config.scan_concurrency < 1:
            raise ValueError("Recovery scan_concurrency must be at least 1.")
        self._started = False
        self._closed = False
        self._recovery_lock = asyncio.Lock()
        self._runtime_refresh_lock = asyncio.Lock()
        self._subscriptions: dict[str, str] = {}
        self._last_best_number: int | None = None
        self._last_best_hash: str | None = None
        self._last_finalized_number: int | None = None
        self._last_finalized_hash: str | None = None
        self._last_finalized_scan_number: int | None = None
        self._runtime_spec_version: int | None = None
        self._pending_best_number: int | None = None
        self._pending_finalized_number: int | None = None
        self._scan_task: asyncio.Task[None] | None = None
        self._finalized_scan_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._last_head_at: float | None = None
        self._last_head_number: int | None = None

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("Recovery tracker is closed.")
        self._started = True
        self._transport.enable_reconnect(
            initial_ms=self._config.reconnect_initial_ms,
            max_ms=self._config.reconnect_max_ms,
        )
        if self._scan_transport is not self._transport:
            self._scan_transport.enable_reconnect(
                initial_ms=self._config.reconnect_initial_ms,
                max_ms=self._config.reconnect_max_ms,
            )
        self._transport.add_connection_state_callback(
            self._handle_connection_state
        )
        await self._validate_chain_identity()
        await self._subscribe_all()
        self._last_head_at = time.monotonic()
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        self._last_best_hash, self._last_best_number = await self._read_best_head()
        self._last_head_number = self._last_best_number
        (
            self._last_finalized_hash,
            self._last_finalized_number,
        ) = await self._read_finalized_head()
        self._last_finalized_scan_number = self._last_finalized_number
        self._runtime_spec_version = int(self._encoder.snapshot.runtime_version)
        await self._reconcile_finalized(
            self._last_finalized_hash,
            self._last_finalized_number,
        )

    async def _validate_chain_identity(self) -> None:
        transports = []
        for transport in (
            self._transport,
            self._scan_transport,
            self._pool_transport,
        ):
            if all(transport is not existing for existing in transports):
                transports.append(transport)
        if len(transports) == 1:
            return
        genesis_hashes = await asyncio.gather(
            *(
                transport.request("chain_getBlockHash", [0])
                for transport in transports
            )
        )
        if any(not isinstance(value, str) for value in genesis_hashes) or len(
            {str(value).lower() for value in genesis_hashes}
        ) != 1:
            raise RuntimeError(
                "Submission, recovery subscription, and recovery scan RPC "
                "transports do not serve the same chain."
            )

    async def reconcile(
        self,
        pending: PendingTransaction[Any],
    ) -> PendingTransaction[Any]:
        if self._closed:
            raise RuntimeError("Recovery tracker is closed.")
        await self._recover(resubscribe=False)
        return pending

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        scan_task = self._scan_task
        self._scan_task = None
        finalized_scan_task = self._finalized_scan_task
        self._finalized_scan_task = None
        watchdog_task = self._watchdog_task
        self._watchdog_task = None
        background_tasks = (scan_task, finalized_scan_task, watchdog_task)
        for task in background_tasks:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in background_tasks if task is not None),
            return_exceptions=True,
        )
        self._transport.remove_connection_state_callback(
            self._handle_connection_state
        )
        self._transport.disable_reconnect()
        if self._scan_transport is not self._transport:
            self._scan_transport.disable_reconnect()
        subscriptions = self._subscriptions
        self._subscriptions = {}
        unsubscribe_methods = {
            "chain_subscribeNewHeads": "chain_unsubscribeNewHeads",
            "chain_subscribeFinalizedHeads": "chain_unsubscribeFinalizedHeads",
            "state_subscribeRuntimeVersion": "state_unsubscribeRuntimeVersion",
        }
        for method, subscription_id in subscriptions.items():
            try:
                await self._transport.unsubscribe(
                    unsubscribe_methods[method],
                    subscription_id,
                )
            except Exception as exc:
                logger.error(
                    "Recovery subscription cleanup failed for %s: %s",
                    method,
                    type(exc).__name__,
                )

    async def _subscribe_all(self) -> None:
        self._subscriptions = {
            "chain_subscribeNewHeads": await self._transport.subscribe(
                "chain_subscribeNewHeads",
                [],
                self._handle_new_head,
            ),
            "chain_subscribeFinalizedHeads": await self._transport.subscribe(
                "chain_subscribeFinalizedHeads",
                [],
                self._handle_finalized_head,
            ),
            "state_subscribeRuntimeVersion": await self._transport.subscribe(
                "state_subscribeRuntimeVersion",
                [],
                self._handle_runtime_version,
            ),
        }

    async def _handle_connection_state(self, state: ConnectionState) -> None:
        if state is ConnectionState.RECOVERING and not self._closed:
            await self._recover(resubscribe=True)

    async def _recover(self, *, resubscribe: bool) -> None:
        async with self._recovery_lock:
            if self._closed:
                return
            if resubscribe:
                await self._validate_chain_identity()
                await self._subscribe_all()

            current_hash, current_number = await self._read_best_head()
            previous = self._last_best_number
            if previous is None:
                self._last_best_hash = current_hash
                self._last_best_number = current_number
            elif current_number > previous:
                await self._scan_best_chain(
                    previous + 1,
                    current_number,
                )
                self._last_best_hash = current_hash
                self._last_best_number = current_number

            finalized_hash, finalized_number = await self._read_finalized_head()
            await self._scan_finalized_chain(finalized_number)
            self._last_finalized_hash = finalized_hash
            self._last_finalized_number = finalized_number
            await self._reconcile_finalized(finalized_hash, finalized_number)

    async def _scan_best_chain(self, start: int, end: int) -> None:
        if not self._tracker.pending_transactions:
            # Nothing to reconcile — don't walk blocks over RPC just to track
            # the head; fast-forward (one RPC for the hash) instead.
            self._last_best_number = end
            self._last_best_hash = await self._block_hash(end)
            return
        gap = end - start + 1
        if gap > self._config.max_blocks:
            # The window can't be covered (high block rate / slow RPC). Only
            # flag transactions that have actually blown their inclusion
            # deadline — freshly pending txs may still land via their own
            # subscription, and marking them would be a false ACTION_REQUIRED.
            overdue = [
                pending
                for pending in self._tracker.pending_transactions
                if int(pending.diagnostics()["elapsed_ms"]) >= pending.timeouts.inclusion_ms
            ]
            if not overdue:
                self._last_best_number = end
                self._last_best_hash = await self._block_hash(end)
                return
            endpoint = getattr(
                self._scan_transport,
                "endpoint",
                "<configured endpoint>",
            )
            for pending in overdue:
                self._tracker.mark_reconciliation_required(
                    pending,
                    missing_start=start,
                    missing_end=end,
                    endpoint=endpoint,
                    reason_code="BEST_SCAN_GAP",
                    scan_start=start,
                    scan_end=end,
                    finalized_head=self._last_finalized_number,
                )
            self._last_best_number = end
            self._last_best_hash = await self._block_hash(end)
            return

        for batch_start in range(start, end + 1, self._config.scan_concurrency):
            numbers = list(
                range(
                    batch_start,
                    min(end + 1, batch_start + self._config.scan_concurrency),
                )
            )
            results = await asyncio.gather(
                *(self._resolve_block_number(number) for number in numbers),
                return_exceptions=True,
            )
            for number, result in zip(numbers, results, strict=True):
                if isinstance(result, BaseException):
                    raise result
                self._last_best_number = number
                self._last_best_hash = result

    async def _scan_finalized_chain(self, finalized_number: int) -> None:
        previous = self._last_finalized_scan_number
        if previous is None:
            self._last_finalized_scan_number = finalized_number
            return
        if finalized_number <= previous:
            return
        start = previous + 1
        end = finalized_number
        pending_transactions = self._tracker.pending_transactions
        if not pending_transactions:
            self._last_finalized_scan_number = end
            return

        endpoint = str(
            getattr(self._scan_transport, "endpoint", "<configured endpoint>")
        )
        if end - start + 1 > self._config.max_blocks:
            self._record_incomplete_finalized_scan(
                start=start,
                end=end,
                finalized_head=finalized_number,
                endpoint=endpoint,
                reason_code="FINALIZED_SCAN_GAP",
            )
            return

        for batch_start in range(start, end + 1, self._config.scan_concurrency):
            numbers = list(
                range(
                    batch_start,
                    min(end + 1, batch_start + self._config.scan_concurrency),
                )
            )
            results = await asyncio.gather(
                *(self._resolve_block_number(number) for number in numbers),
                return_exceptions=True,
            )
            for number, result in zip(numbers, results, strict=True):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, BaseException):
                    self._record_incomplete_finalized_scan(
                        start=number,
                        end=end,
                        finalized_head=finalized_number,
                        endpoint=endpoint,
                        reason_code="FINALIZED_SCAN_RPC_ERROR",
                        cause=result,
                    )
                    return
                self._last_finalized_scan_number = number

        self._record_complete_finalized_scan(
            start=start,
            end=end,
            finalized_head=finalized_number,
            endpoint=endpoint,
        )
        await self._classify_stale_pending(
            start=start,
            end=end,
            finalized_head=finalized_number,
            recovery_endpoint=endpoint,
        )

    async def _resolve_block_number(self, number: int) -> str:
        block_hash = await self._block_hash(number)
        self._tracker.prepare_recovery_block(block_hash)
        await self._tracker.resolve_block(
            block_hash,
            transport=self._scan_transport,
        )
        return block_hash

    def _record_complete_finalized_scan(
        self,
        *,
        start: int,
        end: int,
        finalized_head: int,
        endpoint: str,
    ) -> None:
        for pending in self._tracker.pending_transactions:
            diagnostics = pending.recovery
            if diagnostics.scan_start is None:
                diagnostics.scan_start = start
            diagnostics.scan_end = end
            diagnostics.finalized_head = finalized_head
            diagnostics.scan_complete = True
            diagnostics.missing_ranges = []
            diagnostics.recovery_endpoint = endpoint

    def _record_incomplete_finalized_scan(
        self,
        *,
        start: int,
        end: int,
        finalized_head: int,
        endpoint: str,
        reason_code: str,
        cause: BaseException | None = None,
    ) -> None:
        for pending in self._tracker.pending_transactions:
            diagnostics = pending.recovery
            diagnostics.reason_code = reason_code
            if diagnostics.scan_start is None:
                diagnostics.scan_start = start
            diagnostics.scan_end = end
            diagnostics.finalized_head = finalized_head
            diagnostics.scan_complete = False
            diagnostics.missing_ranges = [(start, end)]
            diagnostics.recovery_endpoint = endpoint
            if cause is not None:
                diagnostics.add_rpc_error(method="finalized_scan", error=cause)
            if int(pending.diagnostics()["elapsed_ms"]) < self._config.stale_ms:
                continue
            self._tracker.mark_reconciliation_required(
                pending,
                missing_start=start,
                missing_end=end,
                endpoint=endpoint,
                reason_code=reason_code,
                scan_start=diagnostics.scan_start,
                scan_end=end,
                finalized_head=finalized_head,
                cause=cause,
            )

    async def _classify_stale_pending(
        self,
        *,
        start: int,
        end: int,
        finalized_head: int,
        recovery_endpoint: str,
    ) -> None:
        stale = [
            pending
            for pending in self._tracker.pending_transactions
            if pending.status
            in {TxStatus.SUBMITTING, TxStatus.SUBMITTED, TxStatus.RETRACTED}
            if int(pending.diagnostics()["elapsed_ms"]) >= self._config.stale_ms
        ]
        if not stale:
            return

        try:
            current_hash, current_number = await self._read_best_head()
            previous = self._last_best_number
            if previous is not None and current_number > previous:
                await self._scan_best_chain(previous + 1, current_number)
                self._last_best_hash = current_hash
                self._last_best_number = current_number
        except Exception as exc:
            for pending in stale:
                pending.recovery.add_rpc_error(method="best_scan", error=exc)
                self._tracker.mark_reconciliation_required(
                    pending,
                    endpoint=recovery_endpoint,
                    reason_code="BEST_SCAN_RPC_ERROR",
                    scan_start=pending.recovery.scan_start,
                    scan_end=end,
                    finalized_head=finalized_head,
                    scan_complete=False,
                    cause=exc,
                )
            return

        stale = [
            pending
            for pending in self._tracker.pending_transactions
            if pending.status
            in {TxStatus.SUBMITTING, TxStatus.SUBMITTED, TxStatus.RETRACTED}
            if int(pending.diagnostics()["elapsed_ms"]) >= self._config.stale_ms
        ]
        if not stale:
            return

        pool_endpoint = str(
            getattr(self._pool_transport, "endpoint", "<configured endpoint>")
        )
        try:
            raw_pending = await self._pool_transport.request(
                "author_pendingExtrinsics",
                [],
            )
        except Exception as exc:
            for pending in stale:
                self._mark_pool_action_required(
                    pending,
                    result="unavailable",
                    reason_code="PENDING_POOL_UNAVAILABLE",
                    pool_endpoint=pool_endpoint,
                    recovery_endpoint=recovery_endpoint,
                    scan_start=start,
                    scan_end=end,
                    finalized_head=finalized_head,
                    cause=exc,
                )
            return

        for pending in stale:
            submission_endpoint = pending.recovery.submission_endpoint
            if submission_endpoint is None:
                self._mark_pool_action_required(
                    pending,
                    result="submission_endpoint_unknown",
                    reason_code="SUBMISSION_ENDPOINT_UNKNOWN",
                    pool_endpoint=pool_endpoint,
                    recovery_endpoint=recovery_endpoint,
                    scan_start=start,
                    scan_end=end,
                    finalized_head=finalized_head,
                )
                continue
            if submission_endpoint != pool_endpoint:
                self._mark_pool_action_required(
                    pending,
                    result="source_mismatch",
                    reason_code="PENDING_POOL_SOURCE_MISMATCH",
                    pool_endpoint=pool_endpoint,
                    recovery_endpoint=recovery_endpoint,
                    scan_start=start,
                    scan_end=end,
                    finalized_head=finalized_head,
                )
                continue
            try:
                in_pool = self._tracker.pending_pool_contains(pending, raw_pending)
            except Exception as exc:
                self._mark_pool_action_required(
                    pending,
                    result="invalid_response",
                    reason_code="PENDING_POOL_INVALID_RESPONSE",
                    pool_endpoint=pool_endpoint,
                    recovery_endpoint=recovery_endpoint,
                    scan_start=start,
                    scan_end=end,
                    finalized_head=finalized_head,
                    cause=exc,
                )
                continue
            self._record_pool_diagnostics(
                pending,
                result="present" if in_pool else "absent",
                pool_endpoint=pool_endpoint,
                recovery_endpoint=recovery_endpoint,
                scan_start=start,
                scan_end=end,
                finalized_head=finalized_head,
            )
            if in_pool:
                pending.recovery.reason_code = "PENDING_IN_POOL"
                continue
            pending.recovery.reason_code = "NOT_INCLUDED_FINALIZED"
            pending.mark_not_included(
                TransactionNotIncluded(
                    code="TRANSACTION_NOT_INCLUDED",
                    stage=TxStage.RECOVERY,
                    tx_hash=pending.tx_hash,
                    cloid=pending.cloid,
                    nonce=pending.nonce,
                    elapsed_ms=int(pending.diagnostics()["elapsed_ms"]),
                    certainty=OutcomeCertainty.NOT_INCLUDED,
                    retryable=False,
                    suggested_action=(
                        "The transaction was absent from the complete finalized scan "
                        "and the observed submission-node pool. Reconcile the indexer "
                        "by tx hash/cloid before rebuilding."
                    ),
                    pending=pending,
                    node_status=pending.node_status,
                )
            )

    @staticmethod
    def _record_pool_diagnostics(
        pending: PendingTransaction[Any],
        *,
        result: str,
        pool_endpoint: str,
        recovery_endpoint: str,
        scan_start: int,
        scan_end: int,
        finalized_head: int,
    ) -> None:
        diagnostics = pending.recovery
        if diagnostics.scan_start is None:
            diagnostics.scan_start = scan_start
        diagnostics.scan_end = scan_end
        diagnostics.finalized_head = finalized_head
        diagnostics.scan_complete = True
        diagnostics.missing_ranges = []
        diagnostics.recovery_endpoint = recovery_endpoint
        diagnostics.pending_pool_checked = True
        diagnostics.pending_pool_endpoint = pool_endpoint
        diagnostics.pending_pool_result = result

    def _mark_pool_action_required(
        self,
        pending: PendingTransaction[Any],
        *,
        result: str,
        reason_code: str,
        pool_endpoint: str,
        recovery_endpoint: str,
        scan_start: int,
        scan_end: int,
        finalized_head: int,
        cause: BaseException | None = None,
    ) -> None:
        self._record_pool_diagnostics(
            pending,
            result=result,
            pool_endpoint=pool_endpoint,
            recovery_endpoint=recovery_endpoint,
            scan_start=scan_start,
            scan_end=scan_end,
            finalized_head=finalized_head,
        )
        if cause is not None:
            pending.recovery.add_rpc_error(
                method="author_pendingExtrinsics",
                error=cause,
            )
        self._tracker.mark_reconciliation_required(
            pending,
            endpoint=recovery_endpoint,
            reason_code=reason_code,
            scan_start=pending.recovery.scan_start,
            scan_end=scan_end,
            finalized_head=finalized_head,
            scan_complete=True,
            cause=cause,
        )

    async def _watchdog_loop(self) -> None:
        # Head-driven scans stop when the heads subscription silently dies.
        # This timer keeps reconciliation alive through the same
        # _scan_best_chain path, and re-subscribes when RPC shows the chain
        # advancing while no head arrives. A chain that is genuinely stalled
        # (best number not advancing) is left alone — no heads can arrive
        # anyway, and the next iteration picks up the recovery for free.
        while not self._closed:
            await asyncio.sleep(self._config.watchdog_interval_s)
            if self._closed:
                return
            last_head_at = self._last_head_at
            silent_for = (
                time.monotonic() - last_head_at if last_head_at is not None else 0.0
            )
            if (
                silent_for < self._config.subscription_liveness_s
                and not self._tracker.pending_transactions
            ):
                continue  # healthy and nothing to reconcile
            try:
                async with self._recovery_lock:
                    if self._closed:
                        return
                    current_hash, current_number = await self._read_best_head()
                    previous = self._last_best_number
                    # The subscription is dead when it stayed silent for the
                    # whole liveness window while the chain moved past the
                    # last head it delivered. Check against the last HEAD
                    # number (not last scan): a watchdog scan catching up
                    # must not mask the fact that heads stopped arriving.
                    last_head_number = self._last_head_number
                    subscription_dead = (
                        silent_for >= self._config.subscription_liveness_s
                        and last_head_number is not None
                        and current_number > last_head_number
                    )
                    if subscription_dead:
                        logger.warning(
                            "Heads subscription silent for %.1fs while the chain "
                            "advanced from %d to %d; re-subscribing",
                            silent_for,
                            last_head_number,
                            current_number,
                        )
                        await self._subscribe_all()
                        self._last_head_at = time.monotonic()
                        self._last_head_number = current_number
                    if previous is not None and current_number > previous:
                        await self._scan_best_chain(previous + 1, current_number)
                        self._last_best_hash = current_hash
                        self._last_best_number = current_number
                    finalized_hash, finalized_number = await self._read_finalized_head()
                    await self._scan_finalized_chain(finalized_number)
                    self._last_finalized_hash = finalized_hash
                    self._last_finalized_number = finalized_number
                    await self._reconcile_finalized(
                        finalized_hash,
                        finalized_number,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Recovery watchdog iteration failed")

    async def _handle_new_head(self, update: object) -> None:
        # Must return fast: the transport drops the subscription when its
        # notification queue overflows (the dev deployment produces ~14 heads/sec, and a
        # catch-up scan through a slow RPC link takes far longer per block).
        self._last_head_at = time.monotonic()
        number = _head_number(update)
        if number is None:
            return
        self._last_head_number = number
        self._pending_best_number = max(
            self._pending_best_number or number,
            number,
        )
        if self._scan_task is None or self._scan_task.done():
            self._scan_task = asyncio.create_task(
                self._drain_best_scans(),
                name="deepx-recovery-best-scan",
            )

    async def _drain_best_scans(self) -> None:
        # Coalesce bursts of heads into as few catch-up scans as possible:
        # re-check after each scan whether newer heads arrived in the meantime.
        try:
            async with self._recovery_lock:
                if self._closed:
                    return
                while not self._closed:
                    target = self._pending_best_number
                    previous = self._last_best_number
                    if target is None or (
                        previous is not None and target <= previous
                    ):
                        return
                    if previous is None:
                        self._last_best_number = target
                        self._last_best_hash = await self._block_hash(target)
                    else:
                        await self._scan_best_chain(previous + 1, target)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Recovery best-head scan failed")

    async def _handle_finalized_head(self, update: object) -> None:
        # Finalized heads can arrive as quickly as best heads. Keep this
        # subscription callback O(1); scanning belongs to the coalescing task.
        number = _head_number(update)
        if number is None:
            return
        self._pending_finalized_number = max(
            self._pending_finalized_number or number,
            number,
        )
        if self._finalized_scan_task is None or self._finalized_scan_task.done():
            self._finalized_scan_task = asyncio.create_task(
                self._drain_finalized_scans(),
                name="deepx-recovery-finalized-scan",
            )

    async def _drain_finalized_scans(self) -> None:
        try:
            async with self._recovery_lock:
                if self._closed:
                    return
                while not self._closed:
                    target = self._pending_finalized_number
                    previous = self._last_finalized_number
                    if target is None or (
                        previous is not None and target <= previous
                    ):
                        return
                    block_hash = await self._block_hash(target)
                    await self._scan_finalized_chain(target)
                    self._last_finalized_hash = block_hash
                    self._last_finalized_number = target
                    await self._reconcile_finalized(block_hash, target)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Recovery finalized-head scan failed")

    async def _reconcile_finalized(
        self,
        finalized_hash: str,
        finalized_number: int,
    ) -> None:
        endpoint = getattr(
            self._scan_transport,
            "endpoint",
            "<configured endpoint>",
        )
        for pending in self._tracker.pending_transactions:
            if (
                pending.status is not TxStatus.IN_BLOCK_SUCCESS
                or pending.block_hash is None
            ):
                continue
            header = await self._scan_transport.request(
                "chain_getHeader",
                [pending.block_hash],
            )
            included_number = _head_number(header)
            if included_number is None or included_number > finalized_number:
                continue
            canonical_hash = await self._block_hash(included_number)
            if canonical_hash != pending.block_hash:
                self._tracker.mark_reconciliation_required(
                    pending,
                    endpoint=endpoint,
                    reason_code="FINALIZED_CANONICAL_MISMATCH",
                    scan_start=included_number,
                    scan_end=included_number,
                    finalized_head=finalized_number,
                )
                continue
            await self._tracker.finalize_ancestor(pending, finalized_hash)

    async def _handle_runtime_version(self, update: object) -> None:
        spec_version = _spec_version(update)
        if spec_version is None or spec_version == self._runtime_spec_version:
            return
        async with self._runtime_refresh_lock:
            current_version = int(self._encoder.snapshot.runtime_version)
            if spec_version <= current_version:
                self._runtime_spec_version = current_version
                return
            old_snapshot = self._encoder.snapshot
            async with old_snapshot.runtime_lock:
                await self._encoder.refresh()
            self._runtime_spec_version = int(
                self._encoder.snapshot.runtime_version
            )

    async def _read_best_head(self) -> tuple[str, int]:
        block_hash = await self._block_hash(None)
        header = await self._scan_transport.request(
            "chain_getHeader",
            [block_hash],
        )
        number = _head_number(header)
        if number is None:
            raise RuntimeError("Current best header has no valid block number.")
        return block_hash, number

    async def _read_finalized_head(self) -> tuple[str, int]:
        block_hash = await self._scan_transport.request(
            "chain_getFinalizedHead",
            [],
        )
        if not isinstance(block_hash, str):
            raise RuntimeError("Finalized-head RPC returned no block hash.")
        header = await self._scan_transport.request(
            "chain_getHeader",
            [block_hash],
        )
        number = _head_number(header)
        if number is None:
            raise RuntimeError("Finalized header has no valid block number.")
        return block_hash, number

    async def _block_hash(self, number: int | None) -> str:
        params: list[object] = [] if number is None else [number]
        block_hash = await self._scan_transport.request(
            "chain_getBlockHash",
            params,
        )
        if not isinstance(block_hash, str):
            raise RuntimeError("Block-hash RPC returned no block hash.")
        return block_hash


def _head_number(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("number")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw, 16) if raw.startswith("0x") else int(raw)
        except ValueError:
            return None
    return None


def _spec_version(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("specVersion")
    if isinstance(raw, bool):
        return None
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


__all__ = ["RecoveryConfig", "RecoveryTracker"]
