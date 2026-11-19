# Modified by DeepX to redact diagnostics and bound reconnect attempts.
import json

from websocket import create_connection, WebSocketConnectionClosedException

from ..._rpc_transport import _safe_http_endpoint
from .base import TransportBase, list_remove_iter
from ..exceptions import SubstrateRequestException


class WebsocketTransport(TransportBase):
    def __init__(self, url=None, websocket=None, ws_options=None, auto_reconnect=True, debug_fn=None, on_connect=None):
        super().__init__(debug_fn=debug_fn)
        self.url = url
        self.websocket = websocket
        self.ws_options = ws_options or {}
        self.auto_reconnect = auto_reconnect
        self.on_connect = on_connect
        self.__rpc_message_queue = []

        if self.websocket and callable(self.on_connect):
            self.on_connect(self.websocket)

        if self.url and not self.websocket:
            self.connect()

    def _transport_error(self, operation, exc):
        endpoint = _safe_http_endpoint(self.url) if self.url else "<configured endpoint>"
        return SubstrateRequestException(f"WebSocket {operation} failed at {endpoint}: {type(exc).__name__}")

    def connect(self):
        if self.url:
            self.debug_message(f"Connecting to {_safe_http_endpoint(self.url)} ...")
            try:
                self.websocket = create_connection(self.url, **self.ws_options)
            except Exception as exc:
                raise self._transport_error("connect", exc) from None
            if callable(self.on_connect):
                self.on_connect(self.websocket)

    def close(self):
        if self.websocket:
            self.debug_message("Closing websocket connection")
            try:
                self.websocket.close()
            except Exception as exc:
                raise self._transport_error("close", exc) from None

    def rpc_request(self, payload, result_handler=None):
        request_id = payload['id']

        body = json.dumps(payload)
        for attempt in range(2):
            try:
                self.websocket.send(body)
                break
            except WebSocketConnectionClosedException as exc:
                if attempt or not self.auto_reconnect or not self.url:
                    raise self._transport_error("send", exc) from None
            except Exception as exc:
                raise self._transport_error("send", exc) from None
            self.debug_message("Connection closed. Reconnecting...")
            self.connect()

        update_nr = 0
        json_body = None
        subscription_id = None

        while json_body is None:
            for message, remove_message in list_remove_iter(self.__rpc_message_queue):
                if 'id' in message and message['id'] == request_id:
                    remove_message()

                    if 'error' in message:
                        raise SubstrateRequestException(message['error'])

                    if callable(result_handler):
                        subscription_id = message['result']
                        self.debug_message("WebSocket subscription created")
                    else:
                        json_body = message

            for message, remove_message in list_remove_iter(self.__rpc_message_queue):
                if 'params' in message and message['params']['subscription'] == subscription_id:
                    remove_message()

                    self.debug_message(f"Received WebSocket subscription update #{update_nr}")

                    callback_result = result_handler(message, update_nr, subscription_id)
                    if callback_result is not None:
                        json_body = callback_result

                    update_nr += 1

            if json_body is None:
                try:
                    raw = self.websocket.recv()
                except Exception as exc:
                    raise self._transport_error("receive", exc) from None
                self.__rpc_message_queue.append(json.loads(raw))

        return json_body
