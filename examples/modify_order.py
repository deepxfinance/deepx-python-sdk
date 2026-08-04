"""Modify order examples for the DeepX Python SDK.

A modify is a single on-chain extrinsic that atomically cancels the old order
and places a new one. If any step fails (the new order fails margin/tick/step
checks, the old order is no longer open, ...) the whole transaction rolls
back and the old order stays untouched. On success you get a NEW order id —
the old oid is gone, track ``res.order_id`` afterwards.

Key facts:

- The new order is a FRESH order: every parameter is explicit (there is no
  "unspecified fields inherit old values" on-chain — that is frontend sugar).
- Perp supports the product-level ``new_total_quantity`` semantic: the total
  size including the already-filled part. The SDK reads the order's filled
  amount and places ``new_total_quantity - filled``; equal means cancel-only,
  smaller is rejected locally.
- Costs 1 quota and uses a timestamp nonce, exactly like place/cancel.

This file is **not** part of the test suite. Fill in the placeholders below,
then run it directly:

    python examples/modify_order.py
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
SPOT_PAIR = "0x950c1bb15508369148679bf2921417929f1465c068c4b22a980c3c23535846c0"  # ETH/USDC spot

PERP_SIZE = int(Decimal("0.001") * (10 ** 18))
PERP_PRICE = int(Decimal("1500") * (10 ** 6))      # far below mark -> rests
SPOT_QUOTE_AMOUNT = int(Decimal("1.5") * (10 ** 6))
SPOT_BASE_AMOUNT = int(Decimal("0.001") * (10 ** 18))

CLOID = 2**31 - 1 + (int(time.time()) % 1_000_000) * 8


# ---------------------------------------------------------------------------
# 2. Client setup
# ---------------------------------------------------------------------------

chain = dx.ChainClient(
    wait_for_finalized=False,  # devnet finalization stalls intermittently
    net=optional("NET", "devnet"),
    private_key=PRIVATE_KEY,
    subaccount=SUBACCOUNT,
)


# ---------------------------------------------------------------------------
# 3. Perp: place, then modify price+size atomically
# ---------------------------------------------------------------------------

def perp_modify_price_and_size() -> None:
    placed = chain.perp_market.place_perp_order_limit(
        market_id=PERP_MARKET_ID, is_long=True, size=PERP_SIZE,
        price=PERP_PRICE, cloid=CLOID,
    )
    print(f"  placed oid={placed.order_id} (cloid)")

    res = chain.perp_market.modify_order(
        order_id=placed.order_id,
        market_id=PERP_MARKET_ID,
        is_long=True,
        size=PERP_SIZE * 2,                    # new remaining size (explicit)
        price=int(Decimal("1400") * (10 ** 6)),
        cloid=CLOID + 1,
    )
    print(f"  modified: old oid={res.canceled_order_id} -> new oid={res.order_id}")


# ---------------------------------------------------------------------------
# 4. Perp: new_total_quantity semantics (total incl. filled)
# ---------------------------------------------------------------------------

def perp_modify_total_quantity() -> None:
    placed = chain.perp_market.place_perp_order_limit(
        market_id=PERP_MARKET_ID, is_long=True, size=PERP_SIZE,
        price=PERP_PRICE, cloid=CLOID + 2,
    )
    # Nothing filled yet, so new_total_quantity == the new size here. With a
    # partially filled order the SDK places (new_total - filled); passing a
    # value equal to the filled amount cancels without placing a new order.
    res = chain.perp_market.modify_order(
        order_id=placed.order_id,
        market_id=PERP_MARKET_ID,
        is_long=True,
        price=PERP_PRICE,
        new_total_quantity=PERP_SIZE * 3,
        cloid=CLOID + 3,
    )
    print(f"  modified to total 0.003 ETH: new oid={res.order_id}")


# ---------------------------------------------------------------------------
# 5. Spot: place, then modify price
# ---------------------------------------------------------------------------

def spot_modify_price() -> None:
    placed = chain.spot_market.subaccount_place_order_buy_b(
        pair=SPOT_PAIR, quote_amount=SPOT_QUOTE_AMOUNT,
        base_amount=SPOT_BASE_AMOUNT, cloid=CLOID + 4,
    )
    res = chain.spot_market.modify_order(
        side="buy",                          # must match the old order's side
        order_id=placed.order_id,
        pair=SPOT_PAIR,
        quote_amount=int(Decimal("1.4") * (10 ** 6)),  # 1400 USDC/ETH
        base_amount=SPOT_BASE_AMOUNT,
        cloid=CLOID + 5,
    )
    print(f"  modified: old oid={res.canceled_order_id} -> new oid={res.order_id}")


# ---------------------------------------------------------------------------
# 6. Failure case: modifying a missing order rolls back cleanly
# ---------------------------------------------------------------------------

def modify_missing_order() -> None:
    try:
        chain.perp_market.modify_order(
            order_id=999_999_999, market_id=PERP_MARKET_ID,
            is_long=True, price=PERP_PRICE, size=PERP_SIZE,
        )
        print("  UNEXPECTED: modify of missing order succeeded")
    except ChainError as e:
        print(f"  rejected: {e.code} {e.name}  # 22_13 OrderNotFound")


# ---------------------------------------------------------------------------
# 7. Run
# ---------------------------------------------------------------------------

def main() -> None:
    for label, fn in [
        ("Perp modify price+size", perp_modify_price_and_size),
        ("Perp new_total_quantity", perp_modify_total_quantity),
        ("Spot modify price", spot_modify_price),
        ("Modify missing order", modify_missing_order),
    ]:
        print(f"=== {label} ===")
        try:
            fn()
        except (ChainError, APIError) as e:
            print(f"  {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
