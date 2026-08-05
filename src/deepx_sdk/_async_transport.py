from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import random
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ._errors import RPCError

try:
    import websockets
except ImportError:  # pragma: no cover - exercised by dependency injection
    websockets = None  # type: ignore[assignment]

try:
    from python_socks.async_.asyncio import Proxy
except ImportError:  # pragma: no cover - exercised by dependency injection
    Proxy = None  # type: ignore[assignment,misc]


logger = logging.getLogger(__name__)

_INSTALL_MESSAGE = (
    "Async WebSocket transport requires the 'websockets' package "
    "(a core dependency); install it with `pip install websockets`."
)
_PROXY_INSTALL_MESSAGE = (
    "Async WebSocket transport through an HTTP proxy requires the "
    "'python-socks' package (a core dependency); install it with "
    "`pip install python-socks`."
)
_MAX_UNSUBSCRIBED_TOMBSTONES = 64
_MAX_ACTIVE_SUBSCRIPTION_NOTIFICATIONS = 64
_MAX_EARLY_NOTIFICATIONS = 32


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    RECOVERING = "recovering"
    CLOSED = "closed"


ConnectFactory = Callable[..., Awaitable[Any]]
NotificationHandler = Callable[[object], Awaitable[None] | None]
ConnectionStateCallback = Callable[
    [ConnectionState], Awaitable[None] | None
]
ReconnectJitter = Callable[[float], float]
ReconnectSleep = Callable[[float], Awaitable[None]]


class TransportRequestError(RPCError):
    """A transport failure that records whether request bytes may have left."""

    def __init__(self, message: str, *, may_have_been_sent: bool) -> None:
        super().__init__(message)
        self.may_have_been_sent = may_have_been_sent


@dataclass
class _SubscriptionRoute:
    handler: NotificationHandler
    queue: asyncio.Queue[object]
    task: asyncio.Task[None] | None = None
    stopping: bool = False


@dataclass
class _SubscribeIntent:
    handler: NotificationHandler
    subscription_id: str | None = None
    route: _SubscriptionRoute | None = None


class _ProtocolError(Exception):
    pass


class _SubscriptionQueueOverflow(_ProtocolError):
    def __init__(self, subscription_id: str, route: _SubscriptionRoute) -> None:
        super().__init__(f"subscription {subscription_id!r} queue overflow")
        self.route = route


class AsyncRpcTransport:
    def __init__(
        self,
        url: str | Sequence[str],
        *,
        headers: Mapping[str, str] | None = None,
        connect_factory: ConnectFactory | None = None,
        auto_reconnect: bool = False,
        reconnect_initial_ms: int = 100,
        reconnect_max_ms: int = 3_000,
        reconnect_jitter: ReconnectJitter | None = None,
        reconnect_sleep: ReconnectSleep = asyncio.sleep,
    ) -> None:
        self._endpoints = _normalize_endpoints(url)
        self._endpoint_index = 0
        self._url = self._endpoints[0]
        self._headers = dict(headers) if headers else None
        self._connect_factory = connect_factory
        self._socket: Any = None
        self._reader_task: asyncio.Task[None] | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._connect_waiters = 0
        self._reconnect_task: asyncio.Task[None] | None = None
        self._auto_reconnect = auto_reconnect
        self._reconnect_initial_ms = reconnect_initial_ms
        self._reconnect_max_ms = reconnect_max_ms
        self._reconnect_jitter = reconnect_jitter or _jittered_delay
        self._reconnect_sleep = reconnect_sleep
        self._connection_state_callbacks: list[ConnectionStateCallback] = []
        self._close_task: asyncio.Task[None] | None = None
        self._retained_sockets: list[Any] = []
        self._disconnect_tasks: dict[
            int, tuple[Any, asyncio.Task[None]]
        ] = {}
        self._disconnect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._next_request_id = 1
        self._requests: dict[int, asyncio.Future[object]] = {}
        self._request_methods: dict[int, str] = {}
        self._request_may_have_been_sent: set[int] = set()
        self._subscribe_intents: dict[int, _SubscribeIntent] = {}
        self._subscriptions: dict[str, _SubscriptionRoute] = {}
        self._early_notifications: dict[str, list[object]] = {}
        self._unsubscribed: OrderedDict[str, None] = OrderedDict()
        self._state = ConnectionState.DISCONNECTED
        self._connection_count = 0

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def endpoint(self) -> str:
        return _safe_url(self._url)

    @property
    def connection_url(self) -> str:
        return self._url

    @property
    def connection_count(self) -> int:
        return self._connection_count

    def enable_reconnect(self, *, initial_ms: int = 100, max_ms: int = 3_000) -> None:
        self._reconnect_initial_ms = initial_ms
        self._reconnect_max_ms = max_ms
        self._auto_reconnect = True

    def disable_reconnect(self) -> None:
        self._auto_reconnect = False
        reconnect_task = self._reconnect_task
        if reconnect_task is not None and not reconnect_task.done():
            reconnect_task.cancel()

    def add_connection_state_callback(
        self,
        callback: ConnectionStateCallback,
    ) -> None:
        if callback not in self._connection_state_callbacks:
            self._connection_state_callbacks.append(callback)

    def remove_connection_state_callback(
        self,
        callback: ConnectionStateCallback,
    ) -> None:
        try:
            self._connection_state_callbacks.remove(callback)
        except ValueError:
            pass

    async def connect(self) -> None:
        if self._state is ConnectionState.CONNECTED:
            return
        if self._state is ConnectionState.CLOSED:
            raise RPCError("WebSocket transport is closed.")

        connect_task = self._connect_task
        if connect_task is None:
            self._state = ConnectionState.CONNECTING
            await self._notify_connection_state(ConnectionState.CONNECTING)
            connect_task = asyncio.create_task(self._connect_available())
            self._connect_task = connect_task

        self._connect_waiters += 1
        try:
            await asyncio.shield(connect_task)
        except asyncio.CancelledError:
            if self._state is ConnectionState.CLOSED:
                raise RPCError("WebSocket transport is closed.") from None
            if self._connect_waiters == 1 and not connect_task.done():
                connect_task.cancel()
                await asyncio.gather(connect_task, return_exceptions=True)
                if self._state is not ConnectionState.CLOSED:
                    self._state = ConnectionState.DISCONNECTED
            raise
        finally:
            self._connect_waiters -= 1

    async def _connect_available(
        self,
        published_state: ConnectionState = ConnectionState.CONNECTED,
    ) -> None:
        current_task = asyncio.current_task()
        last_error: RPCError | None = None
        try:
            for _attempt in range(len(self._endpoints)):
                try:
                    await self._connect_once(published_state)
                    return
                except RPCError as exc:
                    last_error = exc
                    self._advance_endpoint()
            assert last_error is not None
            raise last_error
        finally:
            if self._connect_task is current_task:
                self._connect_task = None

    async def _connect_once(
        self,
        published_state: ConnectionState = ConnectionState.CONNECTED,
    ) -> None:
        socket: Any = None
        published = False
        try:
            await self._retire_disconnected_connection()
            connect_kwargs = await self._proxy_connect_kwargs()
            socket = await self._open_socket(connect_kwargs)
            async with self._disconnect_lock:
                if self._state is ConnectionState.CLOSED:
                    raise RPCError("WebSocket transport is closed.")
                self._socket = socket
                published = True
                self._state = published_state
                self._connection_count += 1
                self._reader_task = asyncio.create_task(self._reader_loop(socket))
            await self._notify_connection_state(published_state)
        except asyncio.CancelledError:
            if self._state is ConnectionState.CLOSED:
                raise RPCError("WebSocket transport is closed.") from None
            self._state = ConnectionState.DISCONNECTED
            raise
        except RPCError:
            if self._state is not ConnectionState.CLOSED:
                self._state = ConnectionState.DISCONNECTED
            raise
        except Exception as exc:
            if self._state is not ConnectionState.CLOSED:
                self._state = ConnectionState.DISCONNECTED
            raise RPCError(
                "WebSocket transport failed to connect to "
                f"{_safe_url(self._url)}: {type(exc).__name__}"
            ) from None
        finally:
            if socket is not None and not published:
                self._retain_socket(socket)
                try:
                    await asyncio.shield(self._close_retained_socket(socket))
                except Exception:
                    pass

    def _advance_endpoint(self) -> None:
        if len(self._endpoints) <= 1:
            return
        self._endpoint_index = (self._endpoint_index + 1) % len(self._endpoints)
        self._url = self._endpoints[self._endpoint_index]

    async def _retire_disconnected_connection(self) -> None:
        async with self._disconnect_lock:
            socket = self._socket
            if socket is not None:
                try:
                    await socket.close()
                except Exception as exc:
                    raise RPCError(
                        "Failed to retire disconnected WebSocket for "
                        f"{_safe_url(self._url)}: {type(exc).__name__}"
                    ) from None
                if self._socket is socket:
                    self._socket = None

    async def request(self, method: str, params: list[object]) -> object:
        return await self._request(method, params)

    async def _request(
        self,
        method: str,
        params: list[object],
        *,
        subscribe_intent: _SubscribeIntent | None = None,
    ) -> object:
        if self._state is not ConnectionState.CONNECTED or self._socket is None:
            raise TransportRequestError(
                f"Cannot call RPC method {method!r}: WebSocket transport is not connected.",
                may_have_been_sent=False,
            )

        request_id = self._next_request_id
        self._next_request_id += 1
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._requests[request_id] = future
        self._request_methods[request_id] = method
        if subscribe_intent is not None:
            self._subscribe_intents[request_id] = subscribe_intent
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        source_socket = self._socket

        try:
            async with self._send_lock:
                if future.done() or self._state is not ConnectionState.CONNECTED:
                    return await future
                self._request_may_have_been_sent.add(request_id)
                await source_socket.send(json.dumps(message))
            return await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                future.exception()
            else:
                future.cancel()
            raise
        except RPCError:
            raise
        except Exception as exc:
            try:
                await self._disconnect_connection(
                    f"send failed with {type(exc).__name__}",
                    source_socket=source_socket,
                )
            except asyncio.CancelledError:
                if future.done() and not future.cancelled():
                    future.exception()
                else:
                    future.cancel()
                raise
            return await future
        finally:
            self._requests.pop(request_id, None)
            self._request_methods.pop(request_id, None)
            self._subscribe_intents.pop(request_id, None)
            if not self._subscribe_intents:
                # No subscribe in flight: race-buffered notifications can no
                # longer be attributed to a pending registration.
                self._early_notifications.clear()
            self._request_may_have_been_sent.discard(request_id)

    async def subscribe(
        self,
        method: str,
        params: list[object],
        handler: NotificationHandler,
    ) -> str:
        intent = _SubscribeIntent(handler=handler)
        completed = False
        try:
            result = await self._request(
                method,
                params,
                subscribe_intent=intent,
            )
            if result is None:
                raise RPCError(f"RPC method {method!r} returned no subscription ID.")

            subscription_id = str(result)
            route = intent.route
            if (
                route is None
                or intent.subscription_id != subscription_id
                or self._subscriptions.get(subscription_id) is not route
            ):
                raise RPCError(
                    f"Subscription {subscription_id!r} cannot be registered: "
                    "WebSocket transport is not connected."
                )
            completed = True
            return subscription_id
        finally:
            if not completed and intent.route is not None:
                subscription_id = intent.subscription_id
                if (
                    subscription_id is not None
                    and self._subscriptions.get(subscription_id) is intent.route
                ):
                    self._subscriptions.pop(subscription_id, None)
                    self._remember_unsubscribed(subscription_id)
                await self._stop_subscription_route(intent.route)

    async def unsubscribe(self, method: str, subscription_id: str) -> None:
        result = await self.request(method, [subscription_id])
        if result is not True:
            raise RPCError(
                f"RPC method {method!r} failed to unsubscribe "
                f"{subscription_id!r}: expected result True, got "
                f"{type(result).__name__} {result!r}."
            )

        await self.forget_subscription(subscription_id)

    async def forget_subscription(self, subscription_id: str) -> None:
        """Release a locally terminal subscription without another RPC."""
        route = self._subscriptions.pop(subscription_id, None)
        self._early_notifications.pop(subscription_id, None)
        self._remember_unsubscribed(subscription_id)
        if route is not None:
            await self._stop_subscription_route(route)

    async def _open_socket(self, connect_kwargs: dict[str, object]) -> Any:
        connect_factory = self._connect_factory
        if connect_factory is None:
            if websockets is None:
                raise RPCError(_INSTALL_MESSAGE)
            connect_factory = websockets.connect

        if self._headers is None:
            return await connect_factory(self._url, **connect_kwargs)

        try:
            return await connect_factory(
                self._url,
                additional_headers=self._headers,
                **connect_kwargs,
            )
        except TypeError as exc:
            if not _is_additional_headers_keyword_error(exc):
                raise
        return await connect_factory(
            self._url,
            extra_headers=self._headers,
            **connect_kwargs,
        )

    async def _proxy_connect_kwargs(self) -> dict[str, object]:
        parsed = urlsplit(self._url)
        if parsed.scheme == "wss":
            environment_name = "https_proxy"
            default_port = 443
        elif parsed.scheme == "ws":
            environment_name = "http_proxy"
            default_port = 80
        else:
            return {}

        proxy_url = os.environ.get(environment_name) or os.environ.get(
            environment_name.upper()
        )
        if not proxy_url:
            return {}
        if Proxy is None:
            raise RPCError(_PROXY_INSTALL_MESSAGE)
        if parsed.hostname is None:
            raise RPCError(
                f"WebSocket endpoint has no hostname: {_safe_url(self._url)}"
            )

        try:
            proxy = Proxy.from_url(proxy_url)
            tunnel_socket = await proxy.connect(
                dest_host=parsed.hostname,
                dest_port=parsed.port or default_port,
            )
        except Exception as exc:
            raise RPCError(
                "Proxy tunnel through "
                f"{_safe_url(proxy_url)} to {_safe_url(self._url)} failed: "
                f"{type(exc).__name__}"
            ) from None
        return {"sock": tunnel_socket}

    async def _reader_loop(self, socket: Any) -> None:
        try:
            while True:
                raw_message = await socket.recv()
                try:
                    message = json.loads(raw_message)
                except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
                    raise _ProtocolError("invalid JSON payload") from None
                self._validate_message(message)
                self._dispatch_message(message)
        except asyncio.CancelledError:
            raise
        except _SubscriptionQueueOverflow as exc:
            await self._stop_subscription_route(exc.route)
            await self._disconnect_connection(
                f"protocol error: {exc}",
                source_socket=socket,
                source_reader=asyncio.current_task(),
            )
        except _ProtocolError as exc:
            await self._disconnect_connection(
                f"protocol error: {exc}",
                source_socket=socket,
                source_reader=asyncio.current_task(),
            )
        except Exception as exc:
            await self._disconnect_connection(
                f"disconnected with {type(exc).__name__}",
                source_socket=socket,
                source_reader=asyncio.current_task(),
            )

    @staticmethod
    def _validate_message(message: object) -> None:
        if not isinstance(message, dict):
            raise _ProtocolError("top-level value must be an object")
        if message.get("jsonrpc") != "2.0":
            raise _ProtocolError("jsonrpc must equal '2.0'")

        if "id" in message:
            request_id = message["id"]
            if type(request_id) is not int:
                raise _ProtocolError("response id must be an integer")
            has_result = "result" in message
            has_error = "error" in message
            if has_result == has_error:
                raise _ProtocolError(
                    "response must contain exactly one of result or error"
                )
            if has_error:
                error = message["error"]
                if not isinstance(error, dict):
                    raise _ProtocolError("response error must be an object")
                if type(error.get("code")) is not int:
                    raise _ProtocolError("response error code must be an integer")
                if not isinstance(error.get("message"), str):
                    raise _ProtocolError("response error message must be a string")
            return

        if not isinstance(message.get("method"), str):
            raise _ProtocolError("notification method must be a string")
        if not isinstance(message.get("params"), dict):
            raise _ProtocolError("notification params must be an object")

    def _dispatch_message(self, message: dict[str, object]) -> None:
        if "id" in message:
            self._dispatch_response(message)
            return

        params = message["params"]
        assert isinstance(params, dict)
        subscription = params.get("subscription")
        if subscription is None:
            return

        subscription_id = str(subscription)
        route = self._subscriptions.get(subscription_id)
        if route is not None:
            try:
                route.queue.put_nowait(params.get("result"))
            except asyncio.QueueFull:
                if self._subscriptions.get(subscription_id) is route:
                    self._subscriptions.pop(subscription_id, None)
                route.stopping = True
                raise _SubscriptionQueueOverflow(subscription_id, route) from None
            return
        if subscription_id in self._unsubscribed:
            return
        # The node may stream notifications before the subscribe response
        # registers the route; buffer them for replay on registration.
        # Only while a subscribe is in flight — anything else is junk.
        if not self._subscribe_intents:
            return
        total = sum(len(items) for items in self._early_notifications.values())
        if total >= _MAX_EARLY_NOTIFICATIONS:
            return
        self._early_notifications.setdefault(subscription_id, []).append(
            params.get("result")
        )

    def _dispatch_response(self, message: dict[str, object]) -> None:
        request_id = message.get("id")
        assert type(request_id) is int
        future = self._requests.get(request_id)
        method = self._request_methods.get(request_id, "<unknown>")
        if future is None or future.done():
            return

        error = message.get("error")
        if error is not None:
            self._requests.pop(request_id, None)
            self._request_methods.pop(request_id, None)
            assert isinstance(error, dict)
            code = error["code"]
            error_message = error["message"]
            future.set_exception(
                RPCError(
                    f"RPC method {method!r} (request id {request_id}) "
                    f"failed with code {code}: {error_message}"
                )
            )
            return

        result = message.get("result")
        intent = self._subscribe_intents.get(request_id)
        if intent is not None and result is not None:
            subscription_id = str(result)
            if subscription_id in self._subscriptions:
                raise _ProtocolError(
                    f"duplicate active subscription id {subscription_id!r}"
                )
            route = _SubscriptionRoute(
                handler=intent.handler,
                queue=asyncio.Queue(
                    maxsize=_MAX_ACTIVE_SUBSCRIPTION_NOTIFICATIONS
                ),
            )
            route.task = asyncio.create_task(
                self._subscription_worker(subscription_id, route)
            )
            self._subscriptions[subscription_id] = route
            self._unsubscribed.pop(subscription_id, None)
            intent.subscription_id = subscription_id
            intent.route = route
            early = self._early_notifications.pop(subscription_id, None)
            if early:
                for early_result in early:
                    try:
                        route.queue.put_nowait(early_result)
                    except asyncio.QueueFull:
                        break

        self._requests.pop(request_id, None)
        self._request_methods.pop(request_id, None)
        future.set_result(result)

    async def _subscription_worker(
        self,
        subscription_id: str,
        route: _SubscriptionRoute,
    ) -> None:
        try:
            while not route.stopping:
                update = await route.queue.get()
                try:
                    await self._invoke_handler(
                        route.handler,
                        update,
                        subscription_id,
                        route,
                    )
                finally:
                    route.queue.task_done()
                if route.stopping:
                    break
        finally:
            if route.task is asyncio.current_task():
                route.task = None
            self._drain_subscription_queue(route)

    async def _stop_subscription_route(self, route: _SubscriptionRoute) -> None:
        route.stopping = True
        task = route.task
        if task is None:
            self._drain_subscription_queue(route)
            return
        if task is asyncio.current_task():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        route.task = None
        self._drain_subscription_queue(route)

    @staticmethod
    def _drain_subscription_queue(route: _SubscriptionRoute) -> None:
        while True:
            try:
                route.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            route.queue.task_done()

    def _remember_unsubscribed(self, subscription_id: str) -> None:
        self._unsubscribed.pop(subscription_id, None)
        self._unsubscribed[subscription_id] = None
        while len(self._unsubscribed) > _MAX_UNSUBSCRIBED_TOMBSTONES:
            self._unsubscribed.popitem(last=False)

    async def _invoke_handler(
        self,
        handler: NotificationHandler,
        update: object,
        subscription_id: str,
        route: _SubscriptionRoute,
    ) -> None:
        task = asyncio.current_task()
        cancellation_count = task.cancelling() if task is not None else 0
        failure_name: str | None = None
        try:
            result = handler(update)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            if route.stopping:
                raise
            failure_name = "CancelledError"
        except Exception as exc:
            failure_name = type(exc).__name__
        finally:
            handler_cancelled = self._reconcile_handler_cancellation(
                task,
                cancellation_count,
                route,
            )
            if (
                handler_cancelled
                and cancellation_count == 0
                and task is not None
            ):
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    if (
                        route.stopping
                        or task.cancelling() > cancellation_count
                    ):
                        raise
            if handler_cancelled:
                failure_name = failure_name or "CancelledError"
            if failure_name is not None:
                logger.error(
                    "Subscription handler failed for %s: %s",
                    subscription_id,
                    failure_name,
                )

    @staticmethod
    def _reconcile_handler_cancellation(
        task: asyncio.Task[Any] | None,
        cancellation_count: int,
        route: _SubscriptionRoute,
    ) -> bool:
        if task is None or route.stopping:
            return False
        handler_cancelled = task.cancelling() > cancellation_count
        while task.cancelling() > cancellation_count:
            task.uncancel()
        return handler_cancelled

    async def _notify_connection_state(self, state: ConnectionState) -> None:
        for callback in tuple(self._connection_state_callbacks):
            try:
                result = callback(state)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                logger.error(
                    "Connection state callback failed for %s: %s",
                    state.value,
                    type(exc).__name__,
                )

    async def _drop_subscriptions_after_disconnect(self) -> None:
        routes = list(self._subscriptions.values())
        self._subscriptions.clear()
        self._early_notifications.clear()
        for route in routes:
            await self._stop_subscription_route(route)

    def _schedule_reconnect(self) -> None:
        if (
            not self._auto_reconnect
            or self._state is ConnectionState.CLOSED
            or (
                self._reconnect_task is not None
                and not self._reconnect_task.done()
            )
        ):
            return
        task = asyncio.create_task(
            self._reconnect_loop(),
            name="deepx-rpc-reconnect",
        )
        self._reconnect_task = task
        task.add_done_callback(_consume_background_exception)

    async def _reconnect_loop(self) -> None:
        current_task = asyncio.current_task()
        delay_ms = self._reconnect_initial_ms
        try:
            while self._auto_reconnect and self._state is not ConnectionState.CLOSED:
                base_delay = min(delay_ms, self._reconnect_max_ms) / 1_000
                delay = min(
                    self._reconnect_max_ms / 1_000,
                    max(0.0, self._reconnect_jitter(base_delay)),
                )
                await self._reconnect_sleep(delay)
                if (
                    not self._auto_reconnect
                    or self._state is ConnectionState.CLOSED
                ):
                    return

                self._state = ConnectionState.RECONNECTING
                await self._notify_connection_state(ConnectionState.RECONNECTING)
                try:
                    await self._connect_available(ConnectionState.RECOVERING)
                except RPCError:
                    delay_ms = min(delay_ms * 2, self._reconnect_max_ms)
                    continue

                if self._state is ConnectionState.RECOVERING:
                    self._state = ConnectionState.CONNECTED
                    await self._notify_connection_state(ConnectionState.CONNECTED)
                return
        finally:
            if self._reconnect_task is current_task:
                self._reconnect_task = None

    def _fail_requests(self, context: str) -> None:
        requests = list(self._requests.items())
        self._requests.clear()
        methods = self._request_methods
        self._request_methods = {}
        sent = self._request_may_have_been_sent
        self._request_may_have_been_sent = set()
        for request_id, future in requests:
            if not future.done():
                method = methods.get(request_id, "<unknown>")
                future.set_exception(
                    TransportRequestError(
                        f"RPC method {method!r} (request id {request_id}) failed: "
                        f"{context}",
                        may_have_been_sent=request_id in sent,
                    )
                )

    async def _disconnect_connection(
        self,
        context: str,
        *,
        source_socket: Any = None,
        source_reader: asyncio.Task[None] | None = None,
    ) -> None:
        if self._state is ConnectionState.CLOSED:
            return
        socket = self._socket if source_socket is None else source_socket
        if socket is None:
            return

        key = id(socket)
        existing = self._disconnect_tasks.get(key)
        if existing is not None and existing[0] is socket:
            task = existing[1]
        else:
            task = asyncio.create_task(
                self._disconnect_cleanup(
                    context,
                    source_socket=socket,
                    source_reader=source_reader,
                )
            )
            self._disconnect_tasks[key] = (socket, task)
            task.add_done_callback(
                lambda completed, key=key, socket=socket: self._forget_disconnect_task(
                    key,
                    socket,
                    completed,
                )
            )
        await asyncio.shield(task)

    def _forget_disconnect_task(
        self,
        key: int,
        socket: Any,
        task: asyncio.Task[None],
    ) -> None:
        existing = self._disconnect_tasks.get(key)
        if (
            existing is not None
            and existing[0] is socket
            and existing[1] is task
        ):
            self._disconnect_tasks.pop(key, None)

    async def _disconnect_cleanup(
        self,
        context: str,
        *,
        source_socket: Any,
        source_reader: asyncio.Task[None] | None,
    ) -> None:
        disconnected = False
        async with self._disconnect_lock:
            if self._state is ConnectionState.CLOSED:
                return
            if self._socket is not source_socket:
                return

            self._state = ConnectionState.DISCONNECTED
            disconnected = True
            self._fail_requests(
                f"WebSocket transport {context} at {_safe_url(self._url)}."
            )

            reader_task = self._reader_task
            current_task = asyncio.current_task()
            if (
                reader_task is not None
                and reader_task is not current_task
                and reader_task is not source_reader
            ):
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
            if self._reader_task is reader_task:
                self._reader_task = None

            socket = self._socket
            if socket is not None:
                try:
                    await socket.close()
                except Exception as exc:
                    logger.error(
                        "Failed to close disconnected WebSocket for %s: %s",
                        _safe_url(self._url),
                        type(exc).__name__,
                    )
                else:
                    if self._socket is socket:
                        self._socket = None
        if disconnected:
            await self._notify_connection_state(ConnectionState.DISCONNECTED)
            if self._auto_reconnect:
                await self._drop_subscriptions_after_disconnect()
                self._advance_endpoint()
                self._schedule_reconnect()

    async def close(self) -> None:
        close_task = self._close_task
        if close_task is None or (
            close_task.done()
            and not close_task.cancelled()
            and close_task.exception() is not None
        ):
            self._state = ConnectionState.CLOSED
            close_task = asyncio.create_task(self._close_cleanup())
            self._close_task = close_task
        await asyncio.shield(close_task)

    async def _close_cleanup(self) -> None:
        reconnect_task = self._reconnect_task
        if reconnect_task is not None and not reconnect_task.done():
            reconnect_task.cancel()
            await asyncio.gather(reconnect_task, return_exceptions=True)
        self._reconnect_task = None

        connect_task = self._connect_task
        if connect_task is not None and not connect_task.done():
            connect_task.cancel()
            await asyncio.gather(connect_task, return_exceptions=True)

        self._fail_requests(
            f"WebSocket transport closed for {_safe_url(self._url)}."
        )

        routes = list(self._subscriptions.values())
        self._subscriptions.clear()
        for route in routes:
            route.stopping = True
            task = route.task
            if task is not None:
                task.cancel()
        if routes:
            await asyncio.gather(
                *(route.task for route in routes if route.task is not None),
                return_exceptions=True,
            )
            for route in routes:
                route.task = None

        self._early_notifications.clear()
        self._unsubscribed.clear()

        disconnect_tasks = [
            task for _socket, task in self._disconnect_tasks.values()
        ]
        if disconnect_tasks:
            await asyncio.gather(
                *(asyncio.shield(task) for task in disconnect_tasks),
                return_exceptions=True,
            )

        close_error: Exception | None = None
        async with self._disconnect_lock:
            reader_task = self._reader_task
            if reader_task is not None:
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
                if self._reader_task is reader_task:
                    self._reader_task = None

            socket = self._socket
            if socket is not None:
                try:
                    await socket.close()
                except Exception as exc:
                    close_error = exc
                else:
                    if self._socket is socket:
                        self._socket = None

            for retained_socket in list(self._retained_sockets):
                try:
                    await self._close_retained_socket(retained_socket)
                except Exception as exc:
                    if close_error is None:
                        close_error = exc

        if close_error is not None:
            raise RPCError(
                f"Failed to close WebSocket transport for "
                f"{_safe_url(self._url)}: {type(close_error).__name__}"
            ) from None

    def _retain_socket(self, socket: Any) -> None:
        if not any(retained is socket for retained in self._retained_sockets):
            self._retained_sockets.append(socket)

    async def _close_retained_socket(self, socket: Any) -> None:
        await socket.close()
        self._retained_sockets = [
            retained for retained in self._retained_sockets if retained is not socket
        ]


def _jittered_delay(delay: float) -> float:
    return random.uniform(delay * 0.5, delay * 1.5)


def _normalize_endpoints(url: str | Sequence[str]) -> tuple[str, ...]:
    values = (url,) if isinstance(url, str) else tuple(url)
    endpoints: list[str] = []
    for candidate in values:
        endpoint = str(candidate).strip()
        if not endpoint:
            raise ValueError("RPC endpoints must contain non-empty URLs")
        if endpoint not in endpoints:
            endpoints.append(endpoint)
    if not endpoints:
        raise ValueError("RPC endpoints must not be empty")
    return tuple(endpoints)


def _consume_background_exception(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


def _is_additional_headers_keyword_error(error: TypeError) -> bool:
    rendered = str(error)
    return (
        "unexpected keyword argument 'additional_headers'" in rendered
        or 'unexpected keyword argument "additional_headers"' in rendered
    )


def _safe_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if not parsed.scheme or hostname is None:
            return "<configured endpoint>"
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))
    except (TypeError, ValueError):
        return "<configured endpoint>"


__all__ = [
    "AsyncRpcTransport",
    "ConnectionStateCallback",
    "ConnectionState",
    "NotificationHandler",
    "TransportRequestError",
]
