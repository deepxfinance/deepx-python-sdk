from __future__ import annotations

import asyncio
import gzip
import json
import sys
import types
from typing import Any

import pytest

if "substrateinterface" not in sys.modules:
    substrate_stub = types.ModuleType("substrateinterface")

    class _SubstrateInterfacePlaceholder:
        pass

    substrate_stub.SubstrateInterface = _SubstrateInterfacePlaceholder
    sys.modules["substrateinterface"] = substrate_stub

import deepx_sdk as dx
from deepx_sdk._network import DEFAULT_NET, network_config
from deepx_sdk.ws_client import (
    WsClient,
    WsMessage,
    WsSession,
    parse_ws_message,
    v1_list,
    v1_ping,
    v1_pong,
    v1_post,
    v1_sub_account_balances,
    v1_sub_account_perp_orders,
    v1_sub_account_perp_positions,
    v1_sub_account_perp_trades,
    v1_sub_account_portfolio,
    v1_sub_account_spot_orders,
    v1_sub_account_spot_trades,
    v1_sub_lending_market_status,
    v1_sub_perp_candles,
    v1_sub_perp_funding_rate,
    v1_sub_perp_long_short_ratio,
    v1_sub_perp_open_interest,
    v1_sub_perp_orderbook,
    v1_sub_perp_prices,
    v1_sub_perp_ticker,
    v1_sub_perp_trades,
    v1_sub_spot_candles,
    v1_sub_spot_orderbook,
    v1_sub_spot_ticker,
    v1_sub_spot_trades,
    v1_subscribe,
    v1_unsub_account_balances,
    v1_unsub_account_perp_positions,
    v1_unsub_lending_market_status,
    v1_unsub_perp_open_interest,
    v1_unsub_perp_orderbook,
    v1_unsub_spot_ticker,
    v1_unsubscribe,
    v1_ws_params,
)


def _make_api() -> dx.ApiClient:
    return dx.ApiClient(base_url="http://127.0.0.1:8080")


def _patch_request(monkeypatch: pytest.MonkeyPatch, api: dx.ApiClient) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_request(
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        captured["method"] = method
        captured["path"] = path
        captured["params"] = params
        captured["json_body"] = json_body
        captured["headers"] = headers
        return {"ok": True, "path": path}

    monkeypatch.setattr(api, "request", fake_request)
    return captured


@pytest.mark.parametrize(
    "invoke,expected_path,expected_params",
    [
        (lambda api: api.v1.ping(), "/v1/ping", None),
        (lambda api: api.v1.time(), "/v1/time", None),
        (
            lambda api: api.v1.account.wallet_subaccounts(address="0xabc"),
            "/v1/account/wallets/0xabc/subaccounts",
            None,
        ),
        (
            lambda api: api.v1.account.wallet_delegate_accounts(address="0xabc"),
            "/v1/account/wallets/0xabc/delegate-accounts",
            None,
        ),
        (
            lambda api: api.v1.account.delegator_accounts(address="0xabc"),
            "/v1/account/delegates/0xabc/delegator-accounts",
            None,
        ),
        (
            lambda api: api.v1.account.internal_delegate_accounts(address="0xabc"),
            "/internal/v1/account/delegate-accounts",
            {"address": "0xabc"},
        ),
        (
            lambda api: api.v1.account.internal_delegator_accounts(address="0xabc"),
            "/internal/v1/account/delegator-accounts",
            {"address": "0xabc"},
        ),
        (
            lambda api: api.v1.account.subaccount_info(address="0xsub"),
            "/v1/account/subaccounts/0xsub/info",
            None,
        ),
        (
            lambda api: api.v1.account.subaccount_margin_ratio(address="0xsub"),
            "/v1/account/subaccounts/0xsub/stats/margin-ratio",
            None,
        ),
        (
            lambda api: api.v1.account.subaccount_perp_positions(
                address="0xsub",
                symbol="ETH-USDC",
            ),
            "/v1/account/subaccounts/0xsub/perp/positions",
            {"symbol": "ETH-USDC"},
        ),
        (
            lambda api: api.v1.account.subaccount_perp_positions_history(
                address="0xsub",
                symbol="ETH-USDC",
                limit=10,
                start_time=100,
                end_time=200,
                sort="DESC",
            ),
            "/v1/account/subaccounts/0xsub/perp/positions/history",
            {
                "symbol": "ETH-USDC",
                "limit": 10,
                "startTime": 100,
                "endTime": 200,
                "sort": "DESC",
            },
        ),
        (
            lambda api: api.v1.account.subaccount_perp_orders(
                address="0xsub",
                symbol="ETH-USDC",
                side="buy",
                from_id=9,
                sort="DESC",
                limit=10,
            ),
            "/v1/account/subaccounts/0xsub/perp/orders",
            {
                "symbol": "ETH-USDC",
                "side": "Buy",
                "fromId": 9,
                "sort": "DESC",
                "limit": 10,
            },
        ),
        (
            lambda api: api.v1.account.subaccount_perp_open_orders(
                address="0xsub",
                symbol="ETH-USDC",
            ),
            "/v1/account/subaccounts/0xsub/perp/orders/open",
            {"symbol": "ETH-USDC"},
        ),
        (
            lambda api: api.v1.account.subaccount_perp_trades(
                address="0xsub",
                symbol="ETH-USDC",
                from_id=10,
                sort="DESC",
                start_time=1,
                end_time=2,
                limit=5,
            ),
            "/v1/account/subaccounts/0xsub/perp/trades",
            {
                "symbol": "ETH-USDC",
                "fromId": 10,
                "sort": "DESC",
                "startTime": 1,
                "endTime": 2,
                "limit": 5,
            },
        ),
        (
            lambda api: api.v1.account.subaccount_perp_funding_payments(
                address="0xsub",
                symbol="ETH-USDC",
                limit=5,
            ),
            "/v1/account/subaccounts/0xsub/perp/funding-payments",
            {"symbol": "ETH-USDC", "limit": 5},
        ),
        (
            lambda api: api.v1.account.subaccount_spot_orders(
                address="0xsub",
                symbol="ETH/USDC",
                side="buy",
                sort="ASC",
                limit=20,
            ),
            "/v1/account/subaccounts/0xsub/spot/orders",
            {"symbol": "ETH/USDC", "side": "Buy", "sort": "ASC", "limit": 20},
        ),
        (
            lambda api: api.v1.account.subaccount_spot_open_orders(
                address="0xsub",
                symbol="ETH/USDC",
            ),
            "/v1/account/subaccounts/0xsub/spot/orders/open",
            {"symbol": "ETH/USDC"},
        ),
        (
            lambda api: api.v1.account.subaccount_spot_trades(
                address="0xsub",
                symbol="ETH/USDC",
                limit=30,
            ),
            "/v1/account/subaccounts/0xsub/spot/trades",
            {"symbol": "ETH/USDC", "limit": 30},
        ),
        (
            lambda api: api.v1.account.subaccount_balances(address="0xsub"),
            "/v1/account/subaccounts/0xsub/balances",
            None,
        ),
        (
            lambda api: api.v1.account.subaccount_portfolio(address="0xsub"),
            "/v1/account/subaccounts/0xsub/portfolio",
            None,
        ),
        (
            lambda api: api.v1.account.subaccount_balance_changes(
                address="0xsub",
                limit=100,
                start_time=1,
                end_time=2,
                from_id=9,
                change_type="deposit,withdraw",
            ),
            "/v1/account/subaccounts/0xsub/balance-events",
            {
                "limit": 100,
                "startTime": 1,
                "endTime": 2,
                "cursor": 9,
                "changeType": "deposit,withdraw",
            },
        ),
        (
            lambda api: api.v1.account.subaccount_liquidations(
                address="0xsub",
                limit=50,
                cursor="abc",
                start_time=1,
                end_time=2,
                liquidator="0xliq",
                liquidation_type="perp",
                symbol="ETH-USDC",
            ),
            "/v1/account/subaccounts/0xsub/liquidations",
            {
                "limit": 50,
                "cursor": "abc",
                "startTime": 1,
                "endTime": 2,
                "liquidator": "0xliq",
                "liquidationType": "perp",
                "symbol": "ETH-USDC",
            },
        ),
        (
            lambda api: api.v1.account.perp_order_by_tx(tx_hash="0xtx"),
            "/v1/account/perp/orders/tx/0xtx",
            None,
        ),
        (
            lambda api: api.v1.account.spot_order_by_tx(tx_hash="0xtx"),
            "/v1/account/spot/orders/tx/0xtx",
            None,
        ),
        (
            lambda api: api.v1.account.perp_order_by_id(address="0xsub", order_id=123),
            "/v1/account/subaccounts/0xsub/perp/orders/123",
            None,
        ),
        (
            lambda api: api.v1.account.spot_order_by_id(address="0xsub", order_id=123),
            "/v1/account/subaccounts/0xsub/spot/orders/123",
            None,
        ),
        (
            lambda api: api.v1.spot.markets(symbols="ETH/USDC,BTC/USDC"),
            "/v1/spot/markets",
            {"symbols": "ETH/USDC,BTC/USDC"},
        ),
        (
            lambda api: api.v1.spot.market(symbol="ETH-USDC"),
            "/v1/spot/markets/ETH-USDC",
            None,
        ),
        (
            lambda api: api.v1.spot.candles(
                symbol="ETH-USDC",
                interval="1m",
                limit=100,
                start_time=10,
                end_time=20,
                price_type="trade",
            ),
            "/v1/spot/markets/ETH-USDC/candles",
            {
                "interval": "1m",
                "limit": 100,
                "startTime": 10,
                "endTime": 20,
                "priceType": "trade",
            },
        ),
        (
            lambda api: api.v1.spot.trades(symbol="ETH-USDC", from_id=7, cursor=8, limit=15),
            "/v1/spot/markets/ETH-USDC/trades",
            {"fromId": 7, "cursor": 8, "limit": 15},
        ),
        (
            lambda api: api.v1.spot.orderbook(symbol="ETH-USDC", limit=100, merge_level=1),
            "/v1/spot/markets/ETH-USDC/orderbook",
            {"limit": 100, "mergeLevel": 1},
        ),
        (lambda api: api.v1.perp.markets(), "/v1/perp/markets", None),
        (
            lambda api: api.v1.perp.market(symbol="ETH-USDC"),
            "/v1/perp/markets/ETH-USDC",
            None,
        ),
        (
            lambda api: api.v1.perp.candles(
                symbol="ETH-USDC",
                interval="5m",
                limit=50,
                price_type="index",
            ),
            "/v1/perp/markets/ETH-USDC/candles",
            {"interval": "5m", "limit": 50, "priceType": "index"},
        ),
        (
            lambda api: api.v1.perp.trades(symbol="ETH-USDC", limit=25, from_id=3, cursor=4),
            "/v1/perp/markets/ETH-USDC/trades",
            {"limit": 25, "fromId": 3, "cursor": 4},
        ),
        (
            lambda api: api.v1.perp.orderbook(symbol="ETH-USDC", limit=50, merge_level=2),
            "/v1/perp/markets/ETH-USDC/orderbook",
            {"limit": 50, "mergeLevel": 2},
        ),
        (
            lambda api: api.v1.perp.open_interest(symbol="ETH-USDC"),
            "/v1/perp/markets/ETH-USDC/open-interest",
            None,
        ),
        (
            lambda api: api.v1.perp.open_interest_history(
                symbol="ETH-USDC",
                interval="1h",
                start_time=10,
                end_time=20,
                limit=100,
                sort="ASC",
            ),
            "/v1/perp/markets/ETH-USDC/open-interest/history",
            {
                "interval": "1h",
                "startTime": 10,
                "endTime": 20,
                "limit": 100,
                "sort": "ASC",
            },
        ),
        (
            lambda api: api.v1.perp.funding_rate(symbol="ETH-USDC"),
            "/v1/perp/markets/ETH-USDC/funding-rate",
            None,
        ),
        (
            lambda api: api.v1.perp.funding_rate_history(
                symbol="ETH-USDC",
                interval="1h",
                limit=100,
                start_time=1,
                end_time=2,
            ),
            "/v1/perp/markets/ETH-USDC/funding-rate/history",
            {"interval": "1h", "limit": 100, "startTime": 1, "endTime": 2},
        ),
        (
            lambda api: api.v1.perp.funding_rate_history(symbol="ETH-USDC"),
            "/v1/perp/markets/ETH-USDC/funding-rate/history",
            {"interval": "1m"},
        ),
        (
            lambda api: api.v1.perp.long_short_ratio(symbol="ETH-USDC"),
            "/v1/perp/markets/ETH-USDC/long-short-ratio",
            None,
        ),
        (
            lambda api: api.v1.perp.long_short_ratio_history(
                symbol="ETH-USDC",
                interval="1h",
                limit=100,
                start_time=1,
                end_time=2,
            ),
            "/v1/perp/markets/ETH-USDC/long-short-ratio/history",
            {"interval": "1h", "limit": 100, "startTime": 1, "endTime": 2},
        ),
        (
            lambda api: api.v1.perp.long_short_ratio_history(symbol="ETH-USDC"),
            "/v1/perp/markets/ETH-USDC/long-short-ratio/history",
            {"interval": "1m"},
        ),
        (lambda api: api.v1.lending.markets(), "/v1/lending/markets", None),
        (
            lambda api: api.v1.lending.market(asset="USDC"),
            "/v1/lending/markets/USDC",
            None,
        ),
        (
            lambda api: api.v1.lending.market_status(),
            "/v1/lending/markets/status",
            None,
        ),
        (
            lambda api: api.v1.lending.market_status(asset="USDC"),
            "/v1/lending/markets/USDC/status",
            None,
        ),
        (
            lambda api: api.v1.lending.market_status_history(
                asset="USDC",
                interval="1h",
                start_time=10,
                end_time=20,
                limit=100,
                sort="DESC",
            ),
            "/v1/lending/markets/USDC/status/history",
            {
                "interval": "1h",
                "startTime": 10,
                "endTime": 20,
                "limit": 100,
                "sort": "DESC",
            },
        ),
    ],
)
def test_api_v1_request_forwarding(
    monkeypatch: pytest.MonkeyPatch,
    invoke,
    expected_path: str,
    expected_params: dict[str, Any] | None,
) -> None:
    api = _make_api()
    captured = _patch_request(monkeypatch, api)

    result = invoke(api)

    assert result == {"ok": True, "path": expected_path}
    assert captured["method"] == "GET"
    assert captured["path"] == expected_path
    assert captured["params"] == expected_params
    assert captured["json_body"] is None


def test_api_v1_ws_helpers() -> None:
    api = _make_api()

    assert api.v1.ws.websocket_url() == f"{network_config(DEFAULT_NET).ws_base_url}/v1/ws"
    api.ws_base_url = "ws://127.0.0.1:8080"
    assert api.v1.ws.websocket_url() == "ws://127.0.0.1:8080/v1/ws"
    assert v1_ws_params(
        "account@perp-orders",
        symbol="ETH-USDC",
        wallet="0xabc",
    ) == {
        "channel": "account@perp-orders",
        "symbol": "ETH-USDC",
        "wallet": "0xabc",
    }
    assert v1_subscribe(1, channel="spot@ticker", symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 1,
        "params": {"channel": "spot@ticker", "symbol": "ETH-USDC"},
    }
    assert v1_unsubscribe("2", channel="account@balances", subaccount="0xsub") == {
        "method": "unsubscribe",
        "id": "2",
        "params": {"channel": "account@balances", "subaccount": "0xsub"},
    }
    assert v1_list(3) == {"method": "list", "id": 3}
    assert v1_ping() == {"method": "ping"}
    assert v1_ping() == {"method": "ping"}
    assert v1_subscribe(1, channel="spot@ticker", symbol="ETH-USDC") == v1_subscribe(
        1,
        channel="spot@ticker",
        symbol="ETH-USDC",
    )
    assert v1_pong() == {"method": "pong"}
    assert v1_post(4, route="placePerpOrder", payload={"signedExtrinsic": "0xabc"}) == {
        "method": "post",
        "id": 4,
        "request": {"route": "placePerpOrder", "payload": {"signedExtrinsic": "0xabc"}},
    }

    # V1 subscribe helper spot
    assert v1_sub_spot_orderbook(1, symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 1,
        "params": {"channel": "spot@orderbook", "symbol": "ETH-USDC"},
    }
    assert v1_sub_spot_trades(2, symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 2,
        "params": {"channel": "spot@trades", "symbol": "ETH-USDC"},
    }
    assert v1_sub_spot_ticker(3, symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 3,
        "params": {"channel": "spot@ticker", "symbol": "ETH-USDC"},
    }
    assert v1_sub_spot_candles(4, symbol="ETH-USDC", interval="1m") == {
        "method": "subscribe",
        "id": 4,
        "params": {"channel": "spot@candles", "symbol": "ETH-USDC", "interval": "1m"},
    }

    # V1 subscribe helper perp
    assert v1_sub_perp_orderbook(5, symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 5,
        "params": {"channel": "perp@orderbook", "symbol": "ETH-USDC"},
    }
    assert v1_sub_perp_trades(6, symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 6,
        "params": {"channel": "perp@trades", "symbol": "ETH-USDC"},
    }
    assert v1_sub_perp_ticker(7, symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 7,
        "params": {"channel": "perp@ticker", "symbol": "ETH-USDC"},
    }
    assert v1_sub_perp_prices(8, symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 8,
        "params": {"channel": "perp@prices", "symbol": "ETH-USDC"},
    }
    assert v1_sub_perp_funding_rate(9, symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 9,
        "params": {"channel": "perp@funding-rate", "symbol": "ETH-USDC"},
    }
    assert v1_sub_perp_open_interest(10, symbol="ETH-USDC", interval="1h") == {
        "method": "subscribe",
        "id": 10,
        "params": {
            "channel": "perp@open-interest",
            "symbol": "ETH-USDC",
            "interval": "1h",
        },
    }
    assert v1_sub_perp_long_short_ratio(11, symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 11,
        "params": {"channel": "perp@long-short-ratio", "symbol": "ETH-USDC"},
    }
    assert v1_sub_perp_candles(12, symbol="ETH-USDC", interval="1m") == {
        "method": "subscribe",
        "id": 12,
        "params": {"channel": "perp@candles", "symbol": "ETH-USDC", "interval": "1m"},
    }

    # V1 subscribe helper lending / account
    assert v1_sub_lending_market_status(13, asset="USDC") == {
        "method": "subscribe",
        "id": 13,
        "params": {"channel": "lending@market-status", "asset": "USDC"},
    }
    assert v1_sub_account_balances(14, subaccount="0xsub") == {
        "method": "subscribe",
        "id": 14,
        "params": {"channel": "account@balances", "subaccount": "0xsub"},
    }
    assert v1_sub_account_portfolio(15, subaccount="0xsub") == {
        "method": "subscribe",
        "id": 15,
        "params": {"channel": "account@portfolio", "subaccount": "0xsub"},
    }
    assert v1_sub_account_perp_positions(16, subaccount="0xsub", symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 16,
        "params": {
            "channel": "account@perp-positions",
            "subaccount": "0xsub",
            "symbol": "ETH-USDC",
        },
    }
    assert v1_sub_account_perp_orders(17, subaccount="0xsub", wallet="0xwal", symbol="ETH-USDC") == {
        "method": "subscribe",
        "id": 17,
        "params": {
            "channel": "account@perp-orders",
            "subaccount": "0xsub",
            "wallet": "0xwal",
            "symbol": "ETH-USDC",
        },
    }
    assert v1_sub_account_spot_orders(18, subaccount="0xsub") == {
        "method": "subscribe",
        "id": 18,
        "params": {"channel": "account@spot-orders", "subaccount": "0xsub"},
    }
    assert v1_sub_account_perp_trades(19, wallet="0xwal") == {
        "method": "subscribe",
        "id": 19,
        "params": {"channel": "account@perp-trades", "wallet": "0xwal"},
    }
    assert v1_sub_account_spot_trades(20, subaccount="0xsub", symbol="ETH/USDC") == {
        "method": "subscribe",
        "id": 20,
        "params": {
            "channel": "account@spot-trades",
            "subaccount": "0xsub",
            "symbol": "ETH/USDC",
        },
    }

    # V1 unsubscribe helpers (mirror some)
    assert v1_unsub_spot_ticker(21, symbol="ETH-USDC") == {
        "method": "unsubscribe",
        "id": 21,
        "params": {"channel": "spot@ticker", "symbol": "ETH-USDC"},
    }
    assert v1_unsub_perp_orderbook(22, symbol="ETH-USDC") == {
        "method": "unsubscribe",
        "id": 22,
        "params": {"channel": "perp@orderbook", "symbol": "ETH-USDC"},
    }
    assert v1_unsub_perp_open_interest(23, symbol="ETH-USDC", interval="1h") == {
        "method": "unsubscribe",
        "id": 23,
        "params": {
            "channel": "perp@open-interest",
            "symbol": "ETH-USDC",
            "interval": "1h",
        },
    }
    assert v1_unsub_account_balances(24, subaccount="0xsub") == {
        "method": "unsubscribe",
        "id": 24,
        "params": {"channel": "account@balances", "subaccount": "0xsub"},
    }
    assert v1_unsub_account_perp_positions(25, subaccount="0xsub") == {
        "method": "unsubscribe",
        "id": 25,
        "params": {"channel": "account@perp-positions", "subaccount": "0xsub"},
    }
    assert v1_unsub_lending_market_status(26) == {
        "method": "unsubscribe",
        "id": 26,
        "params": {"channel": "lending@market-status"},
    }


def test_api_v1_ws_message_parser_and_session_recv_message() -> None:
    payload = {
        "channel": "subscriptionResponse",
        "data": {
            "method": "subscribe",
            "id": 1,
            "result": {"ok": True},
            "params": {"channel": "spot@ticker", "symbol": "ETH-USDC"},
        },
    }

    msg = parse_ws_message(payload)

    assert isinstance(msg, WsMessage)
    assert msg.channel == "subscriptionResponse"
    assert msg.method == "subscribe"
    assert msg.request_id == 1
    assert msg.result == {"ok": True}
    assert msg.error is None
    assert msg.raw is payload
    assert parse_ws_message({"method": "ping"}).method == "ping"
    assert parse_ws_message("raw").data == "raw"
    assert parse_ws_message(
        {"id": 9, "result": "ok", "params": {"channel": "spot@ticker"}}
    ).channel == "spot@ticker"
    data_message = parse_ws_message(
        {
            "data": {
                "id": 10,
                "error": {"message": "bad"},
                "params": {"channel": "perp@ticker"},
            }
        }
    )
    assert data_message.channel == "perp@ticker"
    assert data_message.request_id == 10
    assert data_message.error == {"message": "bad"}

    class FakeWs:
        async def recv(self) -> str:
            return json.dumps(payload)

    received = asyncio.run(WsSession(FakeWs()).recv_message())
    assert received.channel == "subscriptionResponse"
    assert received.request_id == 1

    class GzipWs:
        async def recv(self) -> bytes:
            return gzip.compress(json.dumps(payload).encode("utf-8"))

    gzipped = asyncio.run(WsSession(GzipWs()).recv_json())
    assert gzipped == payload

    class InvalidJsonWs:
        async def recv(self) -> str:
            return "not-json"

    with pytest.raises(json.JSONDecodeError):
        asyncio.run(WsSession(InvalidJsonWs()).recv_json())


def test_ws_client_connect_and_session_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    import deepx_sdk.ws_client as ws_mod

    sent: list[object] = []
    closed = False

    class FakeSocket:
        async def send(self, payload):
            sent.append(payload)

        async def recv(self) -> str:
            return json.dumps({"method": "pong"})

        async def close(self):
            nonlocal closed
            closed = True

    captured: dict[str, Any] = {}

    async def fake_connect(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeSocket()

    monkeypatch.setattr(
        ws_mod,
        "websockets",
        types.SimpleNamespace(connect=fake_connect),
    )

    async def run() -> None:
        client = WsClient(
            base_url="https://ws.example.test",
            path="v1/ws",
            headers={"Authorization": "Bearer key"},
            open_timeout=1,
            close_timeout=2,
            ping_interval=None,
            ping_timeout=None,
        )
        assert client.ws_url() == "wss://ws.example.test/v1/ws"
        session = await client.connect()
        async with session as ws:
            await ws.send("raw")
            await ws.send_json({"method": "ping"})
            assert await ws.recv() == '{"method": "pong"}'
            await ws.subscribe(1, channel="spot@ticker", symbol="ETH-USDC")
            await ws.unsubscribe(2, channel="spot@ticker", symbol="ETH-USDC")
            await ws.list(3)
            await ws.post(4, route="route", payload={"x": 1})
            await ws.ping()
            await ws.pong()

    asyncio.run(run())

    assert captured["url"] == "wss://ws.example.test/v1/ws"
    assert captured["kwargs"]["extra_headers"] == {"Authorization": "Bearer key"}
    assert captured["kwargs"]["open_timeout"] == 1
    assert closed is True
    assert sent[0] == "raw"
    assert json.loads(sent[1]) == {"method": "ping"}
    assert json.loads(sent[-1]) == {"method": "pong"}

    monkeypatch.setattr(ws_mod, "websockets", None)
    with pytest.raises(RuntimeError, match="requires the 'websockets' package"):
        ws_mod._ensure_websockets()
    assert ws_mod._to_ws_url("ws://x") == "ws://x"
    assert ws_mod._to_ws_url("http://x") == "ws://x"
    assert ws_mod._to_ws_url("x") == "ws://x"


def test_ws_unsubscribe_helper_mirrors() -> None:
    import deepx_sdk.ws_client as ws_mod

    cases = [
        (ws_mod.v1_unsub_spot_orderbook(1, symbol="ETH-USDC"), "spot@orderbook"),
        (ws_mod.v1_unsub_spot_trades(2, symbol="ETH-USDC"), "spot@trades"),
        (ws_mod.v1_unsub_spot_candles(3, symbol="ETH-USDC", interval="1m"), "spot@candles"),
        (ws_mod.v1_unsub_perp_trades(4, symbol="ETH-USDC"), "perp@trades"),
        (ws_mod.v1_unsub_perp_ticker(5, symbol="ETH-USDC"), "perp@ticker"),
        (ws_mod.v1_unsub_perp_prices(6, symbol="ETH-USDC"), "perp@prices"),
        (ws_mod.v1_unsub_perp_funding_rate(7, symbol="ETH-USDC"), "perp@funding-rate"),
        (ws_mod.v1_unsub_perp_long_short_ratio(8, symbol="ETH-USDC"), "perp@long-short-ratio"),
        (ws_mod.v1_unsub_perp_candles(9, symbol="ETH-USDC", interval="1m"), "perp@candles"),
        (ws_mod.v1_unsub_account_portfolio(10, subaccount="0xsub"), "account@portfolio"),
        (
            ws_mod.v1_unsub_account_perp_orders(
                11, subaccount="0xsub", wallet="0xwal", symbol="ETH-USDC"
            ),
            "account@perp-orders",
        ),
        (ws_mod.v1_unsub_account_spot_orders(12, subaccount="0xsub"), "account@spot-orders"),
        (ws_mod.v1_unsub_account_perp_trades(13, wallet="0xwal"), "account@perp-trades"),
        (
            ws_mod.v1_unsub_account_spot_trades(
                14, subaccount="0xsub", symbol="ETH-USDC"
            ),
            "account@spot-trades",
        ),
    ]

    for payload, channel in cases:
        assert payload["method"] == "unsubscribe"
        assert payload["params"]["channel"] == channel


def test_api_v1_balance_change_limit_validation() -> None:
    api = _make_api()

    with pytest.raises(ValueError, match="limit must be between 1 and 500"):
        api.v1.account.subaccount_balance_changes(address="0xsub", limit=501)


def test_api_v1_candle_limit_validation() -> None:
    api = _make_api()

    with pytest.raises(ValueError, match="limit must be between 1 and 500"):
        api.v1.spot.candles(symbol="ETH-USDC", interval="1m", limit=501)


def test_api_v1_orderbook_limit_validation() -> None:
    api = _make_api()

    with pytest.raises(ValueError, match="limit must be between 1 and 500"):
        api.v1.spot.orderbook(symbol="ETH-USDC", limit=501)


def test_api_v1_merge_level_validation() -> None:
    api = _make_api()

    with pytest.raises(ValueError, match="merge_level must be between 0 and 3"):
        api.v1.spot.orderbook(symbol="ETH-USDC", merge_level=4)


def test_api_v1_oi_limit_validation() -> None:
    api = _make_api()

    with pytest.raises(ValueError, match="limit must be between 1 and 5000"):
        api.v1.perp.open_interest_history(symbol="ETH-USDC", interval="1h", limit=5001)


def test_api_v1_history_limit_validation() -> None:
    api = _make_api()

    with pytest.raises(ValueError, match="limit must be between 1 and 5000"):
        api.v1.perp.funding_rate_history(symbol="ETH-USDC", limit=5001)
    with pytest.raises(ValueError, match="limit must be between 1 and 5000"):
        api.v1.perp.long_short_ratio_history(symbol="ETH-USDC", limit=5001)
    with pytest.raises(ValueError, match="limit must be between 1 and 5000"):
        api.v1.lending.market_status_history(asset="USDC", limit=5001)


# -----------------------------------------------------------------------------
# ChainTx
# -----------------------------------------------------------------------------


def _patch_chain_tx_signing(monkeypatch: pytest.MonkeyPatch, api: dx.ApiClient) -> None:
    monkeypatch.setattr(
        api.v1.chain_tx,
        "_build_signed_pallet_call_extrinsic",
        lambda **kwargs: "0xfake_signed_extrinsic",
    )


def _patch_chain_tx_signing_capture(
    monkeypatch: pytest.MonkeyPatch, api: dx.ApiClient
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_build_signed_pallet_call_extrinsic(**kwargs):
        captured.update(kwargs)
        return "0xfake_signed_extrinsic"

    monkeypatch.setattr(
        api.v1.chain_tx,
        "_build_signed_pallet_call_extrinsic",
        fake_build_signed_pallet_call_extrinsic,
    )
    return captured

_VALID_SUBACCOUNT = "0xD1b75179e3B69E47732ECe09b9F489D75233CEf2"
_VALID_PAIR = "0x" + "00" * 31 + "01"


def test_api_v1_chain_tx_place_perp_order(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    _patch_chain_tx_signing(monkeypatch, api)
    captured = _patch_request(monkeypatch, api)

    api.v1.chain_tx.place_perp_order(
        market_id=1,
        is_long=True,
        size=100,
        price=1000,
        order_type=0,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/chain/tx/placePerpOrder"
    assert captured["json_body"] == {"signedExtrinsic": "0xfake_signed_extrinsic"}


def test_api_v1_chain_tx_place_perp_order_ioc(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    _patch_chain_tx_signing(monkeypatch, api)
    captured = _patch_request(monkeypatch, api)
    signed = _patch_chain_tx_signing_capture(monkeypatch, api)

    api.v1.chain_tx.place_perp_order_ioc(
        market_id=1,
        is_long=True,
        size=100,
        price=1000,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/chain/tx/placePerpOrder"
    assert captured["json_body"] == {"signedExtrinsic": "0xfake_signed_extrinsic"}
    # Verify the call_params encode IOC correctly (wrapped in PerpPlaceParams)
    params = signed["call_params"]["params"]
    assert params["order_type"] == {"Limit": "IOC"}
    assert params["price"] == 1000
    assert params["take_profit"] is None
    assert params["stop_loss"] is None
    assert params["post_only"] == "None"
    assert params["cloid"] is None


def test_api_v1_chain_tx_cancel_perp_order(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    _patch_chain_tx_signing(monkeypatch, api)
    captured = _patch_request(monkeypatch, api)

    api.v1.chain_tx.cancel_perp_order(
        market_id=1,
        order_id=42,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/chain/tx/cancelPerpOrder"
    assert captured["json_body"] == {"signedExtrinsic": "0xfake_signed_extrinsic"}


def test_api_v1_chain_tx_close_position(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    _patch_chain_tx_signing(monkeypatch, api)
    captured = _patch_request(monkeypatch, api)

    api.v1.chain_tx.close_position(
        market_id=1,
        price=1000,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/chain/tx/closePosition"
    assert captured["json_body"] == {"signedExtrinsic": "0xfake_signed_extrinsic"}


def test_api_v1_chain_tx_place_spot_order_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    _patch_chain_tx_signing(monkeypatch, api)
    captured = _patch_request(monkeypatch, api)

    api.v1.chain_tx.place_spot_order_buy(
        pair=_VALID_PAIR,
        quote_amount=1000,
        base_amount=1,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/chain/tx/placeSpotOrder"
    assert captured["json_body"] == {"signedExtrinsic": "0xfake_signed_extrinsic"}


def test_api_v1_chain_tx_place_spot_order_buy_ioc(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    _patch_chain_tx_signing(monkeypatch, api)
    captured = _patch_request(monkeypatch, api)
    signed = _patch_chain_tx_signing_capture(monkeypatch, api)

    api.v1.chain_tx.place_spot_order_buy_ioc(
        pair=_VALID_PAIR,
        quote_amount=1000,
        base_amount=1,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/chain/tx/placeSpotOrder"
    assert captured["json_body"] == {"signedExtrinsic": "0xfake_signed_extrinsic"}
    assert signed["call_params"]["params"]["order_type"] == {"Limit": "IOC"}
    assert signed["call_params"]["params"]["is_buy"] is True
    assert signed["call_params"]["params"]["post_only"] == "None"
    assert signed["call_function"] == "place_order"


def test_api_v1_chain_tx_place_spot_order_sell_ioc(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    _patch_chain_tx_signing(monkeypatch, api)
    captured = _patch_request(monkeypatch, api)
    signed = _patch_chain_tx_signing_capture(monkeypatch, api)

    api.v1.chain_tx.place_spot_order_sell_ioc(
        pair=_VALID_PAIR,
        quote_amount=2000,
        base_amount=2,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/chain/tx/placeSpotOrder"
    assert captured["json_body"] == {"signedExtrinsic": "0xfake_signed_extrinsic"}
    assert signed["call_params"]["params"]["order_type"] == {"Limit": "IOC"}
    assert signed["call_params"]["params"]["is_buy"] is False
    assert signed["call_function"] == "place_order"


def test_api_v1_chain_tx_cancel_spot_order_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    _patch_chain_tx_signing(monkeypatch, api)
    captured = _patch_request(monkeypatch, api)

    api.v1.chain_tx.cancel_spot_order_buy(
        pair=_VALID_PAIR,
        order_id=99,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/chain/tx/cancelSpotOrder"
    assert captured["json_body"] == {"signedExtrinsic": "0xfake_signed_extrinsic"}


def test_api_v1_chain_tx_helper_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    _patch_chain_tx_signing(monkeypatch, api)
    captured = _patch_request(monkeypatch, api)

    api.v1.chain_tx.place_perp_order_limit(
        market_id=1,
        is_long=True,
        size=100,
        price=1000,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )
    assert captured["path"] == "/v1/chain/tx/placePerpOrder"

    api.v1.chain_tx.place_perp_order_market(
        market_id=1,
        is_long=True,
        size=100,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )
    assert captured["path"] == "/v1/chain/tx/placePerpOrder"

    api.v1.chain_tx.close_position_limit(
        market_id=1,
        price=1000,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )
    assert captured["path"] == "/v1/chain/tx/closePosition"

    api.v1.chain_tx.close_position_market(
        market_id=1,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )
    assert captured["path"] == "/v1/chain/tx/closePosition"

    api.v1.chain_tx.place_spot_order_sell(
        pair=_VALID_PAIR,
        quote_amount=1000,
        base_amount=1,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )
    assert captured["path"] == "/v1/chain/tx/placeSpotOrder"

    api.v1.chain_tx.place_spot_market_order_buy_without_price(
        pair=_VALID_PAIR,
        quote_amount=1000,
        base_amount=1,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )
    assert captured["path"] == "/v1/chain/tx/placeSpotOrder"

    api.v1.chain_tx.place_spot_market_order_buy_with_price(
        pair=_VALID_PAIR,
        quote_amount=1000,
        base_amount=1,
        slippage=9,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )
    assert captured["path"] == "/v1/chain/tx/placeSpotOrder"

    api.v1.chain_tx.place_spot_market_order_sell_without_price(
        pair=_VALID_PAIR,
        quote_amount=1000,
        base_amount=1,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )
    assert captured["path"] == "/v1/chain/tx/placeSpotOrder"

    api.v1.chain_tx.place_spot_market_order_sell_with_price(
        pair=_VALID_PAIR,
        quote_amount=1000,
        base_amount=1,
        slippage=9,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )
    assert captured["path"] == "/v1/chain/tx/placeSpotOrder"

    api.v1.chain_tx.cancel_spot_order_sell(
        pair=_VALID_PAIR,
        order_id=99,
        subaccount=_VALID_SUBACCOUNT,
        evm_rpc_url="http://rpc",
        private_key="0xpk",
    )
    assert captured["path"] == "/v1/chain/tx/cancelSpotOrder"


def test_api_v1_chain_tx_order_methods_build_pallet_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _make_api()
    captured = _patch_chain_tx_signing_capture(monkeypatch, api)
    request = _patch_request(monkeypatch, api)

    pair_hex = _VALID_PAIR.lower()
    cases = [
        (
            lambda: api.v1.chain_tx.place_perp_order(
                market_id=1,
                is_long=True,
                size=100,
                price=1000,
                order_type=0,
                subaccount=_VALID_SUBACCOUNT,
                evm_rpc_url="http://rpc",
                private_key="0xpk",
                nonce_ms=1_781_757_000_123,
            ),
            "/v1/chain/tx/placePerpOrder",
            "PerpMarket",
            "place_order",
            {
                "params": {
                    "subaccount": _VALID_SUBACCOUNT,
                    "market_id": 1,
                    "is_long": True,
                    "size": 100,
                    "price": 1000,
                    "order_type": {"Limit": "GTC"},
                    "take_profit": None,
                    "stop_loss": None,
                    "reduce_only": False,
                    "post_only": "None",
                    "cloid": None,
                }
            },
        ),
        (
            lambda: api.v1.chain_tx.place_perp_order_market(
                market_id=1,
                is_long=True,
                size=100,
                subaccount=_VALID_SUBACCOUNT,
                evm_rpc_url="http://rpc",
                private_key="0xpk",
                nonce_ms=1_781_757_000_123,
            ),
            "/v1/chain/tx/placePerpOrder",
            "PerpMarket",
            "place_order",
            {
                "params": {
                    "subaccount": _VALID_SUBACCOUNT,
                    "market_id": 1,
                    "is_long": True,
                    "size": 100,
                    "price": 0,
                    "order_type": {"Market": None},
                    "take_profit": None,
                    "stop_loss": None,
                    "reduce_only": False,
                    "post_only": "None",
                    "cloid": None,
                }
            },
        ),
        (
            lambda: api.v1.chain_tx.cancel_perp_order(
                market_id=1,
                order_id=42,
                subaccount=_VALID_SUBACCOUNT,
                evm_rpc_url="http://rpc",
                private_key="0xpk",
                nonce_ms=1_781_757_000_123,
            ),
            "/v1/chain/tx/cancelPerpOrder",
            "PerpMarket",
            "cancel_order",
            {
                "params": {
                    "subaccount": _VALID_SUBACCOUNT,
                    "order_id": 42,
                    "market_id": 1,
                    "cancel_reason": "UserCanceled",
                    "fast_cancel": False,
                }
            },
        ),
        (
            lambda: api.v1.chain_tx.close_position(
                market_id=1,
                price=1000,
                subaccount=_VALID_SUBACCOUNT,
                evm_rpc_url="http://rpc",
                private_key="0xpk",
                nonce=1_781_757_000_123,
            ),
            "/v1/chain/tx/closePosition",
            "PerpMarket",
            "close_position",
            {
                "subaccount": _VALID_SUBACCOUNT,
                "market_id": 1,
                "price": 1000,
                "slippage": None,
            },
        ),
        (
            lambda: api.v1.chain_tx.place_spot_order_buy(
                pair=_VALID_PAIR,
                quote_amount=1000,
                base_amount=1,
                subaccount=_VALID_SUBACCOUNT,
                evm_rpc_url="http://rpc",
                private_key="0xpk",
                nonce_ms=1_781_757_000_123,
            ),
            "/v1/chain/tx/placeSpotOrder",
            "SpotMarket",
            "place_order",
            {
                "params": {
                    "subaccount": _VALID_SUBACCOUNT,
                    "pair": pair_hex,
                    "is_buy": True,
                    "quote_amount": 1000,
                    "base_amount": 1,
                    "order_type": {"Limit": "GTC"},
                    "post_only": "None",
                    "reduce_only": False,
                    "cloid": None,
                }
            },
        ),
        (
            lambda: api.v1.chain_tx.place_spot_market_order_buy_with_price(
                pair=_VALID_PAIR,
                quote_amount=1000,
                base_amount=1,
                slippage=9,
                subaccount=_VALID_SUBACCOUNT,
                evm_rpc_url="http://rpc",
                private_key="0xpk",
                nonce_ms=1_781_757_000_123,
            ),
            "/v1/chain/tx/placeSpotOrder",
            "SpotMarket",
            "place_order",
            {
                "params": {
                    "subaccount": _VALID_SUBACCOUNT,
                    "pair": pair_hex,
                    "is_buy": True,
                    "quote_amount": 1000,
                    "base_amount": 1,
                    "order_type": {"Market": 9},
                    "post_only": "None",
                    "reduce_only": False,
                    "cloid": None,
                }
            },
        ),
        (
            lambda: api.v1.chain_tx.place_spot_market_order_sell_without_price(
                pair=_VALID_PAIR,
                quote_amount=1000,
                base_amount=1,
                auto_cancel=True,
                subaccount=_VALID_SUBACCOUNT,
                evm_rpc_url="http://rpc",
                private_key="0xpk",
                nonce_ms=1_781_757_000_123,
            ),
            "/v1/chain/tx/placeSpotOrder",
            "SpotMarket",
            "place_order",
            {
                "params": {
                    "subaccount": _VALID_SUBACCOUNT,
                    "pair": pair_hex,
                    "is_buy": False,
                    "quote_amount": 1000,
                    "base_amount": 1,
                    "order_type": {"Market": None},
                    "post_only": "None",
                    "reduce_only": False,
                    "cloid": None,
                }
            },
        ),
        (
            lambda: api.v1.chain_tx.cancel_spot_order_sell(
                pair=_VALID_PAIR,
                order_id=99,
                subaccount=_VALID_SUBACCOUNT,
                evm_rpc_url="http://rpc",
                private_key="0xpk",
                nonce_ms=1_781_757_000_123,
            ),
            "/v1/chain/tx/cancelSpotOrder",
            "SpotMarket",
            "cancel_order",
            {
                "params": {
                    "subaccount": _VALID_SUBACCOUNT,
                    "pair": pair_hex,
                    "order_id": 99,
                    "is_buy": False,
                    "cancel_reason": "UserCanceled",
                    "fast_cancel": False,
                }
            },
        ),
    ]

    for call, expected_path, expected_module, expected_function, expected_params in cases:
        captured.clear()
        call()
        assert request["path"] == expected_path
        assert request["json_body"] == {"signedExtrinsic": "0xfake_signed_extrinsic"}
        assert captured == {
            "private_key": "0xpk",
            "call_module": expected_module,
            "call_function": expected_function,
            "call_params": expected_params,
            "nonce_ms": 1_781_757_000_123,
        }

def test_api_v1_chain_spot_market_order_slippage_rejects_node_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _make_api()
    _patch_request(monkeypatch, api)

    with pytest.raises(ValueError, match="spot market slippage"):
        api.v1.chain_tx.place_spot_market_order_buy_with_price(
            pair=_VALID_PAIR,
            quote_amount=1000,
            base_amount=1,
            slippage=100,
            subaccount=_VALID_SUBACCOUNT,
            evm_rpc_url="http://rpc",
            private_key="0xpk",
        )


def test_api_v1_chain_tx_uses_client_private_key_for_pallet_signing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = dx.ApiClient(
        base_url="http://127.0.0.1:8080",
        evm_rpc_url="http://rpc",
        private_key="0xclient_pk",
        subaccount=_VALID_SUBACCOUNT,
        perp_precompile_address="0x" + "aa" * 20,
        spot_precompile_address="0x" + "bb" * 20,
    )
    captured_sign = _patch_chain_tx_signing_capture(monkeypatch, api)
    _ = _patch_request(monkeypatch, api)

    api.v1.chain_tx.place_perp_order(
        market_id=1,
        is_long=True,
        size=100,
        price=1000,
        order_type=0,
        nonce_ms=1234567890,
    )
    assert captured_sign["private_key"] == "0xclient_pk"
    assert captured_sign["call_module"] == "PerpMarket"
    assert captured_sign["call_function"] == "place_order"
    assert captured_sign["call_params"]["params"]["subaccount"] == _VALID_SUBACCOUNT
    assert captured_sign["nonce_ms"] == 1234567890

    api.v1.chain_tx.place_spot_order_buy(
        pair=_VALID_PAIR,
        quote_amount=1000,
        base_amount=1,
    )
    assert captured_sign["private_key"] == "0xclient_pk"
    assert captured_sign["call_module"] == "SpotMarket"
    assert captured_sign["call_function"] == "place_order"
    assert captured_sign["call_params"]["params"]["pair"] == _VALID_PAIR.lower()
    assert captured_sign["nonce_ms"] is None


def test_api_v1_chain_tx_accepts_tx_config(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    captured_sign = _patch_chain_tx_signing_capture(monkeypatch, api)
    _ = _patch_request(monkeypatch, api)

    api.v1.chain_tx.place_spot_order_buy(
        pair=_VALID_PAIR,
        quote_amount=1000,
        base_amount=1,
        subaccount=_VALID_SUBACCOUNT,
        private_key="0xpk",
        tx_config=dx.TxConfig(nonce=111, wait_for_finalized=False),
    )
    assert captured_sign["call_module"] == "SpotMarket"
    assert captured_sign["nonce_ms"] == 111

    api.v1.chain_tx.place_perp_order(
        market_id=1,
        is_long=True,
        size=100,
        price=1000,
        order_type=0,
        subaccount=_VALID_SUBACCOUNT,
        private_key="0xpk",
        nonce_ms=222,
        tx_config=dx.TxConfig(nonce_ms=111),
    )
    assert captured_sign["call_module"] == "PerpMarket"
    assert captured_sign["nonce_ms"] == 222

    api.v1.chain_tx.close_position_market(
        market_id=1,
        subaccount=_VALID_SUBACCOUNT,
        private_key="0xpk",
        tx_config=dx.TxConfig(nonce_ms=333),
    )
    assert captured_sign["call_module"] == "PerpMarket"
    assert captured_sign["call_function"] == "close_position"
    assert captured_sign["nonce_ms"] == 333


def test_api_v1_chain_tx_explicit_private_key_override_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = dx.ApiClient(
        base_url="http://127.0.0.1:8080",
        evm_rpc_url="http://rpc",
        private_key="0xclient_pk",
        subaccount=_VALID_SUBACCOUNT,
        perp_precompile_address="0x" + "aa" * 20,
    )
    captured_sign = _patch_chain_tx_signing_capture(monkeypatch, api)
    _ = _patch_request(monkeypatch, api)

    api.v1.chain_tx.place_perp_order(
        market_id=1,
        is_long=True,
        size=100,
        price=1000,
        order_type=0,
        private_key="0xexplicit_pk",
        precompile_address="0x" + "cc" * 20,
    )

    assert captured_sign["private_key"] == "0xexplicit_pk"
    assert "precompile_address" not in captured_sign


def test_api_v1_account_quota_query_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    captured = _patch_request(monkeypatch, api)

    api.v1.account.wallet_quota(address=_VALID_SUBACCOUNT)
    assert captured["method"] == "GET"
    assert captured["path"] == f"/v1/account/wallets/{_VALID_SUBACCOUNT}/quota"

    api.v1.account.quota_summary(wallet="0xabc")
    assert captured["method"] == "GET"
    assert captured["path"] == "/internal/v1/account/quota/summary"
    assert captured["params"] == {"wallet": "0xabc"}

    api.v1.account.quota_claim(claim_id=42)
    assert captured["method"] == "GET"
    assert captured["path"] == "/internal/v1/account/quota/claim"
    assert captured["params"] == {"id": 42}


def test_api_v1_account_claim_quota_signs_exact_message(monkeypatch: pytest.MonkeyPatch) -> None:
    import uuid as _uuid

    from eth_account import Account
    from eth_account.messages import encode_defunct

    key = "0x" + "11" * 32
    api = dx.ApiClient(base_url="http://127.0.0.1:8080", private_key=key)
    captured = _patch_request(monkeypatch, api)
    expected_wallet = Account.from_key(key).address

    api.v1.account.claim_quota(idempotency_key="018f52bc-0f0b-7cc4-b356-a91f62c72e3f")
    body = captured["json_body"]
    assert captured["method"] == "POST"
    assert captured["path"] == f"/v1/account/wallets/{expected_wallet}/quota/claims"
    assert body["wallet"] == expected_wallet
    assert body["idempotencyKey"] == "018f52bc-0f0b-7cc4-b356-a91f62c72e3f"
    assert body["message"] == (
        "DeepX quota claim\n"
        f"Wallet: {expected_wallet}\n"
        "Idempotency-Key: 018f52bc-0f0b-7cc4-b356-a91f62c72e3f"
    )
    recovered = Account.recover_message(
        encode_defunct(text=body["message"]), signature=body["signature"]
    )
    assert recovered == expected_wallet

    # explicit wallet is preserved verbatim; idempotency key defaults to a uuid
    api.v1.account.claim_quota(wallet="0xAbC")
    body = captured["json_body"]
    assert body["wallet"] == "0xAbC"
    _uuid.UUID(body["idempotencyKey"])
    assert f"Wallet: 0xAbC\n" in body["message"]

    # explicit private_key overrides the client key
    other_key = "0x" + "22" * 32
    api_no_key = _make_api()
    captured_no_key = _patch_request(monkeypatch, api_no_key)
    api_no_key.v1.account.claim_quota(private_key=other_key)
    assert captured_no_key["json_body"]["wallet"] == Account.from_key(other_key).address

    # no key anywhere -> error
    with pytest.raises(ValueError, match="private_key is required"):
        api_no_key.v1.account.claim_quota()


def test_api_v1_account_wait_quota_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api()
    states = iter([
        {"data": {"status": "pending"}},
        {"data": {"status": "submitted"}},
        {"data": {"status": "confirmed", "txHash": "0xabc"}},
    ])
    monkeypatch.setattr(api.v1.account, "quota_claim", lambda *, claim_id: next(states))
    result = api.v1.account.wait_quota_claim(claim_id=1, interval_s=0)
    assert result["data"]["status"] == "confirmed"

    monkeypatch.setattr(
        api.v1.account,
        "quota_claim",
        lambda *, claim_id: {"data": {"status": "failed", "lastError": "boom"}},
    )
    with pytest.raises(RuntimeError, match="boom"):
        api.v1.account.wait_quota_claim(claim_id=1, interval_s=0)

    monkeypatch.setattr(
        api.v1.account, "quota_claim", lambda *, claim_id: {"data": {"status": "pending"}}
    )
    with pytest.raises(TimeoutError, match="not confirmed"):
        api.v1.account.wait_quota_claim(claim_id=1, timeout_s=0, interval_s=0)
