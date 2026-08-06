"""CLOID (client order id) examples for the DeepX Python SDK.

A cloid is a user-chosen order id. When you place an order with a cloid, the
cloid *becomes* the order's oid — so you can cancel (or look up) the order
immediately, without waiting for the system-assigned oid to come back. This
matters for market makers that need to cancel within milliseconds of placing.

On-chain rules (both perp and spot):

- valid range: ``[2**31 - 1, 2**32 - 2]`` (system oids stay below ``2**31 - 1``
  and never collide with cloids)
- a cloid is consumed **forever** once used — even after the order fills or is
  cancelled. Reusing it is rejected:
    - perp ``22_76 PerpDuplicateClientOrderId`` / spot ``20_45 SpotDuplicateClientOrderId``
- out-of-range cloids are rejected:
    - perp ``22_75 PlacePerpExceedClientOrderId`` / spot ``20_43 PlaceSpotExceedClientOrderId``
- recommended pattern: allocate cloids locally, monotonically increasing, so
  you never collide with yourself.

This file is **not** part of the test suite. Fill in the placeholders below,
then run it directly:

    python examples/cloid_orders.py
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
#
# Credentials come from ``examples/.env`` (see ``examples/.env.example``).
# ---------------------------------------------------------------------------

PRIVATE_KEY = require("PRIVATE_KEY")
SUBACCOUNT = require("SUBACCOUNT")

PERP_MARKET_ID = 3  # ETH-USDC perp on testnet
SPOT_PAIR = "0x950c1bb15508369148679bf2921417929f1465c068c4b22a980c3c23535846c0"  # ETH/USDC spot

PERP_SIZE = int(Decimal("0.01") * (10 ** 18))       # 0.01 ETH
PERP_PRICE = int(Decimal("1500") * (10 ** 6))       # far below mark -> rests on the book
SPOT_QUOTE_AMOUNT = int(Decimal("1.5") * (10 ** 6))     # 1.5 USDC
SPOT_BASE_AMOUNT = int(Decimal("0.001") * (10 ** 18))   # 0.001 ETH -> 1500 USDC/ETH

# Locally allocated, monotonically increasing cloids. A production client
# would persist its own counter; time-based derivation keeps runs unique.
CLOID_BASE = 2**31 - 1 + (int(time.time()) % 1_000_000) * 8


# ---------------------------------------------------------------------------
# 2. Client setup
# ---------------------------------------------------------------------------

chain = dx.ChainClient(
    wait_for_finalized=False,  # devnet finalization stalls intermittently
    net=optional("NET", "testnet"),
    private_key=PRIVATE_KEY,
    subaccount=SUBACCOUNT,
)
api = dx.ApiClient(net=optional("NET", "testnet"), private_key=PRIVATE_KEY, subaccount=SUBACCOUNT)


# ---------------------------------------------------------------------------
# 3. Perp: place with cloid, cancel by cloid immediately
# ---------------------------------------------------------------------------

def place_perp_with_cloid_then_cancel(cloid: int) -> None:
    res = chain.perp_market.place_perp_order_limit(
        market_id=PERP_MARKET_ID,
        is_long=True,
        size=PERP_SIZE,
        price=PERP_PRICE,
        cloid=cloid,
    )
    # The returned oid IS the cloid — cancel right away, no waiting.
    assert res.order_id == cloid
    cancel = chain.perp_market.cancel_order(market_id=PERP_MARKET_ID, order_id=cloid)
    print(f"  placed+ cancelled oid={cancel.order_id} tx={cancel.tx_hash}")


# ---------------------------------------------------------------------------
# 4. Perp via the high-level dispatcher (also accepts cloid)
# ---------------------------------------------------------------------------

def place_perp_via_dispatcher(cloid: int) -> dx.PlaceOrderResult:
    return chain.perp_market.place_order(
        side="buy",
        size=PERP_SIZE,
        market_id=PERP_MARKET_ID,
        order_type="limit",
        price=PERP_PRICE,
        cloid=cloid,
    )


# ---------------------------------------------------------------------------
# 5. Perp via REST signed-tx path (also accepts cloid)
# ---------------------------------------------------------------------------

def place_perp_via_rest(cloid: int) -> None:
    res = api.v1.chain_tx.place_perp_order_ioc(
        market_id=PERP_MARKET_ID,
        is_long=True,
        size=PERP_SIZE,
        price=int(Decimal("5000") * (10 ** 6)),  # above mark -> fills or dies as IOC
        cloid=cloid,
    )
    print(f"  REST placed: {res}")


# ---------------------------------------------------------------------------
# 6. Spot: place with cloid, cancel by cloid
# ---------------------------------------------------------------------------

def place_spot_with_cloid_then_cancel(cloid: int) -> None:
    res = chain.spot_market.subaccount_place_order_buy_b(
        pair=SPOT_PAIR,
        quote_amount=SPOT_QUOTE_AMOUNT,
        base_amount=SPOT_BASE_AMOUNT,
        cloid=cloid,
    )
    assert res.order_id == cloid
    cancel = chain.spot_market.subaccount_cancel_order_buy_b(pair=SPOT_PAIR, order_id=cloid)
    print(f"  placed+cancelled oid={cancel.order_id} tx={cancel.tx_hash}")


# ---------------------------------------------------------------------------
# 7. Error cases: duplicate and out-of-range cloids
# ---------------------------------------------------------------------------

def show_duplicate_cloid_error(cloid: int) -> None:
    """``cloid`` was already consumed above — placing again must fail."""
    try:
        chain.perp_market.place_perp_order_limit(
            market_id=PERP_MARKET_ID, is_long=True, size=PERP_SIZE,
            price=PERP_PRICE, cloid=cloid,
        )
        print("  UNEXPECTED: duplicate cloid accepted")
    except ChainError as e:
        print(f"  duplicate rejected: {e.code} {e.name}")


def show_out_of_range_cloid_error() -> None:
    try:
        chain.perp_market.place_perp_order_limit(
            market_id=PERP_MARKET_ID, is_long=True, size=PERP_SIZE,
            price=PERP_PRICE, cloid=100,  # < 2**31 - 1: inside the system-oid region
        )
        print("  UNEXPECTED: out-of-range cloid accepted")
    except ChainError as e:
        print(f"  out-of-range rejected: {e.code} {e.name}")


# ---------------------------------------------------------------------------
# 8. Run all of the above
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== Perp: place with cloid, cancel by cloid ===")
    try:
        place_perp_with_cloid_then_cancel(CLOID_BASE)
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Perp dispatcher with cloid ===")
    try:
        res = place_perp_via_dispatcher(CLOID_BASE + 1)
        print(f"  order_id={res.order_id} tx={res.tx_hash}")
        chain.perp_market.cancel_order(market_id=PERP_MARKET_ID, order_id=res.order_id)
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Spot: place with cloid, cancel by cloid ===")
    try:
        place_spot_with_cloid_then_cancel(CLOID_BASE + 2)
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Duplicate cloid (expect 22_76) ===")
    show_duplicate_cloid_error(CLOID_BASE)

    print("=== Out-of-range cloid (expect 22_75) ===")
    show_out_of_range_cloid_error()


if __name__ == "__main__":
    main()
