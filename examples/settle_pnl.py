"""PnL settlement (settle_pnl) examples for the DeepX Python SDK.

PnL settlement converts a perp position's floating PnL and pending funding
into real USDC deposits or borrows:

- floating profit  -> USDC deposit (earns interest, usable as margin)
- floating loss    -> smaller deposit, or a USDC borrow once deposits hit
  zero — and borrows accrue interest, so uniMMR decays over time

The platform cranker settles *losing* positions periodically. Profitable
positions are yours to settle whenever you want. ``settle_pnl`` is
**permissionless**: any signer may settle any subaccount's position.

Settlement is lazy and idempotent — settling twice in a row is a no-op
(the second call settles 0). Any size change pre-settles funding on-chain,
so you never *have* to call this; call it to realize profits (deposit +
interest) or to stop floating losses from lingering interest-free.

This file is **not** part of the test suite. Fill in the placeholders below,
then run it directly:

    python examples/settle_pnl.py
"""

from __future__ import annotations

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
# 3. Settle one market — returns the settled amounts from the SettlePnl event
# ---------------------------------------------------------------------------

def settle_one_market() -> None:
    res = chain.perp_market.settle_pnl(market_id=PERP_MARKET_ID)
    # unrealized/funding/total are i128 in quote base units (USDC, 1e6).
    # They are 0 when there is nothing new to settle (idempotent).
    print(
        f"  market={res.market_id} unrealized={res.unrealized} "
        f"funding={res.funding} total={res.total} tx={res.tx_hash}"
    )


def settle_twice_is_noop() -> None:
    first = chain.perp_market.settle_pnl(market_id=PERP_MARKET_ID)
    second = chain.perp_market.settle_pnl(market_id=PERP_MARKET_ID)
    print(f"  first total={first.total}, second total={second.total}  # second settles 0")


# ---------------------------------------------------------------------------
# 4. Settle ALL markets of a subaccount
#
# Omit market_id/symbol. This path only waits for inclusion (the chain emits
# one event per non-zero settlement — possibly none) and returns a plain
# TxResult. Permissionless: you may pass any subaccount, e.g. a cranker.
# ---------------------------------------------------------------------------

def settle_all_markets() -> None:
    res = chain.perp_market.settle_pnl()
    print(f"  settle-all included, tx={res.tx_hash}")


def crank_settle_another_subaccount(other_subaccount: str) -> None:
    res = chain.perp_market.settle_pnl(subaccount=other_subaccount)
    print(f"  crank-settled {other_subaccount}, tx={res.tx_hash}")


# ---------------------------------------------------------------------------
# 5. Run
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== Settle one market ===")
    try:
        settle_one_market()
    except (ChainError, APIError) as e:
        # 22_14 PerpPositionNotFound when there is no open position
        print(f"  {type(e).__name__}: {e}")

    print("=== Settle twice (idempotent) ===")
    try:
        settle_twice_is_noop()
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Settle all markets ===")
    try:
        settle_all_markets()
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
