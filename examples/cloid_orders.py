"""Timestamp-nonce order IDs for the DeepX Python SDK.

The current devnet runtime no longer accepts an on-chain client order id
(``cloid``). A user order's id is the extrinsic timestamp nonce instead. This
example keeps its historical filename for compatibility, but demonstrates the
current behavior: pass ``nonce_ms`` when a deterministic order id is useful,
then cancel using the id returned by the placement event.

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


PRIVATE_KEY = require("PRIVATE_KEY")
SUBACCOUNT = require("SUBACCOUNT")

PERP_MARKET_ID = 3
SPOT_PAIR = "0x950c1bb15508369148679bf2921417929f1465c068c4b22a980c3c23535846c0"
PERP_SIZE = int(Decimal("0.01") * (10**18))
PERP_PRICE = int(Decimal("1500") * (10**6))
SPOT_QUOTE_AMOUNT = int(Decimal("1.5") * (10**6))
SPOT_BASE_AMOUNT = int(Decimal("0.001") * (10**18))

# Each submitted extrinsic needs a distinct timestamp nonce for this account.
NONCE_BASE = int(time.time() * 1000)

chain = dx.ChainClient(
    wait_for_finalized=False,
    private_key=PRIVATE_KEY,
    subaccount=SUBACCOUNT,
    substrate_ws=optional("SUBSTRATE_WS"),
    evm_rpc_url=optional("EVM_RPC_URL"),
)


def place_perp_then_cancel() -> None:
    nonce_ms = NONCE_BASE
    placed = chain.perp_market.place_perp_order_limit(
        market_id=PERP_MARKET_ID,
        is_long=True,
        size=PERP_SIZE,
        price=PERP_PRICE,
        nonce_ms=nonce_ms,
    )
    assert placed.order_id == nonce_ms
    cancelled = chain.perp_market.cancel_order(
        market_id=PERP_MARKET_ID,
        order_id=placed.order_id,
        nonce_ms=nonce_ms + 1,
    )
    print(f"  perp placed+cancelled oid={cancelled.order_id} tx={cancelled.tx_hash}")


def place_perp_via_dispatcher() -> None:
    nonce_ms = NONCE_BASE + 2
    result = chain.perp_market.place_order(
        side="buy",
        size=PERP_SIZE,
        market_id=PERP_MARKET_ID,
        order_type="limit",
        price=PERP_PRICE,
        nonce_ms=nonce_ms,
    )
    assert result.order_id == nonce_ms
    print(f"  dispatcher placed oid={result.order_id} tx={result.tx_hash}")
    chain.perp_market.cancel_order(
        market_id=PERP_MARKET_ID,
        order_id=result.order_id,
        nonce_ms=nonce_ms + 1,
    )


def place_spot_then_cancel() -> None:
    nonce_ms = NONCE_BASE + 4
    placed = chain.spot_market.subaccount_place_order_buy_b(
        pair=SPOT_PAIR,
        quote_amount=SPOT_QUOTE_AMOUNT,
        base_amount=SPOT_BASE_AMOUNT,
        nonce_ms=nonce_ms,
    )
    assert placed.order_id == nonce_ms
    cancelled = chain.spot_market.subaccount_cancel_order_buy_b(
        pair=SPOT_PAIR,
        order_id=placed.order_id,
        nonce_ms=nonce_ms + 1,
    )
    print(f"  spot placed+cancelled oid={cancelled.order_id} tx={cancelled.tx_hash}")


def main() -> None:
    for label, operation in (
        ("Perp: timestamp nonce", place_perp_then_cancel),
        ("Perp: dispatcher", place_perp_via_dispatcher),
        ("Spot: timestamp nonce", place_spot_then_cancel),
    ):
        print(f"=== {label} ===")
        try:
            operation()
        except (ChainError, APIError) as exc:
            print(f"  {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
