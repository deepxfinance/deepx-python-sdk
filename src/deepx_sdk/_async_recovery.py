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
    # marked reconciliation-required so its pool slot is released and the
    # caller is told to reconcile. Deliberately much larger than the
    # inclusion wait timeout: slow-but-successful inclusions (degraded
    # the dev deployment has shown 30-50s) must not be falsely flagged.
    stale_ms: int = 60_000


class RecoveryTracker:
    def __init__(
        self,
        transport: AsyncRpcTransport,
        tracker: TransactionTracker,
        encoder: ExtrinsicEncoder,
        *,
        config: RecoveryConfig | None = None,
    ) -> None:
        self._transport = transport
        self._tracker = tracker
        self._encoder = encoder
        self._config = config or RecoveryConfig()
        self._started = False
        self._closed = False
        self._recovery_lock = asyncio.Lock()
        self._runtime_refresh_lock = asyncio.Lock()
        self._subscriptions: dict[str, str] = {}
        self._last_best_number: int | None = None
        self._last_best_hash: str | None = None
        self._last_finalized_number: int | None = None
        self._last_finalized_hash: str | None = None
        self._runtime_spec_version: int | None = None
        self._pending_best_number: int | None = None
        self._scan_task: asyncio.Task[None] | None = None
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
        self._transport.add_connection_state_callback(
            self._handle_connection_state
        )
        await self._subscribe_all()
        self._last_head_at = time.monotonic()
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        self._last_best_hash, self._last_best_number = await self._read_best_head()
        self._last_head_number = self._last_best_number
        (
            self._last_finalized_hash,
            self._last_finalized_number,
        ) = await self._read_finalized_head()
        self._runtime_spec_version = int(self._encoder.snapshot.runtime_version)
        await self._reconcile_finalized(
            self._last_finalized_hash,
            self._last_finalized_number,
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
        watchdog_task = self._watchdog_task
        self._watchdog_task = None
        for task in (scan_task, watchdog_task):
            if task is not None and not task.done():
                task.cancel()
        self._transport.remove_connection_state_callback(
            self._handle_connection_state
        )
        self._transport.disable_reconnect()
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
                self._transport,
                "endpoint",
                "<configured endpoint>",
            )
            for pending in overdue:
                self._tracker.mark_reconciliation_required(
                    pending,
                    missing_start=start,
                    missing_end=end,
                    endpoint=endpoint,
                )
            self._last_best_number = end
            self._last_best_hash = await self._block_hash(end)
            return

        for number in range(start, end + 1):
            block_hash = await self._block_hash(number)
            self._tracker.prepare_recovery_block(block_hash)
            await self._tracker.resolve_block(block_hash)
            self._last_best_number = number
            self._last_best_hash = block_hash
        self._flag_stale_pending(start, end)

    def _flag_stale_pending(self, start: int, end: int) -> None:
        # The scan just covered every block through `end`, so any transaction
        # still pending beyond the stale horizon was NOT included (most often
        # a timestamp-nonce tx the node silently dropped after it expired).
        # Mark it reconciliation-required: this releases its pool slot via
        # the client's status callback and tells the caller to reconcile by
        # tx_hash/cloid instead of waiting forever.
        stale = [
            pending
            for pending in self._tracker.pending_transactions
            if int(pending.diagnostics()["elapsed_ms"]) >= self._config.stale_ms
        ]
        if not stale:
            return
        endpoint = getattr(
            self._transport,
            "endpoint",
            "<configured endpoint>",
        )
        for pending in stale:
            logger.warning(
                "Pending transaction %s unresolved after %dms; marking "
                "reconciliation-required",
                getattr(pending, "tx_hash", "<unknown>"),
                self._config.stale_ms,
            )
            self._tracker.mark_reconciliation_required(
                pending,
                missing_start=start,
                missing_end=end,
                endpoint=endpoint,
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
                    if previous is None or current_number <= previous:
                        continue
                    await self._scan_best_chain(previous + 1, current_number)
                    self._last_best_hash = current_hash
                    self._last_best_number = current_number
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
        self._pending_best_number = number
        if self._scan_task is None or self._scan_task.done():
            self._scan_task = asyncio.create_task(self._drain_best_scans())

    async def _drain_best_scans(self) -> None:
        # Coalesce bursts of heads into as few catch-up scans as possible:
        # re-check after each scan whether newer heads arrived in the meantime.
        async with self._recovery_lock:
            if self._closed:
                return
            while not self._closed:
                target = self._pending_best_number
                previous = self._last_best_number
                if target is None or (previous is not None and target <= previous):
                    return
                if previous is None:
                    self._last_best_number = target
                    self._last_best_hash = await self._block_hash(target)
                else:
                    await self._scan_best_chain(previous + 1, target)

    async def _handle_finalized_head(self, update: object) -> None:
        number = _head_number(update)
        if number is None:
            return
        block_hash = await self._block_hash(number)
        self._last_finalized_hash = block_hash
        self._last_finalized_number = number
        await self._reconcile_finalized(block_hash, number)

    async def _reconcile_finalized(
        self,
        finalized_hash: str,
        finalized_number: int,
    ) -> None:
        endpoint = getattr(
            self._transport,
            "endpoint",
            "<configured endpoint>",
        )
        for pending in self._tracker.pending_transactions:
            if (
                pending.status is not TxStatus.IN_BLOCK_SUCCESS
                or pending.block_hash is None
            ):
                continue
            header = await self._transport.request(
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
                    missing_start=included_number,
                    missing_end=included_number,
                    endpoint=endpoint,
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
        header = await self._transport.request(
            "chain_getHeader",
            [block_hash],
        )
        number = _head_number(header)
        if number is None:
            raise RuntimeError("Current best header has no valid block number.")
        return block_hash, number

    async def _read_finalized_head(self) -> tuple[str, int]:
        block_hash = await self._transport.request(
            "chain_getFinalizedHead",
            [],
        )
        if not isinstance(block_hash, str):
            raise RuntimeError("Finalized-head RPC returned no block hash.")
        header = await self._transport.request(
            "chain_getHeader",
            [block_hash],
        )
        number = _head_number(header)
        if number is None:
            raise RuntimeError("Finalized header has no valid block number.")
        return block_hash, number

    async def _block_hash(self, number: int | None) -> str:
        params: list[object] = [] if number is None else [number]
        block_hash = await self._transport.request(
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
