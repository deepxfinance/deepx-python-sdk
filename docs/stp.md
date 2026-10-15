# Self-trade prevention and trade groups

The `devnet` SDK branch supports the STP changes in node `devnet` commit
`2b6d9f8` (development runtime 220). On October 15, 2026, read-only checks found
runtime 220 on development RPC and runtime 376 on public testnet. The node source
also increments the testnet runtime to 377; this does not establish deployment.
SDK `main` is kept separate until the testnet rollout is confirmed.

## Per-order modes

Pass `stp_mode` to synchronous or asynchronous spot/perpetual order placement,
synchronous modify-order methods, sync `submit_order` tickets, standalone signing
helpers, or REST transaction-signing methods. Amounts remain in chain integer
units, as with existing order methods.

| Python value | Runtime meaning |
| --- | --- |
| `None` (default) | Use the chain default; STP-enabled runtime defaults to `EXPIRE_MAKER` |
| `"NONE"` | Disable STP for this order; self-trades settle normally and incur fees |
| `"EXPIRE_MAKER"` | Cancel the maker's remainder and continue matching the taker |
| `"EXPIRE_TAKER"` | Cancel the taker's remainder; retain the maker |
| `"EXPIRE_BOTH"` | Cancel both remainders |

Python `None` and the string `"NONE"` are different. STP uses the **taker's** mode.
Its scope includes subaccounts belonging to the same delegator wallet, and
wallets that belong to the same trade group. The SDK does not implement matching
or infer a fill from successful order submission; the chain determines results.

```python
result = chain.perp_market.place_order(
    market_id=3, side="buy", size=size_in_chain_units,
    price=price_in_chain_units, stp_mode="EXPIRE_TAKER",
)

pending = await async_chain.spot_market.place_order(
    pair=pair, side="sell", quote_amount=quote_units,
    base_amount=base_units, stp_mode="EXPIRE_BOTH",
)
```

The SDK inspects metadata at the block used to encode each order. On an older
runtime without STP, the default `None` preserves existing order encoding.
Explicit modes, including `"NONE"`, raise `ValueError` before submission rather
than silently losing the requested behavior. Atomic modify and batch orders
use the same check. A modified order is a new order: pass its desired mode
explicitly, or use the new order's chain default.

`PerpOrderInfo` and `SpotOrderInfo` expose `stp_mode` using the strings above.
Older response layouts return `None` because they do not expose that field.
Spot order arrays are decoded using their complete tuple layout, including the
new field, so multiple orders retain their correct IDs, owners, and prices.

REST signing still requires a backend deployed against the same chain as the
signing RPC. Local signing support alone does not establish backend acceptance.

## Trade-group management

Trade-group members are **delegator wallet addresses**, not subaccount addresses.
Use the delegator's signing key. Node authorization, invitation expiry, capacity,
and creator transfer rules are enforced on-chain.

The synchronous `chain.subaccount_client` exposes:

- `create_trade_group()`
- `invite_trade_group(group_id=..., account=...)`
- `accept_trade_group(group_id=...)`
- `reject_trade_group(group_id=...)`
- `revoke_trade_group_invitation(group_id=..., account=...)`
- `leave_trade_group()`
- `remove_trade_group_member(group_id=..., account=...)`
- `dissolve_trade_group(group_id=...)`
- `trade_group_for(account=...)`: returns `0` when the wallet has no group.
- `trade_group(group_id=...)`: returns the creator and members in join order.
- `trade_group_invitations(group_id=...)`: returns active invitations and
  `expires_at` timestamps in Unix milliseconds; expired invitations are omitted.

Management methods return `TxResult` with the confirmed event fields. For
example, a successful creation exposes `result.event["group_id"]`. They use
**sequential account nonces**, like existing subaccount management: `nonce=None`
resolves the account nonce, and `nonce=...` overrides it. Do not pass a trading
`nonce_ms`. These methods are not part of `AsyncChainClient`'s timestamp-nonce
submission engine.

## Validation boundaries

Regression tests cover all mode values, old-runtime rejection, nested modify /
batch encoding, sync and async forwarding, standalone and REST signing, new and
legacy order views, and trade-group ordinary nonces and events. Live read-only
checks validate metadata encoding on development runtime 220 and public-testnet
runtime 376. Local signing and signature verification, event decoding, and
historical receipt replay also pass on both chains. No transaction was sent.

Real self-match scenarios, group creation/join/removal, and a backend's handling
of STP cancellation records still require transaction-level acceptance before
claiming end-to-end STP support.


October 15 validation results:

- Python 3.14: **811 passed, 1 skipped**, with the existing temporary-file cleanup
  warning. Overall coverage is **95.32%**; the unchanged **99%** gate still fails.
- Python 3.12 installed-wheel checks: **283 passed**. Wheel and source distribution
  pass `twine check`; the wheel matches source and the sdist includes the new tests.
- Development runtime 220: all eight group management calls compose correctly;
  nonmember lookup returns `0` and invitations for an absent group return `[]`.
  `NextTradeGroupId` was `0`, so an existing group's live view could not be checked.
- Local reports are under `scratch/stp-runtime-v220/`. No funded key was loaded
  and no transaction was submitted.
