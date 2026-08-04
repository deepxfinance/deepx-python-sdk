"""Subaccount onboarding example for the DeepX Python SDK.

This is the **first thing to do** before any trading activity. DeepX uses a
two-tier account model:

    EOA (wallet)                      Subaccount
    ├── holds gas / native token      ├── trading identity
    ├── signs transactions            └── owned by one EOA
    └── creates N subaccounts

The SDK does **not** generate wallets — bring your own private key from
MetaMask, hardware wallet, web3.py, etc. Once you have one, this script:

1. Builds a ``ChainClient`` from a private key (no subaccount yet).
2. Calls ``initialize_subaccount(name=...)`` to create the first subaccount.
3. Reads the new subaccount address out of the ``NewUserRecord`` event.
4. Reconstructs the client with the new subaccount and queries its info.
5. (Optional) creates a second subaccount to show the 1-to-many relationship.
6. (Optional) cleans up by deleting one of them.

Run it directly:

    python examples/onboarding.py
"""

from __future__ import annotations

from decimal import Decimal

from _dotenv import load, optional, require

load()

import deepx_sdk as dx
from deepx_sdk import APIError, ChainError


# ---------------------------------------------------------------------------
# 1. Credentials
#
# Replace the placeholders below. The private key is external — this SDK
# never generates or stores wallets. The subaccount address will be created
# by this script on first run; once known, paste it back into ``SUBACCOUNT``
# for follow-up runs so you don't create a new one every time.
# ---------------------------------------------------------------------------

PRIVATE_KEY = require("PRIVATE_KEY")
SUBACCOUNT = optional("SUBACCOUNT")  # leave blank on first run

NET = optional("NET", "devnet")


# ---------------------------------------------------------------------------
# 2. Step 1 — bootstrap: build a client with no subaccount
#
# ``ChainClient`` accepts a missing subaccount for these methods:
#   - initialize_subaccount()
#   - user_active_orders(user=...)
#   - user_perp_positions(user=...)
#
# For everything else you must pass ``subaccount=...`` after initialization.
# ---------------------------------------------------------------------------

def step1_create_first_subaccount() -> str:
    chain = dx.ChainClient(
    wait_for_finalized=False,  # devnet finalization stalls intermittently
        net=NET,
        private_key=PRIVATE_KEY,
        # subaccount intentionally omitted
    )

    res = chain.subaccount_client.initialize_subaccount(name="my-first-subaccount")
    print(f"  tx_hash:        {res.tx_hash}")
    print(f"  event payload:  {res.event}")

    # The "NewUserRecord" substrate event has fields {owner, subaccount, name}.
    # The SDK exposes them as a Python dict on TxResult.event.
    new_subaccount = res.event["subaccount"]
    print(f"  new subaccount: {new_subaccount}")
    return new_subaccount


# ---------------------------------------------------------------------------
# 3. Step 2 — rebuild the client with the new subaccount, verify on-chain
# ---------------------------------------------------------------------------

def step2_verify_subaccount(subaccount: str) -> None:
    chain = dx.ChainClient(
    wait_for_finalized=False,  # devnet finalization stalls intermittently
        net=NET,
        private_key=PRIVATE_KEY,
        subaccount=subaccount,
    )

    info = chain.subaccount_client.subaccount_info(address=subaccount)
    print(f"  name:        {info.name!r}")
    print(f"  owner:       {info.owner}")
    print(f"  status:      {info.status}")
    print(f"  next_order_id: {info.next_order_id}")


# ---------------------------------------------------------------------------
# 4. Step 3 — create a second subaccount (demonstrates 1-to-many EOA→sub)
# ---------------------------------------------------------------------------

def step3_create_second_subaccount() -> str:
    chain = dx.ChainClient(net=NET, private_key=PRIVATE_KEY, wait_for_finalized=False)
    res = chain.subaccount_client.initialize_subaccount(name="my-second-subaccount")
    second = res.event["subaccount"]
    print(f"  second subaccount: {second}")
    return second


# ---------------------------------------------------------------------------
# 5. Step 4 — query active orders across all of an EOA's subaccounts
#
# ``user_active_orders`` accepts an EOA address (not a subaccount) and
# returns every active order across every subaccount under it. Useful for
# portfolio dashboards.
# ---------------------------------------------------------------------------

def step4_list_all_active_orders(owner: str) -> None:
    chain = dx.ChainClient(net=NET, private_key=PRIVATE_KEY, wait_for_finalized=False)
    orders = chain.perp_market.user_active_orders(user=owner)
    print(f"  total active perp orders: {len(orders)}")
    for o in orders:
        print(f"    order_id={o.order_id} market_id={o.market_id} side={o.is_long}")


# ---------------------------------------------------------------------------
# 6. Cleanup — delete a subaccount
#
# ``delete_subaccount`` requires the subaccount to be empty (no open
# positions, no open orders, no non-zero balances). It will fail with
# ``DeleteSubaccountCheckFailed`` (chain code 19_8) otherwise.
# ---------------------------------------------------------------------------

def step5_delete_subaccount(subaccount: str) -> None:
    chain = dx.ChainClient(
    wait_for_finalized=False,  # devnet finalization stalls intermittently
        net=NET,
        private_key=PRIVATE_KEY,
        subaccount=subaccount,
    )
    res = chain.subaccount_client.delete_subaccount(subaccount=subaccount)
    print(f"  deleted subaccount={subaccount} tx_hash={res.tx_hash}")


# ---------------------------------------------------------------------------
# 7. main — run the full flow with error handling
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== Step 1: create first subaccount ===")
    try:
        new_subaccount = (
            SUBACCOUNT
            if SUBACCOUNT
            else step1_create_first_subaccount()
        )
    except ChainError as e:
        print(f"  ChainError: {e.code} {e.name} ({e.pallet}): {e.message}")
        return
    except APIError as e:
        print(f"  APIError: {e.code} [{e.category}]: {e.message}")
        return

    print(f"\n=== Step 2: verify {new_subaccount} ===")
    try:
        step2_verify_subaccount(new_subaccount)
    except (ChainError, APIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("\n=== Step 3: create a second subaccount ===")
    try:
        second_subaccount = step3_create_second_subaccount()
    except ChainError as e:
        print(f"  ChainError: {e.code} {e.name}: {e.message}")
        second_subaccount = None

    # Derive the EOA address from the private key to list all subaccount orders.
    # In practice you'd store this once and reuse it.
    try:
        from eth_account import Account
        owner = Account.from_key(PRIVATE_KEY).address
        print(f"\n=== Step 4: list all perp orders under EOA {owner} ===")
        step4_list_all_active_orders(owner)
    except Exception as e:
        print(f"  could not derive EOA address: {e}")

    if second_subaccount:
        print(f"\n=== Step 5: delete second subaccount {second_subaccount} ===")
        try:
            step5_delete_subaccount(second_subaccount)
        except ChainError as e:
            print(f"  ChainError: {e.code} {e.name}: {e.message}")
            print("  (expected if the subaccount still holds positions or balances)")

    print("\n=== Done ===")
    print(f"Save this for follow-up runs:\n  SUBACCOUNT = {new_subaccount!r}")


if __name__ == "__main__":
    main()
