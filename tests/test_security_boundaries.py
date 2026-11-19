from __future__ import annotations

import asyncio
import gzip
import http.client
import io
import json
from pathlib import Path
import runpy
import traceback
from types import SimpleNamespace
import urllib.error

import pytest

import deepx_sdk as dx
from deepx_sdk import _evm, _native_py, ws_client
from deepx_sdk._async_transport import AsyncRpcTransport, _safe_url
from deepx_sdk._rpc_transport import (
    RpcEndpointPool,
    RpcTransportConfig,
    _safe_http_endpoint,
    use_substrate_ws_config,
)
from _public_test_keys import PUBLIC_TEST_ADDRESS, PUBLIC_TEST_PRIVATE_KEY


URL = "https://user-marker:password-marker@node.invalid:8443/path-marker?token=query-marker#fragment-marker"
MARKERS = (
    "user-marker", "password-marker", "path-marker", "query-marker",
    "fragment-marker", "private-key-marker", "header-marker", "api-key-marker",
)


def assert_safe(rendered: str) -> None:
    for marker in MARKERS:
        assert marker not in rendered


def test_client_representations_exclude_nested_credentials() -> None:
    headers = {"Authorization": "Bearer header-marker"}
    api = dx.ApiClient(
        base_url=URL, base_urls=[URL], ws_base_url=URL, substrate_ws=URL,
        evm_rpc_url=URL, evm_rpc_endpoints=[URL], private_key="private-key-marker",
        evm_rpc_headers=headers,
    )
    chain = dx.ChainClient(
        substrate_ws=URL.replace("https:", "wss:"), evm_rpc_url=URL,
        private_key="private-key-marker", api_base_url=URL,
        api_client=SimpleNamespace(token="header-marker"), evm_rpc_headers=headers,
        recovery_substrate_ws_endpoints=[URL.replace("https:", "wss:")],
    )
    bridge = dx.BridgeApi(
        rpc_url=URL, contract_address="0x" + "12" * 20,
        private_key="private-key-marker",
    )
    service = dx.BridgeServiceClient(URL, api_key="api-key-marker")
    clients = [
        api, chain, bridge, service,
        dx.AsyncApiClient(base_url=URL, private_key="private-key-marker"),
        dx.BtcChannelApi(rpc_url=URL, contract_address="0x" + "12" * 20,
                         private_key="private-key-marker"),
        ws_client.WsClient(URL, path="/path-marker", headers=headers),
        RpcTransportConfig(headers=headers),
        dx.SDK(chain=chain, api=api, bridge=bridge, bridge_service=service),
        api.v1, api.v1.chain_tx, chain.perp_market,
    ]
    for client in clients:
        assert_safe(repr(client))
        assert_safe(str(client))
    # Redaction affects display only; requests still have the configured values.
    assert api.private_key == "private-key-marker"
    assert chain.evm_rpc_headers == headers
    assert service.api_key == "api-key-marker"


@pytest.mark.parametrize("sanitize", [_safe_http_endpoint, _safe_url])
@pytest.mark.parametrize("url,expected", [
    (URL, "https://node.invalid:8443"),
    ("wss://name:password-marker@[::1]:9944/path-marker?token=query-marker", "wss://[::1]:9944"),
    ("https://node.invalid/v2/path-marker", "https://node.invalid"),
    ("https://node.invalid:bad/path-marker", "<configured endpoint>"),
    ("https://[invalid/path-marker", "<configured endpoint>"),
    ("path-marker", "<configured endpoint>"),
    ("path-marker://node.invalid", "<configured endpoint>"),
    (r"https://node.invalid\path-marker/status", "<configured endpoint>"),
    ("https:///path-marker", "<configured endpoint>"),
])
def test_endpoint_displays_only_origin(sanitize, url, expected) -> None:
    assert sanitize(url) == expected
    assert_safe(sanitize(url))


def test_public_endpoint_displays_hide_path_credentials() -> None:
    pool = RpcEndpointPool((URL,))
    assert pool.active_display == "https://node.invalid:8443"
    assert pool.display(URL) == "https://node.invalid:8443"
    transport = AsyncRpcTransport(URL.replace("https:", "wss:"))
    assert transport.endpoint == "wss://node.invalid:8443"


@pytest.mark.parametrize("async_client", [False, True])
@pytest.mark.parametrize("http_error", [False, True])
def test_rest_failures_hide_credentials_in_tracebacks(monkeypatch, async_client, http_error) -> None:
    def fail(req, **kwargs):
        if http_error:
            raise urllib.error.HTTPError(req.full_url, 503, "Unavailable", {}, io.BytesIO(b'{}'))
        raise urllib.error.URLError(f"header-marker at {req.full_url}")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    cls = dx.AsyncApiClient if async_client else dx.ApiClient
    api = cls(base_url=URL)
    path = "/path-marker"
    params = {"token": "query-marker"}
    with pytest.raises((dx.RPCError, dx.RESTError)) as caught:
        result = api.request("GET", path, params=params)
        if async_client:
            asyncio.run(result)
    assert_safe("".join(traceback.format_exception(caught.value)))
    assert caught.value.__cause__ is None
    if not http_error:
        assert "GET https://node.invalid:8443: URLError" in str(caught.value)


@pytest.mark.parametrize("http_error", [False, True])
@pytest.mark.parametrize("call", [
    lambda url: _native_py._rpc_call(url, "eth_chainId", []),
    lambda url: _evm.evm_call(url, PUBLIC_TEST_ADDRESS, b""),
])
def test_native_rpc_errors_and_debug_hide_credentials(monkeypatch, capsys, http_error, call) -> None:
    monkeypatch.setenv("DEEPX_SDK_NATIVE_DEBUG", "1")

    def fail(req, **kwargs):
        if http_error:
            raise urllib.error.HTTPError(
                req.full_url, 503, f"header-marker at {req.full_url}", {},
                io.BytesIO(b"query-marker"),
            )
        raise urllib.error.URLError(f"header-marker at {req.full_url}")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(RuntimeError) as caught:
        call(URL)
    assert_safe("".join(traceback.format_exception(caught.value)))
    debug = capsys.readouterr().err
    assert "url=https://node.invalid:8443" in debug
    assert_safe(debug)


@pytest.mark.parametrize("failover", [False, True])
def test_substrate_connection_errors_hide_traceback_credentials(monkeypatch, failover) -> None:
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)

    class FailingSubstrate:
        def __init__(self, **kwargs):
            raise OSError(f"header-marker at {kwargs['url']}")

    url = URL.replace("https:", "wss:")
    pool = RpcEndpointPool((url, url.replace("node.invalid", "backup.invalid"))) if failover else None
    with use_substrate_ws_config(endpoint_pool=pool):
        with pytest.raises(dx.RPCError) as caught:
            _native_py._create_substrate(FailingSubstrate, url)
    assert "OSError" in str(caught.value)
    assert_safe("".join(traceback.format_exception(caught.value)))


def test_native_substrate_debug_hides_endpoint_credentials(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DEEPX_SDK_NATIVE_DEBUG", "1")

    class FailingSubstrate:
        def __init__(self, **kwargs):
            raise OSError("connection failed")

    monkeypatch.setattr(_native_py, "_get_substrate_interface_cls", lambda: FailingSubstrate)
    with pytest.raises(dx.RPCError):
        _native_py._submit_signed_pallet_call(
            substrate_ws=URL.replace("https:", "wss:"), evm_rpc_url=None,
            private_key=PUBLIC_TEST_PRIVATE_KEY, call_module="System",
            call_function="remark", call_params={}, nonce_ms=1,
            use_timestamp_nonce=True, wait_for_finalized=False, timeout_ms=500,
        )
    debug = capsys.readouterr().err
    assert "ws=wss://node.invalid:8443" in debug
    assert_safe(debug)


def test_websocket_connect_failure_hides_credentials(monkeypatch) -> None:
    async def fail(*args, **kwargs):
        raise OSError(f"header-marker at {URL}")

    monkeypatch.setattr(ws_client, "websockets", SimpleNamespace(connect=fail))
    with pytest.raises(dx.RPCError) as caught:
        asyncio.run(ws_client.WsClient(URL).connect())
    assert_safe("".join(traceback.format_exception(caught.value)))


class MessageSocket:
    def __init__(self, message):
        self.message = message

    async def recv(self):
        return self.message


@pytest.mark.parametrize("encoding", ["text", "bytes", "gzip", "gzip-members"])
@pytest.mark.parametrize("oversize", [False, True])
def test_ws_json_limits_apply_to_total_decoded_bytes(encoding, oversize) -> None:
    data = json.dumps({"value": "payload"}).encode()
    limit = len(data) - int(oversize)
    if encoding == "text":
        message = data.decode()
    elif encoding == "bytes":
        message = data
    elif encoding == "gzip":
        message = gzip.compress(data)
    else:
        message = gzip.compress(data[:10]) + gzip.compress(data[10:])
    session = ws_client.WsSession(MessageSocket(message), max_message_size=limit)
    if oversize:
        with pytest.raises(ValueError, match="exceeds max_message_size"):
            asyncio.run(session.recv_json())
    else:
        assert asyncio.run(session.recv_json()) == {"value": "payload"}


def test_ws_gzip_bomb_is_rejected_before_json_decode(monkeypatch) -> None:
    compressed = gzip.compress(b"x" * (4 * ws_client.DEFAULT_MAX_MESSAGE_SIZE))
    assert len(compressed) < 8192

    def unexpected_parse(*args, **kwargs):
        pytest.fail("oversized message reached JSON parser")

    monkeypatch.setattr(ws_client.json, "loads", unexpected_parse)
    with pytest.raises(ValueError, match="exceeds max_message_size"):
        asyncio.run(ws_client.WsSession(MessageSocket(compressed)).recv_json())


def test_ws_message_limit_counts_utf8_bytes() -> None:
    data = '"\u00e9"'
    with pytest.raises(ValueError, match="exceeds max_message_size"):
        asyncio.run(ws_client.WsSession(MessageSocket(data), max_message_size=3).recv_json())


@pytest.mark.parametrize("limit", [0, -1])
def test_ws_rejects_non_positive_limits(limit) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        ws_client.WsClient("wss://node.invalid", max_message_size=limit)
    with pytest.raises(ValueError, match="must be positive"):
        ws_client.WsSession(MessageSocket("{}"), max_message_size=limit)


def test_ws_client_passes_limit_to_transport_and_session(monkeypatch) -> None:
    async def connect(url, **kwargs):
        assert kwargs["max_size"] == 8
        return MessageSocket(gzip.compress(b'"' + b"x" * 10 + b'"'))

    monkeypatch.setattr(ws_client, "websockets", SimpleNamespace(connect=connect))

    async def run():
        session = await ws_client.WsClient("wss://node.invalid", max_message_size=8).connect()
        with pytest.raises(ValueError, match="exceeds max_message_size"):
            await session.recv_json()

    asyncio.run(run())


@pytest.mark.parametrize("script", [
    "test_place_order.py", "test_cancel_order.py", "test_close_position.py",
    "test_spot_place_order.py", "test_spot_cancel_order.py", "test_lending_actions.py",
    "test_subaccount_actions.py", "test_set_profit_and_loss_point.py",
])
def test_transaction_scripts_require_explicit_key_before_client_creation(monkeypatch, script) -> None:
    monkeypatch.delenv("PRIVATE_KEY", raising=False)
    monkeypatch.setenv("EVM_RPC_URL", "https://node.invalid")

    def unexpected_client(*args, **kwargs):
        pytest.fail("transaction script constructed a client without PRIVATE_KEY")

    monkeypatch.setattr(dx, "ChainClient", unexpected_client)
    namespace = runpy.run_path(str(Path(__file__).with_name(script)))
    with pytest.raises(RuntimeError, match="PRIVATE_KEY"):
        namespace["main"]()


@pytest.mark.parametrize("message", [
    b"\x1f\x8bnot-a-gzip-header", gzip.compress(b'{}')[:-1],
])
def test_ws_malformed_gzip_is_not_retried_as_plain_json(message) -> None:
    with pytest.raises((OSError, EOFError)):
        asyncio.run(ws_client.WsSession(MessageSocket(message)).recv_json())


def _http_security_call(kind, endpoint):
    if kind == "rest":
        return dx.ApiClient(base_url=endpoint).request("GET", "/status")
    if kind == "async-rest":
        return asyncio.run(dx.AsyncApiClient(base_url=endpoint).request("GET", "/status"))
    if kind == "rpc":
        return _native_py._rpc_call(endpoint, "eth_chainId", [])
    if kind == "evm":
        return _evm.evm_call(endpoint, PUBLIC_TEST_ADDRESS, b"")
    from deepx_sdk.bridge import fetch_sign_bridge_out_signature

    return fetch_sign_bridge_out_signature(endpoint, {}, dst_chain_id=4835)


@pytest.mark.parametrize("kind", ["rest", "async-rest", "rpc", "evm", "bridge"])
@pytest.mark.parametrize("endpoint", [
    "example.invalid/v2/FAKE_MARKER",
    "https://[invalid/v2/FAKE_MARKER",
    "https://node.invalid:bad/v2/FAKE_MARKER",
    "https:///v2/FAKE_MARKER",
    "ftp://node.invalid/v2/FAKE_MARKER",
])
def test_invalid_http_endpoints_hide_credentials_before_network(monkeypatch, kind, endpoint) -> None:
    def unexpected_network(*args, **kwargs):
        pytest.fail("invalid endpoint reached the network")

    monkeypatch.setattr("urllib.request.urlopen", unexpected_network)
    with pytest.raises((dx.RPCError, dx.RESTError, RuntimeError)) as caught:
        _http_security_call(kind, endpoint)
    assert "FAKE_MARKER" not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("kind", ["rest", "async-rest", "rpc", "evm", "bridge"])
@pytest.mark.parametrize("error_type", [ValueError, http.client.InvalidURL])
def test_http_request_construction_errors_hide_credentials(monkeypatch, kind, error_type) -> None:
    def invalid_request(*args, **kwargs):
        raise error_type("invalid URL containing FAKE_MARKER")

    monkeypatch.setattr("urllib.request.Request", invalid_request)
    with pytest.raises((dx.RPCError, dx.RESTError, RuntimeError)) as caught:
        _http_security_call(kind, URL)
    assert "FAKE_MARKER" not in "".join(traceback.format_exception(caught.value))
    assert_safe("".join(traceback.format_exception(caught.value)))


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("proxy error at https://provider.invalid/v2/FAKE_MARKER"),
    TimeoutError("timeout at https://provider.invalid/v2/FAKE_MARKER"),
    OSError("socket error at https://provider.invalid/v2/FAKE_MARKER"),
])
def test_bridge_transport_errors_hide_underlying_credentials(monkeypatch, failure) -> None:
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(dx.RESTError) as caught:
        _http_security_call("bridge", URL)
    rendered = "".join(traceback.format_exception(caught.value))
    assert "FAKE_MARKER" not in rendered
    assert_safe(rendered)
    assert "POST https://node.invalid:8443" in str(caught.value)
    assert type(failure).__name__ in str(caught.value)
    assert caught.value.status_code is None
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("structured", [False, True])
def test_bridge_http_errors_preserve_api_fields_without_transport_chain(monkeypatch, structured) -> None:
    payload = {
        "message": "Invalid bridge payload", "code": 10001,
        "errorType": "VALIDATION", "details": {"field": "amount"},
    }
    data = json.dumps(payload).encode() if structured else b"upstream unavailable"

    def fail(req, **kwargs):
        raise urllib.error.HTTPError(
            req.full_url, 400, "proxy reason containing FAKE_MARKER", {}, io.BytesIO(data),
        )

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(dx.RESTError) as caught:
        _http_security_call("bridge", URL)
    error = caught.value
    assert error.status_code == 400
    assert "FAKE_MARKER" not in "".join(traceback.format_exception(error))
    assert_safe("".join(traceback.format_exception(error)))
    assert error.__cause__ is None
    if structured:
        assert error.message == payload["message"]
        assert error.code == payload["code"]
        assert error.error_type == payload["errorType"]
        assert error.details == payload["details"]
    else:
        assert "POST https://node.invalid:8443: HTTP 400" in error.message


@pytest.mark.parametrize("kind", ["rest", "async-rest"])
@pytest.mark.parametrize("body", [b"{}", b"upstream path-marker", b'["path-marker"]', None])
def test_rest_http_error_fallback_hides_transport_data(monkeypatch, kind, body) -> None:
    class Unreadable(io.BytesIO):
        def read(self, *args):
            raise OSError("read failure at path-marker")

    def fail(req, **kwargs):
        stream = Unreadable() if body is None else io.BytesIO(body)
        raise urllib.error.HTTPError(req.full_url, 502, "upstream query-marker", {}, stream)

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(dx.RESTError) as caught:
        _http_security_call(kind, URL)
    assert caught.value.status_code == 502
    assert caught.value.message == "REST request failed: GET https://node.invalid:8443: HTTP 502"
    assert_safe("".join(traceback.format_exception(caught.value)))


def test_substrate_invalid_proxy_port_hides_transport_details(monkeypatch) -> None:
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:password-marker")
    with pytest.raises(dx.RPCError) as caught:
        _native_py._create_substrate(lambda **kwargs: pytest.fail("unexpected connection"),
                                     URL.replace("https:", "wss:"))
    assert "wss://node.invalid:8443" in str(caught.value)
    assert_safe("".join(traceback.format_exception(caught.value)))


def test_benchmark_output_and_config_hide_credentials(monkeypatch, capsys) -> None:
    script = runpy.run_path(str(Path(__file__).with_name("benchmark_async_chain.py")))
    monkeypatch.setenv("SUBSTRATE_WS", URL.replace("https:", "wss:"))
    monkeypatch.setenv("PRIVATE_KEY", "private-key-marker")
    config = script["BenchmarkConfig"].from_environment(require_credentials=False)
    assert_safe(repr(config))
    assert script["main"](["--dry-run"]) == 0
    output = capsys.readouterr()
    assert "endpoint: wss://node.invalid:8443" in output.out
    assert_safe(output.out + output.err)


def test_substrate_http_transport_errors_and_structured_responses(monkeypatch) -> None:
    from deepx_sdk._vendor_substrate.transport.http import HttpTransport, requests
    from deepx_sdk._vendor_substrate.exceptions import SubstrateRequestException

    transport = HttpTransport(URL, headers={"Authorization": "header-marker"})
    payload = {"id": 1, "method": "state_getMetadata", "params": []}

    def fail(method, url, **kwargs):
        assert url == URL
        assert kwargs["headers"]["Authorization"] == "header-marker"
        raise requests.ConnectionError(f"proxy failed for {url}")

    monkeypatch.setattr(transport.session, "request", fail)
    with pytest.raises(SubstrateRequestException) as caught:
        transport.rpc_request(payload)
    assert "https://node.invalid:8443" in str(caught.value)
    assert_safe("".join(traceback.format_exception(caught.value)))

    error = {"code": -32602, "message": "Invalid params"}
    response = SimpleNamespace(status_code=200, json=lambda: {"error": error})
    monkeypatch.setattr(transport.session, "request", lambda *a, **kw: response)
    with pytest.raises(SubstrateRequestException) as structured:
        transport.rpc_request(payload)
    assert structured.value.args == (error,)


@pytest.mark.parametrize("stage", ["connect", "reconnect", "retry", "send", "receive", "close"])
def test_substrate_ws_transport_diagnostics(monkeypatch, caplog, stage) -> None:
    from deepx_sdk._vendor_substrate import base
    from deepx_sdk._vendor_substrate.transport import websocket as ws

    url = URL.replace("https:", "wss:")
    attempts = []

    def fail(*args):
        raise OSError(f"socket failure at {url}")

    def closed(*args):
        raise ws.WebSocketConnectionClosedException(f"closed at {url}")

    def connect(endpoint, **options):
        attempts.append(endpoint)
        if stage == "connect" or (stage == "reconnect" and len(attempts) == 2):
            fail()
        if stage in {"reconnect", "retry"}:
            send = closed
        elif stage == "send":
            send = fail
        else:
            def send(_):
                pass
        return SimpleNamespace(send=send, recv=fail, close=fail)

    caplog.set_level("DEBUG", logger=base.logger.name)
    monkeypatch.setattr(ws, "create_connection", connect)
    with pytest.raises(ws.SubstrateRequestException) as caught:
        transport = ws.WebsocketTransport(url=url, debug_fn=base.SubstrateInterface.debug_message)
        if stage == "close":
            transport.close()
        else:
            transport.rpc_request({"id": 1, "method": "system_health", "params": []})
    assert attempts == [url] * (2 if stage in {"reconnect", "retry"} else 1)
    assert "wss://node.invalid:8443" in caplog.text
    assert "wss://node.invalid:8443" in str(caught.value)
    assert_safe(caplog.text + "".join(traceback.format_exception(caught.value)))


def test_substrate_ws_reconnect_delivers_subscription_without_logging_payload(monkeypatch, caplog) -> None:
    from deepx_sdk._vendor_substrate import base
    from deepx_sdk._vendor_substrate.transport import websocket as ws

    url = URL.replace("https:", "wss:")
    request = {"id": 1, "method": "state_subscribeStorage", "params": []}
    subscription = "header-marker"
    update = {"params": {"subscription": subscription, "result": "api-key-marker"}}
    messages = iter([json.dumps({"id": 1, "result": subscription}), json.dumps(update)])
    sent = []

    def closed(_):
        raise ws.WebSocketConnectionClosedException("path-marker")

    sockets = iter([SimpleNamespace(send=closed), SimpleNamespace(send=sent.append, recv=messages.__next__)])
    monkeypatch.setattr(ws, "create_connection", lambda *args, **kwargs: next(sockets))
    caplog.set_level("DEBUG", logger=base.logger.name)
    transport = ws.WebsocketTransport(url=url, debug_fn=base.SubstrateInterface.debug_message)
    result = transport.rpc_request(request, result_handler=lambda msg, number, sub: (msg, number, sub))
    assert result == (update, 0, subscription)
    assert [json.loads(body) for body in sent] == [request]
    assert_safe(caplog.text)
