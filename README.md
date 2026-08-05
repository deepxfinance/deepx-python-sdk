# deepx-python-sdk (Python)

Python SDK that encodes ABI calls in Python and signs/submits transactions via
Substrate `ethereum.transact` using a pure-Python implementation.

Clients:

- `ChainClient` for on-chain calls.
- `ApiClient` for HTTP APIs (`v1`).
- `SDK` as a thin wrapper holding both clients.

## Install

### From source (development)

```
git clone https://github.com/deepxfinance/deepx-python-sdk.git
cd deepx-python-sdk
pip install -e ".[dev]"
```

Alternatively, if you use [uv](https://github.com/astral-sh/uv), run the following to install all extras in a virtual environment:

```
git clone https://github.com/deepxfinance/deepx-python-sdk.git
cd deepx-python-sdk
uv sync --all-extras
```

## Onboarding

DeepX uses a **two-tier account model**:

| Tier             | Lives in              | What it does                                   |
| ---------------- | --------------------- | ---------------------------------------------- |
| **EOA** (wallet) | Off-chain (your side) | Holds gas / native token, signs transactions   |
| **Subaccount**   | On-chain              | Trading identity — positions, orders, balances |

A single EOA can own multiple subaccounts; the SDK signs transactions with
your EOA private key but performs every trading operation against a
specific subaccount.

### First-time setup

1. **Get a private key.** The SDK never generates wallets. Bring your own from
   MetaMask, a hardware wallet, or `web3py`/`eth-account`. Fund it with gas on
   the target network (use a faucet on devnet/testnet).

2. **Create a subaccount.** This is the first on-chain step and the only
   "account creation" the SDK performs:

    ```python
    import deepx_sdk as dx

    # No subaccount yet — that's fine for initialize_subaccount.
    chain = dx.ChainClient(net="testnet", private_key="0xYOUR_PRIVATE_KEY")

    res = chain.subaccount_client.initialize_subaccount(name="my-first-subaccount")
    new_subaccount = res.event["subaccount"]  # read from the NewUserRecord event
    ```

3. **Rebuild the client with the subaccount.** Every other method requires it:

    ```python
    chain = dx.ChainClient(
        net="testnet",
        private_key="0xYOUR_PRIVATE_KEY",
        subaccount=new_subaccount,
    )
    ```

4. **Deposit funds** into the subaccount (EOA → subaccount) before trading:

    ```python
    chain.lending.deposit(subaccount=new_subaccount, asset="USDC", amount=1_000_000)
    ```

5. **Trade.** Now perp/spot/lending operations work normally — see
   [Quick start](#quick-start) below.

A runnable version of this flow is in [`examples/onboarding.py`](examples/onboarding.py).

### Common errors at this stage

| Chain code | Name                          | Cause                                                                       |
| ---------- | ----------------------------- | --------------------------------------------------------------------------- |
| `19_0`     | `SubaccountNotInit`           | Tried to trade before calling `initialize_subaccount`                       |
| `19_9`     | `DuplicateSubaccountName`     | The chosen `name` is already used by this EOA                               |
| `19_8`     | `DeleteSubaccountCheckFailed` | Tried to delete a subaccount that still holds positions / orders / balances |

See [Error codes](#error-codes) for the full registry.

## Quick start

```python
import deepx_sdk as dx

chain = dx.ChainClient(
    net="devnet",  # devnet | testnet
    private_key="0x...",
    perp_precompile_address="0x000000000000000000000000000000000000044E",
    spot_precompile_address="0x000000000000000000000000000000000000044D",
    lending_precompile_address="0x0000000000000000000000000000000000000450",
    subaccount_precompile_address="0x0000000000000000000000000000000000000451",
    system_precompile_address="0x0000000000000000000000000000000000000452",
    subaccount="0x...",
)
api = dx.ApiClient(net="devnet")  # devnet | testnet
sdk = dx.SDK(chain=chain, api=api)
```

> **First time?** The SDK does not create wallets. See [Onboarding](#onboarding) below for the
> full first-time setup (private key → subaccount → deposit → trade).

`ChainClient` defaults to `net="devnet"` and auto-resolves RPC endpoints:

- `devnet` -> `evm_rpc_url=https://devnet-rpc-new.deepx.fi`, `substrate_ws=wss://devnet-rpc-new.deepx.fi`
- `testnet` -> `evm_rpc_url=https://rpc-testnet.deepx.fi`, `substrate_ws=wss://rpc-testnet.deepx.fi`

You can still override with custom `evm_rpc_url` and `substrate_ws` when needed.

For ordered Substrate WebSocket failover, provide
`substrate_ws_endpoints`. The first endpoint is the primary; initial connection
failures and later disconnects rotate to the next endpoint:

```python
client = dx.AsyncChainClient(
    substrate_ws_endpoints=[
        "wss://rpc-a.example.com",
        "wss://rpc-b.example.com",
        "wss://rpc-c.example.com",
    ],
    private_key=PRIVATE_KEY,
    subaccount=SUBACCOUNT,
)

await client.connect()
print(client.active_rpc_endpoint)
```

After reconnecting, the client restores subscriptions, scans missed blocks,
and reconciles tracked transactions. A request whose bytes may already have
been sent is not blindly resubmitted. All configured endpoints must serve the
same chain. Endpoint order is preserved and duplicates are removed.

`ChainClient` accepts the same `substrate_ws_endpoints` option for both its
transaction-ticket runtime and synchronous one-shot Substrate methods. A
synchronous submission may switch endpoints only while establishing the
connection. Once extrinsic submission starts, the SDK does not replay the
transaction on another endpoint because the first result may be ambiguous.

EVM JSON-RPC reads and transaction-preparation calls support an ordered HTTP
endpoint list:

```python
chain = dx.ChainClient(
    evm_rpc_endpoints=[
        "https://evm-rpc-a.example.com",
        "https://evm-rpc-b.example.com",
    ],
    private_key=PRIVATE_KEY,
    subaccount=SUBACCOUNT,
)

print(chain.active_evm_rpc_endpoint)
```

Transport failures and HTTP 5xx responses rotate to the next endpoint for
`eth_call`, chain-id, account-nonce, and gas-estimation requests. JSON-RPC
business errors are returned without retrying another node.

REST API reads use `base_urls`:

```python
api = dx.ApiClient(
    base_urls=[
        "https://api-a.example.com",
        "https://api-b.example.com",
    ],
)

print(api.active_api_endpoint)
```

`GET`, `HEAD`, and `OPTIONS` requests fail over on transport failures and HTTP
5xx responses. Mutating REST requests are sent only to the active endpoint and
are never automatically replayed after an ambiguous transport failure. HTTP
429 and application-level 4xx responses also remain visible to the caller.

`ApiClient` defaults to `net="devnet"` and auto-resolves `base_url` / `ws_base_url`:

- `devnet` -> `base_url=https://rest-api-devnet.deepx.fi`, `ws_base_url=wss://ws-api-devnet.deepx.fi`
- `testnet` -> `base_url=https://rest-api-testnet.deepx.fi`, `ws_base_url=wss://ws-api-testnet.deepx.fi`

You can still override with a custom `base_url` or `ws_base_url` when needed.

## Transaction ticket lifecycle

Transaction tickets work out of the box, including WebSocket connections
routed through an HTTP/SOCKS proxy (`http_proxy` / `https_proxy` env vars).

### Synchronous ticket workflow

`ChainClient` can return a ticket as soon as the node accepts the extrinsic.
The SDK owns one background asyncio loop per client, so synchronous callers do
not need to manage an event loop, listener, or tracker:

```python
with dx.ChainClient(
    substrate_ws="wss://...",
    private_key="0x...",
    subaccount="0x...",
    print_state=True,  # optional; print every state transition
) as client:
    ticket = client.perp_market.submit_order(...)

    print(ticket.state)       # ExecutionState.ACCEPTED
    result = ticket.executed()  # block only when the business result is needed
    ticket.finalized()          # optional: wait for chain finality
```

The four synchronous hot-path methods are
`perp_market.submit_order(...)`, `perp_market.submit_cancel(...)`,
`spot_market.submit_order(...)`, and `spot_market.submit_cancel(...)`.
They share one connection and one background event-loop thread owned by the
`ChainClient`; they do not create a thread for every order. Existing
`place_order(...)` and `cancel_order(...)` behavior is unchanged.

Use `ChainClient` as a context manager, or call `client.close()` when the
process no longer needs transaction tickets. Runnable example:
[`examples/sync_orders.py`](examples/sync_orders.py).

`ticket.executed(timeout=...)` and `ticket.finalized(timeout=...)` raise the
same structured `TransactionError` subclasses as the async API. The exception
contains the failed stage, outcome certainty, retryability, transaction
identifiers, and `suggested_action`. A wait timeout does not mutate the ticket,
so the caller can inspect `ticket.state` / `ticket.snapshot()` and continue
tracking it.

### Asynchronous ticket workflow

The normal market-maker path is a transaction ticket. `place_order()` returns
as soon as the node accepts the extrinsic; it does not wait for a block:

```python
ticket = await client.perp_market.place_order(...)

print(ticket.state)  # ExecutionState.ACCEPTED
result = await ticket.executed()
await ticket.finalized()
```

The ticket is a `PendingTransaction`, so existing code remains compatible.
`state` is the concise business state; `status` preserves the exact underlying
transaction status. `executed()` returns the typed business result, while
`finalized()` waits for chain finality.

The current chain runtime targets a 70 ms block slot. `place_order()` and
`cancel_order()` do not wait for that slot: they return the ticket when the
node reports that the transaction is accepted into its pool. `executed()`
waits for successful execution in a block, so its latency includes pool wait,
the remaining part of the current or next 70 ms slot, block processing, and
network delivery. The 70 ms value is a scheduling target, not an SDK latency
guarantee.

Runnable example: [`examples/async_orders.py`](examples/async_orders.py).

If the caller only needs the typed result after execution, use the one-line
wait helpers:

```python
result = await client.perp_market.place_order_and_wait(
    market_id=3,
    side="buy",
    size=123,
    price=456,
    cloid=202607290001,
)
```

The four helpers are
`perp_market.place_order_and_wait(...)`,
`perp_market.cancel_order_and_wait(...)`,
`spot_market.place_order_and_wait(...)`, and
`spot_market.cancel_order_and_wait(...)`. They wait for `EXECUTED`, not
`FINALIZED`; use the regular ticket plus `ticket.finalized()` when finality is
required.

### Advanced monitoring and operations (optional)

For centralized monitoring, `AsyncChainClient` also owns a process-local
`TransactionManager`. A market maker can register one listener for every perp
and spot transaction instead of writing an `updates()` loop for every order.
Normal order submission does not require this API.

```python
import asyncio
import os

import deepx_sdk as dx


async def on_transaction(event: dx.TransactionEvent) -> None:
    # Called for every transaction owned by this client.
    # Forward this event to metrics, alerts, or your strategy state machine.
    if event.execution_state in {
        dx.ExecutionState.FAILED,
        dx.ExecutionState.ACTION_REQUIRED,
    }:
        print("transaction alert:", event.to_dict())


async def main() -> None:
    async with dx.AsyncChainClient(
        substrate_ws=os.environ["SUBSTRATE_WS"],
        private_key=os.environ["PRIVATE_KEY"],
        subaccount=os.environ["SUBACCOUNT"],
        print_state=True,  # optional structured JSON; defaults to False
        transaction_listener=on_transaction,
    ) as chain:
        ticket = await chain.perp_market.place_order(
            market_id=int(os.environ["MARKET_ID"]),  # direct hot-path id
            side="buy",
            size=int(os.environ["SIZE"]),
            price=int(os.environ["PRICE"]),
            cloid=int(os.environ["CLOID"]),  # caller-managed id
        )

        # The handle is already indexed; no per-order callback is required.
        assert chain.transactions.get(ticket.tx_hash) is ticket
        assert chain.transactions.get_by_cloid(ticket.cloid) is ticket
        print("accepted:", ticket.state, ticket.snapshot().to_dict())

        executed_result = await ticket.executed()
        print("executed:", executed_result)

        finalized_result = await ticket.finalized()
        print("finalized:", finalized_result)


asyncio.run(main())
```

Full monitoring example:
[`examples/async_order_monitoring.py`](examples/async_order_monitoring.py).

The manager exposes both business state and the exact Substrate-oriented raw
status:

| Business state    | Product meaning                                                                                       |
| ----------------- | ----------------------------------------------------------------------------------------------------- |
| `SUBMITTING`      | Submission is in progress                                                                             |
| `ACCEPTED`        | The node accepted the transaction; waiting for inclusion; execution is not yet confirmed              |
| `EXECUTED`        | The transaction was included and executed successfully                                                |
| `FINALIZED`       | The transaction reached chain finality                                                                |
| `FAILED`          | The transaction definitively failed; inspect the structured error, failed stage, and suggested action |
| `ACTION_REQUIRED` | The outcome is uncertain; reconcile it using `tx_hash` / `cloid`                                      |

`ticket.snapshot()` returns the current immutable business view, while
`client.transactions.snapshots()` returns the latest view of all transactions
tracked by this client. Lookup is also available through
`client.transactions.get(tx_hash)` and
`client.transactions.get_by_cloid(cloid)`.

`SubmissionTimeout`, `InclusionTimeout`, and `FinalizationTimeout` identify the
failed wait stage. A timeout does not prove that the transaction failed: when
certainty is `UNKNOWN`, or the handle reaches `ACTION_REQUIRED`, reconcile the
node/API state using `tx_hash` or `cloid` before retrying. Only treat
`ticket.safe_to_retry` as permission to retry; never blindly resubmit an
unknown outcome.

`print_state` defaults to `False`. When enabled, the manager's background worker
prints one structured, redacted JSON object per state transition, so printing
does not run on the submission callback hot path. It is convenient for local
debugging and operations, but it is not a persistent audit log. The manager,
its indexes, and its snapshots exist only in the current process; a
multi-process or restart-safe strategy must persist/reconcile its own order
identity and outcome data.

Completed transaction history and resolved block data are bounded to prevent
long-running market-maker processes from retaining every transaction forever.
By default, each client keeps the latest 10,000 completed transactions and 256
resolved blocks:

```python
client = dx.AsyncChainClient(
    ...,
    max_completed_transactions=10_000,
    max_resolved_blocks=256,
)
```

The same options are available on `ChainClient`. Active transactions are never
evicted. Evicting an old completed transaction from client indexes does not
invalidate a ticket still held by application code. Persist audit history
outside the SDK before relying on these bounded in-process indexes.

Timestamp-nonce allocation is unique only within the current client process.
Coordinate explicit nonces when multiple processes or machines submit for the
same account. For latency-sensitive paths, pass `market_id` for perp and the
bytes32 `pair` for spot directly; the async order methods deliberately avoid a
symbol-resolution RPC.

The async client also protects urgent market-maker actions from ordinary quote
traffic:

- `node_pool_limit_per_account` describes the connected node's per-account
  transaction-pool limit and defaults to 50.
- Normal submissions use `max_pool_transactions_per_account`, which defaults
  to 48.
- `priority_pool_reserve` defaults to 2, so fast cancels and explicit
  replacements can use up to 50 pool positions with the default configuration.
- A cancel submitted with `fast_cancel=True`, or an explicit
  `await ticket.replace()` no-op, may use the configured priority reserve.
- Urgent encoding overtakes normal encoding jobs that have not started.
  Encoding already in progress is allowed to finish, because the cached
  Substrate runtime encoder must remain serialized.

This SDK-side priority complements the chain transaction priority; it does not
guarantee inclusion in the current block. The SDK validates that the normal
limit plus the priority reserve does not exceed the configured node limit:

```python
client = dx.AsyncChainClient(
    ...,
    node_pool_limit_per_account=50,
    max_pool_transactions_per_account=48,
    priority_pool_reserve=2,
)
```

The defaults are appropriate for a node with a per-account pool limit of 50.
When connecting to a custom node with a different limit, configure both the
node capability and the amount the strategy should use:

```python
client = dx.AsyncChainClient(
    ...,
    node_pool_limit_per_account=100,
    max_pool_transactions_per_account=96,
    priority_pool_reserve=4,
)
```

`ChainClient` accepts the same three options for its synchronous transaction
tickets. Setting a larger node limit alone does not increase strategy traffic;
raise `max_pool_transactions_per_account` explicitly after considering stale
quote risk.

## API usage (v1)

```python
api = dx.ApiClient(base_url="http://127.0.0.1:8080")

ping = api.v1.ping()
subaccounts = api.v1.account.wallet_subaccounts(address="0xYOUR_WALLET")
subaccount = api.v1.account.subaccount_info(address="0xYOUR_SUBACCOUNT")

spot_markets = api.v1.spot.markets()
spot_candles = api.v1.spot.candles(
    symbol="ETH-USDC",
    interval="1m",
    limit=100,
)

perp_market = api.v1.perp.market(symbol="ETH-USDC")
funding = api.v1.perp.funding_rate(symbol="ETH-USDC")
lending_status = api.v1.lending.market_status(asset="USDC")
```

`api.v1.ws.websocket_url()` returns the v1 WebSocket endpoint URL.
For request payload construction, `deepx_sdk.ws_client` also exposes
`v1_subscribe(...)`, `v1_unsubscribe(...)`, `v1_list(...)`, and `v1_post(...)`.

## On-chain usage

### Precompile defaults

- Perp: `0x000000000000000000000000000000000000044E` (1102)
- Spot: `0x000000000000000000000000000000000000044D` (1101)
- Lending: `0x0000000000000000000000000000000000000450` (1104)
- Subaccount: `0x0000000000000000000000000000000000000451` (1105)
- System: `0x0000000000000000000000000000000000000452` (1106)

`ChainClient` accepts module-specific precompile fields:

- `perp_precompile_address`
- `spot_precompile_address`
- `lending_precompile_address`
- `subaccount_precompile_address`
- `system_precompile_address`
- `evm_rpc_user_agent`
- `evm_rpc_headers`
- `evm_rpc_timeout`

You can override the precompile address per call via `precompile_address`.

Notes:

- `pair` is a bytes32 hex string (64 hex chars, with or without `0x`).
- Runtime compatibility (new node): `MarketSpec.min_order_size` was renamed
  to `min_qty` on-chain. SDK keeps `min_order_size` and also exposes
  `min_qty` aliases in Python objects.
- Optional per-tx overrides on timestamp-nonce tx paths
  (`chain.perp_market.place_*` / `cancel_order` /
  `close_position_limit` / `close_position` / `close_position_market`,
  `chain.spot_market.*`):
  `chain_id`, `gas_limit`, `max_fee_per_gas`, `max_priority_fee_per_gas`,
  `use_legacy`, `nonce_ms`, `wait_for_finalized`, `timeout_ms`.
  Fee fields default to `0`. If RPC gas estimation fails for a precompile
  call, SDK signs with `gas_limit=500000`; pass `gas_limit` to override.
- Optional per-tx overrides on transaction-count nonce tx paths
  (`chain.subaccount_client.*`, `chain.lending.*`,
  `chain.perp_market.set_profit_and_loss_point`):
  `chain_id`, `gas_limit`, `max_fee_per_gas`, `max_priority_fee_per_gas`,
  `use_legacy`, `nonce`, `wait_for_finalized`, `timeout_ms`.

### Perp market orders

```python
res = chain.perp_market.place_perp_order_limit(
    market_id=3,
    is_long=True,
    size=123,
    price=456,
)
print(res.order_id, res.tx_hash)
```

```python
res = chain.perp_market.place_perp_order_market(
    market_id=3,
    is_long=True,
    size=123,
    slippage=100,  # bps, optional; 100 = tolerate 1% adverse move vs oracle
)
print(res.order_id, res.tx_hash)
```

`place_perp_order_market` always sends `price=0` and ignores take_profit/stop_loss.
`slippage` (bps, 1 bps = 0.01%) caps the max adverse price vs the oracle for a
market order; `None` means no user cap (market `max_deviation_bps` still applies).

```python
# IOC (Immediate or Cancel): fill what can be filled immediately,
# cancel the remainder. Never rests on the GTC order book.
# post_only is forced to None; take_profit/stop_loss are not supported.
res = chain.perp_market.place_perp_order_ioc(
    market_id=3,
    is_long=True,
    size=123,
    price=456,
    reduce_only=False,
)
print(res.order_id, res.tx_hash)
```

Leverage is **not** a per-order parameter — it's a per-subaccount sizing cap
(`max_notional = available_margin × effective_leverage`), set globally or per
market before trading. Values are scaled by `LEVERAGE_PRECISION` (1000):
10x = 10000. The more conservative of global vs per-market wins; `None` clears
an override.

```python
chain.perp_market.set_global_leverage(max_leverage=10_000)            # 10x global
chain.perp_market.set_per_market_leverage(market_id=3, max_leverage=3_000)   # 3x for market 3
chain.perp_market.set_per_market_leverage(market_id=3, max_leverage=None)    # clear override

chain.perp_market.global_max_leverage_for()                 # -> 10000
chain.perp_market.per_market_max_leverage_for(market_id=3)  # -> 0 (no override)
chain.perp_market.effective_leverage_for(market_id=3)       # -> min(global, override or global)
```

A runnable version is in [`examples/leverage.py`](examples/leverage.py).

Orders can also be placed through the REST API — it builds a signed extrinsic
client-side and submits via `/v1/chain/tx/*`: `api.v1.chain_tx.place_perp_order_ioc(...)`.
Both paths take the same order parameters. On-chain reverts surface as
`ChainError` and REST rejections as `APIError` (see [Error codes](#error-codes)).

The high-level `chain.perp_market.place_order(..., order_type="ioc")` dispatcher
also routes to `place_perp_order_ioc`. Accepted aliases: `"ioc"`, `"I"`, `"IOC"`, `3`.

All perp place methods accept an optional `cloid` (client order id, `int`) — and
so do the `place_order` dispatcher and the REST `api.v1.chain_tx.place_perp_order*`
methods. A cloid becomes the order's oid, so you can cancel immediately without
waiting for the system oid. Valid range on-chain: `[2**31 - 1, 2**32 - 2]`;
a cloid is consumed forever once used (even after fill/cancel) — reuse is
rejected with `22_76 PerpDuplicateClientOrderId`, out-of-range with
`22_75 PlacePerpExceedClientOrderId`.

```python
res = chain.perp_market.place_perp_order_ioc(market_id=3, is_long=True, size=123,
                                             price=456, cloid=2**31 - 1)
assert res.order_id == 2**31 - 1
chain.perp_market.cancel_order(market_id=3, order_id=res.order_id)
```

A runnable version is in [`examples/cloid_orders.py`](examples/cloid_orders.py).

```python
res = chain.perp_market.cancel_order(market_id=3, order_id=12345)
print(res.order_id, res.tx_hash)
```

`cancel_order` (and `api.v1.chain_tx.cancel_perp_order`) accepts an optional
`fast_cancel=True`: the chain skips the `OrderCancelled` event and prioritizes
the cancel, so the SDK waits for inclusion only and echoes the requested
`order_id` instead of parsing it from the event.

`modify_order` atomically cancels an open order and places a new one in a
single extrinsic (`Subaccount.modify_orders`, transactional — the old order
survives if the new one fails any PlaceOrder check). The new order is a fresh
order, so all parameters are explicit; success returns a NEW `order_id`
(`res.canceled_order_id` is the old one). Perp additionally supports
`new_total_quantity` (total size including the filled part: SDK places
`new_total - filled`; equal → cancel-only; smaller → local `ValueError`):

```python
res = chain.perp_market.modify_order(
    order_id=old_oid, market_id=3, is_long=True,
    price=1_400_000_000, size=10**15, cloid=2**31 - 1,
)
print(res.canceled_order_id, "->", res.order_id)

res = chain.spot_market.modify_order(
    side="buy", order_id=old_oid, pair="0x...32bytes...",
    quote_amount=1_400_000, base_amount=10**15,
)
```

A runnable version is in [`examples/modify_order.py`](examples/modify_order.py).

```python
res = chain.perp_market.close_position_limit(
    market_id=3,
    price=123,
    slippage=None,
)
print(res.order_id, res.tx_hash)
```

PnL settlement converts a position's floating PnL + pending funding into real
USDC deposit/borrow (borrows accrue interest, so floating losses get more
expensive once settled). The platform cranker settles losing positions; settle
profitable ones yourself. It's permissionless and idempotent:

```python
res = chain.perp_market.settle_pnl(market_id=3)
print(res.unrealized, res.funding, res.total)  # i128 USDC base units from the SettlePnl event

chain.perp_market.settle_pnl()  # no market_id -> settle all markets (inclusion only)
```

A runnable version is in [`examples/settle_pnl.py`](examples/settle_pnl.py).

```python
res = chain.perp_market.close_position_market(
    market_id=3,
    slippage=None,
)
print(res.order_id, res.tx_hash)
```

```python
res = chain.perp_market.set_profit_and_loss_point(
    market_id=3,
    take_profit_point=123,
    stop_loss_point=100,
)
print(res.tx_hash, res.extrinsic_hash)
```

### Spot market orders

```python
res = chain.spot_market.subaccount_place_order_buy_b(
    pair="0x...32bytes...",
    quote_amount=1000,
    base_amount=500,
    post_only=0,
    reduce_only=False,
)
print(res.order_id, res.tx_hash)
```

```python
# IOC (Immediate or Cancel) spot orders: fill immediately, cancel the rest.
# post_only is forced to None (chain rejects it for IOC).
res = chain.spot_market.subaccount_place_order_buy_ioc_b(
    pair="0x...32bytes...",
    quote_amount=1000,
    base_amount=500,
    reduce_only=False,
)
print(res.order_id, res.tx_hash)

res = chain.spot_market.subaccount_place_order_sell_ioc_b(
    pair="0x...32bytes...",
    quote_amount=1000,
    base_amount=500,
)
print(res.order_id, res.tx_hash)
```

The high-level `chain.spot_market.place_order(..., order_type="ioc")` dispatcher
also routes to `subaccount_place_order_{buy,sell}_ioc_b`. Accepted aliases:
`"ioc"`, `"I"`, `"IOC"`, `3`.

Spot place methods also accept an optional `cloid` (same semantics and range as
perp; spot-specific errors are `20_43 PlaceSpotExceedClientOrderId` /
`20_45 SpotDuplicateClientOrderId`). Note `auto_cancel` no longer exists
on-chain; the kwarg is kept for compatibility but ignored.

```python
res = chain.spot_market.subaccount_cancel_order_buy_b(
    pair="0x...32bytes...",
    order_id=12345,
)
print(res.order_id, res.tx_hash)
```

Spot cancels (`subaccount_cancel_order_{buy,sell}_b`, the `cancel_order`
dispatcher, and `api.v1.chain_tx.cancel_spot_order_{buy,sell}`) accept
`fast_cancel=True` with the same semantics as perp.

### Intra-block action ordering (protocol guarantee)

Within a block, actions execute in a fixed category order: **(1) no_op →
(2) cancels → (3) order-book actions (place/modify/close) → (4) others**.
This is a protocol-level guarantee, so SDK users can rely on it:

- **Cancel-then-place is safe without waiting.** If you fire a cancel and a
  new place back-to-back and they land in the same block, the cancel executes
  first — the freed margin/order slot is available to the new order. You do
  not need to wait for the cancel's confirmation before re-placing.
  (`modify_order` is still the cleaner atomic primitive for price/size
  changes — it's a single transactional extrinsic in category 3.)
- **`fast_cancel` priority is mempool-selection, not block order.** A
  `fast_cancel=True` tx is more likely to be *included* in the current block;
  inside the block it executes in the same cancel category as regular cancels.
- Within a category, actions run in proposer submission order — timestamp
  nonces do not reorder execution.

### Perp market view calls

```python
market = chain.perp_market.perp_markets(market_id=3)
print(market.base_symbol, market.mark_price)

positions = chain.perp_market.user_perp_positions(
    user="0xYOUR_SUBACCOUNT",
    market_ids=[3],
)

orders = chain.perp_market.user_active_orders(user="0xYOUR_SUBACCOUNT")
order = chain.perp_market.order_info(user="0xYOUR_SUBACCOUNT", order_id=12345)

liq = chain.perp_market.get_liquidate_price(account="0xYOUR_SUBACCOUNT", market_id=3)
oracle = chain.perp_market.get_oracle_price_all()
```

### Spot market view calls

```python
orders = chain.spot_market.user_active_spot_orders(
    user="0xYOUR_SUBACCOUNT",
    pair="0x...32bytes...",  # or omit/None for all pairs
)

spec = chain.spot_market.get_spot_market_spec(
    pair="0x...32bytes...",
)
```

### Subaccount calls

```python
res = chain.subaccount_client.initialize_subaccount(name="demo")
print(res.tx_hash, res.event)
```

```python
res = chain.subaccount_client.rename_subaccount(
    subaccount="0xYOUR_SUBACCOUNT",
    new_name="new-name",
)
print(res.tx_hash, res.event)  # event can be None for non-event calls
```

`no_op` consumes a timestamp nonce with no state change. Its main use is
replacing a stuck pending transaction: submit it with the **same** `nonce_ms`
as the pending tx — `no_op` has the highest mempool priority, so it evicts the
pending tx and permanently consumes that nonce slot (the old tx can never
execute afterwards). With `nonce_ms=None` a fresh millisecond timestamp is
used. A reused/expired nonce is rejected with a 1010 pool error.

```python
res = chain.subaccount_client.no_op(nonce_ms=1781757000123)  # replace pending tx with that nonce
print(res.tx_hash)  # event is always None — no_op emits nothing
```

A runnable version is in [`examples/noop_replace_pending.py`](examples/noop_replace_pending.py).

```python
res = chain.subaccount_client.liquidate_perp_by_transfer(
    market_index=3,
    liquidator_max_base_amount=10**18,
    limit_price=None,  # None -> 0 (no limit)
    target_subaccount="0xTARGET_SUBACCOUNT",
    liquidator="0xLIQUIDATOR_SUBACCOUNT",
)
print(res.tx_hash, res.event)

res = chain.subaccount_client.liquidate_spot_by_transfer(
    asset_symbol="eth",
    liability_symbol="usdc",
    target_account_addr="0xTARGET_SUBACCOUNT",
    liquidator="0xLIQUIDATOR_SUBACCOUNT",
    limit_price=None,  # None -> 0 (no limit)
    liquidator_max_liability_transfer=10**18,
    lending_market_id=1,
)
print(res.tx_hash, res.event)

res = chain.subaccount_client.liquidate_by_market(
    target_subaccount="0xTARGET_SUBACCOUNT",
    liquidator="0xLIQUIDATOR_SUBACCOUNT",
)
print(res.tx_hash, res.event)  # liquidation events are conditional
```

```python
stats = chain.subaccount_client.user_stats(address="0xYOUR_OWNER")
info = chain.subaccount_client.subaccount_info(address="0xYOUR_SUBACCOUNT")
oct_accounts = chain.subaccount_client.one_click_trading_accounts_for(owner="0xYOUR_OWNER")
delegates = chain.subaccount_client.delegate_accounts(user="0xYOUR_OWNER")
print(stats, info, oct_accounts, delegates)
```

Delegate accounts (order-only operators for a subaccount) take a display name
and an expiry (wall-clock ms; past values are rejected with `19_34
DelegateExpiry`). A subaccount can have multiple delegates; re-setting the
same delegate updates its name/expiry:

```python
chain.subaccount_client.set_delegate_account(
    subaccount="0xYOUR_SUBACCOUNT",
    delegate="0xDELEGATE_ADDRESS",
    name="mm-bot",
    valid_until=int(time.time() * 1000) + 86_400_000,  # +24h
)
chain.subaccount_client.remove_delegate_account(
    subaccount="0xYOUR_SUBACCOUNT",
    delegate="0xDELEGATE_ADDRESS",
)
```

### Chain return fields

`chain.perp_market.perp_markets(market_id=...)` decodes using the latest chain layout.

- Fields: `id`, `name`, `base_symbol`, `base_address`, `base_decimal`, `quote_market_id`, `quote_symbol`, `quote_address`, `quote_decimal`, `network`, `height`, `funding_rate`, `last_cacl_funding_rate_time`, `oracle_price`, `mark_price`, `max_deviation_bps`, `initial_margin_ratio`, `maintenance_margin_ratio`, `max_active_orders`, `is_quote_market`, `taker_fee_rate`, `maker_fee_rate`, `order_spec`, `open_interest`, `long_open_pos_num`, `short_open_pos_num`, `base_interest_rate`, `impact_margin_value`, `funding_rate_clamp_upper_bound`, `funding_rate_clamp_lower_bound`, `liquidation_spec`.
- `order_spec` fields: `min_order_size`, `tick_size`, `step_size`,
  optional `min_notional`; alias `min_qty` is also available.
- `liquidation_spec` fields: `liquidation_duration`, `liquidity_bucket_slippage_step`, `liquidity_bucket_slippage_limit`, `liquidity_dust_value`, `liquidation_fee_rate`.
- `liquidation_fee_rate` fields: `liquidator_share_fee_rate`, `insurance_fund_share_fee_rate`.

`chain.market.get_perp_price_bounds(market_id=...)` also returns
`min_order_size` with alias `min_qty`, and optional `min_notional`.

`chain.subaccount_client.subaccount_info(address=...)` supports the latest `AccountInfo` layout, the delegates-vec layout, and the current external legacy `User` layout.

- Always available: `authority`, `delegate`, `name`, `spot_positions`, `borrow_positions`, `next_order_id`, `status`, `is_margin_trading_enabled`.
- Delegates-vec layout: `delegates` is a list of `DelegateInfo(delegate_address, delegate_name, valid_until)`; `delegate` is empty there (and on the legacy layout `delegates` is `None`).
- Latest `AccountInfo` fields (legacy `User` returns `None`): `address`, `liquidation_start_at`, `next_liquidation_id`, `margin_strategy`.
- On latest `AccountInfo`, `borrow_positions` is returned as an empty list because that layout does not include lending borrow details.

### Lending calls

```python
market = chain.lending.lending_markets(market_id=1)
print("market:", market)

pools = chain.lending.asset_pools(market_id=1)
print("asset_pools:", pools)

# LendingAssetPoolState includes: supply_cap, borrow_cap

health = chain.lending.health_for(subaccount="0xYOUR_SUBACCOUNT")
print("health_for:", health)

max_borrow = chain.lending.max_borrow_amount_for(
    account="0xYOUR_SUBACCOUNT",
    lending_market=1,
    asset="usdc",  # bytes or hex string
)
print("max_borrow_amount_for:", max_borrow)

res = chain.lending.deposit(
    subaccount="0xYOUR_SUBACCOUNT",
    asset="usdc",
    amount=10**6,  # USDC has 6 decimals on-chain
)
print(res.tx_hash, res.event)

res = chain.lending.deposit_from_subaccount(
    from_subaccount="0xFROM_SUBACCOUNT",
    subaccount="0xYOUR_SUBACCOUNT",
    asset="usdc",
    amount=10**6,
    auto_borrow=False,
)
print(res.tx_hash, res.event)
```

### System account quota

```python
info = chain.system.system_account(address="0xYOUR_ADDRESS")
print(info.quota, info.is_exist)
```

Note:

- The SDK is pure Python. No native build step is required.

### Quota: query & claim

Every order/cancel costs 1 quota; trading volume earns claimable quota.
Claiming is **backend-executed**: users can't add quota on-chain themselves
(chain `add_quota` is restricted to authorized accounts) — the SDK signs a
fixed message and the backend submits the chain tx asynchronously.

```python
q = api.v1.account.wallet_quota(address="0xYOUR_WALLET")     # {claimable, remaining}
s = api.v1.account.quota_summary(wallet="0xYOUR_WALLET")     # earned/granted/pending + volumes (internal API)

# personal-signs with the client's private_key (or pass private_key=/wallet=)
res = api.v1.account.claim_quota(wallet="0xYOUR_WALLET")     # POST /v1/account/quota/claim
claim_id = res["claim"]["id"]                                # raw object; status="noop" when nothing to claim

final = api.v1.account.wait_quota_claim(claim_id=claim_id, timeout_s=120)  # polls to confirmed
```

`idempotency_key` makes retries safe (same key → same claim; one active claim
per wallet). The on-chain view of the same quota is
`chain.system.system_account(address).quota` (0 = not activated,
2\*\*32-1 = frozen).

Quota can also be **bought** directly on-chain (`Lending.buy_quota`): cost is
`QuoteAmountPerQuota × quota` in USDC (devnet: 500 base units = 0.0005 USDC
per quota). Paid from the signer's wallet by default, or from a subaccount's
spot balance via `from_subaccount`. Note the extrinsic itself costs 1 quota,
so buying N quota nets N−1.

```python
res = chain.lending.buy_quota(
    account="0xYOUR_WALLET",            # beneficiary (quota is wallet-level)
    quota=100,
    from_subaccount="0xYOUR_SUBACCOUNT",  # optional: pay from subaccount spot USDC
)
```

A runnable version is in [`examples/quota.py`](examples/quota.py).

## Error codes

The SDK exposes two parallel error registries that mirror the upstream
specs (`https://github.com/deepxfinance/notes-and-specs/blob/main/specs/error-codes.md`):

| Source                       | Code format                                             | Range                       | When raised                                                     |
| ---------------------------- | ------------------------------------------------------- | --------------------------- | --------------------------------------------------------------- |
| **On-chain** (pallet errors) | `"<pallet_index>_<error_index>"` string, e.g. `"22_17"` | Pallet 19/20/21/22/23/24/26 | Transaction was submitted and reverted on-chain                 |
| **REST API**                 | Sequential integer, e.g. `10010`                        | `10001`+                    | Request was rejected by the API layer before reaching the chain |

### Typed exceptions

```python
from deepx_sdk import ChainError, APIError

# Raised when a transaction reverts on-chain. Carries pallet metadata.
try:
    client.perp_market.place_perp_order_limit(
        market_id=3, is_long=True, size=100, price=1000,
    )
except ChainError as e:
    print(e.code)        # "22_48"
    print(e.name)        # "PerpOrderMarginExceed"
    print(e.pallet)      # "PerpMarket"
    print(e.pallet_index)  # 22
    print(e.error_index)   # 48

# Raised when the REST API rejects a request. Carries category metadata.
try:
    api.v1.perp.market(symbol="DOES-NOT-EXIST")
except APIError as e:
    print(e.code)        # 10007
    print(e.category)    # "NOT_FOUND"
    print(e.message)     # "Market 'DOES-NOT-EXIST' does not exist or is not active."
```

### Looking up codes directly

```python
from deepx_sdk._error_codes import lookup_chain_error, lookup_api_error, format_msg

# Look up a chain error by its code
entry = lookup_chain_error("22_73")
print(entry.name, entry.pallet, entry.msg)
# UnsupportedTimeInForce PerpMarket Unsupported time-in-force (TIF) type.

# Look up an API error by its integer code
entry = lookup_api_error(10010)
print(entry.category, entry.format(retryAfter=30))
# RATE_LIMIT Rate limit exceeded. Retry after 30 seconds.

# Render a message template with placeholders
print(format_msg("Invalid parameter: {param}.", param="side"))
# Invalid parameter: side.
```

### Error categories

| Category     | Source | Meaning                                                                |
| ------------ | ------ | ---------------------------------------------------------------------- |
| `ON_CHAIN`   | Chain  | Transaction was executed on-chain and reverted                         |
| `VALIDATION` | API    | Request parameters failed client-side validation                       |
| `AUTH`       | API    | Authentication / signature failure                                     |
| `NOT_FOUND`  | API    | Requested resource (market, order, subaccount, channel) does not exist |
| `RATE_LIMIT` | API    | Request rate limit exceeded                                            |
| `CONFLICT`   | API    | Operation conflicts with current resource state                        |
| `INTERNAL`   | API    | Backend-side error or service unavailability                           |

### Adding new error codes

When the upstream YAML registries change:

1. Update the matching entry in `_error_codes.py` (the `_CHAIN_ENTRIES` /
   `_API_ENTRIES` tuples at the top of the module).
2. The test suite (`tests/test_error_codes.py`) enforces registry invariants —
   sequential numbering from `10001`, pallet-index alignment, name format,
   and category coverage. Any deviation fails the build.

## WebSocket usage (v1)

The websocket client lives in `deepx_sdk.ws_client`.

Basic subscribe example:

```python
import asyncio
from deepx_sdk.ws_client import WsClient, v1_sub_perp_ticker


async def main() -> None:
    client = WsClient(base_url="http://127.0.0.1:8080")
    async with await client.connect() as ws:
        await ws.send_json(v1_sub_perp_ticker("ticker-1", symbol="ETH-USDC"))
        msg = await ws.recv_json()
        print("ws message:", msg)


asyncio.run(main())
```

Account channel example:

```python
import asyncio
from deepx_sdk.ws_client import WsClient, v1_sub_account_perp_orders


async def main() -> None:
    client = WsClient(base_url="http://127.0.0.1:8080")
    async with await client.connect() as ws:
        await ws.send_json(
            v1_sub_account_perp_orders(
                "orders-1",
                wallet="0xYOUR_WALLET",
                symbol="ETH-USDC",
            )
        )
        print(await ws.recv_json())


asyncio.run(main())
```

Notes:

- Client messages use `method` with optional `id`, `params`, or `request`.
- Supported methods are `subscribe`, `unsubscribe`, `list`, `post`, `ping`, and `pong`.
- Use `headers={"Authorization": "Bearer <API_KEY>"}` when your WS requires auth.
- Server subscription acks use channel `subscriptionResponse`.
- Server errors use channel `error`.
- Server may send `{"method": "ping"}`; reply with `{"method": "pong"}`.

### WebSocket subscription catalog (v1)

Market channels:

| Channel                 | Required params      | Optional params |
| ----------------------- | -------------------- | --------------- |
| `spot@orderbook`        | `symbol`             | -               |
| `spot@trades`           | `symbol`             | -               |
| `spot@ticker`           | `symbol`             | -               |
| `spot@candles`          | `symbol`, `interval` | -               |
| `perp@orderbook`        | `symbol`             | -               |
| `perp@trades`           | `symbol`             | -               |
| `perp@ticker`           | `symbol`             | -               |
| `perp@prices`           | `symbol`             | -               |
| `perp@funding-rate`     | `symbol`             | -               |
| `perp@open-interest`    | `symbol`             | `interval`      |
| `perp@long-short-ratio` | `symbol`             | -               |
| `perp@candles`          | `symbol`, `interval` | -               |
| `lending@market-status` | -                    | `asset`         |

Account channels:

| Channel                  | Required params          | Optional params |
| ------------------------ | ------------------------ | --------------- |
| `account@balances`       | `subaccount`             | -               |
| `account@portfolio`      | `subaccount`             | -               |
| `account@perp-positions` | `subaccount`             | `symbol`        |
| `account@perp-orders`    | `subaccount` or `wallet` | `symbol`        |
| `account@spot-orders`    | `subaccount` or `wallet` | `symbol`        |
| `account@perp-trades`    | `subaccount` or `wallet` | `symbol`        |
| `account@spot-trades`    | `subaccount` or `wallet` | `symbol`        |

## ETH-USDC examples

Perp market (ETH-USDC): `market_id = 3`

```python
from decimal import Decimal
import deepx_sdk as dx

chain = dx.ChainClient(
    substrate_ws="ws://127.0.0.1:9944",
    evm_rpc_url="http://127.0.0.1:8545",
    private_key="0xYOUR_PRIVATE_KEY",
    perp_precompile_address="0x000000000000000000000000000000000000044E",
    subaccount="0xYOUR_SUBACCOUNT",
)

size = int(Decimal("0.01") * (10 ** 18))      # base (ETH) uses 1e18
price = int(Decimal("2200.50") * (10 ** 6))   # perp price uses 1e6 (quote USD)

res = chain.perp_market.place_perp_order_limit(
    market_id=3,
    is_long=True,
    size=size,
    price=price,
)
print(res.order_id, res.tx_hash)
```

Spot pair (ETH/USDC):
`pair = 0x950c1bb15508369148679bf2921417929f1465c068c4b22a980c3c23535846c0`

```python
pair = "0x950c1bb15508369148679bf2921417929f1465c068c4b22a980c3c23535846c0"

quote_amount = int(Decimal("100") * (10 ** 6))   # USDC decimals = 6
base_amount = int(Decimal("0.05") * (10 ** 18))

res = chain.spot_market.subaccount_place_order_buy_b(
    pair=pair,
    quote_amount=quote_amount,
    base_amount=base_amount,
    post_only=0,
    reduce_only=False,
    slippage=None,  # None for limit orders
    auto_cancel=False,
    chain_id=None,
    gas_limit=None,
    max_fee_per_gas=None,
    max_priority_fee_per_gas=None,
    use_legacy=False,
    nonce_ms=None,
    wait_for_finalized=True,
)
print(res.order_id, res.tx_hash)
```
