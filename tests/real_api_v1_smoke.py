from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
import urllib.error
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import deepx_sdk as dx
from deepx_sdk.ws_client import (
    WsClient,
    v1_list,
    v1_ping,
    v1_post,
    v1_subscribe,
    v1_unsubscribe,
)


BASE_URL = "https://rest-api-devnet.deepx.fi"
WS_BASE_URL = "wss://ws-api-devnet.deepx.fi"
SUBSTRATE_WS = "wss://devnet-rpc-new.deepx.fi"
REPORT = Path("/tmp/deepx_api_v1_real_report.json")
SECRET_KEY_FILE = Path(__file__).resolve().parents[1] / ".sk"

PERP_SYMBOL = "ETH-USDC"
PERP_MARKET_ID = 3
SPOT_SYMBOL = "ETH-USDC"
SPOT_PAIR = "0x9068d4ac891a14784c17877eb74bd8489b3367c71d72766dbfa4dfbfb662fa37"
LENDING_ASSET = "usdc"

WALLET = "0xBF34E1d049BcF588f7B8C80273259c3deA1AC3a3"
SUBACCOUNT = "0x6faeedfd51e04a183396195b43104d17d42c3bee"
FUNDED_SUBACCOUNT = "0xd1b75179e3b69e47732ece09b9f489d75233cef2"

PERP_ORDER_OWNER = "0x600cde340d68e751e25fcfd76880ccfe1fc2980e"
PERP_ORDER_ID = 3458756
PERP_ORDER_TX = "0xd1f2e0b9dfa4a78cc262d7b5249869664f25712544fff887d6ecb9377389eb48"
SPOT_ORDER_MAKER = "0xe2bbfd683e39ae185fdf13a5e296de5576aa2391"
SPOT_ORDER_ID = 1613087
SPOT_ORDER_TX = "0xbb310f5f806bd85e63e591c1eea40d5761031111a60a0d7a45f8b3000f42271b"


PERP_SIZE_RAW = 1_000_000_000_000_000
PERP_PRICE_RAW = 1_000_000_000
SPOT_BUY_QUOTE_RAW = 1_000_000
SPOT_BUY_BASE_RAW = 1_000_000_000_000_000
SPOT_METHOD_QUOTE_RAW = 2_000_000
SPOT_METHOD_BASE_RAW = 1_000_000_000_000_000
SPOT_METHOD_SELL_LIMIT_QUOTE_RAW = 1_800_000
SPOT_METHOD_SELL_LIMIT_BASE_RAW = 1_000_000_000_000_000


def ok_shape(value: Any) -> bool:
    return value is not None


def page_shape(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("items"), list)


def page_or_list_shape(value: Any) -> bool:
    return page_shape(value) or isinstance(value, list)


def list_shape(value: Any) -> bool:
    return isinstance(value, list)


def dict_shape(value: Any) -> bool:
    return isinstance(value, dict)


def summarize(value: Any) -> str:
    if isinstance(value, list):
        return f"list(len={len(value)})"
    if isinstance(value, dict):
        keys = ",".join(list(value.keys())[:8])
        return f"dict(keys={keys})"
    return type(value).__name__


def data_payload(value: Any) -> Any:
    if isinstance(value, dict) and value.get("code") == 200 and value.get("fail") is False:
        return value.get("data")
    return value


def response_tx_hash(value: Any) -> str | None:
    data = data_payload(value)
    if not isinstance(data, dict):
        return None
    tx_hash = data.get("txHash") or data.get("tx_hash")
    return str(tx_hash) if tx_hash else None


def response_order_id(value: Any) -> int | None:
    data = data_payload(value)
    if not isinstance(data, dict):
        return None
    order_id = data.get("orderId") or data.get("order_id")
    if order_id is None or order_id == "":
        return None
    return int(Decimal(str(order_id)))


def chain_tx_shape(value: Any) -> bool:
    data = data_payload(value)
    return (
        isinstance(data, dict)
        and response_tx_hash(value) is not None
        and response_order_id(value) is not None
    )


def _positions(value: Any) -> list[dict[str, Any]]:
    payload = data_payload(value)
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        payload = payload["items"]
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _position_market_matches(position: dict[str, Any]) -> bool:
    symbol = position.get("symbol") or position.get("marketSymbol") or position.get("market_symbol")
    if symbol == PERP_SYMBOL:
        return True
    market_id = position.get("marketId") or position.get("market_id")
    if market_id is None:
        return False
    try:
        return int(Decimal(str(market_id))) == PERP_MARKET_ID
    except Exception:
        return False


def _position_has_non_zero_size(position: dict[str, Any]) -> bool:
    size_fields = (
        "size",
        "positionSize",
        "position_size",
        "netSize",
        "net_size",
        "quantity",
        "qty",
        "baseAmount",
        "base_amount",
    )
    seen_size = False
    for field in size_fields:
        value = position.get(field)
        if value is None or value == "":
            continue
        seen_size = True
        try:
            if Decimal(str(value)) != 0:
                return True
        except Exception:
            return True
    return not seen_size


def has_perp_position_shape(value: Any) -> bool:
    return any(
        _position_market_matches(position) and _position_has_non_zero_size(position)
        for position in _positions(value)
    )


def no_perp_position_shape(value: Any) -> bool:
    return not any(
        _position_market_matches(position) and _position_has_non_zero_size(position)
        for position in _positions(value)
    )


def chain_tx_details(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    data = value.get("data")
    return {
        "code": value.get("code"),
        "msg": value.get("msg"),
        "fail": value.get("fail"),
        "order_id": (data or {}).get("order_id") if isinstance(data, dict) else None,
        "tx_hash": (data or {}).get("tx_hash") if isinstance(data, dict) else None,
    }


def result_details(group: str, value: Any) -> Any:
    if group in {"chain_tx", "chain_tx_methods"}:
        return chain_tx_details(value)
    if group == "ws_chain_tx":
        return chain_tx_details(ws_post_response(value))
    return None


def ws_post_response(msg: Any) -> Any:
    if not isinstance(msg, dict):
        return None
    data = msg.get("data")
    if not isinstance(data, dict):
        return None
    return data.get("response")


def ws_post_tx_shape(msg: Any) -> bool:
    response = ws_post_response(msg)
    return chain_tx_shape(response)


def read_private_key() -> str:
    key = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError(f"empty private key file: {SECRET_KEY_FILE}")
    return key


def wait_for_indexed(
    fn: Callable[[], Any],
    *,
    attempts: int = 12,
    delay_s: float = 2.0,
) -> Any:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return fn()
        except urllib.error.HTTPError as exc:
            last_error = exc
            try:
                exc.read()
            except Exception:
                pass
        except Exception as exc:
            last_error = exc
        time.sleep(delay_s)
    if last_error is not None:
        raise last_error
    raise TimeoutError("index wait exhausted")


def record(results: list[dict[str, Any]], group: str, name: str, fn: Callable[[], Any], shape: Callable[[Any], bool] = ok_shape) -> Any:
    started = time.time()
    try:
        value = fn()
        elapsed_ms = int((time.time() - started) * 1000)
        passed = bool(shape(value))
        results.append(
            {
                "group": group,
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "elapsed_ms": elapsed_ms,
                "summary": summarize(value),
                "error": None if passed else "unexpected response shape",
                "details": result_details(group, value),
            }
        )
        return value
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        results.append(
            {
                "group": group,
                "name": name,
                "status": "FAIL",
                "elapsed_ms": int((time.time() - started) * 1000),
                "summary": None,
                "error": f"HTTP {exc.code}: {body}",
            }
        )
    except Exception as exc:
        results.append(
            {
                "group": group,
                "name": name,
                "status": "FAIL",
                "elapsed_ms": int((time.time() - started) * 1000),
                "summary": None,
                "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(limit=3),
            }
        )
    return None


async def recv_json_timeout(ws, timeout: float = 8.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = await asyncio.wait_for(ws.recv_json(), timeout=max(0.1, deadline - time.time()))
        if msg == {"method": "ping"}:
            await ws.send_json({"method": "pong"})
            continue
        return msg
    raise TimeoutError("ws recv timeout")


async def wait_for_ack(ws, method: str, req_id: int, timeout: float = 8.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = await recv_json_timeout(ws, timeout=max(0.1, deadline - time.time()))
        data = msg.get("data", {}) if isinstance(msg, dict) else {}
        if msg.get("channel") == "subscriptionResponse" and data.get("method") == method and data.get("id") == req_id:
            return msg
        if method == "subscribe" and msg.get("type") == "subscribed":
            return msg
        if method == "unsubscribe" and msg.get("type") == "unsubscribed":
            return msg
        if msg.get("channel") == "post" and data.get("id") == req_id:
            return msg
        if msg.get("channel") == "error":
            raise RuntimeError(json.dumps(msg))
    raise TimeoutError(f"missing {method} ack id={req_id}")


async def run_ws(results: list[dict[str, Any]]) -> None:
    async def ws_case(name: str, payload: dict[str, Any], *, method: str = "subscribe", req_id: int) -> None:
        started = time.time()
        try:
            client = WsClient(
                base_url=WS_BASE_URL,
                path="/v1/ws",
                open_timeout=8,
                close_timeout=2,
                ping_interval=None,
                ping_timeout=None,
            )
            ws = await asyncio.wait_for(client.connect(), timeout=10)
            async with ws:
                await ws.send_json(payload)
                if method == "ping":
                    msg = await recv_json_timeout(ws, timeout=6)
                    passed = msg == {"method": "pong"}
                else:
                    msg = await wait_for_ack(ws, method, req_id, timeout=6)
                    passed = isinstance(msg, dict)
            results.append(
                {
                    "group": "ws",
                    "name": name,
                    "status": "PASS" if passed else "FAIL",
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "summary": summarize(msg),
                    "error": None if passed else "unexpected ws response",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "group": "ws",
                    "name": name,
                    "status": "FAIL",
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "summary": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
        print(f"WS {name}: {results[-1]['status']} {results[-1].get('error') or ''}", flush=True)

    await ws_case("ping", v1_ping(), method="ping", req_id=0)
    await ws_case("list", v1_list(1), method="list", req_id=1)
    channels: list[tuple[str, dict[str, Any]]] = [
        ("spot_orderbook", v1_subscribe(10, channel="spot@orderbook", symbol=SPOT_SYMBOL)),
        ("spot_trades", v1_subscribe(11, channel="spot@trades", symbol=SPOT_SYMBOL)),
        ("spot_ticker", v1_subscribe(12, channel="spot@ticker", symbol=SPOT_SYMBOL)),
        ("spot_candles", v1_subscribe(13, channel="spot@candles", symbol=SPOT_SYMBOL, interval="1m")),
        ("perp_orderbook", v1_subscribe(20, channel="perp@orderbook", symbol=PERP_SYMBOL)),
        ("perp_trades", v1_subscribe(21, channel="perp@trades", symbol=PERP_SYMBOL)),
        ("perp_ticker", v1_subscribe(22, channel="perp@ticker", symbol=PERP_SYMBOL)),
        ("perp_prices", v1_subscribe(23, channel="perp@prices", symbol=PERP_SYMBOL)),
        ("perp_funding_rate", v1_subscribe(24, channel="perp@funding-rate", symbol=PERP_SYMBOL)),
        ("perp_open_interest", v1_subscribe(25, channel="perp@open-interest", symbol=PERP_SYMBOL, interval="1h")),
        ("perp_long_short_ratio", v1_subscribe(26, channel="perp@long-short-ratio", symbol=PERP_SYMBOL)),
        ("perp_candles", v1_subscribe(27, channel="perp@candles", symbol=PERP_SYMBOL, interval="1m")),
        ("lending_market_status", v1_subscribe(30, channel="lending@market-status", asset=LENDING_ASSET)),
        ("account_balances", v1_subscribe(40, channel="account@balances", subaccount=SUBACCOUNT)),
        ("account_portfolio", v1_subscribe(41, channel="account@portfolio", subaccount=SUBACCOUNT)),
        ("account_perp_positions", v1_subscribe(42, channel="account@perp-positions", subaccount=SUBACCOUNT)),
        ("account_perp_orders", v1_subscribe(43, channel="account@perp-orders", subaccount=SUBACCOUNT)),
        ("account_spot_orders", v1_subscribe(44, channel="account@spot-orders", subaccount=SUBACCOUNT)),
        ("account_perp_trades", v1_subscribe(45, channel="account@perp-trades", subaccount=SUBACCOUNT)),
        ("account_spot_trades", v1_subscribe(46, channel="account@spot-trades", subaccount=SUBACCOUNT)),
    ]
    for idx, (name, payload) in enumerate(channels, start=100):
        payload["id"] = idx
        await ws_case(name, payload, method="subscribe", req_id=idx)
    await ws_case(
        "unsubscribe_spot_ticker",
        v1_unsubscribe(200, channel="spot@ticker", symbol=SPOT_SYMBOL),
        method="unsubscribe",
        req_id=200,
    )
    await ws_case(
        "post_unknown_route",
        v1_post(300, route="unknownRoute", payload={"signedExtrinsic": "0x00"}),
        method="post",
        req_id=300,
    )


async def ws_post_request(route: str, signed_extrinsic: str, *, req_id: int) -> dict[str, Any]:
    client = WsClient(
        base_url=WS_BASE_URL,
        path="/v1/ws",
        open_timeout=8,
        close_timeout=2,
        ping_interval=None,
        ping_timeout=None,
    )
    ws = await asyncio.wait_for(client.connect(), timeout=10)
    async with ws:
        await ws.send_json(
            v1_post(req_id, route=route, payload={"signedExtrinsic": signed_extrinsic})
        )
        return await wait_for_ack(ws, "post", req_id, timeout=12)


def run_rest(results: list[dict[str, Any]]) -> None:
    api = dx.ApiClient(base_url=BASE_URL, ws_base_url=WS_BASE_URL, timeout=25)
    v1 = api.v1
    rest_cases: list[tuple[str, Callable[[], Any], Callable[[Any], bool]]] = [
        ("ping", lambda: v1.ping(), ok_shape),
        ("time", lambda: v1.time(), ok_shape),
        ("ws.websocket_url", lambda: v1.ws.websocket_url(), lambda x: isinstance(x, str) and x.startswith("wss://")),
        ("spot.markets", lambda: v1.spot.markets(), list_shape),
        ("spot.markets symbols", lambda: v1.spot.markets(symbols=SPOT_SYMBOL), list_shape),
        ("spot.market", lambda: v1.spot.market(symbol=SPOT_SYMBOL), dict_shape),
        ("spot.candles", lambda: v1.spot.candles(symbol=SPOT_SYMBOL, interval="1m", limit=2), list_shape),
        ("spot.trades", lambda: v1.spot.trades(symbol=SPOT_SYMBOL, limit=2), page_shape),
        ("spot.orderbook", lambda: v1.spot.orderbook(symbol=SPOT_SYMBOL, limit=5, merge_level=0), dict_shape),
        ("perp.markets", lambda: v1.perp.markets(), list_shape),
        ("perp.market", lambda: v1.perp.market(symbol=PERP_SYMBOL), dict_shape),
        ("perp.candles", lambda: v1.perp.candles(symbol=PERP_SYMBOL, interval="1m", limit=2), list_shape),
        ("perp.trades", lambda: v1.perp.trades(symbol=PERP_SYMBOL, limit=2), page_shape),
        ("perp.orderbook", lambda: v1.perp.orderbook(symbol=PERP_SYMBOL, limit=5, merge_level=0), dict_shape),
        ("perp.open_interest", lambda: v1.perp.open_interest(symbol=PERP_SYMBOL), dict_shape),
        ("perp.open_interest_history", lambda: v1.perp.open_interest_history(symbol=PERP_SYMBOL, interval="1h", limit=2), list_shape),
        ("perp.funding_rate", lambda: v1.perp.funding_rate(symbol=PERP_SYMBOL), dict_shape),
        ("perp.funding_rate_history", lambda: v1.perp.funding_rate_history(symbol=PERP_SYMBOL, interval="1m", limit=2), list_shape),
        ("perp.long_short_ratio", lambda: v1.perp.long_short_ratio(symbol=PERP_SYMBOL), dict_shape),
        ("perp.long_short_ratio_history", lambda: v1.perp.long_short_ratio_history(symbol=PERP_SYMBOL, interval="1m", limit=2), list_shape),
        ("lending.markets", lambda: v1.lending.markets(), list_shape),
        ("lending.market", lambda: v1.lending.market(asset=LENDING_ASSET), dict_shape),
        ("lending.market_status all", lambda: v1.lending.market_status(), list_shape),
        ("lending.market_status asset", lambda: v1.lending.market_status(asset=LENDING_ASSET), dict_shape),
        ("lending.market_status_history", lambda: v1.lending.market_status_history(asset=LENDING_ASSET, interval="1m", limit=2), list_shape),
        ("account.wallet_subaccounts", lambda: v1.account.wallet_subaccounts(address=WALLET), list_shape),
        ("account.wallet_delegate_accounts", lambda: v1.account.wallet_delegate_accounts(address=WALLET), list_shape),
        ("account.delegator_accounts", lambda: v1.account.delegator_accounts(address=WALLET), list_shape),
        ("account.subaccount_info", lambda: v1.account.subaccount_info(address=SUBACCOUNT), dict_shape),
        ("account.subaccount_margin_ratio", lambda: v1.account.subaccount_margin_ratio(address=SUBACCOUNT), dict_shape),
        ("account.subaccount_perp_positions", lambda: v1.account.subaccount_perp_positions(address=SUBACCOUNT, symbol=PERP_SYMBOL), list_shape),
        ("account.subaccount_perp_positions_history", lambda: v1.account.subaccount_perp_positions_history(address=SUBACCOUNT, symbol=PERP_SYMBOL, limit=2), page_shape),
        ("account.subaccount_perp_orders", lambda: v1.account.subaccount_perp_orders(address=PERP_ORDER_OWNER, symbol="SOL-USDC", limit=2), page_shape),
        ("account.subaccount_perp_open_orders", lambda: v1.account.subaccount_perp_open_orders(address=SUBACCOUNT, symbol=PERP_SYMBOL), list_shape),
        ("account.subaccount_perp_trades", lambda: v1.account.subaccount_perp_trades(address=SUBACCOUNT, symbol=PERP_SYMBOL, limit=2), page_shape),
        ("account.subaccount_perp_funding_payments", lambda: v1.account.subaccount_perp_funding_payments(address=SUBACCOUNT, symbol=PERP_SYMBOL, limit=2), page_or_list_shape),
        ("account.subaccount_spot_orders", lambda: v1.account.subaccount_spot_orders(address=SPOT_ORDER_MAKER, symbol="SOL/USDC", limit=2), page_shape),
        ("account.subaccount_spot_open_orders", lambda: v1.account.subaccount_spot_open_orders(address=SUBACCOUNT, symbol=SPOT_SYMBOL), list_shape),
        ("account.subaccount_spot_trades", lambda: v1.account.subaccount_spot_trades(address=SUBACCOUNT, symbol=SPOT_SYMBOL, limit=2), page_shape),
        ("account.subaccount_balances", lambda: v1.account.subaccount_balances(address=SUBACCOUNT), list_shape),
        ("account.subaccount_portfolio", lambda: v1.account.subaccount_portfolio(address=SUBACCOUNT), dict_shape),
        ("account.subaccount_balance_changes", lambda: v1.account.subaccount_balance_changes(address=SUBACCOUNT, limit=2), page_shape),
        ("account.subaccount_liquidations", lambda: v1.account.subaccount_liquidations(address=SUBACCOUNT, limit=2), page_shape),
        ("account.perp_order_by_tx", lambda: v1.account.perp_order_by_tx(tx_hash=PERP_ORDER_TX), ok_shape),
        ("account.spot_order_by_tx", lambda: v1.account.spot_order_by_tx(tx_hash=SPOT_ORDER_TX), ok_shape),
        ("account.perp_order_by_id", lambda: v1.account.perp_order_by_id(address=PERP_ORDER_OWNER, order_id=PERP_ORDER_ID), ok_shape),
        ("account.spot_order_by_id", lambda: v1.account.spot_order_by_id(address=SPOT_ORDER_MAKER, order_id=SPOT_ORDER_ID), ok_shape),
    ]
    for name, fn, shape in rest_cases:
        record(results, "rest", name, fn, shape)


def build_signed_pallet_call(
    api: dx.ApiClient,
    *,
    private_key: str,
    call_module: str,
    call_function: str,
    call_params: dict[str, Any],
) -> str:
    return api.v1.chain_tx._build_signed_pallet_call_extrinsic(
        private_key=private_key,
        call_module=call_module,
        call_function=call_function,
        call_params=call_params,
        nonce_ms=int(time.time() * 1000),
    )


def record_ws_post_tx(
    results: list[dict[str, Any]],
    *,
    name: str,
    route: str,
    signed_extrinsic: str,
    req_id: int,
) -> Any:
    return record(
        results,
        "ws_chain_tx",
        name,
        lambda: asyncio.run(ws_post_request(route, signed_extrinsic, req_id=req_id)),
        ws_post_tx_shape,
    )


def ws_post_order_id(msg: Any) -> int | None:
    return response_order_id(ws_post_response(msg))


def ws_post_tx_hash(msg: Any) -> str | None:
    return response_tx_hash(ws_post_response(msg))


def _nonce_ms() -> int:
    return int(time.time() * 1000)


def _record_perp_order_index(
    results: list[dict[str, Any]],
    v1: Any,
    *,
    group: str,
    prefix: str,
    order: Any,
) -> None:
    tx_hash = response_tx_hash(order)
    order_id = response_order_id(order)
    if tx_hash:
        record(
            results,
            group,
            f"{prefix}.perp_order_by_tx",
            lambda: wait_for_indexed(lambda: v1.account.perp_order_by_tx(tx_hash=tx_hash)),
            ok_shape,
        )
    if order_id is not None:
        record(
            results,
            group,
            f"{prefix}.perp_order_by_id",
            lambda: wait_for_indexed(
                lambda: v1.account.perp_order_by_id(
                    address=SUBACCOUNT,
                    order_id=order_id,
                )
            ),
            ok_shape,
        )


def _record_spot_order_index(
    results: list[dict[str, Any]],
    v1: Any,
    *,
    group: str,
    prefix: str,
    order: Any,
    subaccount: str = SUBACCOUNT,
    check_by_id: bool = True,
) -> None:
    tx_hash = response_tx_hash(order)
    order_id = response_order_id(order)
    if tx_hash:
        record(
            results,
            group,
            f"{prefix}.spot_order_by_tx",
            lambda: wait_for_indexed(lambda: v1.account.spot_order_by_tx(tx_hash=tx_hash)),
            ok_shape,
        )
    if check_by_id and order_id is not None:
        record(
            results,
            group,
            f"{prefix}.spot_order_by_id",
            lambda: wait_for_indexed(
                lambda: v1.account.spot_order_by_id(
                    address=subaccount,
                    order_id=order_id,
                )
            ),
            ok_shape,
        )


def _cancel_perp_if_present(
    results: list[dict[str, Any]],
    v1: Any,
    *,
    group: str,
    prefix: str,
    order: Any,
) -> None:
    order_id = response_order_id(order)
    if order_id is None:
        return
    cancel = record(
        results,
        group,
        f"{prefix}.cancel_perp_order",
        lambda: v1.chain_tx.cancel_perp_order(
            market_id=PERP_MARKET_ID,
            order_id=order_id,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    order_id = response_order_id(cancel)
    if order_id is not None:
        record(
            results,
            group,
            f"{prefix}.after_cancel.perp_order_by_id",
            lambda: wait_for_indexed(
                lambda: v1.account.perp_order_by_id(
                    address=SUBACCOUNT,
                    order_id=order_id,
                )
            ),
            ok_shape,
        )


def _cancel_spot_buy_if_present(
    results: list[dict[str, Any]],
    v1: Any,
    *,
    group: str,
    prefix: str,
    order: Any,
) -> None:
    order_id = response_order_id(order)
    if order_id is None:
        return
    cancel = record(
        results,
        group,
        f"{prefix}.cancel_spot_order_buy",
        lambda: v1.chain_tx.cancel_spot_order_buy(
            pair=SPOT_PAIR,
            order_id=order_id,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    order_id = response_order_id(cancel)
    if order_id is not None:
        record(
            results,
            group,
            f"{prefix}.after_cancel.spot_order_by_id",
            lambda: wait_for_indexed(
                lambda: v1.account.spot_order_by_id(
                    address=SUBACCOUNT,
                    order_id=order_id,
                )
            ),
            ok_shape,
        )


def _cancel_spot_sell_if_present(
    results: list[dict[str, Any]],
    v1: Any,
    *,
    group: str,
    prefix: str,
    order: Any,
) -> None:
    order_id = response_order_id(order)
    if order_id is None:
        return
    cancel = record(
        results,
        group,
        f"{prefix}.cancel_spot_order_sell",
        lambda: v1.chain_tx.cancel_spot_order_sell(
            pair=SPOT_PAIR,
            order_id=order_id,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    order_id = response_order_id(cancel)
    if order_id is not None:
        record(
            results,
            group,
            f"{prefix}.after_cancel.spot_order_by_id",
            lambda: wait_for_indexed(
                lambda: v1.account.spot_order_by_id(
                    address=SUBACCOUNT,
                    order_id=order_id,
                )
            ),
            ok_shape,
        )


def run_ws_chain_tx(results: list[dict[str, Any]]) -> None:
    private_key = read_private_key()
    api = dx.ApiClient(
        base_url=BASE_URL,
        ws_base_url=WS_BASE_URL,
        substrate_ws=SUBSTRATE_WS,
        private_key=private_key,
        subaccount=SUBACCOUNT,
        timeout=45,
    )
    v1 = api.v1

    perp_place_signed = build_signed_pallet_call(
        api,
        private_key=private_key,
        call_module="PerpMarket",
        call_function="place_order",
        call_params={
            "subaccount": FUNDED_SUBACCOUNT,
            "market_id": PERP_MARKET_ID,
            "is_long": True,
            "size": PERP_SIZE_RAW,
            "price": PERP_PRICE_RAW,
            "order_type": "Limit",
            "slippage": None,
            "leverage": 1,
            "take_profit": None,
            "stop_loss": None,
            "reduce_only": False,
            "post_only": "MustPostOnly",
        },
    )
    perp_place = record_ws_post_tx(
        results,
        name="post_place_perp_order",
        route="placePerpOrder",
        signed_extrinsic=perp_place_signed,
        req_id=10_001,
    )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    perp_order_id = ws_post_order_id(perp_place)
    perp_tx_hash = ws_post_tx_hash(perp_place)
    if perp_tx_hash:
        record(
            results,
            "ws_chain_tx",
            "perp_order_by_tx_after_ws_post_place",
            lambda: wait_for_indexed(lambda: v1.account.perp_order_by_tx(tx_hash=perp_tx_hash)),
            ok_shape,
        )
    if perp_order_id is not None:
        record(
            results,
            "ws_chain_tx",
            "perp_order_by_id_after_ws_post_place",
            lambda: wait_for_indexed(
                lambda: v1.account.perp_order_by_id(
                    address=FUNDED_SUBACCOUNT,
                    order_id=perp_order_id,
                )
            ),
            ok_shape,
        )
        perp_cancel_signed = build_signed_pallet_call(
            api,
            private_key=private_key,
            call_module="PerpMarket",
            call_function="cancel_order",
            call_params={
                "subaccount": FUNDED_SUBACCOUNT,
                "order_id": perp_order_id,
                "market_id": PERP_MARKET_ID,
                "cancel_reason": "UserCanceled",
            },
        )
        perp_cancel = record_ws_post_tx(
            results,
            name="post_cancel_perp_order",
            route="cancelPerpOrder",
            signed_extrinsic=perp_cancel_signed,
            req_id=10_002,
        )
        if ws_post_tx_hash(perp_cancel):
            record(
                results,
                "ws_chain_tx",
                "perp_order_by_id_after_ws_post_cancel",
                lambda: wait_for_indexed(
                    lambda: v1.account.perp_order_by_id(
                        address=FUNDED_SUBACCOUNT,
                        order_id=perp_order_id,
                    )
                ),
                ok_shape,
            )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    spot_place_signed = build_signed_pallet_call(
        api,
        private_key=private_key,
        call_module="SpotMarket",
        call_function="place_order_buy",
        call_params={
            "pair": SPOT_PAIR,
            "subaccount": FUNDED_SUBACCOUNT,
            "quote_amount": SPOT_BUY_QUOTE_RAW,
            "base_amount": SPOT_BUY_BASE_RAW,
            "price_type": "Limit",
            "post_only": "MustPostOnly",
            "reduce_only": False,
        },
    )
    spot_place = record_ws_post_tx(
        results,
        name="post_place_spot_order_buy",
        route="placeSpotOrder",
        signed_extrinsic=spot_place_signed,
        req_id=10_003,
    )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    spot_order_id = ws_post_order_id(spot_place)
    spot_tx_hash = ws_post_tx_hash(spot_place)
    if spot_tx_hash:
        record(
            results,
            "ws_chain_tx",
            "spot_order_by_tx_after_ws_post_place",
            lambda: wait_for_indexed(lambda: v1.account.spot_order_by_tx(tx_hash=spot_tx_hash)),
            ok_shape,
        )
    if spot_order_id is not None:
        record(
            results,
            "ws_chain_tx",
            "spot_order_by_id_after_ws_post_place",
            lambda: wait_for_indexed(
                lambda: v1.account.spot_order_by_id(
                    address=FUNDED_SUBACCOUNT,
                    order_id=spot_order_id,
                )
            ),
            ok_shape,
        )
        spot_cancel_signed = build_signed_pallet_call(
            api,
            private_key=private_key,
            call_module="SpotMarket",
            call_function="cancel_order_buy",
            call_params={
                "pair": SPOT_PAIR,
                "subaccount": FUNDED_SUBACCOUNT,
                "order_id": spot_order_id,
            },
        )
        spot_cancel = record_ws_post_tx(
            results,
            name="post_cancel_spot_order_buy",
            route="cancelSpotOrder",
            signed_extrinsic=spot_cancel_signed,
            req_id=10_004,
        )
        if ws_post_tx_hash(spot_cancel):
            record(
                results,
                "ws_chain_tx",
                "spot_order_by_id_after_ws_post_cancel",
                lambda: wait_for_indexed(
                    lambda: v1.account.spot_order_by_id(
                        address=FUNDED_SUBACCOUNT,
                        order_id=spot_order_id,
                    )
                ),
                ok_shape,
            )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    close_place_signed = build_signed_pallet_call(
        api,
        private_key=private_key,
        call_module="PerpMarket",
        call_function="place_order",
        call_params={
            "subaccount": SUBACCOUNT,
            "market_id": PERP_MARKET_ID,
            "is_long": True,
            "size": PERP_SIZE_RAW,
            "price": 0,
            "order_type": "Market",
            "slippage": None,
            "leverage": 1,
            "take_profit": None,
            "stop_loss": None,
            "reduce_only": False,
            "post_only": "None",
        },
    )
    close_place = record_ws_post_tx(
        results,
        name="post_place_perp_order_market_for_close",
        route="placePerpOrder",
        signed_extrinsic=close_place_signed,
        req_id=10_005,
    )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    close_place_tx = ws_post_tx_hash(close_place)
    if close_place_tx:
        record(
            results,
            "ws_chain_tx",
            "perp_order_by_tx_after_ws_post_market_place_for_close",
            lambda: wait_for_indexed(lambda: v1.account.perp_order_by_tx(tx_hash=close_place_tx)),
            ok_shape,
        )
    position_after_ws_place = record(
        results,
        "ws_chain_tx",
        "perp_positions_after_ws_post_market_place_for_close",
        lambda: wait_for_indexed(
            lambda: v1.account.subaccount_perp_positions(
                address=SUBACCOUNT,
                symbol=PERP_SYMBOL,
            )
        ),
        has_perp_position_shape,
    )
    if has_perp_position_shape(position_after_ws_place):
        close_signed = build_signed_pallet_call(
            api,
            private_key=private_key,
            call_module="PerpMarket",
            call_function="close_position",
            call_params={
                "subaccount": SUBACCOUNT,
                "market_id": PERP_MARKET_ID,
                "price": 0,
                "slippage": 10,
            },
        )
        close_result = record_ws_post_tx(
            results,
            name="post_close_position",
            route="closePosition",
            signed_extrinsic=close_signed,
            req_id=10_006,
        )
        close_tx = ws_post_tx_hash(close_result)
        if close_tx:
            record(
                results,
                "ws_chain_tx",
                "perp_order_by_tx_after_ws_post_close_position",
                lambda: wait_for_indexed(lambda: v1.account.perp_order_by_tx(tx_hash=close_tx)),
                ok_shape,
            )
        record(
            results,
            "ws_chain_tx",
            "perp_positions_after_ws_post_close_position",
            lambda: wait_for_indexed(
                lambda: v1.account.subaccount_perp_positions(
                    address=SUBACCOUNT,
                    symbol=PERP_SYMBOL,
                )
            ),
            no_perp_position_shape,
        )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")


def run_chain_tx(results: list[dict[str, Any]]) -> None:
    private_key = read_private_key()
    api = dx.ApiClient(
        base_url=BASE_URL,
        ws_base_url=WS_BASE_URL,
        substrate_ws=SUBSTRATE_WS,
        private_key=private_key,
        subaccount=SUBACCOUNT,
        timeout=45,
    )
    v1 = api.v1

    perp_place = record(
        results,
        "chain_tx",
        "place_perp_order_limit",
        lambda: v1.chain_tx.place_perp_order_limit(
            market_id=PERP_MARKET_ID,
            is_long=True,
            size=PERP_SIZE_RAW,
            price=PERP_PRICE_RAW,
            leverage=1,
            post_only=1,
            nonce_ms=int(time.time() * 1000),
        ),
        chain_tx_shape,
    )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    perp_order_id = response_order_id(perp_place)
    perp_tx_hash = response_tx_hash(perp_place)
    if perp_tx_hash:
        record(
            results,
            "chain_tx",
            "perp_order_by_tx_after_place",
            lambda: wait_for_indexed(lambda: v1.account.perp_order_by_tx(tx_hash=perp_tx_hash)),
            ok_shape,
        )
    if perp_order_id is not None:
        record(
            results,
            "chain_tx",
            "perp_order_by_id_after_place",
            lambda: wait_for_indexed(
                lambda: v1.account.perp_order_by_id(
                    address=SUBACCOUNT,
                    order_id=perp_order_id,
                )
            ),
            ok_shape,
        )
        perp_cancel = record(
            results,
            "chain_tx",
            "cancel_perp_order",
            lambda: v1.chain_tx.cancel_perp_order(
                market_id=PERP_MARKET_ID,
                order_id=perp_order_id,
                nonce_ms=int(time.time() * 1000),
            ),
            chain_tx_shape,
        )
        perp_cancel_tx = response_tx_hash(perp_cancel)
        if perp_cancel_tx:
            record(
                results,
                "chain_tx",
                "perp_order_by_id_after_cancel",
                lambda: wait_for_indexed(
                    lambda: v1.account.perp_order_by_id(
                        address=SUBACCOUNT,
                        order_id=perp_order_id,
                    )
                ),
                ok_shape,
            )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    spot_place = record(
        results,
        "chain_tx",
        "place_spot_order_buy",
        lambda: v1.chain_tx.place_spot_order_buy(
            pair=SPOT_PAIR,
            quote_amount=SPOT_BUY_QUOTE_RAW,
            base_amount=SPOT_BUY_BASE_RAW,
            post_only=1,
            subaccount=FUNDED_SUBACCOUNT,
            nonce_ms=int(time.time() * 1000),
        ),
        chain_tx_shape,
    )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    spot_order_id = response_order_id(spot_place)
    spot_tx_hash = response_tx_hash(spot_place)
    if spot_tx_hash:
        record(
            results,
            "chain_tx",
            "spot_order_by_tx_after_place",
            lambda: wait_for_indexed(lambda: v1.account.spot_order_by_tx(tx_hash=spot_tx_hash)),
            ok_shape,
        )
    if spot_order_id is not None:
        record(
            results,
            "chain_tx",
            "spot_order_by_id_after_place",
            lambda: wait_for_indexed(
                lambda: v1.account.spot_order_by_id(
                    address=FUNDED_SUBACCOUNT,
                    order_id=spot_order_id,
                )
            ),
            ok_shape,
        )
        spot_cancel = record(
            results,
            "chain_tx",
            "cancel_spot_order_buy",
            lambda: v1.chain_tx.cancel_spot_order_buy(
                pair=SPOT_PAIR,
                order_id=spot_order_id,
                subaccount=FUNDED_SUBACCOUNT,
                nonce_ms=int(time.time() * 1000),
            ),
            chain_tx_shape,
        )
        spot_cancel_tx = response_tx_hash(spot_cancel)
        if spot_cancel_tx:
            record(
                results,
                "chain_tx",
                "spot_order_by_id_after_cancel",
                lambda: wait_for_indexed(
                    lambda: v1.account.spot_order_by_id(
                        address=FUNDED_SUBACCOUNT,
                        order_id=spot_order_id,
                    )
                ),
                ok_shape,
            )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    close_place = record(
        results,
        "chain_tx",
        "place_perp_order_market_for_close",
        lambda: v1.chain_tx.place_perp_order_market(
            market_id=PERP_MARKET_ID,
            is_long=True,
            size=PERP_SIZE_RAW,
            leverage=1,
            nonce_ms=int(time.time() * 1000),
        ),
        chain_tx_shape,
    )
    close_place_tx = response_tx_hash(close_place)
    if close_place_tx:
        record(
            results,
            "chain_tx",
            "perp_order_by_tx_after_market_place_for_close",
            lambda: wait_for_indexed(lambda: v1.account.perp_order_by_tx(tx_hash=close_place_tx)),
            ok_shape,
        )
    record(
        results,
        "chain_tx",
        "perp_positions_after_market_place_for_close",
        lambda: wait_for_indexed(
            lambda: v1.account.subaccount_perp_positions(
                address=SUBACCOUNT,
                symbol=PERP_SYMBOL,
            )
        ),
        has_perp_position_shape,
    )
    close_result = record(
        results,
        "chain_tx",
        "close_position_market",
        lambda: v1.chain_tx.close_position_market(
            market_id=PERP_MARKET_ID,
            slippage=10,
            nonce=int(time.time() * 1000),
        ),
        chain_tx_shape,
    )
    close_tx = response_tx_hash(close_result)
    if close_tx:
        record(
            results,
            "chain_tx",
            "perp_order_by_tx_after_close_position",
            lambda: wait_for_indexed(lambda: v1.account.perp_order_by_tx(tx_hash=close_tx)),
            ok_shape,
        )
    record(
        results,
        "chain_tx",
        "perp_positions_after_close_position",
        lambda: wait_for_indexed(
            lambda: v1.account.subaccount_perp_positions(
                address=SUBACCOUNT,
                symbol=PERP_SYMBOL,
            )
        ),
        no_perp_position_shape,
    )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")


def run_chain_tx_methods(results: list[dict[str, Any]]) -> None:
    group = "chain_tx_methods"
    private_key = read_private_key()
    api = dx.ApiClient(
        base_url=BASE_URL,
        ws_base_url=WS_BASE_URL,
        substrate_ws=SUBSTRATE_WS,
        private_key=private_key,
        subaccount=SUBACCOUNT,
        timeout=45,
    )
    v1 = api.v1

    generic_perp = record(
        results,
        group,
        "place_perp_order",
        lambda: v1.chain_tx.place_perp_order(
            market_id=PERP_MARKET_ID,
            is_long=True,
            size=PERP_SIZE_RAW,
            price=PERP_PRICE_RAW,
            order_type=0,
            leverage=1,
            post_only=1,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    _record_perp_order_index(results, v1, group=group, prefix="place_perp_order", order=generic_perp)
    _cancel_perp_if_present(results, v1, group=group, prefix="place_perp_order", order=generic_perp)
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    close_limit_place = record(
        results,
        group,
        "place_perp_order_market_for_close_position_limit",
        lambda: v1.chain_tx.place_perp_order_market(
            market_id=PERP_MARKET_ID,
            is_long=True,
            size=PERP_SIZE_RAW,
            leverage=1,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    _record_perp_order_index(
        results,
        v1,
        group=group,
        prefix="place_perp_order_market_for_close_position_limit",
        order=close_limit_place,
    )
    position_for_close_limit = record(
        results,
        group,
        "positions_before_close_position_limit",
        lambda: wait_for_indexed(
            lambda: v1.account.subaccount_perp_positions(
                address=SUBACCOUNT,
                symbol=PERP_SYMBOL,
            )
        ),
        has_perp_position_shape,
    )
    if has_perp_position_shape(position_for_close_limit):
        close_limit = record(
            results,
            group,
            "close_position_limit",
            lambda: v1.chain_tx.close_position_limit(
                market_id=PERP_MARKET_ID,
                price=PERP_PRICE_RAW,
                nonce=_nonce_ms(),
            ),
            chain_tx_shape,
        )
        _record_perp_order_index(results, v1, group=group, prefix="close_position_limit", order=close_limit)
        record(
            results,
            group,
            "positions_after_close_position_limit",
            lambda: wait_for_indexed(
                lambda: v1.account.subaccount_perp_positions(
                    address=SUBACCOUNT,
                    symbol=PERP_SYMBOL,
                )
            ),
            no_perp_position_shape,
        )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    close_generic_place = record(
        results,
        group,
        "place_perp_order_market_for_close_position",
        lambda: v1.chain_tx.place_perp_order_market(
            market_id=PERP_MARKET_ID,
            is_long=True,
            size=PERP_SIZE_RAW,
            leverage=1,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    _record_perp_order_index(
        results,
        v1,
        group=group,
        prefix="place_perp_order_market_for_close_position",
        order=close_generic_place,
    )
    position_for_close_generic = record(
        results,
        group,
        "positions_before_close_position",
        lambda: wait_for_indexed(
            lambda: v1.account.subaccount_perp_positions(
                address=SUBACCOUNT,
                symbol=PERP_SYMBOL,
            )
        ),
        has_perp_position_shape,
    )
    if has_perp_position_shape(position_for_close_generic):
        close_generic = record(
            results,
            group,
            "close_position",
            lambda: v1.chain_tx.close_position(
                market_id=PERP_MARKET_ID,
                price=0,
                slippage=10,
                nonce=_nonce_ms(),
            ),
            chain_tx_shape,
        )
        _record_perp_order_index(results, v1, group=group, prefix="close_position", order=close_generic)
        record(
            results,
            group,
            "positions_after_close_position",
            lambda: wait_for_indexed(
                lambda: v1.account.subaccount_perp_positions(
                    address=SUBACCOUNT,
                    symbol=PERP_SYMBOL,
                )
            ),
            no_perp_position_shape,
        )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    spot_limit_sell_funding = record(
        results,
        group,
        "place_spot_market_order_buy_without_price_for_limit_sell",
        lambda: v1.chain_tx.place_spot_market_order_buy_without_price(
            pair=SPOT_PAIR,
            quote_amount=SPOT_METHOD_QUOTE_RAW,
            base_amount=SPOT_METHOD_BASE_RAW,
            subaccount=FUNDED_SUBACCOUNT,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    _record_spot_order_index(
        results,
        v1,
        group=group,
        prefix="place_spot_market_order_buy_without_price_for_limit_sell",
        order=spot_limit_sell_funding,
        subaccount=FUNDED_SUBACCOUNT,
        check_by_id=False,
    )
    spot_limit_sell = record(
        results,
        group,
        "place_spot_order_sell",
        lambda: v1.chain_tx.place_spot_order_sell(
            pair=SPOT_PAIR,
            quote_amount=SPOT_METHOD_SELL_LIMIT_QUOTE_RAW,
            base_amount=SPOT_METHOD_SELL_LIMIT_BASE_RAW,
            post_only=1,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    _record_spot_order_index(results, v1, group=group, prefix="place_spot_order_sell", order=spot_limit_sell)
    _cancel_spot_sell_if_present(results, v1, group=group, prefix="place_spot_order_sell", order=spot_limit_sell)
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    spot_market_buy_with_price = record(
        results,
        group,
        "place_spot_market_order_buy_with_price",
        lambda: v1.chain_tx.place_spot_market_order_buy_with_price(
            pair=SPOT_PAIR,
            quote_amount=SPOT_METHOD_QUOTE_RAW,
            base_amount=SPOT_METHOD_BASE_RAW,
            slippage=10,
            subaccount=FUNDED_SUBACCOUNT,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    _record_spot_order_index(
        results,
        v1,
        group=group,
        prefix="place_spot_market_order_buy_with_price",
        order=spot_market_buy_with_price,
        subaccount=FUNDED_SUBACCOUNT,
        check_by_id=False,
    )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    spot_market_sell_without_price = record(
        results,
        group,
        "place_spot_market_order_sell_without_price",
        lambda: v1.chain_tx.place_spot_market_order_sell_without_price(
            pair=SPOT_PAIR,
            quote_amount=SPOT_METHOD_QUOTE_RAW,
            base_amount=SPOT_METHOD_BASE_RAW,
            subaccount=FUNDED_SUBACCOUNT,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    _record_spot_order_index(
        results,
        v1,
        group=group,
        prefix="place_spot_market_order_sell_without_price",
        order=spot_market_sell_without_price,
        subaccount=FUNDED_SUBACCOUNT,
        check_by_id=False,
    )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    spot_market_buy_without_price = record(
        results,
        group,
        "place_spot_market_order_buy_without_price",
        lambda: v1.chain_tx.place_spot_market_order_buy_without_price(
            pair=SPOT_PAIR,
            quote_amount=SPOT_METHOD_QUOTE_RAW,
            base_amount=SPOT_METHOD_BASE_RAW,
            subaccount=FUNDED_SUBACCOUNT,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    _record_spot_order_index(
        results,
        v1,
        group=group,
        prefix="place_spot_market_order_buy_without_price",
        order=spot_market_buy_without_price,
        subaccount=FUNDED_SUBACCOUNT,
        check_by_id=False,
    )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    spot_market_sell_with_price = record(
        results,
        group,
        "place_spot_market_order_sell_with_price",
        lambda: v1.chain_tx.place_spot_market_order_sell_with_price(
            pair=SPOT_PAIR,
            quote_amount=SPOT_METHOD_QUOTE_RAW,
            base_amount=SPOT_METHOD_BASE_RAW,
            slippage=10,
            nonce_ms=_nonce_ms(),
        ),
        chain_tx_shape,
    )
    _record_spot_order_index(
        results,
        v1,
        group=group,
        prefix="place_spot_market_order_sell_with_price",
        order=spot_market_sell_with_price,
        check_by_id=False,
    )
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    results: list[dict[str, Any]] = []
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in {"all", "rest"}:
        run_rest(results)
        REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    if mode in {"all", "ws"}:
        if mode == "ws" and REPORT.exists():
            results = json.loads(REPORT.read_text(encoding="utf-8"))
            results = [r for r in results if r.get("group") != "ws"]
        asyncio.run(run_ws(results))
    if mode in {"all", "chain_tx"}:
        if mode == "chain_tx" and REPORT.exists():
            results = json.loads(REPORT.read_text(encoding="utf-8"))
            results = [r for r in results if r.get("group") != "chain_tx"]
        run_chain_tx(results)
    if mode in {"all", "ws_chain_tx"}:
        if mode == "ws_chain_tx" and REPORT.exists():
            results = json.loads(REPORT.read_text(encoding="utf-8"))
            results = [r for r in results if r.get("group") != "ws_chain_tx"]
        run_ws_chain_tx(results)
    if mode in {"all", "chain_tx_methods"}:
        if mode == "chain_tx_methods" and REPORT.exists():
            results = json.loads(REPORT.read_text(encoding="utf-8"))
            results = [r for r in results if r.get("group") != "chain_tx_methods"]
        run_chain_tx_methods(results)
    REPORT.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = [r for r in results if r["status"] != "PASS"]
    print(json.dumps({"report": str(REPORT), "passed": passed, "failed": len(failed), "failures": failed[:20]}, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
