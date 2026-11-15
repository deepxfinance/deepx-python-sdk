from __future__ import annotations

import asyncio
import io
import json
import urllib.error
import urllib.parse

import pytest

from deepx_sdk import ApiClient, AsyncApiClient
from deepx_sdk._errors import APIError
from deepx_sdk.ws_client import (
    parse_ws_message,
    v1_sub_perp_liquidations,
    v1_unsub_perp_liquidations,
)


@pytest.mark.parametrize(
    "method,kwargs,path,query,response",
    [
        ("insurance_fund", {}, "/v1/insurance-fund", {},
         {"balance": "732602.453867", "pendingAssets": [{"asset": "ETH", "qty": "7.026752628223551471"}]}),
        ("insurance_fund_history", {}, "/v1/insurance-fund/history", {}, []),
        ("insurance_fund_history", {"interval": "1M", "limit": 500, "start_time": 0, "end_time": 200},
         "/v1/insurance-fund/history", {"interval": ["1M"], "limit": ["500"], "startTime": ["0"], "endTime": ["200"]},
         [{"time": 100, "balance": "1.000001"}]),
        ("perp.liquidations", {"symbol": "ETH-USDC"}, "/v1/perp/markets/ETH-USDC/liquidations", {},
         {"items": [], "hasNext": False, "nextCursor": None}),
        ("perp.liquidations", {"symbol": "ETH-USDC", "limit": 2, "cursor": "opaque+/==", "start_time": 0, "end_time": 200},
         "/v1/perp/markets/ETH-USDC/liquidations",
         {"limit": ["2"], "cursor": ["opaque+/=="], "startTime": ["0"], "endTime": ["200"]},
         {"items": [{"id": "9007199254740993", "blockNumber": 123}], "hasNext": True, "nextCursor": "next+/=="}),
        ("perp.adl_queue", {"symbol": "ETH-USDC"}, "/v1/perp/markets/ETH-USDC/adl-queue", {},
         {"symbol": "ETH-USDC", "time": 100, "candidates": []}),
        ("perp.adl_queue", {"symbol": "ETH-USDC", "limit": 30}, "/v1/perp/markets/ETH-USDC/adl-queue", {"limit": ["30"]},
         {"candidates": [{"rank": 1, "side": "BUY", "qty": "0.000000000000000001", "adlScore": "12345678901234567890123456789"}]}),
    ],
)
@pytest.mark.parametrize("client_type", [ApiClient, AsyncApiClient])
def test_public_market_http(monkeypatch, method, kwargs, path, query, response, client_type):
    requests = []

    def respond(request, **options):
        requests.append(request)
        return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr("urllib.request.urlopen", respond)
    target = client_type(base_url="https://backend.example").v1
    for name in method.split("."):
        target = getattr(target, name)
    result = target(**kwargs)
    if client_type is AsyncApiClient:
        result = asyncio.run(result)
    assert result == response
    assert len(requests) == 1
    request = requests[0]
    url = urllib.parse.urlsplit(request.full_url)
    assert request.get_method() == "GET"
    assert request.data is None
    assert url.netloc == "backend.example"
    assert url.path == path
    assert urllib.parse.parse_qs(url.query) == query


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("insurance_fund_history", {"interval": "1m"}),
        ("insurance_fund_history", {"interval": ""}),
        ("insurance_fund_history", {"limit": 0}),
        ("insurance_fund_history", {"limit": 501}),
        ("insurance_fund_history", {"start_time": 200, "end_time": 100}),
        ("insurance_fund_history", {"start_time": -1}),
        ("insurance_fund_history", {"end_time": -1}),
        ("perp.liquidations", {"symbol": ""}),
        ("perp.liquidations", {"symbol": "ETH-USDC", "limit": 0}),
        ("perp.liquidations", {"symbol": "ETH-USDC", "limit": 501}),
        ("perp.liquidations", {"symbol": "ETH-USDC", "cursor": 1}),
        ("perp.liquidations", {"symbol": "ETH-USDC", "cursor": " "}),
        ("perp.liquidations", {"symbol": "ETH-USDC", "start_time": 200, "end_time": 100}),
        ("perp.liquidations", {"symbol": "ETH-USDC", "start_time": -1}),
        ("perp.liquidations", {"symbol": "ETH-USDC", "end_time": -1}),
        ("perp.adl_queue", {"symbol": " "}),
        ("perp.adl_queue", {"symbol": "ETH-USDC", "limit": 0}),
        ("perp.adl_queue", {"symbol": "ETH-USDC", "limit": 31}),
    ],
)
def test_invalid_public_market_query_never_sends_request(monkeypatch, method, kwargs):
    api = ApiClient()
    monkeypatch.setattr(api, "request", lambda *a, **kw: pytest.fail("invalid query reached backend"))
    target = api.v1
    for name in method.split("."):
        target = getattr(target, name)
    with pytest.raises(ValueError):
        target(**kwargs)


@pytest.mark.parametrize("method", ["insurance_fund", "insurance_fund_history", "adl_queue", "liquidations"])
def test_snapshot_and_history_errors_remain_visible(monkeypatch, method):
    body = json.dumps({"code": 10012, "msg": "Snapshot unavailable"}).encode()
    error = urllib.error.HTTPError("https://backend.example", 503, "Unavailable", {}, io.BytesIO(body))

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    api = ApiClient(base_url="https://backend.example")
    target = api.v1.perp if method in {"adl_queue", "liquidations"} else api.v1
    kwargs = {"symbol": "ETH-USDC"} if target is api.v1.perp else {}
    with pytest.raises(APIError) as caught:
        getattr(target, method)(**kwargs)
    assert caught.value.status_code == 503
    assert caught.value.code == 10012


def test_liquidation_subscription_and_record_identity():
    for helper, method in [(v1_sub_perp_liquidations, "subscribe"), (v1_unsub_perp_liquidations, "unsubscribe")]:
        assert helper(0, symbol="ETH-USDC") == {
            "method": method, "id": 0,
            "params": {"channel": "perp@liquidations", "symbol": "ETH-USDC"},
        }
    record = {"id": "9007199254740993", "blockNumber": 123, "detail": {"liquidateBaseQty": "0.000001"}}
    payload = {"channel": "perp@liquidations", "data": record}
    message = parse_ws_message(payload)
    assert message.channel == "perp@liquidations"
    assert message.request_id is None
    assert message.data is record
    assert message.raw is payload
