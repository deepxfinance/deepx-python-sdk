from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable

import deepx_sdk as dx


BASE_URL = os.environ.get("DEEPX_REST_URL", "https://rest-api-devnet.deepx.fi")
EVM_RPC_URL = os.environ.get("DEEPX_EVM_RPC_URL", "https://devnet-rpc-new.deepx.fi")
SUBSTRATE_WS = os.environ.get("DEEPX_SUBSTRATE_WS", "wss://devnet-rpc-new.deepx.fi")
REPORT = Path("/tmp/deepx_devnet_readonly_smoke_report.json")

PERP_SYMBOL = os.environ.get("DEEPX_PERP_SYMBOL", "ETH-USDC")
SPOT_SYMBOL = os.environ.get("DEEPX_SPOT_SYMBOL", "ETH-USDC")
LENDING_SYMBOL = os.environ.get("DEEPX_LENDING_SYMBOL", "usdc")
SUBACCOUNT = os.environ.get(
    "DEEPX_VIEW_SUBACCOUNT",
    "0x6faeedfd51e04a183396195b43104d17d42c3bee",
)
LENDING_MARKET_ID = int(os.environ.get("DEEPX_LENDING_MARKET_ID", "1"))


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
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, list):
        return {"len": len(value), "first": details(value[0]) if value else None}
    if isinstance(value, dict):
        return {key: value[key] for key in list(value.keys())[:8]}
    return repr(value)[:300]


def write_report(results: list[dict[str, Any]]) -> None:
    passed = sum(1 for row in results if row["status"] == "PASS")
    failed = sum(1 for row in results if row["status"] == "FAIL")
    payload = {
        "meta": {
            "base_url": BASE_URL,
            "evm_rpc_url": EVM_RPC_URL,
            "substrate_ws": SUBSTRATE_WS,
            "perp_symbol": PERP_SYMBOL,
            "spot_symbol": SPOT_SYMBOL,
            "lending_symbol": LENDING_SYMBOL,
            "subaccount": SUBACCOUNT,
        },
        "passed": passed,
        "failed": failed,
        "results": results,
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
        print(f"{name}: PASS {row['summary']} {row['details']}", flush=True)
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


def main() -> None:
    api = dx.ApiClient(base_url=BASE_URL, net="devnet")
    chain = dx.ChainClient(
        net="devnet",
        api_base_url=BASE_URL,
        evm_rpc_url=EVM_RPC_URL,
        substrate_ws=SUBSTRATE_WS,
        private_key="0x" + "00" * 32,
        subaccount=SUBACCOUNT,
    )
    results: list[dict[str, Any]] = []
    write_report(results)

    record(results, "api.v1.ping", lambda: api.v1.ping())
    record(results, "api.v1.time", lambda: api.v1.time())
    record(results, "async_api.v1.ping", lambda: asyncio.run(dx.AsyncApiClient(base_url=BASE_URL, net="devnet").v1.ping()))
    record(results, "api.v1.perp.markets", lambda: api.v1.perp.markets())
    record(results, "api.v1.spot.markets", lambda: api.v1.spot.markets())
    record(results, "api.v1.lending.markets", lambda: api.v1.lending.markets())

    record(results, "chain.preload_markets", lambda: chain.preload_markets())
    record(results, "chain.perp_market.perp_markets.symbol", lambda: chain.perp_market.perp_markets(symbol=PERP_SYMBOL))
    record(results, "chain.perp_market.mark_price_for.symbol", lambda: chain.perp_market.mark_price_for(symbol=PERP_SYMBOL))
    record(results, "chain.perp_market.last_trade_price_for.symbol", lambda: chain.perp_market.last_trade_price_for(symbol=PERP_SYMBOL))
    record(results, "chain.perp_market.user_perp_positions.symbols", lambda: chain.perp_market.user_perp_positions(user=SUBACCOUNT, symbols=[PERP_SYMBOL]))
    record(results, "chain.spot_market.get_spot_market_spec.symbol", lambda: chain.spot_market.get_spot_market_spec(symbol=SPOT_SYMBOL))
    record(results, "chain.lending.max_borrow_amount_for.symbol", lambda: chain.lending.max_borrow_amount_for(account=SUBACCOUNT, lending_market=LENDING_MARKET_ID, symbol=LENDING_SYMBOL))
    record(results, "chain.lending.max_withdraw_amount_for.symbol", lambda: chain.lending.max_withdraw_amount_for(account=SUBACCOUNT, lending_market=LENDING_MARKET_ID, symbol=LENDING_SYMBOL))

    write_report(results)
    failed = [row for row in results if row["status"] == "FAIL"]
    print(
        json.dumps(
            {
                "report": str(REPORT),
                "passed": len(results) - len(failed),
                "failed": len(failed),
                "failures": failed,
            },
            indent=2,
        ),
        flush=True,
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
