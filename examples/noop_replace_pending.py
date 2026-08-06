"""No-Op transaction examples for the DeepX Python SDK.

A no-op is a transaction with no payload and no state change — its only
effect is consuming a (timestamp) nonce. Two uses:

1. **Replace a stuck pending transaction.** Every order/cancel you send is
   signed with a millisecond-timestamp nonce. If such a tx sits in the mempool
   unprocessed, it stays executable for days. Submitting ``no_op`` with the
   **same** ``nonce_ms`` evicts it: no-op has the highest mempool priority on
   DeepX, so it replaces the pending tx and permanently consumes that nonce.
   The old tx can never execute afterwards.

2. **Explicitly skip a nonce slot** so an old, possibly-still-replayable
   transaction can never land.

To replace a pending tx you must know the nonce it was signed with. If you
submitted it through this SDK without an explicit ``nonce_ms``, the SDK used
the current millisecond timestamp at signing time — so for transactions you
intend to be replaceable, pass an explicit ``nonce_ms`` and record it.

This file is **not** part of the test suite. Fill in the placeholders below,
then run it directly:

    python examples/noop_replace_pending.py
"""

from __future__ import annotations

import time
from decimal import Decimal

from _dotenv import load, optional, require

load()

import deepx_sdk as dx
from deepx_sdk import APIError, ChainError


# ---------------------------------------------------------------------------
# 1. Credentials and market constants
# ---------------------------------------------------------------------------

PRIVATE_KEY = require("PRIVATE_KEY")
SUBACCOUNT = require("SUBACCOUNT")

PERP_MARKET_ID = 3  # ETH-USDC perp on testnet
PERP_SIZE = int(Decimal("0.01") * (10 ** 18))
PERP_PRICE = int(Decimal("1500") * (10 ** 6))  # far below mark -> rests on the book


# ---------------------------------------------------------------------------
# 2. Client setup
# ---------------------------------------------------------------------------

chain = dx.ChainClient(
    wait_for_finalized=False,  # devnet finalization stalls intermittently
    net=optional("NET", "testnet"),
    private_key=PRIVATE_KEY,
    subaccount=SUBACCOUNT,
)


# ---------------------------------------------------------------------------
# 3. Plain no-op: consume a fresh nonce
# ---------------------------------------------------------------------------

def consume_fresh_nonce() -> None:
    res = chain.subaccount_client.no_op()  # nonce_ms=None -> current ms timestamp
    print(f"  no-op included, tx_hash={res.tx_hash}")


# ---------------------------------------------------------------------------
# 4. Replace a pending transaction
#
# Pattern: place an order with an EXPLICIT nonce_ms you record. If it gets
# stuck (not included after a while), kill it with a no-op at the same nonce.
#
# NOTE: SDK submit calls block until inclusion, so a real client submits the
# order from one thread/async task and fires the no-op from a watcher when
# inclusion doesn't happen within its tolerance. This demo just shows both
# halves back-to-back.
# ---------------------------------------------------------------------------

def place_order_with_recorded_nonce(nonce_ms: int) -> None:
    """Half 1: submit with an explicit nonce_ms you keep."""
    res = chain.perp_market.place_perp_order_limit(
        market_id=PERP_MARKET_ID,
        is_long=True,
        size=PERP_SIZE,
        price=PERP_PRICE,
        nonce_ms=nonce_ms,
    )
    print(f"  order landed (nothing to replace): oid={res.order_id}")


def kill_nonce(nonce_ms: int) -> None:
    """Half 2: evict the pending tx / consume the nonce slot."""
    res = chain.subaccount_client.no_op(nonce_ms=nonce_ms)
    print(f"  nonce {nonce_ms} consumed, tx_hash={res.tx_hash}")


# ---------------------------------------------------------------------------
# 5. Run
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== Plain no-op (fresh nonce) ===")
    try:
        consume_fresh_nonce()
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Order with recorded nonce + no-op kill (pattern demo) ===")
    nonce_ms = int(time.time() * 1000)
    try:
        place_order_with_recorded_nonce(nonce_ms)
        # If the line above had instead been stuck in the mempool, this is
        # how you would evict it:
        #   chain.subaccount_client.no_op(nonce_ms=nonce_ms)
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Reused nonce is rejected (1010/1012 pool error) ===")
    nonce_ms = int(time.time() * 1000) + 500
    try:
        chain.subaccount_client.no_op(nonce_ms=nonce_ms)
        print(f"  first no-op included (nonce {nonce_ms})")
        chain.subaccount_client.no_op(nonce_ms=nonce_ms)
        print("  UNEXPECTED: duplicate nonce accepted")
    except Exception as e:  # pool-level 1010/1012, not a pallet error
        print(f"  duplicate rejected: {type(e).__name__}: {str(e)[:160]}")


if __name__ == "__main__":
    main()
