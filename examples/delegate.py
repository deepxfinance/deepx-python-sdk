"""Delegate account examples for the DeepX Python SDK.

A delegate is a **wallet-level** operator: a delegate set on your EOA can act
on every subaccount the wallet owns. Typical use: let a market-making bot key
place/cancel orders without holding the wallet key.

Lifecycle shown here:

1. ``set_delegate_account`` — register a delegate with a name + expiry
   (wall-clock ms). New delegates default to mode ``PlaceOrCancelOrder``;
   re-setting the same delegate updates its name/expiry in place.
2. ``update_delegate_mode`` — switch modes. Only ``0=PlaceOrCancelOrder`` and
   ``3=Disable`` are usable; modes 1/2 are disabled on-chain.
3. ``delegate_accounts_for`` — list the wallet's delegates (chain view).
4. ``remove_delegate_account`` — remove it.

All four are Nonce-type calls. This file is **not** part of the test suite.
Fill in the placeholders below, then run it directly:

    python examples/delegate.py
"""

from __future__ import annotations

import time

from _dotenv import load, optional, require

load()

import deepx_sdk as dx
from deepx_sdk import APIError, ChainError
from eth_account import Account


PRIVATE_KEY = require("PRIVATE_KEY")
SUBACCOUNT = require("SUBACCOUNT")  # the client still needs one, though delegates are wallet-level

# The delegate key you want to authorize (e.g. a bot's address). Set in .env.
DELEGATE = require("DELEGATE")

chain = dx.ChainClient(
    wait_for_finalized=False,  # finalization can stall; don't block on it
    private_key=PRIVATE_KEY,
    subaccount=SUBACCOUNT,
    # SDK development only: point these at the internal deployment.
    substrate_ws=optional("SUBSTRATE_WS"),
    evm_rpc_url=optional("EVM_RPC_URL"),
)


def main() -> None:
    owner = Account.from_key(PRIVATE_KEY).address

    try:
        # 1. register the delegate, valid for 24h
        valid_until = int(time.time() * 1000) + 86_400_000
        res = chain.subaccount_client.set_delegate_account(
            delegate=DELEGATE,
            name="mm-bot",
            valid_until=valid_until,
        )
        print(f"  set delegate, tx: {res.tx_hash}")

        # 2. read back the wallet's delegates
        delegates = chain.subaccount_client.delegate_accounts_for(owner=owner)
        for d in delegates:
            print(f"  delegate {d.delegate_address} name={d.delegate_name!r} mode={d.mode} valid_until={d.valid_until}")

        # 3. suspend it without removing (mode 3 = Disable)
        res = chain.subaccount_client.update_delegate_mode(delegate=DELEGATE, new_mode=3)
        print(f"  disabled, tx: {res.tx_hash}")

        # 4. re-enable (mode 0 = PlaceOrCancelOrder)
        res = chain.subaccount_client.update_delegate_mode(delegate=DELEGATE, new_mode=0)
        print(f"  re-enabled, tx: {res.tx_hash}")

        # 5. remove it entirely
        res = chain.subaccount_client.remove_delegate_account(delegate=DELEGATE)
        print(f"  removed, tx: {res.tx_hash}")

    except (ChainError, APIError) as e:
        # 19_34 DelegateExpiry (past valid_until), 19_37 DelegateAccountNotInit,
        # 19_40 InvalidDelegateMode (modes 1/2 are disabled on-chain)
        print(f"  {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
