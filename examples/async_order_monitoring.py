"""Monitor every asynchronous order through one client-level listener.

This is the advanced operations example. For the normal ticket workflow, see
``examples/async_orders.py``.

Configuration comes from ``examples/.env`` (see ``examples/.env.example``);
plain exported environment variables also work and take precedence:
    PRIVATE_KEY, SUBACCOUNT, SUBSTRATE_WS, MARKET_ID, SIDE, SIZE, PRICE, NONCE_MS

Run with:
    python examples/async_order_monitoring.py
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from typing import Any

from _dotenv import load

load()

import deepx_sdk as dx


def _nonce_ms() -> int | None:
    raw = os.environ.get("NONCE_MS", "").strip()
    return int(raw) if raw else None


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _structured_error(
    exc: dx.TransactionError | dx.ChainError,
    ticket: dx.PendingTransaction | None,
) -> dict[str, Any]:
    details = exc.to_dict()
    error_ticket = getattr(exc, "pending", None) or ticket
    details["ticket"] = (
        error_ticket.snapshot().to_dict() if error_ticket is not None else None
    )
    return details


async def _transaction_listener(event: dx.TransactionEvent) -> None:
    """One listener receives events for every transaction on this client."""
    if event.execution_state in {
        dx.ExecutionState.FAILED,
        dx.ExecutionState.NOT_INCLUDED,
        dx.ExecutionState.ACTION_REQUIRED,
    }:
        print(
            json.dumps(
                {"source": "listener_alert", "event": event.to_dict()},
                ensure_ascii=False,
                sort_keys=True,
            )
        )


async def main() -> None:
    ticket: dx.PendingTransaction | None = None
    try:
        order = {
            "market_id": int(_required_env("MARKET_ID")),
            "side": _required_env("SIDE"),
            "size": int(_required_env("SIZE")),
            "price": int(_required_env("PRICE")),
            "nonce_ms": _nonce_ms(),
        }
        async with dx.AsyncChainClient(
            substrate_ws=os.environ.get("SUBSTRATE_WS", ""),
            private_key=_required_env("PRIVATE_KEY"),
            subaccount=_required_env("SUBACCOUNT"),
            print_state=True,
            transaction_listener=_transaction_listener,
        ) as client:
            ticket = await client.perp_market.place_order(**order)
            tracked_by_hash = client.transactions.get(ticket.tx_hash)
            print(
                json.dumps(
                    {
                        "phase": "accepted",
                        "state": ticket.state.value,
                        "tracked_by_hash": tracked_by_hash is ticket,
                        "nonce": ticket.nonce,
                        "snapshot": ticket.snapshot().to_dict(),
                    },
                    sort_keys=True,
                )
            )

            result = await ticket.executed(timeout=120)
            print(
                json.dumps(
                    {
                        "phase": "executed",
                        "result_type": type(result).__name__,
                        "result": asdict(result),
                    },
                    sort_keys=True,
                )
            )

            finalized_result = await ticket.finalized(timeout=120)
            print(
                json.dumps(
                    {
                        "phase": "finalized",
                        "result_type": type(finalized_result).__name__,
                        "result": asdict(finalized_result),
                        "snapshot": ticket.snapshot().to_dict(),
                    },
                    sort_keys=True,
                )
            )
    except (dx.TransactionError, dx.ChainError) as exc:
        print(
            json.dumps(
                {"phase": "error", "error": _structured_error(exc, ticket)},
                sort_keys=True,
            )
        )
        error_ticket = getattr(exc, "pending", None) or ticket
        unknown_outcome = (
            isinstance(exc, dx.TransactionError)
            and exc.certainty is dx.OutcomeCertainty.UNKNOWN
        )
        reconciliation_needed = (
            error_ticket is not None
            and error_ticket.state
            in {
                dx.ExecutionState.NOT_INCLUDED,
                dx.ExecutionState.ACTION_REQUIRED,
            }
        )
        if unknown_outcome or reconciliation_needed:
            print(
                json.dumps(
                    {
                        "phase": "reconcile_before_retry",
                        "tx_hash": getattr(exc, "tx_hash", None)
                        or (
                            error_ticket.tx_hash
                            if error_ticket is not None
                            else None
                        ),
                        "nonce": getattr(exc, "nonce", None)
                        or (error_ticket.nonce if error_ticket is not None else None),
                        "message": (
                            "Outcome is uncertain. Reconcile by tx_hash/nonce; "
                            "do not blindly retry."
                        ),
                    },
                    sort_keys=True,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
