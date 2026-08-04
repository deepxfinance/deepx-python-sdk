from __future__ import annotations

import gzip
import inspect
import json
from dataclasses import dataclass
from typing import Any, Optional

try:
    import websockets
except Exception:  # pragma: no cover - optional dependency
    websockets = None


def _ensure_websockets() -> None:
    if websockets is None:
        raise RuntimeError("ws_client requires the 'websockets' package. Install via pip.")


def _to_ws_url(base_url: str) -> str:
    base = base_url.strip()
    if base.startswith("ws://") or base.startswith("wss://"):
        return base
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :]
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :]
    return "ws://" + base


def v1_ws_params(
    channel: str,
    *,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    subaccount: Optional[str] = None,
    wallet: Optional[str] = None,
    asset: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"channel": channel}
    if symbol:
        payload["symbol"] = symbol
    if interval:
        payload["interval"] = interval
    if subaccount:
        payload["subaccount"] = subaccount
    if wallet:
        payload["wallet"] = wallet
    if asset:
        payload["asset"] = asset
    return payload


def v1_subscribe(
    request_id: Any,
    *,
    channel: str,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    subaccount: Optional[str] = None,
    wallet: Optional[str] = None,
    asset: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "method": "subscribe",
        "id": request_id,
        "params": v1_ws_params(
            channel,
            symbol=symbol,
            interval=interval,
            subaccount=subaccount,
            wallet=wallet,
            asset=asset,
        ),
    }


def v1_unsubscribe(
    request_id: Any,
    *,
    channel: str,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    subaccount: Optional[str] = None,
    wallet: Optional[str] = None,
    asset: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "method": "unsubscribe",
        "id": request_id,
        "params": v1_ws_params(
            channel,
            symbol=symbol,
            interval=interval,
            subaccount=subaccount,
            wallet=wallet,
            asset=asset,
        ),
    }


def v1_list(request_id: Any) -> dict[str, Any]:
    return {"method": "list", "id": request_id}


def v1_ping() -> dict[str, Any]:
    return {"method": "ping"}


def v1_pong() -> dict[str, Any]:
    return {"method": "pong"}


def v1_post(request_id: Any, *, route: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "post",
        "id": request_id,
        "request": {
            "route": route,
            "payload": payload,
        },
    }


@dataclass
class WsMessage:
    channel: Optional[str]
    data: Any = None
    method: Optional[str] = None
    request_id: Any = None
    result: Any = None
    error: Any = None
    raw: Any = None


def _present(mapping: dict[str, Any], key: str) -> Any:
    return mapping[key] if key in mapping else None


def parse_ws_message(payload: Any) -> WsMessage:
    if not isinstance(payload, dict):
        return WsMessage(channel=None, data=payload, raw=payload)

    data = payload.get("data")
    data_map = data if isinstance(data, dict) else {}
    params = payload.get("params")
    data_params = data_map.get("params")

    channel = payload.get("channel")
    if channel is None and isinstance(params, dict):
        channel = params.get("channel")
    if channel is None and isinstance(data_params, dict):
        channel = data_params.get("channel")

    request_id = _present(payload, "id")
    if request_id is None:
        request_id = _present(data_map, "id")

    result = _present(payload, "result")
    if result is None:
        result = _present(data_map, "result")

    error = _present(payload, "error")
    if error is None:
        error = _present(data_map, "error")

    return WsMessage(
        channel=channel,
        data=data,
        method=payload.get("method") or data_map.get("method"),
        request_id=request_id,
        result=result,
        error=error,
        raw=payload,
    )


# ---------------------------------------------------------------------------
# V1 subscribe/unsubscribe helpers
# ---------------------------------------------------------------------------

def v1_sub_spot_orderbook(
    request_id: Any,
    *,
    symbol: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="spot@orderbook", symbol=symbol)


def v1_sub_spot_trades(
    request_id: Any,
    *,
    symbol: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="spot@trades", symbol=symbol)


def v1_sub_spot_ticker(
    request_id: Any,
    *,
    symbol: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="spot@ticker", symbol=symbol)


def v1_sub_spot_candles(
    request_id: Any,
    *,
    symbol: str,
    interval: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="spot@candles", symbol=symbol, interval=interval)


def v1_sub_perp_orderbook(
    request_id: Any,
    *,
    symbol: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="perp@orderbook", symbol=symbol)


def v1_sub_perp_trades(
    request_id: Any,
    *,
    symbol: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="perp@trades", symbol=symbol)


def v1_sub_perp_ticker(
    request_id: Any,
    *,
    symbol: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="perp@ticker", symbol=symbol)


def v1_sub_perp_prices(
    request_id: Any,
    *,
    symbol: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="perp@prices", symbol=symbol)


def v1_sub_perp_funding_rate(
    request_id: Any,
    *,
    symbol: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="perp@funding-rate", symbol=symbol)


def v1_sub_perp_open_interest(
    request_id: Any,
    *,
    symbol: str,
    interval: Optional[str] = None,
) -> dict[str, Any]:
    return v1_subscribe(
        request_id,
        channel="perp@open-interest",
        symbol=symbol,
        interval=interval,
    )


def v1_sub_perp_long_short_ratio(
    request_id: Any,
    *,
    symbol: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="perp@long-short-ratio", symbol=symbol)


def v1_sub_perp_candles(
    request_id: Any,
    *,
    symbol: str,
    interval: str,
) -> dict[str, Any]:
    return v1_subscribe(
        request_id,
        channel="perp@candles",
        symbol=symbol,
        interval=interval,
    )


def v1_sub_lending_market_status(
    request_id: Any,
    *,
    asset: Optional[str] = None,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="lending@market-status", asset=asset)


def v1_sub_account_balances(
    request_id: Any,
    *,
    subaccount: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="account@balances", subaccount=subaccount)


def v1_sub_account_portfolio(
    request_id: Any,
    *,
    subaccount: str,
) -> dict[str, Any]:
    return v1_subscribe(request_id, channel="account@portfolio", subaccount=subaccount)


def v1_sub_account_perp_positions(
    request_id: Any,
    *,
    subaccount: str,
    symbol: Optional[str] = None,
) -> dict[str, Any]:
    return v1_subscribe(
        request_id,
        channel="account@perp-positions",
        subaccount=subaccount,
        symbol=symbol,
    )


def v1_sub_account_perp_orders(
    request_id: Any,
    *,
    subaccount: Optional[str] = None,
    wallet: Optional[str] = None,
    symbol: Optional[str] = None,
) -> dict[str, Any]:
    return v1_subscribe(
        request_id,
        channel="account@perp-orders",
        subaccount=subaccount,
        wallet=wallet,
        symbol=symbol,
    )


def v1_sub_account_spot_orders(
    request_id: Any,
    *,
    subaccount: Optional[str] = None,
    wallet: Optional[str] = None,
    symbol: Optional[str] = None,
) -> dict[str, Any]:
    return v1_subscribe(
        request_id,
        channel="account@spot-orders",
        subaccount=subaccount,
        wallet=wallet,
        symbol=symbol,
    )


def v1_sub_account_perp_trades(
    request_id: Any,
    *,
    subaccount: Optional[str] = None,
    wallet: Optional[str] = None,
    symbol: Optional[str] = None,
) -> dict[str, Any]:
    return v1_subscribe(
        request_id,
        channel="account@perp-trades",
        subaccount=subaccount,
        wallet=wallet,
        symbol=symbol,
    )


def v1_sub_account_spot_trades(
    request_id: Any,
    *,
    subaccount: Optional[str] = None,
    wallet: Optional[str] = None,
    symbol: Optional[str] = None,
) -> dict[str, Any]:
    return v1_subscribe(
        request_id,
        channel="account@spot-trades",
        subaccount=subaccount,
        wallet=wallet,
        symbol=symbol,
    )


# Unsubscribe helpers (mirror subscribe helpers)

def v1_unsub_spot_orderbook(request_id: Any, *, symbol: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="spot@orderbook", symbol=symbol)


def v1_unsub_spot_trades(request_id: Any, *, symbol: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="spot@trades", symbol=symbol)


def v1_unsub_spot_ticker(request_id: Any, *, symbol: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="spot@ticker", symbol=symbol)


def v1_unsub_spot_candles(request_id: Any, *, symbol: str, interval: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="spot@candles", symbol=symbol, interval=interval)


def v1_unsub_perp_orderbook(request_id: Any, *, symbol: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="perp@orderbook", symbol=symbol)


def v1_unsub_perp_trades(request_id: Any, *, symbol: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="perp@trades", symbol=symbol)


def v1_unsub_perp_ticker(request_id: Any, *, symbol: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="perp@ticker", symbol=symbol)


def v1_unsub_perp_prices(request_id: Any, *, symbol: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="perp@prices", symbol=symbol)


def v1_unsub_perp_funding_rate(request_id: Any, *, symbol: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="perp@funding-rate", symbol=symbol)


def v1_unsub_perp_open_interest(
    request_id: Any, *, symbol: str, interval: Optional[str] = None
) -> dict[str, Any]:
    return v1_unsubscribe(
        request_id, channel="perp@open-interest", symbol=symbol, interval=interval
    )


def v1_unsub_perp_long_short_ratio(request_id: Any, *, symbol: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="perp@long-short-ratio", symbol=symbol)


def v1_unsub_perp_candles(request_id: Any, *, symbol: str, interval: str) -> dict[str, Any]:
    return v1_unsubscribe(
        request_id,
        channel="perp@candles",
        symbol=symbol,
        interval=interval,
    )


def v1_unsub_lending_market_status(
    request_id: Any, *, asset: Optional[str] = None
) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="lending@market-status", asset=asset)


def v1_unsub_account_balances(request_id: Any, *, subaccount: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="account@balances", subaccount=subaccount)


def v1_unsub_account_portfolio(request_id: Any, *, subaccount: str) -> dict[str, Any]:
    return v1_unsubscribe(request_id, channel="account@portfolio", subaccount=subaccount)


def v1_unsub_account_perp_positions(
    request_id: Any, *, subaccount: str, symbol: Optional[str] = None
) -> dict[str, Any]:
    return v1_unsubscribe(
        request_id,
        channel="account@perp-positions",
        subaccount=subaccount,
        symbol=symbol,
    )


def v1_unsub_account_perp_orders(
    request_id: Any,
    *,
    subaccount: Optional[str] = None,
    wallet: Optional[str] = None,
    symbol: Optional[str] = None,
) -> dict[str, Any]:
    return v1_unsubscribe(
        request_id,
        channel="account@perp-orders",
        subaccount=subaccount,
        wallet=wallet,
        symbol=symbol,
    )


def v1_unsub_account_spot_orders(
    request_id: Any,
    *,
    subaccount: Optional[str] = None,
    wallet: Optional[str] = None,
    symbol: Optional[str] = None,
) -> dict[str, Any]:
    return v1_unsubscribe(
        request_id,
        channel="account@spot-orders",
        subaccount=subaccount,
        wallet=wallet,
        symbol=symbol,
    )


def v1_unsub_account_perp_trades(
    request_id: Any,
    *,
    subaccount: Optional[str] = None,
    wallet: Optional[str] = None,
    symbol: Optional[str] = None,
) -> dict[str, Any]:
    return v1_unsubscribe(
        request_id,
        channel="account@perp-trades",
        subaccount=subaccount,
        wallet=wallet,
        symbol=symbol,
    )


def v1_unsub_account_spot_trades(
    request_id: Any,
    *,
    subaccount: Optional[str] = None,
    wallet: Optional[str] = None,
    symbol: Optional[str] = None,
) -> dict[str, Any]:
    return v1_unsubscribe(
        request_id,
        channel="account@spot-trades",
        subaccount=subaccount,
        wallet=wallet,
        symbol=symbol,
    )


@dataclass
class WsClient:
    base_url: str
    path: str = "/v1/ws"
    headers: Optional[dict[str, str]] = None
    open_timeout: int = 10
    close_timeout: int = 10
    ping_interval: Optional[int] = 20
    ping_timeout: Optional[int] = 20

    def ws_url(self) -> str:
        base = _to_ws_url(self.base_url).rstrip("/")
        path = self.path.lstrip("/")
        return f"{base}/{path}"

    async def connect(self) -> "WsSession":
        _ensure_websockets()
        kwargs = {
            "open_timeout": self.open_timeout,
            "close_timeout": self.close_timeout,
            "ping_interval": self.ping_interval,
            "ping_timeout": self.ping_timeout,
        }
        if self.headers:
            try:
                params = inspect.signature(websockets.connect).parameters
            except (TypeError, ValueError):
                params = {}
            if "additional_headers" in params:
                kwargs["additional_headers"] = self.headers
            else:
                kwargs["extra_headers"] = self.headers
        ws = await websockets.connect(self.ws_url(), **kwargs)
        return WsSession(ws)


class WsSession:
    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def __aenter__(self) -> "WsSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        await self._ws.close()

    async def send(self, payload: str | bytes) -> None:
        await self._ws.send(payload)

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(payload))

    async def recv(self) -> str | bytes:
        return await self._ws.recv()

    async def recv_json(self) -> Any:
        raw = await self.recv()
        if isinstance(raw, bytes):
            try:
                raw = gzip.decompress(raw).decode("utf-8")
            except Exception:
                raw = raw.decode("utf-8", errors="replace")
        return json.loads(raw)

    async def recv_message(self) -> WsMessage:
        return parse_ws_message(await self.recv_json())

    async def subscribe(
        self,
        request_id: Any,
        *,
        channel: str,
        symbol: Optional[str] = None,
        interval: Optional[str] = None,
        subaccount: Optional[str] = None,
        wallet: Optional[str] = None,
        asset: Optional[str] = None,
    ) -> None:
        await self.send_json(
            v1_subscribe(
                request_id,
                channel=channel,
                symbol=symbol,
                interval=interval,
                subaccount=subaccount,
                wallet=wallet,
                asset=asset,
            )
        )

    async def unsubscribe(
        self,
        request_id: Any,
        *,
        channel: str,
        symbol: Optional[str] = None,
        interval: Optional[str] = None,
        subaccount: Optional[str] = None,
        wallet: Optional[str] = None,
        asset: Optional[str] = None,
    ) -> None:
        await self.send_json(
            v1_unsubscribe(
                request_id,
                channel=channel,
                symbol=symbol,
                interval=interval,
                subaccount=subaccount,
                wallet=wallet,
                asset=asset,
            )
        )

    async def list(self, request_id: Any) -> None:
        await self.send_json(v1_list(request_id))

    async def post(self, request_id: Any, *, route: str, payload: dict[str, Any]) -> None:
        await self.send_json(v1_post(request_id, route=route, payload=payload))

    async def ping(self) -> None:
        await self.send_json(v1_ping())

    async def pong(self) -> None:
        await self.send_json(v1_pong())
