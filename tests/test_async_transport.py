from __future__ import annotations

import asyncio
import gc
import json
import logging
from typing import Any

import pytest

import deepx_sdk._async_transport as transport_module
from deepx_sdk._async_transport import AsyncRpcTransport, ConnectionState
from deepx_sdk._errors import RPCError


class FakeSocket:
    def __init__(
        self,
        *,
        send_gate: asyncio.Event | None = None,
        send_error: BaseException | None = None,
        fail_on_send: int | None = None,
        close_error: BaseException | None = None,
        reader_cancelled: asyncio.Event | None = None,
        reader_release: asyncio.Event | None = None,
    ) -> None:
        self.inbound: asyncio.Queue[str | BaseException] = asyncio.Queue()
        self.sent: list[dict[str, object]] = []
        self.close_calls = 0
        self.send_started = asyncio.Event()
        self._send_gate = send_gate
        self._send_error = send_error
        self._fail_on_send = fail_on_send
        self._close_error = close_error
        self._reader_cancelled = reader_cancelled
        self._reader_release = reader_release
        self._sent_condition = asyncio.Condition()
        self._received_count = 0
        self._received_condition = asyncio.Condition()

    async def send(self, message: str) -> None:
        self.send_started.set()
        if self._send_gate is not None:
            await self._send_gate.wait()
        if self._send_error is not None and (
            self._fail_on_send is None
            or len(self.sent) + 1 == self._fail_on_send
        ):
            raise self._send_error
        async with self._sent_condition:
            self.sent.append(json.loads(message))
            self._sent_condition.notify_all()

    async def recv(self) -> str:
        try:
            message = await self.inbound.get()
        except asyncio.CancelledError:
            if self._reader_cancelled is not None:
                self._reader_cancelled.set()
            if self._reader_release is not None:
                await self._reader_release.wait()
            raise
        async with self._received_condition:
            self._received_count += 1
            self._received_condition.notify_all()
        if isinstance(message, BaseException):
            raise message
        return message

    async def close(self) -> None:
        self.close_calls += 1
        if self._close_error is not None:
            raise self._close_error

    async def receive(self, message: dict[str, Any]) -> None:
        await self.inbound.put(json.dumps(message))

    async def receive_raw(self, message: str) -> None:
        await self.inbound.put(message)

    async def disconnect(self, error: BaseException) -> None:
        await self.inbound.put(error)

    async def wait_for_sent(self, count: int) -> None:
        async with self._sent_condition:
            await self._sent_condition.wait_for(lambda: len(self.sent) >= count)

    async def wait_for_received(self, count: int) -> None:
        async with self._received_condition:
            await self._received_condition.wait_for(
                lambda: self._received_count >= count
            )


@pytest.fixture(autouse=True)
def clear_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_transport_routes_out_of_order_responses() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()

        first = asyncio.create_task(transport.request("first", [1]))
        second = asyncio.create_task(transport.request("second", [2]))
        await socket.wait_for_sent(2)
        ids = {message["method"]: message["id"] for message in socket.sent}

        await socket.receive(
            {"jsonrpc": "2.0", "id": ids["second"], "result": "B"}
        )
        await socket.receive(
            {"jsonrpc": "2.0", "id": ids["first"], "result": "A"}
        )

        assert await first == "A"
        assert await second == "B"
        await transport.close()

    asyncio.run(run())


def test_initial_connect_fails_over_to_next_endpoint() -> None:
    async def run() -> None:
        backup = FakeSocket()
        attempts: list[str] = []

        async def connect_factory(url: str, **_kwargs: object) -> FakeSocket:
            attempts.append(url)
            if url == "ws://primary.test":
                raise OSError("primary unavailable")
            return backup

        transport = AsyncRpcTransport(
            ["ws://primary.test", "ws://backup.test"],
            connect_factory=connect_factory,
        )
        await transport.connect()

        assert attempts == ["ws://primary.test", "ws://backup.test"]
        assert transport.endpoint == "ws://backup.test"
        assert transport.state is ConnectionState.CONNECTED
        await transport.close()

    asyncio.run(run())


def test_disconnect_rotates_to_backup_endpoint() -> None:
    async def run() -> None:
        primary = FakeSocket()
        backup = FakeSocket()
        attempts: list[str] = []

        async def connect_factory(url: str, **_kwargs: object) -> FakeSocket:
            attempts.append(url)
            return primary if url == "ws://primary.test" else backup

        transport = AsyncRpcTransport(
            ["ws://primary.test", "ws://backup.test"],
            connect_factory=connect_factory,
            auto_reconnect=True,
            reconnect_initial_ms=0,
            reconnect_max_ms=0,
            reconnect_jitter=lambda delay: delay,
        )
        await transport.connect()
        await primary.disconnect(ConnectionError("primary lost"))

        for _ in range(100):
            if (
                transport.state is ConnectionState.CONNECTED
                and transport.endpoint == "ws://backup.test"
            ):
                break
            await asyncio.sleep(0)

        assert attempts == ["ws://primary.test", "ws://backup.test"]
        assert transport.endpoint == "ws://backup.test"
        assert transport.state is ConnectionState.CONNECTED
        await transport.close()

    asyncio.run(run())


def test_subscription_drains_notification_that_arrives_before_registration() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        updates: list[object] = []
        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()

        subscription = asyncio.create_task(
            transport.subscribe("author_submitAndWatchExtrinsic", ["0x01"], updates.append)
        )
        await socket.wait_for_sent(1)
        request_id = socket.sent[0]["id"]
        await socket.receive(
            {"jsonrpc": "2.0", "id": request_id, "result": "subscription-1"}
        )
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "author_extrinsicUpdate",
                "params": {
                    "subscription": "subscription-1",
                    "result": "ready",
                },
            }
        )

        assert await subscription == "subscription-1"
        assert updates == ["ready"]
        await transport.close()

    asyncio.run(run())


def test_subscription_replays_notification_before_subscribe_response() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        updates: list[object] = []
        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()

        subscription = asyncio.create_task(
            transport.subscribe("subscribe", [], updates.append)
        )
        await socket.wait_for_sent(1)
        request_id = socket.sent[0]["id"]
        # The node streams a notification BEFORE the subscribe response
        # registers the route; it must be buffered and replayed.
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {
                    "subscription": "subscription-early",
                    "result": "early-update",
                },
            }
        )
        await socket.receive(
            {"jsonrpc": "2.0", "id": request_id, "result": "subscription-early"}
        )

        assert await subscription == "subscription-early"
        for _ in range(100):
            if updates:
                break
            await asyncio.sleep(0)
        assert updates == ["early-update"]
        await transport.close()

    asyncio.run(run())


def test_subscriptions_receive_only_their_own_notifications() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        first_updates: list[object] = []
        second_updates: list[object] = []
        first_received = asyncio.Event()
        second_received = asyncio.Event()

        def handle_first(update: object) -> None:
            first_updates.append(update)
            first_received.set()

        async def handle_second(update: object) -> None:
            second_updates.append(update)
            second_received.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        first = asyncio.create_task(
            transport.subscribe("subscribe_first", [], handle_first)
        )
        second = asyncio.create_task(
            transport.subscribe("subscribe_second", [], handle_second)
        )
        await socket.wait_for_sent(2)
        ids = {message["method"]: message["id"] for message in socket.sent}
        await socket.receive(
            {"jsonrpc": "2.0", "id": ids["subscribe_first"], "result": "sub-a"}
        )
        await socket.receive(
            {"jsonrpc": "2.0", "id": ids["subscribe_second"], "result": "sub-b"}
        )
        assert await first == "sub-a"
        assert await second == "sub-b"

        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "sub-b", "result": "B"},
            }
        )
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "sub-a", "result": "A"},
            }
        )
        await asyncio.gather(first_received.wait(), second_received.wait())

        assert first_updates == ["A"]
        assert second_updates == ["B"]
        await transport.close()

    asyncio.run(run())


def test_rpc_error_includes_method_code_and_message() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        request = asyncio.create_task(transport.request("state_getStorage", ["0x01"]))
        await socket.wait_for_sent(1)
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "id": socket.sent[0]["id"],
                "error": {"code": -32602, "message": "Invalid params"},
            }
        )

        with pytest.raises(RPCError) as caught:
            await request
        rendered = str(caught.value)
        assert "state_getStorage" in rendered
        assert "-32602" in rendered
        assert "Invalid params" in rendered
        await transport.close()

    asyncio.run(run())


def test_reader_disconnect_fails_a_request_waiting_to_send_with_context() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        request_registered = asyncio.Event()

        class SignallingRequests(dict[int, asyncio.Future[object]]):
            def __setitem__(
                self,
                key: int,
                value: asyncio.Future[object],
            ) -> None:
                super().__setitem__(key, value)
                request_registered.set()

        transport = AsyncRpcTransport(
            "ws://node.test/rpc",
            connect_factory=connect_factory,
        )
        await transport.connect()
        transport._requests = SignallingRequests()
        await transport._send_lock.acquire()
        request = asyncio.create_task(transport.request("pending_method", []))
        await request_registered.wait()

        await socket.disconnect(ConnectionError("node stopped"))
        assert transport._reader_task is not None
        await transport._reader_task
        transport._send_lock.release()

        with pytest.raises(RPCError) as caught:
            await request
        rendered = str(caught.value)
        assert "ws://node.test/rpc" in rendered
        assert "disconnected" in rendered.lower()
        assert not socket.sent
        assert not transport._requests
        assert transport.state is ConnectionState.DISCONNECTED
        await transport.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("url", "environment_name", "proxy_url", "expected_port"),
    [
        (
            "ws://node.test/rpc",
            "http_proxy",
            "http://alice:secret@ws-proxy.test:8080",
            80,
        ),
        (
            "wss://secure-node.test/rpc",
            "https_proxy",
            "socks5://bob:secret@wss-proxy.test:1080",
            443,
        ),
    ],
)
def test_proxy_environment_creates_tunnel_for_matching_websocket_scheme(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    environment_name: str,
    proxy_url: str,
    expected_port: int,
) -> None:
    async def run() -> None:
        monkeypatch.setenv(environment_name, proxy_url)
        socket = FakeSocket()
        tunnel_socket = object()
        captured: dict[str, object] = {}

        class FakeProxy:
            @classmethod
            def from_url(cls, configured_url: str) -> FakeProxy:
                captured["proxy_url"] = configured_url
                return cls()

            async def connect(self, **kwargs: object) -> object:
                captured["destination"] = kwargs
                return tunnel_socket

        async def connect_factory(
            connected_url: str, **kwargs: object
        ) -> FakeSocket:
            captured["url"] = connected_url
            captured["connect_kwargs"] = kwargs
            return socket

        monkeypatch.setattr(transport_module, "Proxy", FakeProxy, raising=False)
        transport = AsyncRpcTransport(url, connect_factory=connect_factory)
        await transport.connect()

        assert captured["proxy_url"] == proxy_url
        assert captured["destination"] == {
            "dest_host": "secure-node.test" if url.startswith("wss:") else "node.test",
            "dest_port": expected_port,
        }
        assert captured["connect_kwargs"] == {"sock": tunnel_socket}
        await transport.close()

    asyncio.run(run())


def test_proxy_connection_error_redacts_authentication(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        proxy_url = "http://proxy-user:proxy-password@proxy.test:8080"
        monkeypatch.setenv("http_proxy", proxy_url)

        class FailingProxy:
            @classmethod
            def from_url(cls, _configured_url: str) -> FailingProxy:
                return cls()

            async def connect(self, **_kwargs: object) -> object:
                raise RuntimeError(f"proxy tunnel failed: {proxy_url}")

        async def unused_connect_factory(
            _url: str, **_kwargs: object
        ) -> FakeSocket:
            raise AssertionError("WebSocket connect must not run without a tunnel")

        monkeypatch.setattr(
            transport_module,
            "Proxy",
            FailingProxy,
            raising=False,
        )
        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=unused_connect_factory,
        )
        with caplog.at_level(logging.ERROR, logger="deepx_sdk._async_transport"):
            with pytest.raises(RPCError) as caught:
                await transport.connect()

        combined = f"{caught.value}\n{caplog.text}"
        assert "proxy.test:8080" in combined
        assert "proxy-user" not in combined
        assert "proxy-password" not in combined

    asyncio.run(run())


def test_headers_retry_with_legacy_keyword_only_for_keyword_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        socket = FakeSocket()
        attempts: list[dict[str, object]] = []

        async def connect_factory(
            _url: str, **kwargs: object
        ) -> FakeSocket:
            attempts.append(kwargs)
            if "additional_headers" in kwargs:
                raise TypeError(
                    "connect() got an unexpected keyword argument 'additional_headers'"
                )
            return socket

        transport = AsyncRpcTransport(
            "wss://node.test",
            headers={"Authorization": "Bearer token"},
            connect_factory=connect_factory,
        )
        await transport.connect()

        assert attempts == [
            {"additional_headers": {"Authorization": "Bearer token"}},
            {"extra_headers": {"Authorization": "Bearer token"}},
        ]
        await transport.close()

    asyncio.run(run())


def test_unrelated_connect_type_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        attempts = 0

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            nonlocal attempts
            attempts += 1
            raise TypeError("invalid endpoint configuration")

        transport = AsyncRpcTransport(
            "wss://node.test",
            headers={"X-Test": "yes"},
            connect_factory=connect_factory,
        )
        with pytest.raises(RPCError, match="TypeError"):
            await transport.connect()
        assert attempts == 1

    asyncio.run(run())


def test_missing_websockets_names_websockets_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(transport_module, "websockets", None, raising=False)
        transport = AsyncRpcTransport("ws://node.test")

        with pytest.raises(RPCError, match=r"pip install websockets"):
            await transport.connect()

    asyncio.run(run())


def test_missing_python_socks_names_python_socks_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setenv("https_proxy", "http://proxy.test:8080")
        monkeypatch.setattr(transport_module, "Proxy", None, raising=False)

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            raise AssertionError("WebSocket connect must not run without python-socks")

        transport = AsyncRpcTransport(
            "wss://node.test",
            connect_factory=connect_factory,
        )
        with pytest.raises(RPCError, match=r"pip install python-socks"):
            await transport.connect()

    asyncio.run(run())


def test_unsubscribe_removes_the_registered_handler() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribe = asyncio.create_task(
            transport.subscribe("subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sub-1"}
        )
        assert await subscribe == "sub-1"

        unsubscribe = asyncio.create_task(transport.unsubscribe("unsubscribe", "sub-1"))
        await socket.wait_for_sent(2)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[1]["id"], "result": True}
        )
        assert await unsubscribe is None
        assert "sub-1" not in transport._subscriptions
        await transport.close()

    asyncio.run(run())


def test_close_is_idempotent_and_fails_all_unresolved_requests() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        request = asyncio.create_task(transport.request("still_pending", []))
        await socket.wait_for_sent(1)

        await transport.close()
        await transport.close()

        with pytest.raises(RPCError, match="closed"):
            await request
        assert socket.close_calls == 1
        assert transport._reader_task is None
        assert not transport._requests
        assert not transport._subscriptions
        assert not transport._early_notifications
        assert transport.state is ConnectionState.CLOSED

    asyncio.run(run())


def test_connection_lifecycle_rejects_calls_outside_connected_state() -> None:
    async def run() -> None:
        socket = FakeSocket()
        connect_calls = 0

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            nonlocal connect_calls
            connect_calls += 1
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        with pytest.raises(RPCError, match="not connected"):
            await transport.request("before_connect", [])

        await transport.connect()
        await transport.connect()
        assert connect_calls == 1

        await transport.close()
        with pytest.raises(RPCError, match="closed"):
            await transport.connect()

    asyncio.run(run())


def test_cancelling_request_removes_its_pending_future() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        request = asyncio.create_task(transport.request("cancelled_method", []))
        await socket.wait_for_sent(1)
        request.cancel()

        with pytest.raises(asyncio.CancelledError):
            await request
        assert not transport._requests
        await transport.close()

    asyncio.run(run())


def test_send_failure_has_transport_context_and_cleans_pending_future() -> None:
    async def run() -> None:
        socket = FakeSocket(send_error=BrokenPipeError("wire-secret"))

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test/rpc",
            connect_factory=connect_factory,
        )
        await transport.connect()

        with pytest.raises(RPCError) as caught:
            await transport.request("failed_method", [])
        rendered = str(caught.value)
        assert "failed_method" in rendered
        assert "ws://node.test/rpc" in rendered
        assert "BrokenPipeError" in rendered
        assert "wire-secret" not in rendered
        assert not transport._requests
        await transport.close()

    asyncio.run(run())


def test_subscribe_rejects_missing_subscription_id() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribe = asyncio.create_task(
            transport.subscribe("subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": None}
        )

        with pytest.raises(RPCError, match="no subscription ID"):
            await subscribe
        await transport.close()

    asyncio.run(run())


def test_subscribe_rejects_registration_after_transport_disconnects() -> None:
    async def run() -> None:
        class DisconnectingTransport(AsyncRpcTransport):
            async def _request(
                self,
                method: str,
                params: list[object],
                **_kwargs: object,
            ) -> object:
                self._state = ConnectionState.DISCONNECTED
                return "sub-after-disconnect"

        transport = DisconnectingTransport("ws://node.test")
        transport._state = ConnectionState.CONNECTED

        with pytest.raises(RPCError, match="cannot be registered"):
            await transport.subscribe("subscribe", [], lambda _update: None)

    asyncio.run(run())


def test_default_websockets_connector_is_used_when_no_factory_is_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        socket = FakeSocket()
        captured: dict[str, object] = {}

        async def connect(url: str, **kwargs: object) -> FakeSocket:
            captured["url"] = url
            captured["kwargs"] = kwargs
            return socket

        monkeypatch.setattr(
            transport_module,
            "websockets",
            type("FakeWebsockets", (), {"connect": staticmethod(connect)}),
        )
        transport = AsyncRpcTransport("wss://node.test/rpc")
        await transport.connect()

        assert captured == {"url": "wss://node.test/rpc", "kwargs": {}}
        await transport.close()

    asyncio.run(run())


def test_non_websocket_scheme_does_not_resolve_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setenv("http_proxy", "http://proxy.test:8080")
        monkeypatch.setenv("https_proxy", "http://proxy.test:8080")
        socket = FakeSocket()
        captured: dict[str, object] = {}

        async def connect_factory(_url: str, **kwargs: object) -> FakeSocket:
            captured.update(kwargs)
            return socket

        transport = AsyncRpcTransport(
            "http://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()

        assert captured == {}
        await transport.close()

    asyncio.run(run())


def test_proxy_rejects_websocket_endpoint_without_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setenv("http_proxy", "http://proxy.test:8080")
        transport = AsyncRpcTransport(
            "ws:///rpc",
            connect_factory=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        )

        with pytest.raises(RPCError) as caught:
            await transport.connect()
        assert "no hostname" in str(caught.value)
        assert "<configured endpoint>" in str(caught.value)

    asyncio.run(run())


def test_proxy_error_safely_renders_endpoint_with_invalid_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setenv(
            "http_proxy",
            "http://proxy-user:proxy-password@proxy.test:8080",
        )

        class FakeProxy:
            @classmethod
            def from_url(cls, _configured_url: str) -> FakeProxy:
                return cls()

            async def connect(self, **_kwargs: object) -> object:
                return object()

        monkeypatch.setattr(transport_module, "Proxy", FakeProxy)
        transport = AsyncRpcTransport(
            "ws://node.test:invalid/rpc",
            connect_factory=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        )
        with pytest.raises(RPCError) as caught:
            await transport.connect()

        rendered = str(caught.value)
        assert "<configured endpoint>" in rendered
        assert "proxy.test:8080" in rendered
        assert "proxy-user" not in rendered
        assert "proxy-password" not in rendered

    asyncio.run(run())


def test_valid_unknown_and_late_response_ids_are_ignored() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        request = asyncio.create_task(transport.request("valid_method", []))
        await socket.wait_for_sent(1)
        request_id = socket.sent[0]["id"]

        await socket.receive({"jsonrpc": "2.0", "id": 999, "result": "unknown"})
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "unrelated_notification",
                "params": {"result": "ignored"},
            }
        )
        await socket.receive(
            {"jsonrpc": "2.0", "id": request_id, "result": "expected"}
        )

        assert await request == "expected"
        await socket.receive(
            {"jsonrpc": "2.0", "id": request_id, "result": "late duplicate"}
        )
        await socket.wait_for_received(4)
        assert transport.state is ConnectionState.CONNECTED
        await transport.close()

    asyncio.run(run())


def test_non_mapping_rpc_error_disconnects_with_protocol_context() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        request = asyncio.create_task(transport.request("broken_method", []))
        await socket.wait_for_sent(1)
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "id": socket.sent[0]["id"],
                "error": "broken response",
            }
        )

        with pytest.raises(RPCError) as caught:
            await request
        rendered = str(caught.value)
        assert "broken_method" in rendered
        assert "request id 1" in rendered
        assert "protocol error" in rendered
        assert transport.state is ConnectionState.DISCONNECTED
        await transport.close()

    asyncio.run(run())


def test_handler_failure_is_logged_without_secret_and_reader_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        socket = FakeSocket()
        handler_invoked = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        def broken_handler(_update: object) -> None:
            handler_invoked.set()
            raise ValueError("proxy-user:proxy-password")

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribe = asyncio.create_task(
            transport.subscribe("subscribe", [], broken_handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sub-1"}
        )
        assert await subscribe == "sub-1"

        with caplog.at_level(logging.ERROR, logger="deepx_sdk._async_transport"):
            await socket.receive(
                {
                    "jsonrpc": "2.0",
                    "method": "subscription",
                    "params": {"subscription": "sub-1", "result": "update"},
                }
            )
            await handler_invoked.wait()

        request = asyncio.create_task(transport.request("after_handler_error", []))
        await socket.wait_for_sent(2)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[1]["id"], "result": "alive"}
        )
        assert await request == "alive"
        assert "ValueError" in caplog.text
        assert "proxy-user" not in caplog.text
        assert "proxy-password" not in caplog.text
        await transport.close()

    asyncio.run(run())


def test_close_cancels_active_async_notification_handler() -> None:
    async def run() -> None:
        socket = FakeSocket()
        handler_started = asyncio.Event()
        handler_cancelled = asyncio.Event()
        never_finishes = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        async def handler(_update: object) -> None:
            handler_started.set()
            try:
                await never_finishes.wait()
            finally:
                handler_cancelled.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribe = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sub-1"}
        )
        assert await subscribe == "sub-1"
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "sub-1", "result": "update"},
            }
        )
        await handler_started.wait()

        await transport.close()

        await handler_cancelled.wait()
        assert not transport._subscriptions

    asyncio.run(run())


def test_close_failure_is_wrapped_with_safe_transport_context() -> None:
    async def run() -> None:
        socket = FakeSocket(close_error=OSError("close-secret"))

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "wss://node.test/rpc",
            connect_factory=connect_factory,
        )
        await transport.connect()

        with pytest.raises(RPCError) as caught:
            await transport.close()
        rendered = str(caught.value)
        assert "wss://node.test/rpc" in rendered
        assert "OSError" in rendered
        assert "close-secret" not in rendered
        assert transport.state is ConnectionState.CLOSED

    asyncio.run(run())


def test_concurrent_connect_callers_share_one_attempt() -> None:
    async def run() -> None:
        socket = FakeSocket()
        factory_entered = asyncio.Event()
        release_factory = asyncio.Event()
        connect_calls = 0

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            nonlocal connect_calls
            connect_calls += 1
            factory_entered.set()
            await release_factory.wait()
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        first = asyncio.create_task(transport.connect())
        await factory_entered.wait()
        second = asyncio.create_task(transport.connect())
        second_had_a_turn = asyncio.Event()
        asyncio.get_running_loop().call_soon(second_had_a_turn.set)
        await second_had_a_turn.wait()

        assert connect_calls == 1
        release_factory.set()
        await asyncio.gather(first, second)
        assert transport.state is ConnectionState.CONNECTED
        await transport.close()

    asyncio.run(run())


def test_close_wins_against_late_connect_without_resurrecting_transport() -> None:
    async def run() -> None:
        socket = FakeSocket()
        factory_entered = asyncio.Event()
        release_factory = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            factory_entered.set()
            try:
                await release_factory.wait()
            except asyncio.CancelledError:
                await release_factory.wait()
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        connecting = asyncio.create_task(transport.connect())
        await factory_entered.wait()
        closing = asyncio.create_task(transport.close())
        close_had_a_turn = asyncio.Event()
        asyncio.get_running_loop().call_soon(close_had_a_turn.set)
        await close_had_a_turn.wait()
        release_factory.set()

        await closing
        with pytest.raises(RPCError, match="closed"):
            await connecting
        assert transport.state is ConnectionState.CLOSED
        assert transport._socket is None
        assert transport._reader_task is None
        assert socket.close_calls == 1

    asyncio.run(run())


def test_cancelling_only_connect_attempt_restores_disconnected_state() -> None:
    async def run() -> None:
        factory_entered = asyncio.Event()
        never_connects = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            factory_entered.set()
            await never_connects.wait()
            raise AssertionError("unreachable")

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        connecting = asyncio.create_task(transport.connect())
        await factory_entered.wait()
        connecting.cancel()

        with pytest.raises(asyncio.CancelledError):
            await connecting
        assert transport.state is ConnectionState.DISCONNECTED
        assert transport._socket is None
        assert transport._reader_task is None

    asyncio.run(run())


def test_cancelled_close_caller_does_not_cancel_shared_cleanup() -> None:
    async def run() -> None:
        reader_cancelled = asyncio.Event()
        release_reader = asyncio.Event()
        socket = FakeSocket(
            reader_cancelled=reader_cancelled,
            reader_release=release_reader,
        )

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        pending = asyncio.create_task(transport.request("pending_during_close", []))
        await socket.wait_for_sent(1)

        first_close = asyncio.create_task(transport.close())
        await reader_cancelled.wait()
        first_close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_close

        second_close = asyncio.create_task(transport.close())
        second_had_a_turn = asyncio.Event()
        asyncio.get_running_loop().call_soon(second_had_a_turn.set)
        await second_had_a_turn.wait()
        assert not second_close.done()

        release_reader.set()
        await second_close
        with pytest.raises(RPCError) as caught:
            await pending
        rendered = str(caught.value)
        assert "pending_during_close" in rendered
        assert "request id 1" in rendered
        assert transport.state is ConnectionState.CLOSED
        assert transport._socket is None
        assert transport._reader_task is None
        assert not transport._requests
        assert socket.close_calls == 1

    asyncio.run(run())


def test_second_send_failure_disconnects_and_fails_all_pending_requests() -> None:
    async def run() -> None:
        socket = FakeSocket(
            send_error=BrokenPipeError("wire-secret"),
            fail_on_send=2,
        )

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test/rpc",
            connect_factory=connect_factory,
        )
        await transport.connect()
        first = asyncio.create_task(transport.request("first_pending", []))
        await socket.wait_for_sent(1)
        second = asyncio.create_task(transport.request("second_breaks", []))

        with pytest.raises(RPCError) as second_error:
            await second
        assert first.done(), "send failure must fail requests already awaiting responses"
        with pytest.raises(RPCError) as first_error:
            await first

        assert "second_breaks" in str(second_error.value)
        assert "request id 2" in str(second_error.value)
        assert "BrokenPipeError" in str(second_error.value)
        assert "first_pending" in str(first_error.value)
        assert "request id 1" in str(first_error.value)
        assert transport.state is ConnectionState.DISCONNECTED
        assert transport._socket is None
        assert transport._reader_task is None
        assert not transport._requests
        assert socket.close_calls == 1
        await transport.close()

    asyncio.run(run())


def test_reconnect_closes_socket_left_by_reader_disconnect() -> None:
    async def run() -> None:
        first_socket = FakeSocket()
        second_socket = FakeSocket()
        sockets = [first_socket, second_socket]

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return sockets.pop(0)

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        await first_socket.disconnect(ConnectionError("node stopped"))
        assert transport._reader_task is not None
        await transport._reader_task
        assert transport.state is ConnectionState.DISCONNECTED

        await transport.connect()

        assert first_socket.close_calls == 1
        assert transport._socket is second_socket
        assert transport.state is ConnectionState.CONNECTED
        await transport.close()
        assert second_socket.close_calls == 1

    asyncio.run(run())


def test_early_and_live_notifications_are_serialized_in_arrival_order() -> None:
    async def run() -> None:
        socket = FakeSocket()
        early_started = asyncio.Event()
        release_early = asyncio.Event()
        live_handled = asyncio.Event()
        sequence: list[str] = []

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        async def handler(update: object) -> None:
            if update == "early":
                sequence.append("early-start")
                early_started.set()
                await release_early.wait()
                sequence.append("early-end")
            else:
                sequence.append(str(update))
                live_handled.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sub-1"}
        )
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "sub-1", "result": "early"},
            }
        )
        await early_started.wait()
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "sub-1", "result": "live"},
            }
        )
        await socket.wait_for_received(3)

        assert sequence == ["early-start"]
        release_early.set()
        await live_handled.wait()
        assert await subscribing == "sub-1"
        assert sequence == ["early-start", "early-end", "live"]
        await transport.close()

    asyncio.run(run())


def test_slow_subscription_does_not_block_another_subscription_worker() -> None:
    async def run() -> None:
        socket = FakeSocket()
        slow_started = asyncio.Event()
        release_slow = asyncio.Event()
        fast_handled = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        async def slow_handler(_update: object) -> None:
            slow_started.set()
            await release_slow.wait()

        async def fast_handler(_update: object) -> None:
            fast_handled.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        slow_subscribe = asyncio.create_task(
            transport.subscribe("slow_subscribe", [], slow_handler)
        )
        fast_subscribe = asyncio.create_task(
            transport.subscribe("fast_subscribe", [], fast_handler)
        )
        await socket.wait_for_sent(2)
        ids = {message["method"]: message["id"] for message in socket.sent}
        await socket.receive(
            {"jsonrpc": "2.0", "id": ids["slow_subscribe"], "result": "slow"}
        )
        await socket.receive(
            {"jsonrpc": "2.0", "id": ids["fast_subscribe"], "result": "fast"}
        )
        assert await slow_subscribe == "slow"
        assert await fast_subscribe == "fast"

        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "slow", "result": "blocked"},
            }
        )
        await slow_started.wait()
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "fast", "result": "independent"},
            }
        )
        await fast_handled.wait()

        release_slow.set()
        await transport.close()

    asyncio.run(run())


def test_successful_unsubscribe_cancels_and_joins_subscription_worker() -> None:
    async def run() -> None:
        socket = FakeSocket()
        handler_started = asyncio.Event()
        handler_cancelled = asyncio.Event()
        never_finishes = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        async def handler(_update: object) -> None:
            handler_started.set()
            try:
                await never_finishes.wait()
            finally:
                handler_cancelled.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sub-1"}
        )
        assert await subscribing == "sub-1"
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "sub-1", "result": "blocked"},
            }
        )
        await handler_started.wait()

        unsubscribing = asyncio.create_task(
            transport.unsubscribe("unsubscribe", "sub-1")
        )
        await socket.wait_for_sent(2)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[1]["id"], "result": True}
        )
        await unsubscribing

        assert handler_cancelled.is_set()
        assert "sub-1" not in transport._subscriptions
        await transport.close()

    asyncio.run(run())


def test_close_stops_early_notification_worker_and_finishes_subscribe() -> None:
    async def run() -> None:
        socket = FakeSocket()
        handler_started = asyncio.Event()
        handler_cancelled = asyncio.Event()
        never_finishes = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        async def handler(_update: object) -> None:
            handler_started.set()
            try:
                await never_finishes.wait()
            finally:
                handler_cancelled.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sub-1"}
        )
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "sub-1", "result": "early"},
            }
        )
        await handler_started.wait()

        await transport.close()

        assert handler_cancelled.is_set()
        assert subscribing.done()
        assert await subscribing == "sub-1"
        assert not transport._subscriptions

    asyncio.run(run())


def test_unknown_notification_without_pending_subscribe_is_discarded() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "unknown", "result": "ignored"},
            }
        )
        await socket.wait_for_received(1)

        assert not transport._early_notifications
        await transport.close()

    asyncio.run(run())


def test_pending_subscribe_race_buffer_has_small_total_bound() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        for index in range(40):
            await socket.receive(
                {
                    "jsonrpc": "2.0",
                    "method": "subscription",
                    "params": {
                        "subscription": f"unknown-{index}",
                        "result": index,
                    },
                }
            )
        await socket.wait_for_received(40)

        buffered = sum(
            len(messages) for messages in transport._early_notifications.values()
        )
        assert buffered <= 32
        subscribing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await subscribing
        await transport.close()

    asyncio.run(run())


def test_failed_subscribe_clears_race_notifications() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "race-id", "result": "early"},
            }
        )
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "id": socket.sent[0]["id"],
                "error": {"code": -1, "message": "subscribe failed"},
            }
        )

        with pytest.raises(RPCError):
            await subscribing
        assert not transport._early_notifications
        await transport.close()

    asyncio.run(run())


def test_cancelled_subscribe_clears_race_notifications() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "race-id", "result": "early"},
            }
        )
        await socket.wait_for_received(1)
        subscribing.cancel()

        with pytest.raises(asyncio.CancelledError):
            await subscribing
        assert not transport._early_notifications
        await transport.close()

    asyncio.run(run())


def test_late_notification_after_unsubscribe_is_not_buffered_during_new_race() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        first_subscribe = asyncio.create_task(
            transport.subscribe("first_subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "old-sub"}
        )
        assert await first_subscribe == "old-sub"
        unsubscribe = asyncio.create_task(
            transport.unsubscribe("unsubscribe", "old-sub")
        )
        await socket.wait_for_sent(2)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[1]["id"], "result": True}
        )
        await unsubscribe

        new_subscribe = asyncio.create_task(
            transport.subscribe("new_subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(3)
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "old-sub", "result": "late"},
            }
        )
        await socket.wait_for_received(3)

        assert "old-sub" not in transport._early_notifications
        new_subscribe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await new_subscribe
        await transport.close()

    asyncio.run(run())


@pytest.mark.parametrize("unsubscribe_result", [False, None, "yes", 1])
def test_unsubscribe_requires_exact_true_and_retains_handler(
    unsubscribe_result: object,
) -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribe = asyncio.create_task(
            transport.subscribe("subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sub-1"}
        )
        assert await subscribe == "sub-1"

        unsubscribe = asyncio.create_task(
            transport.unsubscribe("unsubscribe_method", "sub-1")
        )
        await socket.wait_for_sent(2)
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "id": socket.sent[1]["id"],
                "result": unsubscribe_result,
            }
        )
        with pytest.raises(RPCError) as caught:
            await unsubscribe

        rendered = str(caught.value)
        assert "unsubscribe_method" in rendered
        assert "sub-1" in rendered
        assert "sub-1" in transport._subscriptions
        await transport.close()

    asyncio.run(run())


def test_unsubscribe_rpc_error_retains_handler() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribe = asyncio.create_task(
            transport.subscribe("subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sub-1"}
        )
        assert await subscribe == "sub-1"
        unsubscribe = asyncio.create_task(
            transport.unsubscribe("unsubscribe", "sub-1")
        )
        await socket.wait_for_sent(2)
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "id": socket.sent[1]["id"],
                "error": {"code": -1, "message": "node refused"},
            }
        )

        with pytest.raises(RPCError):
            await unsubscribe
        assert "sub-1" in transport._subscriptions
        await transport.close()

    asyncio.run(run())


def test_unsubscribe_disconnect_retains_handler() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribe = asyncio.create_task(
            transport.subscribe("subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sub-1"}
        )
        assert await subscribe == "sub-1"
        unsubscribe = asyncio.create_task(
            transport.unsubscribe("unsubscribe", "sub-1")
        )
        await socket.wait_for_sent(2)
        await socket.disconnect(ConnectionError("node stopped"))

        with pytest.raises(RPCError):
            await unsubscribe
        assert "sub-1" in transport._subscriptions
        await transport.close()

    asyncio.run(run())


def test_unsubscribe_cancellation_retains_handler() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribe = asyncio.create_task(
            transport.subscribe("subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sub-1"}
        )
        assert await subscribe == "sub-1"
        unsubscribe = asyncio.create_task(
            transport.unsubscribe("unsubscribe", "sub-1")
        )
        await socket.wait_for_sent(2)
        unsubscribe.cancel()

        with pytest.raises(asyncio.CancelledError):
            await unsubscribe
        assert "sub-1" in transport._subscriptions
        await transport.close()

    asyncio.run(run())


def test_header_fallback_does_not_retry_internal_keyword_type_error() -> None:
    async def run() -> None:
        socket = FakeSocket()
        attempts = 0

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TypeError("additional_headers contains an invalid keyword")
            return socket

        transport = AsyncRpcTransport(
            "wss://node.test",
            headers={"X-Test": "yes"},
            connect_factory=connect_factory,
        )
        with pytest.raises(RPCError, match="TypeError"):
            await transport.connect()
        assert attempts == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "build_message",
    [
        lambda request_id: {
            "jsonrpc": "2.0",
            "id": True,
            "result": "spoofed",
        },
        lambda request_id: {"id": request_id, "result": "missing version"},
        lambda request_id: {
            "jsonrpc": "1.0",
            "id": request_id,
            "result": "wrong version",
        },
        lambda request_id: {"jsonrpc": "2.0", "id": request_id},
        lambda request_id: {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": "ambiguous",
            "error": {"code": -1, "message": "also error"},
        },
        lambda request_id: {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {},
        },
        lambda request_id: {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": True, "message": "boolean code"},
        },
        lambda request_id: {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -1},
        },
    ],
    ids=[
        "boolean-id",
        "missing-version",
        "wrong-version",
        "missing-result-and-error",
        "both-result-and-error",
        "missing-error-fields",
        "boolean-error-code",
        "missing-error-message",
    ],
)
def test_malformed_response_disconnects_with_context(
    build_message: Any,
) -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test/rpc",
            connect_factory=connect_factory,
        )
        await transport.connect()
        request = asyncio.create_task(transport.request("validated_method", []))
        await socket.wait_for_sent(1)
        await socket.receive(build_message(socket.sent[0]["id"]))

        with pytest.raises(RPCError) as caught:
            await request
        rendered = str(caught.value)
        assert "validated_method" in rendered
        assert "request id 1" in rendered
        assert "protocol error" in rendered
        assert "ws://node.test/rpc" in rendered
        assert transport.state is ConnectionState.DISCONNECTED
        assert socket.close_calls == 1
        await transport.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "raw_message",
    [
        "{not valid json",
        "[]",
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": "not an object",
            }
        ),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "params": {},
            }
        ),
    ],
    ids=[
        "invalid-json",
        "non-object-top-level",
        "malformed-notification",
        "missing-notification-method",
    ],
)
def test_malformed_frame_uses_same_protocol_disconnect_policy(
    raw_message: str,
) -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test/rpc",
            connect_factory=connect_factory,
        )
        await transport.connect()
        request = asyncio.create_task(transport.request("pending_for_frame", []))
        await socket.wait_for_sent(1)
        await socket.receive_raw(raw_message)
        await socket.wait_for_received(1)
        request_had_a_turn = asyncio.Event()
        asyncio.get_running_loop().call_soon(request_had_a_turn.set)
        await request_had_a_turn.wait()

        assert request.done(), "malformed frame must fail pending requests immediately"
        with pytest.raises(RPCError) as caught:
            await request
        rendered = str(caught.value)
        assert "pending_for_frame" in rendered
        assert "request id 1" in rendered
        assert "protocol error" in rendered
        assert transport.state is ConnectionState.DISCONNECTED
        assert socket.close_calls == 1
        await transport.close()

    asyncio.run(run())


def test_close_cancels_blocked_connector_and_connect_reports_closed() -> None:
    async def run() -> None:
        factory_entered = asyncio.Event()
        never_connects = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            factory_entered.set()
            await never_connects.wait()
            raise AssertionError("unreachable")

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        connecting = asyncio.create_task(transport.connect())
        await factory_entered.wait()

        await transport.close()

        with pytest.raises(RPCError, match="closed"):
            await connecting
        assert transport.state is ConnectionState.CLOSED
        assert transport._connect_task is None
        assert transport._socket is None

    asyncio.run(run())


def test_reconnect_retries_socket_retirement_after_close_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        class FailsTwiceOnCloseSocket(FakeSocket):
            async def close(self) -> None:
                self.close_calls += 1
                if self.close_calls <= 2:
                    raise OSError("close-secret")

        first_socket = FailsTwiceOnCloseSocket()
        second_socket = FakeSocket()
        sockets = [first_socket, second_socket]

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return sockets.pop(0)

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        with caplog.at_level(logging.ERROR, logger="deepx_sdk._async_transport"):
            await first_socket.disconnect(ConnectionError("node stopped"))
            reader_task = transport._reader_task
            assert reader_task is not None
            await reader_task

        assert transport.state is ConnectionState.DISCONNECTED
        assert transport._socket is first_socket
        with pytest.raises(RPCError, match="retire disconnected"):
            await transport.connect()
        assert transport._socket is first_socket

        await transport.connect()

        assert first_socket.close_calls == 3
        assert transport._socket is second_socket
        assert "OSError" in caplog.text
        assert "close-secret" not in caplog.text
        await transport.close()

    asyncio.run(run())


def test_reader_disconnect_errors_name_each_pending_method_and_id() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test/rpc",
            connect_factory=connect_factory,
        )
        await transport.connect()
        first = asyncio.create_task(transport.request("first_method", []))
        second = asyncio.create_task(transport.request("second_method", []))
        await socket.wait_for_sent(2)
        await socket.disconnect(ConnectionError("node stopped"))

        with pytest.raises(RPCError) as first_error:
            await first
        with pytest.raises(RPCError) as second_error:
            await second
        assert "first_method" in str(first_error.value)
        assert "request id 1" in str(first_error.value)
        assert "second_method" in str(second_error.value)
        assert "request id 2" in str(second_error.value)
        await transport.close()

    asyncio.run(run())


def test_unsubscribed_tombstone_memory_is_bounded() -> None:
    transport = AsyncRpcTransport("ws://node.test")
    for index in range(70):
        transport._remember_unsubscribed(f"sub-{index}")

    assert len(transport._unsubscribed) == 64
    assert "sub-0" not in transport._unsubscribed
    assert "sub-69" in transport._unsubscribed


def test_late_disconnect_after_close_does_not_change_terminal_state() -> None:
    async def run() -> None:
        transport = AsyncRpcTransport("ws://node.test")
        await transport.close()

        await transport._disconnect_connection("late reader failure")

        assert transport.state is ConnectionState.CLOSED

    asyncio.run(run())


def test_disconnect_without_owned_socket_is_a_noop() -> None:
    async def run() -> None:
        transport = AsyncRpcTransport("ws://node.test")

        await transport._disconnect_connection("nothing connected")

        assert transport.state is ConnectionState.DISCONNECTED

    asyncio.run(run())


def test_stale_socket_disconnect_does_not_affect_current_connection() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()

        await transport._disconnect_connection(
            "stale reader failure",
            source_socket=object(),
        )

        assert transport.state is ConnectionState.CONNECTED
        assert transport._socket is socket
        assert socket.close_calls == 0
        await transport.close()

    asyncio.run(run())


def test_cancel_after_socket_creation_closes_unpublished_socket() -> None:
    async def run() -> None:
        socket = FakeSocket()
        factory_entered = asyncio.Event()
        release_factory = asyncio.Event()
        factory_returning = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            factory_entered.set()
            await release_factory.wait()
            factory_returning.set()
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        connecting = asyncio.create_task(transport.connect())
        await factory_entered.wait()
        await transport._disconnect_lock.acquire()
        release_factory.set()
        await factory_returning.wait()
        connect_blocked_on_lock = asyncio.Event()
        asyncio.get_running_loop().call_soon(connect_blocked_on_lock.set)
        await connect_blocked_on_lock.wait()

        connecting.cancel()
        transport._disconnect_lock.release()
        with pytest.raises(asyncio.CancelledError):
            await connecting

        assert socket.close_calls == 1
        assert transport._socket is None
        assert transport.state is ConnectionState.DISCONNECTED
        await transport.close()

    asyncio.run(run())


def test_failed_late_socket_close_remains_owned_and_close_reports_failure() -> None:
    async def run() -> None:
        socket = FakeSocket(close_error=OSError("late-close-secret"))
        factory_entered = asyncio.Event()
        release_factory = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            factory_entered.set()
            try:
                await release_factory.wait()
            except asyncio.CancelledError:
                await release_factory.wait()
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        connecting = asyncio.create_task(transport.connect())
        await factory_entered.wait()
        closing = asyncio.create_task(transport.close())
        close_started = asyncio.Event()
        asyncio.get_running_loop().call_soon(close_started.set)
        await close_started.wait()
        release_factory.set()

        with pytest.raises(RPCError) as close_error:
            await closing
        with pytest.raises(RPCError):
            await connecting
        rendered = str(close_error.value)
        assert "OSError" in rendered
        assert "late-close-secret" not in rendered
        assert (
            transport._socket is socket
            or socket in transport._retained_sockets
        )
        assert socket.close_calls >= 2

    asyncio.run(run())


def test_close_retries_transient_socket_close_failure() -> None:
    async def run() -> None:
        class FailsOnceOnCloseSocket(FakeSocket):
            async def close(self) -> None:
                self.close_calls += 1
                if self.close_calls == 1:
                    raise OSError("transient-close-secret")

        socket = FailsOnceOnCloseSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()

        with pytest.raises(RPCError) as first_error:
            await transport.close()
        assert "OSError" in str(first_error.value)
        assert transport._socket is socket

        await transport.close()

        assert socket.close_calls == 2
        assert transport._socket is None
        assert transport.state is ConnectionState.CLOSED

    asyncio.run(run())


def test_late_old_send_failure_does_not_disconnect_replacement_socket() -> None:
    async def run() -> None:
        release_old_send = asyncio.Event()

        class BlockingBrokenSocket(FakeSocket):
            async def send(self, _message: str) -> None:
                self.send_started.set()
                await release_old_send.wait()
                raise BrokenPipeError("old-wire-secret")

        old_socket = BlockingBrokenSocket()
        new_socket = FakeSocket()
        sockets = [old_socket, new_socket]

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return sockets.pop(0)

        transport = AsyncRpcTransport(
            "ws://node.test/rpc",
            connect_factory=connect_factory,
        )
        await transport.connect()
        old_request = asyncio.create_task(transport.request("old_request", []))
        await old_socket.send_started.wait()
        old_reader = transport._reader_task
        assert old_reader is not None
        await old_socket.disconnect(ConnectionError("old reader stopped"))
        await old_reader
        await transport.connect()
        assert transport._socket is new_socket

        new_request = asyncio.create_task(transport.request("new_request", []))
        release_old_send.set()
        with pytest.raises(RPCError):
            await old_request
        new_send_had_a_turn = asyncio.Event()
        asyncio.get_running_loop().call_soon(new_send_had_a_turn.set)
        await new_send_had_a_turn.wait()

        assert new_socket.sent
        assert transport.state is ConnectionState.CONNECTED
        assert new_socket.close_calls == 0
        await new_socket.receive(
            {
                "jsonrpc": "2.0",
                "id": new_socket.sent[0]["id"],
                "result": "new-ok",
            }
        )
        assert await new_request == "new-ok"
        await transport.close()

    asyncio.run(run())


def test_cancelled_send_caller_does_not_cancel_joinable_disconnect_cleanup() -> None:
    async def run() -> None:
        reader_cancelled = asyncio.Event()
        release_reader = asyncio.Event()
        socket = FakeSocket(
            send_error=BrokenPipeError("wire-secret"),
            reader_cancelled=reader_cancelled,
            reader_release=release_reader,
        )
        loop = asyncio.get_running_loop()
        unhandled_contexts: list[dict[str, object]] = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(
            lambda _loop, context: unhandled_contexts.append(context)
        )

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test/rpc",
            connect_factory=connect_factory,
        )
        try:
            await transport.connect()
            request = asyncio.create_task(transport.request("cancelled_send", []))
            await reader_cancelled.wait()
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request

            joining = asyncio.create_task(
                transport._disconnect_connection(
                    "joining cleanup",
                    source_socket=socket,
                )
            )
            join_had_a_turn = asyncio.Event()
            asyncio.get_running_loop().call_soon(join_had_a_turn.set)
            await join_had_a_turn.wait()
            assert not joining.done()

            release_reader.set()
            await joining
            request = None
            gc.collect()
            warning_callbacks_ran = asyncio.Event()
            asyncio.get_running_loop().call_soon(warning_callbacks_ran.set)
            await warning_callbacks_ran.wait()

            assert not unhandled_contexts
            assert transport.state is ConnectionState.DISCONNECTED
            assert transport._socket is None
            assert transport._reader_task is None
            assert socket.close_calls == 1
        finally:
            loop.set_exception_handler(previous_handler)
            release_reader.set()
            await transport.close()

    asyncio.run(run())


def test_subscription_handler_can_unsubscribe_itself_without_self_cancellation() -> None:
    async def run() -> None:
        socket = FakeSocket()
        handled: list[object] = []
        handler_finished = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )

        async def handler(update: object) -> None:
            await transport.unsubscribe("unsubscribe", "self-sub")
            handled.append(update)
            handler_finished.set()

        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "self-sub"}
        )
        assert await subscribing == "self-sub"
        route = transport._subscriptions["self-sub"]

        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "self-sub", "result": "done"},
            }
        )
        await socket.wait_for_sent(2)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[1]["id"], "result": True}
        )
        await handler_finished.wait()
        worker_finished = asyncio.Event()
        asyncio.get_running_loop().call_soon(worker_finished.set)
        await worker_finished.wait()

        assert handled == ["done"]
        assert route.task is None
        assert "self-sub" not in transport._subscriptions
        await transport._stop_subscription_route(route)

        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "self-sub", "result": "late"},
            }
        )
        await socket.wait_for_received(4)
        assert handled == ["done"]
        await transport.close()

    asyncio.run(run())


def test_handler_origin_cancelled_error_is_isolated_and_worker_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        socket = FakeSocket()
        first_attempted = asyncio.Event()
        second_handled = asyncio.Event()
        calls = 0

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        async def handler(_update: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                first_attempted.set()
                raise asyncio.CancelledError
            second_handled.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sub-1"}
        )
        assert await subscribing == "sub-1"
        route = transport._subscriptions["sub-1"]

        with caplog.at_level(logging.ERROR, logger="deepx_sdk._async_transport"):
            await socket.receive(
                {
                    "jsonrpc": "2.0",
                    "method": "subscription",
                    "params": {"subscription": "sub-1", "result": "first"},
                }
            )
            await first_attempted.wait()
            await socket.receive(
                {
                    "jsonrpc": "2.0",
                    "method": "subscription",
                    "params": {"subscription": "sub-1", "result": "second"},
                }
            )
            await second_handled.wait()

        assert calls == 2
        assert route.task is not None
        assert not route.task.done()
        assert "CancelledError" in caplog.text
        await transport.close()

    asyncio.run(run())


def test_sync_handler_self_cancel_return_keeps_same_worker_live(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        socket = FakeSocket()
        first_cancel_requested = asyncio.Event()
        second_handled = asyncio.Event()
        handler_tasks: list[asyncio.Task[Any]] = []

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        def handler(update: object) -> None:
            task = asyncio.current_task()
            assert task is not None
            handler_tasks.append(task)
            if update == "first":
                task.cancel()
                first_cancel_requested.set()
                return
            second_handled.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "sync-cancel"}
        )
        assert await subscribing == "sync-cancel"
        route = transport._subscriptions["sync-cancel"]
        worker = route.task
        assert worker is not None

        with caplog.at_level(logging.ERROR, logger="deepx_sdk._async_transport"):
            await socket.receive(
                {
                    "jsonrpc": "2.0",
                    "method": "subscription",
                    "params": {
                        "subscription": "sync-cancel",
                        "result": "first",
                    },
                }
            )
            await first_cancel_requested.wait()
            cancellation_processed = asyncio.Event()
            asyncio.get_running_loop().call_soon(cancellation_processed.set)
            await cancellation_processed.wait()

            assert transport._subscriptions["sync-cancel"] is route
            assert route.task is worker
            assert not worker.done()

            await socket.receive(
                {
                    "jsonrpc": "2.0",
                    "method": "subscription",
                    "params": {
                        "subscription": "sync-cancel",
                        "result": "second",
                    },
                }
            )
            await second_handled.wait()

        assert handler_tasks == [worker, worker]
        assert route.queue.empty()
        assert caplog.text.count("CancelledError") == 1
        await transport.close()

    asyncio.run(run())


def test_async_handler_self_cancel_after_final_await_keeps_same_worker_live(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        socket = FakeSocket()
        final_await_started = asyncio.Event()
        release_final_await = asyncio.Event()
        cancel_requested = asyncio.Event()
        second_handled = asyncio.Event()
        handler_tasks: list[asyncio.Task[Any]] = []

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        async def handler(update: object) -> None:
            task = asyncio.current_task()
            assert task is not None
            handler_tasks.append(task)
            if update == "first":
                final_await_started.set()
                await release_final_await.wait()
                task.cancel()
                cancel_requested.set()
                return
            second_handled.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "async-cancel"}
        )
        assert await subscribing == "async-cancel"
        route = transport._subscriptions["async-cancel"]
        worker = route.task
        assert worker is not None

        with caplog.at_level(logging.ERROR, logger="deepx_sdk._async_transport"):
            await socket.receive(
                {
                    "jsonrpc": "2.0",
                    "method": "subscription",
                    "params": {
                        "subscription": "async-cancel",
                        "result": "first",
                    },
                }
            )
            await final_await_started.wait()
            release_final_await.set()
            await cancel_requested.wait()
            cancellation_processed = asyncio.Event()
            asyncio.get_running_loop().call_soon(cancellation_processed.set)
            await cancellation_processed.wait()

            assert transport._subscriptions["async-cancel"] is route
            assert route.task is worker
            assert not worker.done()

            await socket.receive(
                {
                    "jsonrpc": "2.0",
                    "method": "subscription",
                    "params": {
                        "subscription": "async-cancel",
                        "result": "second",
                    },
                }
            )
            await second_handled.wait()

        assert handler_tasks == [worker, worker]
        assert route.queue.empty()
        assert caplog.text.count("CancelledError") == 1
        await transport.close()

    asyncio.run(run())


def test_handler_self_cancellation_is_isolated_and_worker_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        socket = FakeSocket()
        first_handler_entered = asyncio.Event()
        second_handled = asyncio.Event()
        never_released = asyncio.Event()
        calls = 0

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        async def handler(_update: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                first_handler_entered.set()
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                await never_released.wait()
            second_handled.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "self-cancel"}
        )
        assert await subscribing == "self-cancel"
        route = transport._subscriptions["self-cancel"]

        with caplog.at_level(logging.ERROR, logger="deepx_sdk._async_transport"):
            await socket.receive(
                {
                    "jsonrpc": "2.0",
                    "method": "subscription",
                    "params": {
                        "subscription": "self-cancel",
                        "result": "first",
                    },
                }
            )
            await first_handler_entered.wait()
            cancellation_processed = asyncio.Event()
            asyncio.get_running_loop().call_soon(cancellation_processed.set)
            await cancellation_processed.wait()

            assert transport._subscriptions["self-cancel"] is route
            assert route.task is not None
            assert not route.task.done()

            await socket.receive(
                {
                    "jsonrpc": "2.0",
                    "method": "subscription",
                    "params": {
                        "subscription": "self-cancel",
                        "result": "second",
                    },
                }
            )
            await socket.wait_for_received(3)
            worker_had_a_turn = asyncio.Event()
            asyncio.get_running_loop().call_soon(worker_had_a_turn.set)
            await worker_had_a_turn.wait()

        assert second_handled.is_set()
        assert calls == 2
        assert route.queue.empty()
        assert "CancelledError" in caplog.text
        await transport.close()

    asyncio.run(run())


def test_transport_close_wins_race_with_handler_self_cancellation() -> None:
    async def run() -> None:
        socket = FakeSocket()
        self_cancel_requested = asyncio.Event()
        never_released = asyncio.Event()
        handler_returned = False

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        async def handler(_update: object) -> None:
            nonlocal handler_returned
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            self_cancel_requested.set()
            await never_released.wait()
            handler_returned = True

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "race-sub"}
        )
        assert await subscribing == "race-sub"
        route = transport._subscriptions["race-sub"]
        worker = route.task
        assert worker is not None

        async def close_after_self_cancel() -> None:
            await self_cancel_requested.wait()
            await transport.close()

        closing = asyncio.create_task(close_after_self_cancel())
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "race-sub", "result": "first"},
            }
        )
        await closing

        assert route.stopping
        assert worker.done()
        assert route.task is None
        assert "race-sub" not in transport._subscriptions
        assert not handler_returned

    asyncio.run(run())


@pytest.mark.parametrize("stop_kind", ["unsubscribe", "close"])
def test_transport_stop_wins_during_handler_cancellation_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    stop_kind: str,
) -> None:
    async def run() -> None:
        socket = FakeSocket()
        self_cancel_requested = asyncio.Event()
        checkpoint_entered = asyncio.Event()
        checkpoint_exited = asyncio.Event()
        never_release_checkpoint = asyncio.Event()
        route: Any = None

        async def controlled_checkpoint(delay: float) -> None:
            assert delay == 0
            checkpoint_entered.set()
            try:
                await never_release_checkpoint.wait()
            except asyncio.CancelledError:
                task = asyncio.current_task()
                assert task is not None
                if (route is not None and route.stopping) or task.cancelling():
                    raise
                await never_release_checkpoint.wait()
            finally:
                checkpoint_exited.set()

        monkeypatch.setattr(
            transport_module.asyncio,
            "sleep",
            controlled_checkpoint,
        )

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        class ImmediateUnsubscribeTransport(AsyncRpcTransport):
            async def request(
                self,
                method: str,
                params: list[object],
            ) -> object:
                assert method == "unsubscribe"
                assert params == ["checkpoint-race"]
                return True

        def handler(_update: object) -> None:
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            self_cancel_requested.set()

        transport = ImmediateUnsubscribeTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "id": socket.sent[0]["id"],
                "result": "checkpoint-race",
            }
        )
        assert await subscribing == "checkpoint-race"
        route = transport._subscriptions["checkpoint-race"]
        worker = route.task
        assert worker is not None

        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {
                    "subscription": "checkpoint-race",
                    "result": "first",
                },
            }
        )
        await self_cancel_requested.wait()
        checkpoint_had_a_turn = asyncio.Event()
        asyncio.get_running_loop().call_soon(checkpoint_had_a_turn.set)
        await checkpoint_had_a_turn.wait()
        assert checkpoint_entered.is_set()
        assert not checkpoint_exited.is_set()

        if stop_kind == "unsubscribe":
            await transport.unsubscribe("unsubscribe", "checkpoint-race")
        else:
            await transport.close()

        assert checkpoint_exited.is_set()
        assert route.stopping
        assert worker.done()
        assert worker.cancelled()
        assert route.task is None
        assert "checkpoint-race" not in transport._subscriptions
        assert route.queue.empty()
        await transport.close()

    asyncio.run(run())


def test_active_subscription_queue_overflow_disconnects_without_leaking_worker() -> None:
    async def run() -> None:
        socket = FakeSocket()
        handler_started = asyncio.Event()
        release_handler = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        async def handler(_update: object) -> None:
            handler_started.set()
            await release_handler.wait()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], handler)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "slow-sub"}
        )
        assert await subscribing == "slow-sub"
        route = transport._subscriptions["slow-sub"]
        assert route.queue.maxsize > 0

        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "slow-sub", "result": "blocked"},
            }
        )
        await handler_started.wait()
        for index in range(route.queue.maxsize + 1):
            await socket.receive(
                {
                    "jsonrpc": "2.0",
                    "method": "subscription",
                    "params": {"subscription": "slow-sub", "result": index},
                }
            )

        reader_task = transport._reader_task
        assert reader_task is not None
        await reader_task

        assert transport.state is ConnectionState.DISCONNECTED
        assert "slow-sub" not in transport._subscriptions
        assert route.task is None
        assert route.queue.empty()
        assert socket.close_calls == 1
        release_handler.set()
        await transport.close()

    asyncio.run(run())


def test_reused_subscription_id_routes_first_new_notification_atomically() -> None:
    async def run() -> None:
        socket = FakeSocket()
        updates: list[object] = []
        update_received = asyncio.Event()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        def handler(update: object) -> None:
            updates.append(update)
            update_received.set()

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        first = asyncio.create_task(
            transport.subscribe("first_subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "reused"}
        )
        assert await first == "reused"
        unsubscribe = asyncio.create_task(
            transport.unsubscribe("unsubscribe", "reused")
        )
        await socket.wait_for_sent(2)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[1]["id"], "result": True}
        )
        await unsubscribe

        second = asyncio.create_task(
            transport.subscribe("second_subscribe", [], handler)
        )
        await socket.wait_for_sent(3)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[2]["id"], "result": "reused"}
        )
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "method": "subscription",
                "params": {"subscription": "reused", "result": "first-new"},
            }
        )

        assert await second == "reused"
        await update_received.wait()
        assert updates == ["first-new"]
        await transport.close()

    asyncio.run(run())


def test_cancel_after_atomic_subscription_activation_stops_new_route() -> None:
    async def run() -> None:
        socket = FakeSocket()
        route_activated = asyncio.Event()
        keep_subscribe_paused = asyncio.Event()

        class PausingTransport(AsyncRpcTransport):
            async def _request(
                self,
                method: str,
                params: list[object],
                **kwargs: object,
            ) -> object:
                result = await super()._request(method, params, **kwargs)
                if kwargs.get("subscribe_intent") is not None:
                    route_activated.set()
                    await keep_subscribe_paused.wait()
                return result

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = PausingTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        subscribing = asyncio.create_task(
            transport.subscribe("subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(1)
        await socket.receive(
            {"jsonrpc": "2.0", "id": socket.sent[0]["id"], "result": "cancelled-sub"}
        )
        await route_activated.wait()
        route = transport._subscriptions["cancelled-sub"]

        subscribing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await subscribing

        assert "cancelled-sub" not in transport._subscriptions
        assert "cancelled-sub" in transport._unsubscribed
        assert route.task is None
        await transport.close()

    asyncio.run(run())


def test_duplicate_active_subscription_id_is_protocol_safe_without_worker_leak() -> None:
    async def run() -> None:
        socket = FakeSocket()

        async def connect_factory(_url: str, **_kwargs: object) -> FakeSocket:
            return socket

        transport = AsyncRpcTransport(
            "ws://node.test",
            connect_factory=connect_factory,
        )
        await transport.connect()
        first = asyncio.create_task(
            transport.subscribe("first_subscribe", [], lambda _update: None)
        )
        second = asyncio.create_task(
            transport.subscribe("second_subscribe", [], lambda _update: None)
        )
        await socket.wait_for_sent(2)
        request_ids = {message["method"]: message["id"] for message in socket.sent}
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "id": request_ids["first_subscribe"],
                "result": "duplicate",
            }
        )
        assert await first == "duplicate"
        first_route = transport._subscriptions["duplicate"]
        await socket.receive(
            {
                "jsonrpc": "2.0",
                "id": request_ids["second_subscribe"],
                "result": "duplicate",
            }
        )

        with pytest.raises(RPCError):
            await second
        assert transport.state is ConnectionState.DISCONNECTED
        assert transport._subscriptions["duplicate"] is first_route
        assert first_route.task is not None
        first_worker = first_route.task

        await transport.close()
        assert first_worker.done()

    asyncio.run(run())
