"""Submit one asynchronous order through the transaction-ticket API.

Configuration comes from ``examples/.env`` (see ``examples/.env.example``);
plain exported environment variables also work and take precedence:
    PRIVATE_KEY, SUBACCOUNT, SUBSTRATE_WS, MARKET_ID, SIDE, SIZE, PRICE, CLOID

Run with:
    python examples/async_orders.py
"""

from __future__ import annotations

import asyncio
import os

from _dotenv import load

load()

import deepx_sdk as dx


def _cloid() -> int:
    raw = os.environ.get("CLOID", "").strip()
    if raw:
        return int(raw)
    import random

    return random.randint(2**31 - 1, 2**32 - 2)



async def main() -> None:
    async with dx.AsyncChainClient(
        substrate_ws=os.environ["SUBSTRATE_WS"],
        private_key=os.environ["PRIVATE_KEY"],
        subaccount=os.environ["SUBACCOUNT"],
    ) as client:
        ticket = await client.perp_market.place_order(
            market_id=int(os.environ["MARKET_ID"]),
            side=os.environ["SIDE"],
            size=int(os.environ["SIZE"]),
            price=int(os.environ["PRICE"]),
            cloid=_cloid(),
        )

        # The node accepted the transaction; no block wait happened yet.
        print("state:", ticket.state)

        # Wait only when the strategy needs the typed execution result.
        result = await ticket.executed(timeout=120)
        print("executed:", result)

        # Optional for workflows that require chain finality.
        await ticket.finalized(timeout=120)
        print("finalized:", ticket.state)


if __name__ == "__main__":
    asyncio.run(main())
