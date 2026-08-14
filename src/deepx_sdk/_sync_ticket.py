"""Blocking transaction tickets backed by one client-owned asyncio loop."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar, cast

from ._pending_tx import (
    ExecutionState,
    PendingTransaction,
    TransactionSnapshot,
    TxStatus,
)
from ._tx_diagnostics import TransactionError


ResultT = TypeVar("ResultT")


class SyncTransactionTicket(Generic[ResultT]):
    """A synchronous view of a transaction tracked by the async engine."""

    def __init__(
        self,
        runtime: _SyncTicketRuntime,
        pending: PendingTransaction[ResultT],
    ) -> None:
        self._runtime = runtime
        self._pending = pending
        self.tx_hash = pending.tx_hash
        self.nonce = pending.nonce
        self.cloid = pending.cloid

    @property
    def state(self) -> ExecutionState:
        return self._runtime.read(lambda: self._pending.state)

    @property
    def status(self) -> TxStatus:
        return self._runtime.read(lambda: self._pending.status)

    @property
    def node_status(self) -> str | None:
        return self._runtime.read(lambda: self._pending.node_status)

    @property
    def block_hash(self) -> str | None:
        return self._runtime.read(lambda: self._pending.block_hash)

    @property
    def extrinsic_hash(self) -> str | None:
        return self._runtime.read(lambda: self._pending.extrinsic_hash)

    @property
    def error(self) -> TransactionError | None:
        return self._runtime.read(lambda: self._pending.error)

    @property
    def safe_to_retry(self) -> bool:
        return self._runtime.read(lambda: self._pending.safe_to_retry)

    @property
    def replacement_allowed(self) -> bool:
        return self._runtime.read(lambda: self._pending.replacement_allowed)

    def executed(self, timeout: float | None = None) -> ResultT:
        return self._runtime.wait_for(
            self._pending,
            lambda: self._pending.executed(timeout),
            require_finalized=False,
        )

    def finalized(self, timeout: float | None = None) -> ResultT:
        return self._runtime.wait_for(
            self._pending,
            lambda: self._pending.finalized(timeout),
            require_finalized=True,
        )

    def snapshot(self) -> TransactionSnapshot:
        return self._runtime.read(self._pending.snapshot)

    def diagnostics(self) -> dict[str, Any]:
        return self._runtime.read(self._pending.diagnostics)


class _SyncTicketRuntime:
    """Own one background event loop and one connected AsyncChainClient."""

    def __init__(
        self,
        *,
        substrate_ws: str,
        substrate_ws_endpoints: tuple[str, ...],
        recovery_substrate_ws_endpoints: tuple[str, ...],
        private_key: str,
        subaccount: str,
        net: str,
        print_state: bool,
        max_completed_transactions: int,
        max_resolved_blocks: int,
        node_pool_limit_per_account: int,
        max_pool_transactions_per_account: int,
        priority_pool_reserve: int,
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._close_lock = threading.Lock()
        self._closing = False
        self._closed = False
        self._client: Any = None
        self._thread = threading.Thread(
            target=self._run_loop,
            name="deepx-sdk-transactions",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()
        try:
            self._run(
                self._connect(
                    substrate_ws=substrate_ws,
                    substrate_ws_endpoints=substrate_ws_endpoints,
                    recovery_substrate_ws_endpoints=(
                        recovery_substrate_ws_endpoints
                    ),
                    private_key=private_key,
                    subaccount=subaccount,
                    net=net,
                    print_state=print_state,
                    max_completed_transactions=max_completed_transactions,
                    max_resolved_blocks=max_resolved_blocks,
                    node_pool_limit_per_account=node_pool_limit_per_account,
                    max_pool_transactions_per_account=(
                        max_pool_transactions_per_account
                    ),
                    priority_pool_reserve=priority_pool_reserve,
                )
            )
        except BaseException:
            self._stop_loop()
            raise

    @classmethod
    def from_chain_client(cls, client: Any) -> _SyncTicketRuntime:
        return cls(
            substrate_ws=client.substrate_ws,
            substrate_ws_endpoints=client._substrate_rpc_pool.ordered(),
            recovery_substrate_ws_endpoints=(
                client.recovery_substrate_ws_endpoints
            ),
            private_key=client.private_key,
            subaccount=client.subaccount,
            net=client.net,
            print_state=client.print_state,
            max_completed_transactions=client.max_completed_transactions,
            max_resolved_blocks=client.max_resolved_blocks,
            node_pool_limit_per_account=client.node_pool_limit_per_account,
            max_pool_transactions_per_account=(
                client.max_pool_transactions_per_account
            ),
            priority_pool_reserve=client.priority_pool_reserve,
        )

    def submit(
        self,
        operation: Callable[[Any], Awaitable[PendingTransaction[ResultT]]],
    ) -> SyncTransactionTicket[ResultT]:
        async def invoke() -> PendingTransaction[ResultT]:
            return await operation(self._client)

        pending = self._run(invoke())
        return SyncTransactionTicket(self, pending)

    def read(self, callback: Callable[[], ResultT]) -> ResultT:
        if self._closed:
            return callback()

        async def invoke() -> ResultT:
            return callback()

        return self._run(invoke())

    @property
    def active_rpc_endpoint(self) -> str:
        return self.read(lambda: self._client.active_rpc_endpoint)

    def wait_for(
        self,
        pending: PendingTransaction[ResultT],
        operation: Callable[[], Awaitable[ResultT]],
        *,
        require_finalized: bool,
    ) -> ResultT:
        if self._closed:
            return self._result_after_close(
                pending,
                require_finalized=require_finalized,
            )
        return self._run(operation())

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closing = True
            try:
                if self._client is not None:
                    self._run(self._client.close(), allow_closing=True)
            finally:
                self._stop_loop()

    async def _connect(self, **kwargs: Any) -> None:
        from .async_client import AsyncChainClient

        self._client = AsyncChainClient(**kwargs)
        await self._client.connect()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        self._loop.close()

    def _run(
        self,
        operation: Awaitable[ResultT],
        *,
        allow_closing: bool = False,
    ) -> ResultT:
        if self._closed or (self._closing and not allow_closing):
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            raise RuntimeError("ChainClient is closed and cannot submit transactions.")
        future = asyncio.run_coroutine_threadsafe(
            cast(Any, operation),
            self._loop,
        )
        return future.result()

    def _stop_loop(self) -> None:
        if self._closed:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._closed = True
        self._closing = False

    @staticmethod
    def _result_after_close(
        pending: PendingTransaction[ResultT],
        *,
        require_finalized: bool,
    ) -> ResultT:
        if pending.error is not None:
            raise pending.error
        if pending.status is TxStatus.FINALIZED:
            return cast(ResultT, pending.result)
        if not require_finalized and pending.status is TxStatus.IN_BLOCK_SUCCESS:
            return cast(ResultT, pending.result)
        raise RuntimeError("Transaction result is unavailable because ChainClient is closed.")
