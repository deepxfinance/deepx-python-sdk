from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from decimal import Decimal

import pytest

from deepx_sdk import ApiClient, AsyncApiClient
from deepx_sdk._errors import APIError


@pytest.mark.parametrize(
    "invoke,path,params",
    [
        (lambda a: a.v1.stats(), "/v1/stats", {}),
        (lambda a: a.v1.prices(), "/v1/prices", {}),
        (lambda a: a.v1.prices(asset="USDC"), "/v1/prices", {"asset": ["USDC"]}),
        (lambda a: a.v1.perp.prices(), "/v1/perp/prices", {}),
        (lambda a: a.v1.perp.prices(symbol="ETH-USDC"), "/v1/perp/markets/ETH-USDC/prices", {}),
        (lambda a: a.v1.account.subaccount_bad_debts(address="0xabc"), "/v1/account/subaccounts/0xabc/bad-debts", {}),
        (
            lambda a: a.v1.account.subaccount_bankruptcy_records(
                address="0xabc", limit=2, cursor="47", start_time=100, end_time=200,
            ),
            "/v1/account/subaccounts/0xabc/bankruptcy-records",
            {"limit": ["2"], "cursor": ["47"], "startTime": ["100"], "endTime": ["200"]},
        ),
        (lambda a: a.v1.chain_tx.status(tx_hash="0xhash"), "/v1/chain/tx/status", {"txHash": ["0xhash"]}),
    ],
)
def test_new_queries_use_configured_backend(monkeypatch, invoke, path, params):
    response = {"amount": "51771594.151432504926754701225"}
    requests = []

    def respond(request, **kwargs):
        requests.append(request)
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr("urllib.request.urlopen", respond)
    api = ApiClient(base_url="https://internal.example")
    assert invoke(api) == response
    assert len(requests) == 1
    request = requests[0]
    url = urllib.parse.urlsplit(request.full_url)
    assert request.get_method() == "GET"
    assert request.data is None
    assert url.netloc == "internal.example"
    assert url.path == path
    assert urllib.parse.parse_qs(url.query) == params
    assert Decimal(response["amount"]) == Decimal("51771594.151432504926754701225")


@pytest.mark.parametrize("status,code", [(503, 10012), (429, 10010)])
def test_stats_preserves_backend_error(monkeypatch, status, code):
    body = json.dumps({"code": code, "msg": "Unavailable", "param": None}).encode()
    error = urllib.error.HTTPError("https://test.example/v1/stats", status, "Unavailable", {}, io.BytesIO(body))
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(APIError) as caught:
        ApiClient(base_url="https://test.example").v1.stats()
    assert caught.value.status_code == status
    assert caught.value.code == code


def test_async_stats_preserves_snapshot_and_precision(monkeypatch):
    import asyncio

    snapshot = {"time": 1791968227764, "totalValueLocked": "44233900.144706"}
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(snapshot).encode()))
    api = AsyncApiClient(base_url="https://test.example")
    assert asyncio.run(api.v1.stats()) == snapshot


@pytest.mark.parametrize(
    "method,kwargs,removed",
    [
        ("subaccount_perp_positions_history", {"sort": "DESC"}, "sort"),
        ("subaccount_perp_orders", {"from_id": 1}, "from_id"),
        ("subaccount_perp_orders", {"sort": "ASC"}, "sort"),
        ("subaccount_perp_trades", {"from_id": 1}, "from_id"),
        ("subaccount_spot_orders", {"sort": "DESC"}, "sort"),
        ("subaccount_spot_trades", {"from_id": 1}, "from_id"),
        ("subaccount_balance_changes", {"from_id": 1}, "from_id"),
        ("subaccount_perp_funding_payments", {"cursor": "token"}, "cursor"),
        ("subaccount_perp_funding_payments", {"from_id": 1}, "from_id"),
    ],
)
def test_removed_account_parameters_fail_before_request(monkeypatch, method, kwargs, removed):
    api = ApiClient()
    monkeypatch.setattr(api, "request", lambda *args, **kwargs: pytest.fail("unsupported query reached backend"))
    with pytest.raises(ValueError, match=f"{removed} is no longer supported"):
        getattr(api.v1.account, method)(address="0xabc", **kwargs)


@pytest.mark.parametrize(
    "invoke,removed",
    [
        (lambda a: a.v1.spot.trades(symbol="ETH-USDC", from_id=1), "from_id"),
        (lambda a: a.v1.perp.trades(symbol="ETH-USDC", from_id=1), "from_id"),
        (lambda a: a.v1.perp.open_interest_history(symbol="ETH-USDC", interval="1m", sort="DESC"), "sort"),
        (lambda a: a.v1.lending.market_status_history(asset="USDC", time_frame="1m"), "time_frame"),
        (lambda a: a.v1.lending.market_status_history(asset="USDC", market_id=1), "market_id"),
        (lambda a: a.v1.lending.market_status_history(asset="USDC", sort="ASC"), "sort"),
    ],
)
def test_removed_market_parameters_fail_before_request(monkeypatch, invoke, removed):
    api = ApiClient()
    monkeypatch.setattr(api, "request", lambda *args, **kwargs: pytest.fail("unsupported query reached backend"))
    with pytest.raises(ValueError, match=f"{removed} is no longer supported"):
        invoke(api)


@pytest.mark.parametrize("cursor", [1, True, "", "  "])
def test_numeric_and_empty_cursors_fail_before_request(monkeypatch, cursor):
    api = ApiClient()
    monkeypatch.setattr(api, "request", lambda *args, **kwargs: pytest.fail("invalid cursor reached backend"))
    with pytest.raises(ValueError, match="cursor must be a non-empty string"):
        api.v1.account.subaccount_perp_orders(address="0xabc", cursor=cursor)


def test_opaque_cursor_and_liquidation_filter_are_forwarded_unchanged(monkeypatch):
    api = ApiClient()
    calls = []
    token = "eyJpZCI6MX0+/=="
    monkeypatch.setattr(api, "request", lambda method, path, **kwargs: calls.append((path, kwargs["params"])))
    api.v1.spot.trades(symbol="ETH-USDC", cursor=token)
    api.v1.account.subaccount_liquidations(address="0xabc", cursor=token, liquidation_type="LIQUIDATE_PERP")
    assert calls[0][1] == {"cursor": token}
    assert calls[1][1] == {"cursor": token, "liqType": "LIQUIDATE_PERP"}


@pytest.mark.parametrize(
    "invoke",
    [
        lambda a: a.v1.prices(asset=""),
        lambda a: a.v1.perp.prices(symbol=""),
        lambda a: a.v1.account.subaccount_bad_debts(address=""),
        lambda a: a.v1.account.subaccount_bankruptcy_records(address=""),
        lambda a: a.v1.account.subaccount_bankruptcy_records(address="0xabc", limit=501),
        lambda a: a.v1.account.subaccount_bankruptcy_records(address="0xabc", cursor=1),
        lambda a: a.v1.account.subaccount_bankruptcy_records(address="0xabc", start_time=200, end_time=100),
        lambda a: a.v1.chain_tx.status(tx_hash=""),
    ],
)
def test_new_queries_validate_before_request(monkeypatch, invoke):
    api = ApiClient()
    monkeypatch.setattr(api, "request", lambda *args, **kwargs: pytest.fail("invalid query reached backend"))
    with pytest.raises(ValueError):
        invoke(api)
