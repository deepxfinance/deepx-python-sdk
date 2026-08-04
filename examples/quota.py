"""Quota query & claim examples for the DeepX Python SDK.

Quota is the per-transaction fee unit on DeepX: every order/cancel costs 1
quota. Trading volume earns *claimable* quota; claiming turns it into usable
quota on-chain.

Ordinary users CANNOT claim on-chain themselves (chain `add_quota` is
restricted to authorized accounts). The product flow is backend-executed:

1. ``quota_summary`` / ``wallet_quota`` show earned (claimable) quota.
2. ``claim_quota`` personal-signs a fixed three-line message with your wallet
   key and POSTs it; the backend reserves the claimable amount and submits
   the chain transaction for you, asynchronously.
3. ``wait_quota_claim`` polls the claim task until it is confirmed on-chain.

This file is **not** part of the test suite. Fill in the placeholders below,
then run it directly:

    python examples/quota.py
"""

from __future__ import annotations

import deepx_sdk as dx
from _dotenv import load, optional, require

load()

from deepx_sdk import APIError, ChainError, RESTError


# ---------------------------------------------------------------------------
# 1. Credentials
# ---------------------------------------------------------------------------

PRIVATE_KEY = require("PRIVATE_KEY")
WALLET = optional("WALLET")  # EOA address of the key above; derived from the key when empty
if not WALLET:
    from eth_account import Account

    WALLET = Account.from_key(PRIVATE_KEY).address


# ---------------------------------------------------------------------------
# 2. Client setup
# ---------------------------------------------------------------------------

api = dx.ApiClient(
    net="devnet",  # devnet | testnet
    private_key=PRIVATE_KEY,  # used to personal-sign the claim message
)
chain = dx.ChainClient(net="devnet", private_key=PRIVATE_KEY, wait_for_finalized=False)


# ---------------------------------------------------------------------------
# 3. Query
# ---------------------------------------------------------------------------

def show_quota() -> None:
    q = api.v1.account.wallet_quota(address=WALLET)
    print(f"  wallet_quota: {q}")  # {claimable, remaining}

    summary = api.v1.account.quota_summary(wallet=WALLET)
    data = summary.get("data", summary)
    print(
        f"  earned={data.get('quotaEarned')} granted={data.get('quotaGranted')} "
        f"pending={data.get('quotaPending')} volume=${data.get('totalVolumeUsd')}"
    )

    # On-chain view of the same quota (nonce/quota/time_nonce window):
    acc = chain.system.system_account(address=WALLET)
    print(f"  on-chain quota={acc.quota}  (0 = not activated, 2**32-1 = frozen)")


# ---------------------------------------------------------------------------
# 4. Buy quota directly on-chain (Lending.buy_quota)
#
# Cost = QuoteAmountPerQuota x quota in USDC (devnet: 500 base units per
# quota). Pays from the signer's wallet by default; pass from_subaccount to
# pay from a subaccount's spot USDC balance instead.
# ---------------------------------------------------------------------------

SUBACCOUNT = optional("SUBACCOUNT")  # optional payer for buy_quota


def buy_quota_onchain() -> None:
    res = chain.lending.buy_quota(
        account=WALLET,                 # beneficiary (quota is wallet-level)
        quota=10,
        from_subaccount=SUBACCOUNT,     # pay from subaccount spot USDC
    )
    print(f"  bought 10 quota, tx={res.tx_hash}")


# ---------------------------------------------------------------------------
# 5. Claim
# ---------------------------------------------------------------------------
def claim_and_wait() -> None:
    res = api.v1.account.claim_quota(wallet=WALLET)  # signs with the client key
    data = res.get("data", res)  # public endpoint: raw object; tolerate envelope
    if data.get("status") == "noop" or not data.get("claim"):
        # claim=None: nothing claimable right now.
        print(f"  nothing to claim (status={data.get('status')})")
        return
    claim = data["claim"]
    claim_id = claim.get("id")
    print(f"  claim created: id={claim_id} status={claim.get('status')} amount={claim.get('quotaAmount')}")

    final = api.v1.account.wait_quota_claim(claim_id=claim_id, timeout_s=120)
    done = final.get("data", final)
    print(f"  confirmed: tx={done.get('txHash')} quota={done.get('quotaAmount')}")


# ---------------------------------------------------------------------------
# 6. Run
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== Quota query ===")
    try:
        show_quota()
    except (APIError, RESTError, ChainError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Quota claim (needs claimable > 0) ===")
    try:
        claim_and_wait()
    except (APIError, RESTError, ChainError, RuntimeError, TimeoutError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("=== Buy quota on-chain ===")
    try:
        buy_quota_onchain()
    except (APIError, RESTError, ChainError, RuntimeError) as e:
        print(f"  {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
