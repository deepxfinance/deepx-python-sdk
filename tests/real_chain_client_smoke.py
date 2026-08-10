from __future__ import annotations

import json
import os
import time
import traceback
import urllib.request
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import deepx_sdk as dx
from eth_account import Account
from eth_utils import keccak


EVM_RPC_URL = os.environ.get("DEEPX_EVM_RPC_URL", "https://devnet-rpc-new.deepx.fi")
SUBSTRATE_WS = os.environ.get("DEEPX_SUBSTRATE_WS", "wss://devnet-rpc-new.deepx.fi")
REST_URL = os.environ.get("DEEPX_REST_URL", "https://rest-api-devnet.deepx.fi")
REPORT = Path("/tmp/deepx_chain_client_real_report.json")
SECRET_KEY_FILE = Path(__file__).resolve().parents[1] / ".sk"

SUBACCOUNT = "0x6faeedfd51e04a183396195b43104d17d42c3bee"
FUNDED_SUBACCOUNT = "0xd1b75179e3b69e47732ece09b9f489d75233cef2"
WALLET = "0xBF34E1d049BcF588f7B8C80273259c3deA1AC3a3"
OTHER = "0x000000000000000000000000000000000000dEaD"
PAIR = "0x9068d4ac891a14784c17877eb74bd8489b3367c71d72766dbfa4dfbfb662fa37"
PERP_SYMBOL = "ETH-USDC"
SPOT_SYMBOL = "ETH-USDC"
PERP_MARKET_ID = 3
LENDING_MARKET_ID = 1
ASSET = "usdc"

PERP_SIZE_RAW = 1_000_000_000_000_000
PERP_PRICE_RAW = 1_000_000_000
SPOT_QUOTE_RAW = 2_000_000
SPOT_BUY_LIMIT_QUOTE_RAW = 1_000_000
SPOT_BASE_RAW = 1_000_000_000_000_000
SPOT_SELL_LIMIT_QUOTE_RAW = 1_800_000
BORROW_ASSET = "sol"
ZERO32 = "0x" + "00" * 32


def read_private_key() -> str:
    key = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError(f"empty private key file: {SECRET_KEY_FILE}")
    return key


def summarize(value: Any) -> str:
    if value is None:
        return "None"
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, list):
        return f"list(len={len(value)})"
    if isinstance(value, dict):
        return "dict(keys=" + ",".join(list(value.keys())[:8]) + ")"
    return type(value).__name__


def details(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, list):
        return {"len": len(value), "first": details(value[0]) if value else None}
    if isinstance(value, dict):
        out = {}
        for key in ("order_id", "tx_hash", "extrinsic_hash", "event", "market_id", "asset", "nonce", "gas_limit"):
            if key in value:
                out[key] = value[key]
        return out or {"keys": list(value.keys())[:8]}
    for key in ("order_id", "tx_hash", "extrinsic_hash", "event", "nonce", "gas_limit"):
        if hasattr(value, key):
            return {key: getattr(value, key) for key in ("order_id", "tx_hash", "extrinsic_hash", "event", "nonce", "gas_limit") if hasattr(value, key)}
    return repr(value)[:300]


def write_report(results: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> None:
    payload: Any
    if meta is None:
        payload = results
    else:
        passed = sum(1 for row in results if row["status"] == "PASS")
        blocked = sum(1 for row in results if row["status"] == "BLOCKED")
        failed = sum(1 for row in results if row["status"] == "FAIL")
        payload = {
            "meta": meta,
            "results": results,
            "passed": passed,
            "blocked": blocked,
            "failed": failed,
        }
    REPORT.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def record(results: list[dict[str, Any]], name: str, fn: Callable[[], Any]) -> Any:
    started = time.time()
    try:
        value = fn()
        row = {
            "name": name,
            "status": "PASS",
            "elapsed_ms": int((time.time() - started) * 1000),
            "summary": summarize(value),
            "details": details(value),
            "error": None,
        }
        results.append(row)
        write_report(results)
        print(f"{name}: PASS {row['details']}", flush=True)
        return value
    except Exception as exc:
        row = {
            "name": name,
            "status": "FAIL",
            "elapsed_ms": int((time.time() - started) * 1000),
            "summary": None,
            "details": None,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=3),
        }
        results.append(row)
        write_report(results)
        print(f"{name}: FAIL {row['error']}", flush=True)
        return None


def record_blocked(
    results: list[dict[str, Any]],
    name: str,
    reason: str,
    *,
    evidence: Any = None,
) -> None:
    row = {
        "name": name,
        "status": "BLOCKED",
        "elapsed_ms": 0,
        "summary": "business precondition not available",
        "details": evidence,
        "error": reason,
    }
    results.append(row)
    write_report(results)
    print(f"{name}: BLOCKED {reason}", flush=True)


def rpc_call(method: str, params: list[Any]) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        EVM_RPC_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "deepx-python-sdk/0.1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


def native_nonce(address: str) -> int:
    return int(rpc_call("eth_getTransactionCount", [address, "pending"]), 16)


def chain_id() -> int:
    return int(rpc_call("eth_chainId", []), 16)


class NonceManager:
    def __init__(self, start: int):
        self.value = start

    def next(self) -> int:
        value = self.value
        self.value += 1
        return value


def wait_index(fn: Callable[[], Any], attempts: int = 12, delay: float = 2.0) -> Any:
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            time.sleep(delay)
    if last_exc:
        raise last_exc
    raise TimeoutError("index wait exhausted")


def api_v1() -> Any:
    return dx.ApiClient(base_url="https://rest-api-devnet.deepx.fi", ws_base_url="wss://ws-api-devnet.deepx.fi").v1


def unique_h160(label: str, unique: int) -> str:
    digest = keccak(text=f"deepx-python-sdk:{label}:{unique}")
    return "0x" + digest[-20:].hex()


def event_subaccount(value: Any) -> str | None:
    event = getattr(value, "event", None)
    if isinstance(event, dict):
        raw = event.get("subaccount")
        if isinstance(raw, str) and raw.startswith("0x") and len(raw) == 42:
            return raw
    return None


def main() -> None:
    private_key = read_private_key()
    signer = Account.from_key(private_key).address
    try:
        resolved_chain_id = chain_id()
    except Exception as exc:
        print(f"chain id lookup failed, continuing with default 570: {type(exc).__name__}: {exc}", flush=True)
        resolved_chain_id = 570
    try:
        nonce_start = native_nonce(signer)
    except Exception as exc:
        print(f"native nonce lookup failed, continuing from 0: {type(exc).__name__}: {exc}", flush=True)
        nonce_start = 0
    nonce = NonceManager(nonce_start)
    client = dx.ChainClient(
        substrate_ws=SUBSTRATE_WS,
        evm_rpc_url=EVM_RPC_URL,
        private_key=private_key,
        subaccount=SUBACCOUNT,
        chain_id=resolved_chain_id,
        gas_limit=0,
        max_fee_per_gas=0,
        max_priority_fee_per_gas=0,
        use_legacy=False,
        wait_for_finalized=False,
        api_base_url=REST_URL,
    )
    v1 = api_v1()
    results: list[dict[str, Any]] = []
    meta = {
        "evm_rpc_url": EVM_RPC_URL,
        "substrate_ws": SUBSTRATE_WS,
        "rest_url": REST_URL,
        "chain_id": resolved_chain_id,
        "signer": signer,
    }
    write_report([], meta)

    # Top-level and market helpers.
    record(results, "chain_client.preload_markets", lambda: client.preload_markets())
    record(results, "market.get_perp_price_bounds", lambda: client.market.get_perp_price_bounds(PERP_MARKET_ID))

    # Perp actions and views.
    perp_alias = record(
        results,
        "perp_market.place_order.symbol_limit_tx_config",
        lambda: client.perp_market.place_order(
            symbol=PERP_SYMBOL,
            side="long",
            size=PERP_SIZE_RAW,
            price=PERP_PRICE_RAW,
            leverage=1,
            post_only=1,
            tx_config=dx.TxConfig(timeout_ms=30_000, wait_for_finalized=False),
        ),
    )
    if perp_alias is not None:
        record(
            results,
            "perp_market.cancel_order.symbol_tx_config",
            lambda: client.perp_market.cancel_order(
                symbol=PERP_SYMBOL,
                order_id=perp_alias.order_id,
                tx_config=dx.TxConfig(timeout_ms=30_000, wait_for_finalized=False),
            ),
        )
    else:
        record_blocked(results, "perp_market.cancel_order.symbol_tx_config", "place_order alias failed; cannot cancel an order that was not created")
    perp_limit = record(results, "perp_market.place_perp_order_limit", lambda: client.perp_market.place_perp_order_limit(market_id=PERP_MARKET_ID, is_long=True, size=PERP_SIZE_RAW, price=PERP_PRICE_RAW, leverage=1, post_only=1, timeout_ms=30_000))
    perp_limit_order_id = perp_limit.order_id if perp_limit is not None else 1
    record(results, "perp_market.cancel_order", lambda: client.perp_market.cancel_order(market_id=PERP_MARKET_ID, order_id=perp_limit_order_id, timeout_ms=30_000))
    perp_generic = record(results, "perp_market.place_perp_order", lambda: client.perp_market.place_perp_order(market_id=PERP_MARKET_ID, is_long=True, size=PERP_SIZE_RAW, price=PERP_PRICE_RAW, order_type=0, leverage=1, post_only=1, timeout_ms=30_000))
    perp_generic_order_id = perp_generic.order_id if perp_generic is not None else 1
    record(results, "perp_market.cancel_order.generic", lambda: client.perp_market.cancel_order(market_id=PERP_MARKET_ID, order_id=perp_generic_order_id, timeout_ms=30_000))
    record(results, "perp_market.place_perp_order_market.for_close_limit", lambda: client.perp_market.place_perp_order_market(market_id=PERP_MARKET_ID, is_long=True, size=PERP_SIZE_RAW, leverage=1, timeout_ms=30_000))
    record(results, "perp_market.close_position_limit", lambda: client.perp_market.close_position_limit(market_id=PERP_MARKET_ID, price=PERP_PRICE_RAW, timeout_ms=30_000))
    record(results, "perp_market.place_perp_order_market.for_close", lambda: client.perp_market.place_perp_order_market(market_id=PERP_MARKET_ID, is_long=True, size=PERP_SIZE_RAW, leverage=1, timeout_ms=30_000))
    record(results, "perp_market.close_position", lambda: client.perp_market.close_position(market_id=PERP_MARKET_ID, price=0, slippage=10, timeout_ms=30_000))
    record(results, "perp_market.place_perp_order_market.for_close_market", lambda: client.perp_market.place_perp_order_market(market_id=PERP_MARKET_ID, is_long=True, size=PERP_SIZE_RAW, leverage=1, timeout_ms=30_000))
    record(results, "perp_market.close_position_market", lambda: client.perp_market.close_position_market(market_id=PERP_MARKET_ID, slippage=10, timeout_ms=30_000))
    record(results, "perp_market.place_perp_order_market.for_pnl", lambda: client.perp_market.place_perp_order_market(market_id=PERP_MARKET_ID, is_long=True, size=PERP_SIZE_RAW, leverage=1, timeout_ms=30_000))
    record(results, "perp_market.set_profit_and_loss_point", lambda: client.perp_market.set_profit_and_loss_point(market_id=PERP_MARKET_ID, take_profit_point=2_000_000_000, timeout_ms=120_000))
    record(results, "perp_market.close_position_market.after_pnl_cleanup", lambda: client.perp_market.close_position_market(market_id=PERP_MARKET_ID, slippage=10, timeout_ms=30_000))

    record(results, "perp_market.perp_markets", lambda: client.perp_market.perp_markets(market_id=PERP_MARKET_ID))
    record(results, "perp_market.perp_markets.symbol", lambda: client.perp_market.perp_markets(symbol=PERP_SYMBOL))
    record(results, "perp_market.user_perp_positions", lambda: client.perp_market.user_perp_positions(user=SUBACCOUNT, market_ids=[PERP_MARKET_ID]))
    record(results, "perp_market.user_perp_positions.symbols", lambda: client.perp_market.user_perp_positions(user=SUBACCOUNT, symbols=[PERP_SYMBOL]))
    record(results, "perp_market.active_pos_for_market", lambda: client.perp_market.active_pos_for_market(market_id=PERP_MARKET_ID))
    record(results, "perp_market.active_pos_for_market.symbol", lambda: client.perp_market.active_pos_for_market(symbol=PERP_SYMBOL))
    record(results, "perp_market.user_active_orders", lambda: client.perp_market.user_active_orders(user=SUBACCOUNT))
    record(results, "perp_market.order_info", lambda: client.perp_market.order_info(user=SUBACCOUNT, order_id=perp_limit_order_id))
    record(results, "perp_market.free_deposit_for", lambda: client.perp_market.free_deposit_for(account=SUBACCOUNT))
    record(results, "perp_market.mark_price_for", lambda: client.perp_market.mark_price_for(market_id=PERP_MARKET_ID))
    record(results, "perp_market.mark_price_for.symbol", lambda: client.perp_market.mark_price_for(symbol=PERP_SYMBOL))
    record(results, "perp_market.last_trade_price_for", lambda: client.perp_market.last_trade_price_for(market_id=PERP_MARKET_ID))
    record(results, "perp_market.last_trade_price_for.symbol", lambda: client.perp_market.last_trade_price_for(symbol=PERP_SYMBOL))
    record(results, "perp_market.total_collateral_and_margin_required_for", lambda: client.perp_market.total_collateral_and_margin_required_for(account=SUBACCOUNT, direction=0))
    record(results, "perp_market.get_liquidate_price", lambda: client.perp_market.get_liquidate_price(account=SUBACCOUNT, market_id=PERP_MARKET_ID))
    record(results, "perp_market.get_oracle_price_all", lambda: client.perp_market.get_oracle_price_all())

    # Spot actions and views.
    # Use the owner's funded subaccount for spot buy paths. The default
    # subaccount can run out of quote balance after repeated real smokes.
    spot_client = dx.ChainClient(
        substrate_ws=SUBSTRATE_WS,
        evm_rpc_url=EVM_RPC_URL,
        private_key=private_key,
        subaccount=FUNDED_SUBACCOUNT,
        chain_id=resolved_chain_id,
        gas_limit=0,
        max_fee_per_gas=0,
        max_priority_fee_per_gas=0,
        use_legacy=False,
        wait_for_finalized=False,
        api_base_url=REST_URL,
    )
    spot_alias = record(
        results,
        "spot_market.place_order.symbol_limit_tx_config",
        lambda: spot_client.spot_market.place_order(
            symbol=SPOT_SYMBOL,
            side="buy",
            quote_amount=SPOT_BUY_LIMIT_QUOTE_RAW,
            base_amount=SPOT_BASE_RAW,
            post_only=1,
            tx_config=dx.TxConfig(timeout_ms=60_000, wait_for_finalized=False),
        ),
    )
    if spot_alias is not None:
        record(
            results,
            "spot_market.cancel_order.symbol_tx_config",
            lambda: spot_client.spot_market.cancel_order(
                symbol=SPOT_SYMBOL,
                side="buy",
                order_id=spot_alias.order_id,
                tx_config=dx.TxConfig(timeout_ms=60_000, wait_for_finalized=False),
            ),
        )
    else:
        record_blocked(results, "spot_market.cancel_order.symbol_tx_config", "place_order alias failed; cannot cancel a spot order that was not created")
    spot_buy = record(results, "spot_market.subaccount_place_order_buy_b", lambda: spot_client.spot_market.subaccount_place_order_buy_b(pair=PAIR, quote_amount=SPOT_BUY_LIMIT_QUOTE_RAW, base_amount=SPOT_BASE_RAW, post_only=1, timeout_ms=60_000))
    if spot_buy is not None:
        spot_buy_order_id = spot_buy.order_id
        record(results, "spot_market.subaccount_cancel_order_buy_b", lambda: spot_client.spot_market.subaccount_cancel_order_buy_b(pair=PAIR, order_id=spot_buy_order_id, timeout_ms=60_000))
    else:
        record_blocked(results, "spot_market.subaccount_cancel_order_buy_b", "place buy order failed; cannot cancel a buy order that was not created")
    record(results, "spot_market.subaccount_place_market_order_buy_b_without_price.funding", lambda: spot_client.spot_market.subaccount_place_market_order_buy_b_without_price(pair=PAIR, quote_amount=SPOT_QUOTE_RAW, base_amount=SPOT_BASE_RAW, timeout_ms=60_000))
    spot_sell = record(results, "spot_market.subaccount_place_order_sell_b", lambda: client.spot_market.subaccount_place_order_sell_b(pair=PAIR, quote_amount=SPOT_SELL_LIMIT_QUOTE_RAW, base_amount=SPOT_BASE_RAW, post_only=1, timeout_ms=30_000))
    spot_sell_order_id = spot_sell.order_id if spot_sell is not None else 1
    record(results, "spot_market.subaccount_cancel_order_sell_b", lambda: client.spot_market.subaccount_cancel_order_sell_b(pair=PAIR, order_id=spot_sell_order_id, timeout_ms=30_000))
    record(results, "spot_market.subaccount_place_market_order_buy_b_with_price", lambda: spot_client.spot_market.subaccount_place_market_order_buy_b_with_price(pair=PAIR, quote_amount=SPOT_QUOTE_RAW, base_amount=SPOT_BASE_RAW, slippage=10, timeout_ms=60_000))
    record(results, "spot_market.subaccount_place_market_order_sell_b_without_price", lambda: client.spot_market.subaccount_place_market_order_sell_b_without_price(pair=PAIR, quote_amount=SPOT_QUOTE_RAW, base_amount=SPOT_BASE_RAW, timeout_ms=30_000))
    record(results, "spot_market.subaccount_place_market_order_buy_b_without_price", lambda: spot_client.spot_market.subaccount_place_market_order_buy_b_without_price(pair=PAIR, quote_amount=SPOT_QUOTE_RAW, base_amount=SPOT_BASE_RAW, timeout_ms=60_000))
    record(results, "spot_market.subaccount_place_market_order_sell_b_with_price", lambda: client.spot_market.subaccount_place_market_order_sell_b_with_price(pair=PAIR, quote_amount=SPOT_QUOTE_RAW, base_amount=SPOT_BASE_RAW, slippage=10, timeout_ms=30_000))
    record(results, "spot_market.user_active_spot_orders", lambda: client.spot_market.user_active_spot_orders(user=SUBACCOUNT, pair=PAIR))
    record(results, "spot_market.get_spot_market_spec", lambda: client.spot_market.get_spot_market_spec(pair=PAIR))
    record(results, "spot_market.get_spot_market_spec.symbol", lambda: client.spot_market.get_spot_market_spec(symbol=SPOT_SYMBOL))

    # Subaccount actions and views.
    unique = int(time.time() * 1000)
    delegate = unique_h160("delegate", unique)
    lifecycle = record(results, "subaccount_client.initialize_subaccount", lambda: client.subaccount_client.initialize_subaccount(name=f"codex-{unique}".encode(), timeout_ms=120_000))
    lifecycle_subaccount = event_subaccount(lifecycle)
    record(results, "subaccount_client.rename_subaccount", lambda: client.subaccount_client.rename_subaccount(subaccount=SUBACCOUNT, new_name=f"codex-renamed-{unique}".encode(), timeout_ms=30_000))
    if lifecycle_subaccount is not None:
        record(results, "subaccount_client.set_spot_margin.true", lambda: client.subaccount_client.set_spot_margin(subaccount=lifecycle_subaccount, enable_spot_margin=True, timeout_ms=120_000))
        record(results, "subaccount_client.set_spot_margin.false", lambda: client.subaccount_client.set_spot_margin(subaccount=lifecycle_subaccount, enable_spot_margin=False, timeout_ms=120_000))
    else:
        record_blocked(results, "subaccount_client.set_spot_margin.true", "initialize_subaccount did not return a new subaccount; cannot safely toggle spot margin without mutating an existing user subaccount")
        record_blocked(results, "subaccount_client.set_spot_margin.false", "initialize_subaccount did not return a new subaccount; cannot safely toggle spot margin without mutating an existing user subaccount")
    # Wallet-level delegates (runtime 190): no subaccount binding, so they are
    # safe to set/remove regardless of the lifecycle subaccount.
    delegate_set = record(results, "subaccount_client.set_delegate_account", lambda: client.subaccount_client.set_delegate_account(delegate=delegate, name=f"codex-{unique}".encode(), valid_until=(unique + 86_400_000), timeout_ms=60_000))
    if delegate_set is not None:
        record(results, "subaccount_client.update_delegate_mode", lambda: client.subaccount_client.update_delegate_mode(delegate=delegate, new_mode=3, timeout_ms=60_000))
        record(results, "subaccount_client.delegate_accounts_for", lambda: client.subaccount_client.delegate_accounts_for(owner=WALLET))
        record(results, "subaccount_client.remove_delegate_account", lambda: client.subaccount_client.remove_delegate_account(delegate=delegate, timeout_ms=60_000))
    else:
        record_blocked(results, "subaccount_client.update_delegate_mode", "set_delegate_account failed; cannot update mode on a non-existent delegate")
        record_blocked(results, "subaccount_client.remove_delegate_account", "set_delegate_account failed; cannot remove a non-existent delegate")
    if lifecycle_subaccount is not None:
        record(results, "subaccount_client.delete_subaccount", lambda: client.subaccount_client.delete_subaccount(subaccount=lifecycle_subaccount, timeout_ms=60_000))
    else:
        record_blocked(results, "subaccount_client.delete_subaccount", "initialize_subaccount did not return a new subaccount; cannot safely delete an existing user subaccount")
    record_blocked(
        results,
        "subaccount_client.liquidate_perp_by_transfer",
        "requires a liquidatable perp target; current SDK subaccount is healthy, so a successful liquidation cannot be produced from generic smoke data",
        evidence={"target_subaccount": SUBACCOUNT, "liquidator": SUBACCOUNT},
    )
    record_blocked(
        results,
        "subaccount_client.liquidate_spot_by_transfer",
        "requires a liquidatable spot liability target; current SDK subaccount is healthy, so a successful liquidation cannot be produced from generic smoke data",
        evidence={"target_subaccount": SUBACCOUNT, "liquidator": SUBACCOUNT},
    )
    record_blocked(
        results,
        "subaccount_client.liquidate_by_market",
        "requires a liquidatable/bankrupt target; current SDK subaccount does not satisfy node liquidation preconditions",
        evidence={"target_subaccount": SUBACCOUNT, "liquidator": SUBACCOUNT},
    )
    record(results, "subaccount_client.user_stats", lambda: client.subaccount_client.user_stats(address=WALLET))
    record(results, "subaccount_client.subaccount_info", lambda: client.subaccount_client.subaccount_info(address=SUBACCOUNT))
    record(results, "subaccount_client.delegate_accounts_for", lambda: client.subaccount_client.delegate_accounts_for(owner=WALLET))
    record(results, "subaccount_client.delegator_accounts_for", lambda: client.subaccount_client.delegator_accounts_for(delegate=WALLET))

    # System and lending.
    record(results, "system.system_account", lambda: client.system.system_account(address=signer))
    record(results, "lending.deposit", lambda: client.lending.deposit(subaccount=SUBACCOUNT, asset=ASSET, amount=1, timeout_ms=90_000))
    record(results, "lending.deposit_from_subaccount", lambda: client.lending.deposit_from_subaccount(from_subaccount=SUBACCOUNT, subaccount=SUBACCOUNT, asset=ASSET, amount=1, timeout_ms=90_000))
    record(results, "lending.withdraw", lambda: client.lending.withdraw(subaccount=SUBACCOUNT, asset=ASSET, amount=1, timeout_ms=90_000))
    borrow = record(results, "lending.borrow", lambda: client.lending.borrow(borrower=FUNDED_SUBACCOUNT, market_id=LENDING_MARKET_ID, asset=BORROW_ASSET, amount=1, timeout_ms=90_000))
    if borrow is not None:
        record(results, "lending.repay", lambda: client.lending.repay(who=FUNDED_SUBACCOUNT, market_id=LENDING_MARKET_ID, asset=BORROW_ASSET, amount=1, timeout_ms=90_000))
    else:
        record_blocked(results, "lending.repay", "borrow failed; cannot repay a borrow position that was not created", evidence={"asset": BORROW_ASSET})
    record(results, "lending.buy_quota", lambda: client.lending.buy_quota(account=SUBACCOUNT, quota=1, timeout_ms=30_000))
    record_blocked(
        results,
        "lending.bridge_invoke",
        "requires a valid bridge/address-manager payload and custom_data; dummy uid/custom_data is rejected by runtime design",
        evidence={"uid": ZERO32, "custom_data": ""},
    )
    record_blocked(
        results,
        "lending.withdraw_and_swap",
        "requires a valid Quota address manager / consumer and swap signature; arbitrary consumer_address is rejected by runtime design",
        evidence={"consumer_address": OTHER},
    )
    record_blocked(
        results,
        "lending.borrow_and_swap",
        "requires a valid Quota address manager / consumer and a borrower state that allows borrowing; generic SDK account is not suitable",
        evidence={"consumer_address": OTHER},
    )
    record(results, "lending.lending_markets", lambda: client.lending.lending_markets(market_id=LENDING_MARKET_ID))
    record(results, "lending.asset_pools", lambda: client.lending.asset_pools(market_id=LENDING_MARKET_ID))
    record(results, "lending.health_for", lambda: client.lending.health_for(subaccount=SUBACCOUNT))
    record(results, "lending.max_borrow_amount_for", lambda: client.lending.max_borrow_amount_for(account=SUBACCOUNT, lending_market=LENDING_MARKET_ID, asset=ASSET))
    record(results, "lending.max_borrow_amount_for.symbol", lambda: client.lending.max_borrow_amount_for(account=SUBACCOUNT, lending_market=LENDING_MARKET_ID, symbol=ASSET))
    record(results, "lending.max_withdraw_amount_for", lambda: client.lending.max_withdraw_amount_for(account=SUBACCOUNT, lending_market=LENDING_MARKET_ID, asset=ASSET))
    record(results, "lending.max_withdraw_amount_for.symbol", lambda: client.lending.max_withdraw_amount_for(account=SUBACCOUNT, lending_market=LENDING_MARKET_ID, symbol=ASSET))

    write_report(results, meta)
    passed = sum(1 for row in results if row["status"] == "PASS")
    blocked = [row for row in results if row["status"] == "BLOCKED"]
    failed = [row for row in results if row["status"] == "FAIL"]
    print(
        json.dumps(
            {
                "report": str(REPORT),
                "passed": passed,
                "blocked": len(blocked),
                "failed": len(failed),
                "failures": failed,
                "blocked_cases": blocked,
            },
            indent=2,
        ),
        flush=True,
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
