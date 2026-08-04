"""On-chain leverage examples for the DeepX Python SDK.

Leverage on DeepX is a per-subaccount *sizing cap*, not a margin parameter:

    max_notional = available_margin x effective_leverage
    effective    = min(global_max_leverage, per_market_override or global)

Both the cap and uniIMR >= 1 must pass for an order to be accepted.
Values are scaled by LEVERAGE_PRECISION (1000): 10x = 10000. The default is
the protocol max; users lower caps to self-limit. Reducing a cap never closes
existing positions — it only blocks new increases (reduce-only always allowed).

This file is **not** part of the test suite. Fill in the placeholders below,
then run it directly:

    python examples/leverage.py
"""

from __future__ import annotations

from _dotenv import load, optional, require

load()

import deepx_sdk as dx
from deepx_sdk import APIError, ChainError


PRIVATE_KEY = require("PRIVATE_KEY")
SUBACCOUNT = require("SUBACCOUNT")
PERP_MARKET_ID = 3  # ETH-USDC perp on testnet

chain = dx.ChainClient(
    wait_for_finalized=False,  # devnet finalization stalls intermittently
    net=optional("NET", "devnet"),
    private_key=PRIVATE_KEY,
    subaccount=SUBACCOUNT,
)


def show_leverage() -> None:
    g = chain.perp_market.global_max_leverage_for()
    m = chain.perp_market.per_market_max_leverage_for(market_id=PERP_MARKET_ID)
    e = chain.perp_market.effective_leverage_for(market_id=PERP_MARKET_ID)
    print(f"  global={g} (={g / 1000}x)  per-market={m} (0 = no override)  effective={e} (={e / 1000}x)")


def set_and_clear() -> None:
    chain.perp_market.set_global_leverage(max_leverage=10_000)   # 10x global
    print("  set global 10x")
    show_leverage()

    chain.perp_market.set_per_market_leverage(market_id=PERP_MARKET_ID, max_leverage=3_000)
    print("  set per-market 3x -> effective drops to 3x (more conservative wins)")
    show_leverage()

    chain.perp_market.set_per_market_leverage(market_id=PERP_MARKET_ID, max_leverage=None)
    print("  cleared per-market override -> effective back to global")
    show_leverage()


def main() -> None:
    try:
        set_and_clear()
    except (ChainError, APIError) as e:
        # 22_6 InvalidLeverage: value not a multiple of 1000 / out of range
        print(f"  {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
