from __future__ import annotations

import asyncio
import sys
import types

# Avoid hard dependency during package import in test environments.
if "substrateinterface" not in sys.modules:
    substrate_stub = types.ModuleType("substrateinterface")

    class _SubstrateInterfacePlaceholder:
        pass

    substrate_stub.SubstrateInterface = _SubstrateInterfacePlaceholder
    sys.modules["substrateinterface"] = substrate_stub

import deepx_sdk as dx
from deepx_sdk._network import DEFAULT_NET, network_config
import deepx_sdk.api as api_mod
import deepx_sdk.client as chain_mod
import deepx_sdk._evm as evm_mod
import deepx_sdk._native_py as native_py_mod
from deepx_sdk._rpc_transport import (
    RpcEndpointPool,
    use_evm_rpc_config,
    use_substrate_ws_config,
)
import pytest


class _DummyResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        pass


def _lower_headers(req) -> dict[str, str]:
    return {k.lower(): v for k, v in req.header_items()}


def test_api_client_uses_non_urllib_default_user_agent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout: int = 0):
        captured["req"] = req
        captured["timeout"] = timeout
        return _DummyResponse('{"ok": true}')

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)

    client = dx.ApiClient(base_url="https://rest-api-testnet.deepx.fi", timeout=12)
    res = client.request("GET", "/health")

    assert res == {"ok": True}
    assert captured["timeout"] == 12
    headers = _lower_headers(captured["req"])
    assert headers["accept"] == "application/json"
    assert headers["user-agent"].startswith("deepx-python-sdk/")
    assert not headers["user-agent"].startswith("Python-urllib/")


def test_async_api_client_uses_v1_methods(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout: int = 0):
        captured["req"] = req
        captured["timeout"] = timeout
        return _DummyResponse('{"ok": true}')

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)

    async def run() -> object:
        client = dx.AsyncApiClient(base_url="https://rest-api-testnet.deepx.fi", timeout=9)
        return await client.v1.ping()

    assert asyncio.run(run()) == {"ok": True}
    assert captured["timeout"] == 9
    assert captured["req"].full_url == "https://rest-api-testnet.deepx.fi/v1/ping"


def test_api_client_allows_user_agent_override(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout: int = 0):
        captured["req"] = req
        return _DummyResponse('{"ok": true}')

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)

    client = dx.ApiClient(base_url="https://rest-api-testnet.deepx.fi")
    _ = client.request(
        "GET",
        "/health",
        headers={"User-Agent": "Mozilla/5.0 (DeepX SDK test)"},
    )

    headers = _lower_headers(captured["req"])
    assert headers["user-agent"] == "Mozilla/5.0 (DeepX SDK test)"


def test_api_client_http_error_raises_rest_error(monkeypatch) -> None:
    def fake_urlopen(req, timeout: int = 0):
        raise api_mod.urllib.error.HTTPError(
            req.full_url,
            404,
            "Not Found",
            {},
            _DummyResponse(
                '{"code": 3001, "errorType": "MARKET_NOT_FOUND", '
                '"message": "Market not found", "details": {"field": "symbol"}}'
            ),
        )

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)

    client = dx.ApiClient(base_url="https://rest-api-testnet.deepx.fi")
    with pytest.raises(dx.RESTError) as exc_info:
        client.request("GET", "/v1/perp/markets/NOPE-USDC")

    err = exc_info.value
    assert isinstance(err, dx.DeepXSDKError)
    assert err.status_code == 404
    assert err.code == 3001
    assert err.error_type == "MARKET_NOT_FOUND"
    assert err.message == "Market not found"
    assert err.details == {"field": "symbol"}
    assert str(err) == "HTTP 404 MARKET_NOT_FOUND 3001: Market not found"


def test_api_client_http_error_registered_code_raises_api_error(monkeypatch) -> None:
    def fake_urlopen(req, timeout: int = 0):
        raise api_mod.urllib.error.HTTPError(
            req.full_url,
            404,
            "Not Found",
            {},
            _DummyResponse('{"code": 10008, "message": "Order 999 not found."}'),
        )

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)

    client = dx.ApiClient(base_url="https://rest-api-testnet.deepx.fi")
    with pytest.raises(dx.APIError) as exc_info:
        client.request("GET", "/v1/account/subaccounts/0xX/perp/orders/999")

    err = exc_info.value
    assert isinstance(err, dx.RESTError)  # APIError is a RESTError subclass
    assert err.code == 10008
    assert err.category == "NOT_FOUND"
    assert err.status_code == 404
    assert err.message == "Order 999 not found."  # backend message preserved (not registry template)


def test_api_client_http_error_with_plain_body_raises_rest_error(monkeypatch) -> None:
    def fake_urlopen(req, timeout: int = 0):
        raise api_mod.urllib.error.HTTPError(
            req.full_url,
            503,
            "Service Unavailable",
            {},
            _DummyResponse("temporary backend failure"),
        )

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)

    client = dx.ApiClient(base_url="https://rest-api-testnet.deepx.fi")
    with pytest.raises(dx.RESTError) as exc_info:
        client.request("GET", "/v1/perp/markets")

    err = exc_info.value
    assert err.status_code == 503
    assert err.message == "Service Unavailable"
    assert err.code is None
    assert err.error_type is None


def test_api_client_transport_error_raises_rpc_error(monkeypatch) -> None:
    def fake_urlopen(req, timeout: int = 0):
        raise api_mod.urllib.error.URLError("timed out")

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)

    client = dx.ApiClient(base_url="https://rest-api-testnet.deepx.fi")
    with pytest.raises(dx.RPCError, match="REST request failed"):
        client.request("GET", "/v1/perp/markets")


def test_api_client_get_fails_over_but_post_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    def fake_urlopen(req, timeout: int = 0):
        attempts.append(req.full_url)
        if req.full_url.startswith("https://api-a.example"):
            raise api_mod.urllib.error.URLError("primary unavailable")
        return _DummyResponse('{"ok": true}')

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)
    client = dx.ApiClient(
        base_urls=[
            "https://api-a.example",
            "https://api-b.example",
        ]
    )

    assert client.request("GET", "/v1/ping") == {"ok": True}
    assert attempts == [
        "https://api-a.example/v1/ping",
        "https://api-b.example/v1/ping",
    ]
    assert client.active_api_endpoint == "https://api-b.example"

    post_attempts: list[str] = []

    def fail_post(req, timeout: int = 0):
        post_attempts.append(req.full_url)
        raise api_mod.urllib.error.URLError("unknown write outcome")

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fail_post)
    with pytest.raises(dx.RPCError):
        client.request("POST", "/v1/order", json_body={"size": 1})
    assert post_attempts == ["https://api-b.example/v1/order"]


def test_api_client_default_net_and_base_url() -> None:
    client = dx.ApiClient()
    expected = network_config(DEFAULT_NET)
    assert client.net == DEFAULT_NET
    assert client.base_url == expected.api_base_url
    assert client.ws_base_url == expected.ws_base_url
    assert client.evm_rpc_url == expected.evm_rpc_url
    assert client.substrate_ws == expected.substrate_ws
    assert not hasattr(client, "internal_v1")
    assert not hasattr(client, "v2")
    assert not hasattr(client, "v3")


@pytest.mark.parametrize("net", ["testnet"])
def test_api_client_base_url_resolved_by_net(net: str) -> None:
    client = dx.ApiClient(net=net)
    assert client.net == net
    assert client.base_url == network_config(net).api_base_url


@pytest.mark.parametrize("net", ["testnet"])
def test_api_client_ws_base_url_resolved_by_net(net: str) -> None:
    client = dx.ApiClient(net=net)
    assert client.net == net
    assert client.ws_base_url == network_config(net).ws_base_url


def test_api_client_custom_base_url_overrides_net_mapping() -> None:
    client = dx.ApiClient(net="testnet", base_url="http://127.0.0.1:8080")
    assert client.net == "testnet"
    assert client.base_url == "http://127.0.0.1:8080"


def test_api_client_custom_transport_urls_and_request_edges(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout: int = 0):
        captured["req"] = req
        captured["timeout"] = timeout
        return _DummyResponse("")

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)

    client = dx.ApiClient(
        net="testnet",
        base_url=" http://api.example.test ",
        ws_base_url=" ws://ws.example.test ",
        substrate_ws=" ws://node.example.test ",
        evm_rpc_url=" http://rpc.example.test ",
    )

    assert client.ws_base_url == "ws://ws.example.test"
    assert client.substrate_ws == "ws://node.example.test"
    assert client.evm_rpc_url == "http://rpc.example.test"
    assert (
        client._make_url("/v1/path with space", {"a": ["x", "y"]})
        == "http://api.example.test/v1/path%20with%20space?a=x&a=y"
    )
    assert (
        client.request(
            "POST",
            "/v1/empty",
            json_body={"x": 1},
            headers={"X-Test": "yes"},
        )
        is None
    )

    headers = _lower_headers(captured["req"])
    assert headers["x-test"] == "yes"
    assert captured["req"].data == b'{"x": 1}'

    monkeypatch.setattr(
        api_mod.urllib.request,
        "urlopen",
        lambda req, timeout=0: _DummyResponse("plain text"),
    )
    assert client.request("GET", "/v1/plain") == "plain text"

    def json_list_error(req, timeout: int = 0):
        raise api_mod.urllib.error.HTTPError(
            req.full_url,
            500,
            "Internal Error",
            {},
            _DummyResponse('["not", "object"]'),
        )

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", json_list_error)
    with pytest.raises(dx.RESTError) as exc_info:
        client.request("GET", "/v1/error")
    assert exc_info.value.status_code == 500
    assert exc_info.value.message == '["not", "object"]'


def test_api_client_invalid_net_raises() -> None:
    with pytest.raises(ValueError, match="net must be one of"):
        _ = dx.ApiClient(net="staging")


def test_api_client_mainnet_raises_until_deployed() -> None:
    with pytest.raises(ValueError, match="not deployed yet"):
        _ = dx.ApiClient(net="mainnet")


def test_chain_client_default_net_and_urls() -> None:
    client = dx.ChainClient(
        private_key="0x" + "11" * 32,
        subaccount="0x" + "33" * 20,
    )
    expected = network_config(DEFAULT_NET)
    assert client.net == DEFAULT_NET
    assert client.evm_rpc_url == expected.evm_rpc_url
    assert client.substrate_ws == expected.substrate_ws
    assert client.perp_precompile_address == ""
    assert client.spot_precompile_address == ""
    assert client.lending_precompile_address == ""
    assert client.subaccount_precompile_address == ""
    assert client.system_precompile_address == ""


@pytest.mark.parametrize("net", ["testnet"])
def test_chain_client_urls_resolved_by_net(net: str) -> None:
    client = dx.ChainClient(
        net=net,
        private_key="0x" + "11" * 32,
        subaccount="0x" + "33" * 20,
    )
    assert client.net == net
    assert client.evm_rpc_url == network_config(net).evm_rpc_url
    assert client.substrate_ws == network_config(net).substrate_ws


def test_chain_client_custom_urls_override_net_mapping() -> None:
    client = dx.ChainClient(
        net="testnet",
        evm_rpc_url="http://127.0.0.1:8545",
        substrate_ws="ws://127.0.0.1:9944",
        private_key="0x" + "11" * 32,
        subaccount="0x" + "33" * 20,
    )
    assert client.net == "testnet"
    assert client.evm_rpc_url == "http://127.0.0.1:8545"
    assert client.substrate_ws == "ws://127.0.0.1:9944"


def test_chain_client_custom_evm_rpc_transport_is_scoped_to_native_rpc_calls(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout: float = 0):
        captured["req"] = req
        captured["timeout"] = timeout
        return _DummyResponse('{"jsonrpc":"2.0","result":"0x1","id":1}')

    monkeypatch.setattr(native_py_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        chain_mod,
        "place_perp_order_limit",
        lambda **kwargs: native_py_mod._rpc_call(kwargs["evm_rpc_url"], "eth_chainId", []),
    )

    client = dx.ChainClient(
        evm_rpc_url="https://rpc-testnet.deepx.fi",
        substrate_ws="wss://rpc-testnet.deepx.fi",
        private_key="0x" + "11" * 32,
        subaccount="0x" + "33" * 20,
        evm_rpc_user_agent="Mozilla/5.0 (DeepX SDK RPC test)",
        evm_rpc_headers={"X-DeepX-Test": "rpc-header"},
        evm_rpc_timeout=7.5,
    )

    assert (
        client.perp_market.place_perp_order_limit(
            market_id=1,
            is_long=True,
            size=1,
            price=1,
        )
        == "0x1"
    )
    headers = _lower_headers(captured["req"])
    assert headers["user-agent"] == "Mozilla/5.0 (DeepX SDK RPC test)"
    assert headers["x-deepx-test"] == "rpc-header"
    assert captured["timeout"] == 7.5


def test_chain_client_custom_evm_rpc_transport_is_scoped_to_evm_calls(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout: float = 0):
        captured["req"] = req
        captured["timeout"] = timeout
        return _DummyResponse('{"jsonrpc":"2.0","result":"0x","id":1}')

    monkeypatch.setattr(evm_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        chain_mod,
        "mark_price_for",
        lambda evm_rpc_url, precompile_address, market_id: evm_mod.evm_call(
            evm_rpc_url, precompile_address, b""
        ),
    )

    client = dx.ChainClient(
        evm_rpc_url="https://rpc-testnet.deepx.fi",
        substrate_ws="wss://rpc-testnet.deepx.fi",
        private_key="0x" + "11" * 32,
        subaccount="0x" + "33" * 20,
        evm_rpc_user_agent="DeepXCustomUA/1.0",
        evm_rpc_headers={"Authorization": "Bearer test-token"},
        evm_rpc_timeout=3,
    )

    assert client.perp_market.mark_price_for(market_id=1) == b""
    headers = _lower_headers(captured["req"])
    assert headers["user-agent"] == "DeepXCustomUA/1.0"
    assert headers["authorization"] == "Bearer test-token"
    assert captured["timeout"] == 3


def test_chain_client_evm_read_fails_over_to_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    def fake_urlopen(req, timeout: float = 0):
        attempts.append(req.full_url)
        if req.full_url == "https://evm-a.example":
            raise evm_mod.urllib.error.URLError("primary unavailable")
        return _DummyResponse('{"jsonrpc":"2.0","result":"0x","id":1}')

    monkeypatch.setattr(evm_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        chain_mod,
        "mark_price_for",
        lambda evm_rpc_url, precompile_address, market_id: evm_mod.evm_call(
            evm_rpc_url, precompile_address, b""
        ),
    )
    client = dx.ChainClient(
        evm_rpc_endpoints=[
            "https://evm-a.example",
            "https://evm-b.example",
        ],
        private_key="0x" + "11" * 32,
        subaccount="0x" + "33" * 20,
    )

    assert client.perp_market.mark_price_for(market_id=1) == b""
    assert attempts == [
        "https://evm-a.example",
        "https://evm-b.example",
    ]
    assert client.active_evm_rpc_endpoint == "https://evm-b.example"


def test_native_evm_rpc_preparation_uses_endpoint_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    def fake_urlopen(req, timeout: float = 0):
        attempts.append(req.full_url)
        if req.full_url == "https://evm-a.example":
            raise native_py_mod.urllib.error.URLError("primary unavailable")
        return _DummyResponse('{"jsonrpc":"2.0","result":"0x1","id":1}')

    monkeypatch.setattr(native_py_mod.urllib.request, "urlopen", fake_urlopen)
    pool = RpcEndpointPool(
        ("https://evm-a.example", "https://evm-b.example")
    )

    with use_evm_rpc_config(endpoint_pool=pool):
        assert native_py_mod._rpc_get_chain_id("https://evm-a.example") == 1

    assert attempts == [
        "https://evm-a.example",
        "https://evm-b.example",
    ]
    assert pool.active_display == "https://evm-b.example"


def test_chain_client_sync_no_op_fails_over_before_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted: list[str] = []

    class FakeSubstrate:
        def __init__(self, *, url: str, ws_options=None) -> None:
            attempted.append(url)
            if url == "wss://substrate-a.example":
                raise ConnectionError("handshake failed with HTTP 502")
            self.url = url
            self.config: dict[str, object] = {}

    def fake_no_op(**kwargs):
        substrate = native_py_mod._create_substrate(
            FakeSubstrate,
            kwargs["substrate_ws"],
            timeout_ms=500,
        )
        return types.SimpleNamespace(tx_hash="0xtx", endpoint=substrate.url)

    monkeypatch.setattr(chain_mod, "no_op", fake_no_op)

    client = dx.ChainClient(
        substrate_ws_endpoints=[
            "wss://substrate-a.example",
            "wss://substrate-b.example",
        ],
        private_key="0xprivate",
    )
    result = client.subaccount_client.no_op(wait_for_finalized=False)

    assert attempted == [
        "wss://substrate-a.example",
        "wss://substrate-b.example",
    ]
    assert result.endpoint == "wss://substrate-b.example"
    assert client.active_rpc_endpoint == "wss://substrate-b.example"


def test_substrate_ws_failover_reports_all_connection_errors() -> None:
    class FailingSubstrate:
        def __init__(self, *, url: str, ws_options=None) -> None:
            raise ConnectionError(f"cannot connect to {url}")

    pool = RpcEndpointPool(
        (
            "wss://user:secret@substrate-a.example/rpc?token=secret",
            "wss://substrate-b.example/rpc",
        )
    )

    with use_substrate_ws_config(endpoint_pool=pool):
        with pytest.raises(dx.RPCError) as exc_info:
            native_py_mod._create_substrate(
                FailingSubstrate,
                pool.active,
                timeout_ms=500,
            )

    message = str(exc_info.value)
    assert "2 attempted" in message
    assert "substrate-a.example/rpc" in message
    assert "substrate-b.example/rpc" in message
    assert "user" not in message
    assert "secret" not in message


def test_chain_client_invalid_net_raises() -> None:
    with pytest.raises(ValueError, match="net must be one of"):
        _ = dx.ChainClient(
            net="staging",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "33" * 20,
        )


def test_chain_client_mainnet_raises_until_deployed() -> None:
    with pytest.raises(ValueError, match="not deployed yet"):
        _ = dx.ChainClient(
            net="mainnet",
            private_key="0x" + "11" * 32,
            subaccount="0x" + "33" * 20,
        )


def test_chain_client_routes_module_calls_to_matching_precompile(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_perp(**kwargs):
        captured["perp"] = kwargs["precompile_address"]
        return "perp"

    def fake_spot(**kwargs):
        captured["spot"] = kwargs["precompile_address"]
        return "spot"

    def fake_subaccount(**kwargs):
        captured["subaccount"] = kwargs["precompile_address"]
        return "subaccount"

    def fake_lending(**kwargs):
        captured["lending"] = kwargs["precompile_address"]
        return "lending"

    def fake_system(**kwargs):
        captured["system"] = kwargs["precompile_address"]
        return "system"

    monkeypatch.setattr(chain_mod, "place_perp_order_limit", fake_perp)
    monkeypatch.setattr(chain_mod, "place_perp_order_ioc", fake_perp)
    monkeypatch.setattr(chain_mod, "subaccount_place_order_buy_b", fake_spot)
    monkeypatch.setattr(chain_mod, "subaccount_place_order_buy_ioc_b", fake_spot)
    monkeypatch.setattr(chain_mod, "subaccount_place_order_sell_ioc_b", fake_spot)
    monkeypatch.setattr(chain_mod, "initialize_subaccount", fake_subaccount)
    monkeypatch.setattr(chain_mod, "deposit", fake_lending)
    monkeypatch.setattr(chain_mod, "system_account", fake_system)

    client = dx.ChainClient(
        private_key="0x" + "11" * 32,
        subaccount="0x" + "33" * 20,
        perp_precompile_address="0x" + "aa" * 20,
        spot_precompile_address="0x" + "bb" * 20,
        lending_precompile_address="0x" + "cc" * 20,
        subaccount_precompile_address="0x" + "dd" * 20,
        system_precompile_address="0x" + "ee" * 20,
    )

    assert client.perp_market.place_perp_order_limit(
        market_id=1,
        is_long=True,
        size=1,
        price=1,
    ) == "perp"
    assert client.perp_market.place_perp_order_ioc(
        market_id=1,
        is_long=True,
        size=1,
        price=1,
    ) == "perp"
    assert client.spot_market.subaccount_place_order_buy_b(
        pair="0x" + "11" * 32,
        quote_amount=1,
        base_amount=1,
    ) == "spot"
    assert client.spot_market.subaccount_place_order_buy_ioc_b(
        pair="0x" + "11" * 32,
        quote_amount=1,
        base_amount=1,
    ) == "spot"
    assert client.spot_market.subaccount_place_order_sell_ioc_b(
        pair="0x" + "11" * 32,
        quote_amount=1,
        base_amount=1,
    ) == "spot"
    assert client.subaccount_client.initialize_subaccount(name="test") == "subaccount"
    assert client.lending.deposit(
        subaccount="0x" + "33" * 20,
        asset="USDC",
        amount=1,
    ) == "lending"
    assert client.system.system_account(address="0x" + "44" * 20) == "system"

    assert captured == {
        "perp": "0x" + "aa" * 20,
        "spot": "0x" + "bb" * 20,
        "subaccount": "0x" + "dd" * 20,
        "lending": "0x" + "cc" * 20,
        "system": "0x" + "ee" * 20,
    }
