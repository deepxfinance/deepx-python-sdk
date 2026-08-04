# Hyperliquid Python SDK vs DeepX SDK: transaction state model comparison

> Date: 2026-08-04
> Method: cloned [hyperliquid-dex/hyperliquid-python-sdk](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)
> and read the sources (`exchange.py` / `api.py` / `websocket_manager.py` /
> `utils/types.py` / examples), then compared against deepx-python-sdk's
> transaction lifecycle design.
> Question: does the Hyperliquid SDK have chain states like DeepX's
> ACCEPTED / EXECUTED?

## Conclusion

**No.** The Hyperliquid SDK has no ACCEPTED/EXECUTED-style chain state machine
at all — and that's a consequence of architecture, not an oversight.

## Hyperliquid's order model: one HTTP round-trip returns the final state

The order path in `exchange.py`:

```text
order(...) → bulk_orders(...) → local signature → POST /exchange → JSON response
```

The JSON response **is the final business outcome**:

```python
{"status": "ok", "response": {"data": {"statuses": [
    {"resting": {"oid": ...}}      # resting on the book
    {"filled": {...}}              # filled
    {"error": "insufficient..."}   # failure reason string
]}}}
```

There is no mempool, no "inclusion", no "finalization" — Hyperliquid's API
nodes act as the submitter and **hide the entire chain interaction behind the
backend**; by the time the SDK sees the response, everything is settled. The
nonce is just a millisecond timestamp; there is no reconciliation, no retry
policy, no state tracking (`api.py` is a bare `session.post(timeout=...)`).

Later order-state changes arrive via **WebSocket subscription pass-through**
(`orderUpdates` / `userEvents`); the SDK forwards messages to the user's
callback without maintaining any state of its own.

## Comparison

| | Hyperliquid SDK | DeepX SDK |
|---|---|---|
| Essence | client of a centralized API gateway | true chain client (direct substrate extrinsics) |
| Order return | one POST = final state (resting/filled/error) | ACCEPT → ticket returned; EXECUTED/FINALIZED arrive later |
| State machine | **none** | CREATED→SUBMITTING→ACCEPTED→EXECUTED→FINALIZED / FAILED / ACTION_REQUIRED |
| Failure semantics | HTTP code + error string | staged errors + outcome certainty (unknown ⇒ do not blindly retry) + retryability flags |
| Intermediate states | invisible | fully observable (accept_ms / exec_ms metrics come from this) |
| Reconciliation | WS push + REST query (query_order_by_oid); no certainty semantics in the SDK | ticket states + cloid/tx_hash reconciliation + recovery flow |

## Implications for market-making bots / the product

- **Hyperliquid model**: simple and fast (one RTT to a final state), but a
  black box — when the response never arrives, you know nothing about the
  transaction's fate.
- **DeepX model**: more complex (tickets, event decoding, reconnects,
  reconciliation), but every stage is measurable and diagnosable.

That is exactly the product point: with Hyperliquid-style encapsulation,
fine-grained performance data like `accept_ms` / `exec_ms` **cannot exist** —
DeepX's state-machine design turns "the real performance of chain + SDK" into
quantifiable metrics. If we ever present the SDK's design differentiation
externally, this is the core argument.

## Appendix: DeepX-side measurement source

`bot/metrics.jsonl` (produced by the market-making bot running at 1-second
ticks on devnet):

- accept (mempool) p50 ≈ 370ms
- inclusion (on-chain confirmation) p50 ≈ 6-7s (devnet block cadence)
- trading-operation success rate > 99% over 2 hours of continuous running
