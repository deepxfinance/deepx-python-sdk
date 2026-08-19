"""Submit one order with the synchronous transaction-ticket API.

Configuration comes from ``examples/.env`` (see ``examples/.env.example``);
plain exported environment variables also work and take precedence:
    PRIVATE_KEY, SUBACCOUNT, SUBSTRATE_WS, MARKET_ID, SIDE, SIZE, PRICE, NONCE_MS

Run with:
    python examples/sync_orders.py
"""

from __future__ import annotations

import json
import os

from _dotenv import load

load()

import deepx_sdk as dx


def main() -> None:
    ticket: dx.SyncTransactionTicket | None = None
    try:
        with dx.ChainClient(
            substrate_ws=os.environ.get("SUBSTRATE_WS", ""),
            private_key=os.environ["PRIVATE_KEY"],
            subaccount=os.environ["SUBACCOUNT"],
            print_state=True,  # Optional: print every state transition as JSON.
        ) as client:
            ticket = client.perp_market.submit_order(
                market_id=int(os.environ["MARKET_ID"]),
                side=os.environ["SIDE"],
                size=int(os.environ["SIZE"]),
                price=int(os.environ["PRICE"]),
                nonce_ms=(
                    int(os.environ["NONCE_MS"])
                    if os.environ.get("NONCE_MS")
                    else None
                ),
            )

            # The node accepted the transaction; no block wait happened yet.
            print("accepted:", ticket.state.value, ticket.tx_hash)

            # TODO: update the strategy/order record after successful execution.
            result = ticket.executed(timeout=120)
            print("executed:", result)

            # TODO: release any workflow that requires irreversible confirmation.
            ticket.finalized(timeout=120)
            print("finalized:", ticket.state.value)
    except (dx.TransactionError, dx.ChainError) as exc:
        # Includes the failed stage, outcome certainty, retryability, and action.
        error = exc.to_dict()
        error["ticket"] = ticket.snapshot().to_dict() if ticket is not None else None
        print(json.dumps(error, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
