"""IOC (Immediate or Cancel) order examples for the DeepX Python SDK.

IOC orders fill whatever they can immediately against the resting order book
and cancel the remainder — they never sit on the GTC book.

This file is **not** part of the test suite. Fill in the placeholders below,
then run it directly:

    python examples/ioc_orders.py

The SDK reads Substrate ``Substrate.transact`` errors and REST ``ApiError``
responses through the typed exception classes ``ChainError`` and ``APIError``
(see ``deepx_sdk._error_codes`` for the full registry).
"""

from __future__ import annotations

from decimal import Decimal

from _dotenv import load, optional, require

load()

import deepx_sdk as dx
from deepx_sdk import APIError, ChainError


# ---------------------------------------------------------------------------
# 1. Credentials and market constants
#
# Credentials come from ``examples/.env`` (see ``examples/.env.example``). Public values (market ids,
# precompile addresses) are real testnet constants and can stay as-is.
# ---------------------------------------------------------------------------

PRIVATE_KEY = require("PRIVATE_KEY")
SUBACCOUNT = require("SUBACCOUNT")

# ETH-USDC perp market id on testnet (from README "ETH-USDC examples").
PERP_MARKET_ID = 3

# Spot pair bytes32 for ETH/USDC on testnet.
SPOT_PAIR = "0x950c1bb15508369148679bf2921417929f1465c068c4b22a980c3c23535846c0"

# Amount denominated in base units (wei / 1e18 for ETH, 1e6 for USDC).
PERP_SIZE = int(Decimal("0.01") * (10 ** 18))   # 0.01 ETH
PERP_PRICE = int(Decimal("2200.50") * (10 ** 6))  # 2200.50 USD, perp price decimals = 1e6
SPOT_QUOTE_AMOUNT = int(Decimal("100") * (10 ** 6))   # 100 USDC
SPOT_BASE_AMOUNT = int(Decimal("0.05") * (10 ** 18))   # 0.05 ETH

# IOC orders placed within ±2% of mark typically fill partially or in full.
# Adjust these if the resting book is far away and you want to exercise the
# "no liquidity → order rejected" branch.


# ---------------------------------------------------------------------------
# 2. Client setup
# ---------------------------------------------------------------------------

chain = dx.ChainClient(
    wait_for_finalized=False,  # devnet finalization stalls intermittently
    net=optional("NET", "devnet"),
    private_key=PRIVATE_KEY,
    subaccount=SUBACCOUNT,
)

# Optional: API client for read-only queries and REST error path.
api = dx.ApiClient(net=optional("NET", "devnet"))


# ---------------------------------------------------------------------------
# 3. Perp IOC — explicit method
#
# ``place_perp_order_ioc`` is the typed entry point for perp IOC orders.
# It maps to ``PerpMarket.place_order`` with ``TimeInForce::IOC`` on-chain.
# post_only is forced to None (chain rejects IOC + post_only).
# Leverage is NOT a per-order param — set it first via
# ``chain.perp_market.set_global_leverage`` / ``set_per_market_leverage``.
# ---------------------------------------------------------------------------

def place_perp_ioc_long() -> dx.PlaceOrderResult:
    return chain.perp_market.place_perp_order_ioc(
        market_id=PERP_MARKET_ID,
        is_long=True,
        size=PERP_SIZE,
        price=PERP_PRICE,
        reduce_only=False,
    )


def place_perp_ioc_short_reduce_only() -> dx.PlaceOrderResult:
    """Closing an existing long position with a reduce-only IOC."""
    return chain.perp_market.place_perp_order_ioc(
        market_id=PERP_MARKET_ID,
        is_long=False,
        size=PERP_SIZE,
        price=PERP_PRICE,
        reduce_only=True,
    )


# ---------------------------------------------------------------------------
# 4. Perp IOC — high-level dispatcher
#
# ``place_order(order_type="ioc")`` routes to the right method based on the
# side and order_type strings. Accepted aliases: "ioc" / "I" / "IOC" / 3.
# ---------------------------------------------------------------------------

def place_perp_ioc_via_dispatcher() -> dx.PlaceOrderResult:
    return chain.perp_market.place_order(
        side="buy",
        size=PERP_SIZE,
        market_id=PERP_MARKET_ID,
        order_type="ioc",
        price=PERP_PRICE,
        reduce_only=False,
    )


# ---------------------------------------------------------------------------
# 5. Spot IOC — explicit methods
# ---------------------------------------------------------------------------

def place_spot_ioc_buy() -> dx.SpotPlaceOrderResult:
    return chain.spot_market.subaccount_place_order_buy_ioc_b(
        pair=SPOT_PAIR,
        quote_amount=SPOT_QUOTE_AMOUNT,
        base_amount=SPOT_BASE_AMOUNT,
        reduce_only=False,
    )


def place_spot_ioc_sell() -> dx.SpotPlaceOrderResult:
    return chain.spot_market.subaccount_place_order_sell_ioc_b(
        pair=SPOT_PAIR,
        quote_amount=SPOT_QUOTE_AMOUNT,
        base_amount=SPOT_BASE_AMOUNT,
        reduce_only=False,
    )


# ---------------------------------------------------------------------------
# 6. Spot IOC — high-level dispatcher
# ---------------------------------------------------------------------------

def place_spot_ioc_buy_via_dispatcher() -> dx.SpotPlaceOrderResult:
    return chain.spot_market.place_order(
        side="buy",
        quote_amount=SPOT_QUOTE_AMOUNT,
        base_amount=SPOT_BASE_AMOUNT,
        order_type="ioc",
    )


# ---------------------------------------------------------------------------
# 7. Error handling
#
# Chain reverts surface as ``ChainError`` with on-chain pallet metadata;
# REST rejections surface as ``APIError`` with category metadata.
# ---------------------------------------------------------------------------

def place_perp_ioc_with_error_handling() -> None:
    try:
        res = place_perp_ioc_long()
    except ChainError as e:
        # ``code`` is the canonical "<pallet>_<error>" string, e.g. "22_17".
        # ``name`` and ``pallet`` are populated from the ErrorCodes.yaml registry.
        print(f"chain reverted: {e.code} {e.name} ({e.pallet}): {e.message}")
    except APIError as e:
        # REST layer rejected the request before it reached the chain.
        # ``category`` is one of: VALIDATION / AUTH / NOT_FOUND / RATE_LIMIT
        #                      / CONFLICT / INTERNAL.
        print(f"REST rejected: {e.code} [{e.category}]: {e.message}")
    except dx.RESTError as e:
        # Catch-all for transport-level errors (timeouts, bad JSON, etc.).
        print(f"transport error: HTTP {e.status_code}: {e.message}")
    except dx.TxError as e:
        # Substrate ``ethereum.transact`` build/sign failures.
        print(f"tx error: {e}")


# ---------------------------------------------------------------------------
# 8. Run all of the above
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== Perp IOC (explicit) ===")
    try:
        res = place_perp_ioc_long()
        print(f"  order_id={res.order_id} tx_hash={res.tx_hash}")
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Perp IOC (high-level dispatcher) ===")
    try:
        res = place_perp_ioc_via_dispatcher()
        print(f"  order_id={res.order_id} tx_hash={res.tx_hash}")
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Spot IOC buy (explicit) ===")
    try:
        res = place_spot_ioc_buy()
        print(f"  order_id={res.order_id} tx_hash={res.tx_hash}")
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Spot IOC sell (explicit) ===")
    try:
        res = place_spot_ioc_sell()
        print(f"  order_id={res.order_id} tx_hash={res.tx_hash}")
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Spot IOC (high-level dispatcher) ===")
    try:
        res = place_spot_ioc_buy_via_dispatcher()
        print(f"  order_id={res.order_id} tx_hash={res.tx_hash}")
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
