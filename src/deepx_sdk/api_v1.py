from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Optional

from .api import ApiClient
from ._rpc_transport import use_evm_rpc_config
from ._tx_config import merge_tx_config_kwargs
from ._abi import (
    normalize_address,
    normalize_bytes32,
)

_VALID_SORT = {"ASC", "DESC"}
_VALID_ORDER_SIDE = {"BUY": "Buy", "SELL": "Sell"}


def _validate_int_limit(name: str, limit: Optional[int], min_val: int, max_val: int) -> None:
    if limit is None:
        return
    if not (min_val <= limit <= max_val):
        raise ValueError(f"{name} must be between {min_val} and {max_val}")


def _validate_candle_limit(limit: Optional[int]) -> None:
    _validate_int_limit("limit", limit, 1, 500)


def _validate_oi_limit(limit: Optional[int]) -> None:
    _validate_int_limit("limit", limit, 1, 5000)


def _validate_orderbook_limit(limit: Optional[int]) -> None:
    _validate_int_limit("limit", limit, 1, 500)


def _validate_merge_level(merge_level: Optional[int]) -> None:
    if merge_level is None:
        return
    if not (0 <= merge_level <= 3):
        raise ValueError("merge_level must be between 0 and 3")


_DEFAULT_PERP_PRECOMPILE = "0x000000000000000000000000000000000000044E"
_DEFAULT_SPOT_PRECOMPILE = "0x000000000000000000000000000000000000044D"


def _optional_u128(value: Optional[int]) -> int | None:
    return None if value is None else int(value)


def _optional_u64(value: Optional[int]) -> int | None:
    return None if value is None else int(value)


def _perp_order_type(value: int, slippage: Optional[int] = None) -> Any:
    # On-chain OrderType is `Limit(TimeInForce) | Market(Option<u64>) | Stop`.
    #   0 Limit (GTC) -> {"Limit": "GTC"}
    #   1 Market      -> {"Market": <slippage u64 or None>}   (slippage in bps)
    #   2 Stop        -> "Stop"
    #   3 IOC         -> {"Limit": "IOC"}
    mapping = {
        0: lambda: {"Limit": "GTC"},
        1: lambda: {"Market": _optional_u64(slippage)},
        2: lambda: "Stop",
        3: lambda: {"Limit": "IOC"},
    }
    try:
        return mapping[int(value)]()
    except KeyError as exc:
        raise ValueError(f"invalid perp order_type: {value}") from exc


def _post_only_param(value: int) -> str:
    mapping = {0: "None", 1: "MustPostOnly", 2: "Adaptive"}
    try:
        return mapping[int(value)]
    except KeyError as exc:
        raise ValueError(f"invalid post_only: {value}") from exc


def _spot_slippage_u8(value: int) -> int:
    slippage = int(value)
    if slippage < 0 or slippage >= 100:
        raise ValueError("spot market slippage must be between 0 and 99")
    return slippage


def _spot_pair_hex(pair: str) -> str:
    return "0x" + normalize_bytes32(pair).hex()


def _spot_place_params(
    *,
    subaccount: str,
    pair: str,
    is_buy: bool,
    quote_amount: int,
    base_amount: int,
    order_type: Any,
    post_only: str,
    reduce_only: bool,
    cloid: Optional[int],
) -> dict[str, Any]:
    # On-chain `place_order` takes a single `params: SpotPlaceParams` arg.
    # `order_type` shares the perp OrderType enum: Limit(TimeInForce) |
    # Market(Option<u64> slippage); `cloid` is an optional client order id.
    return {
        "params": {
            "subaccount": normalize_address(subaccount),
            "pair": _spot_pair_hex(pair),
            "is_buy": bool(is_buy),
            "quote_amount": int(quote_amount),
            "base_amount": int(base_amount),
            "order_type": order_type,
            "post_only": post_only,
            "reduce_only": bool(reduce_only),
            "cloid": _optional_u64(cloid),
        }
    }


def _spot_cancel_params(
    *,
    subaccount: str,
    pair: str,
    order_id: int,
    is_buy: bool,
    fast_cancel: bool,
) -> dict[str, Any]:
    # On-chain `cancel_order` takes a single `params: SpotCancelParams` arg.
    return {
        "params": {
            "subaccount": normalize_address(subaccount),
            "pair": _spot_pair_hex(pair),
            "order_id": int(order_id),
            "is_buy": bool(is_buy),
            "cancel_reason": "UserCanceled",
            "fast_cancel": bool(fast_cancel),
        }
    }


def _camelize(key: str) -> str:
    if "_" not in key:
        return key
    head, *tail = key.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail if part)


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        out_key = _camelize(key)
        if isinstance(value, dict):
            clean[out_key] = json.dumps(value)
        elif isinstance(value, bool):
            clean[out_key] = "true" if value else "false"
        else:
            clean[out_key] = value
    return clean


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _require_value(name: str, value: Any) -> None:
    if _is_blank(value):
        raise ValueError(f"{name} is required")


def _validate_sort(sort: Optional[str]) -> None:
    if sort is None:
        return
    if sort.upper() not in _VALID_SORT:
        raise ValueError("sort must be 'ASC' or 'DESC'")


def _validate_start_end(start: Optional[int], end: Optional[int]) -> None:
    if start is not None and end is not None and start > end:
        raise ValueError("start must be <= end")


def _normalize_order_side(order_side: str) -> str:
    key = order_side.strip().upper()
    if key not in _VALID_ORDER_SIDE:
        raise ValueError("order_side must be Buy or Sell")
    return _VALID_ORDER_SIDE[key]


@dataclass
class V1Client:
    _client: ApiClient
    account: "AccountV1Client" = field(init=False, repr=False)
    chain_tx: "ChainTxV1Client" = field(init=False, repr=False)
    spot: "SpotV1Client" = field(init=False, repr=False)
    perp: "PerpV1Client" = field(init=False, repr=False)
    lending: "LendingV1Client" = field(init=False, repr=False)
    ws: "WsV1Client" = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.account = AccountV1Client(self._client)
        self.chain_tx = ChainTxV1Client(self._client)
        self.spot = SpotV1Client(self._client)
        self.perp = PerpV1Client(self._client)
        self.lending = LendingV1Client(self._client)
        self.ws = WsV1Client(self._client)

    def ping(self) -> Any:
        return self._client.request("GET", "/v1/ping")

    def time(self) -> Any:
        return self._client.request("GET", "/v1/time")


@dataclass
class WsV1Client:
    _client: ApiClient

    def websocket_url(self) -> str:
        base = str(self._client.ws_base_url).rstrip("/")
        return f"{base}/v1/ws"


@dataclass
class AccountV1Client:
    _client: ApiClient

    def wallet_subaccounts(self, *, address: str) -> Any:
        _require_value("address", address)
        return self._client.request(
            "GET",
            f"/v1/account/wallets/{address}/subaccounts",
        )

    def wallet_one_click_trading_accounts(self, *, address: str) -> Any:
        _require_value("address", address)
        return self._client.request(
            "GET",
            f"/v1/account/wallets/{address}/one-click-trading-accounts",
        )

    def subaccount_info(self, *, address: str) -> Any:
        _require_value("address", address)
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/info",
        )

    def subaccount_margin_ratio(self, *, address: str) -> Any:
        _require_value("address", address)
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/stats/margin-ratio",
        )

    def subaccount_perp_positions(
        self,
        *,
        address: str,
        symbol: Optional[str] = None,
    ) -> Any:
        _require_value("address", address)
        params = _clean_params({"symbol": symbol})
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/perp/positions",
            params=params,
        )

    def subaccount_perp_positions_history(
        self,
        *,
        address: str,
        symbol: Optional[str] = None,
        limit: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        sort: Optional[str] = None,
    ) -> Any:
        _require_value("address", address)
        _validate_int_limit("limit", limit, 1, 500)
        _validate_sort(sort)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "symbol": symbol,
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
                "sort": sort,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/perp/positions/history",
            params=params,
        )

    def subaccount_perp_orders(
        self,
        *,
        address: str,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        limit: Optional[int] = None,
        from_id: Optional[int] = None,
        cursor: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        sort: Optional[str] = None,
    ) -> Any:
        _require_value("address", address)
        _validate_int_limit("limit", limit, 1, 500)
        _validate_sort(sort)
        _validate_start_end(start_time, end_time)
        if side is not None:
            side = _normalize_order_side(side)
        params = _clean_params(
            {
                "symbol": symbol,
                "side": side,
                "limit": limit,
                "from_id": from_id,
                "cursor": cursor,
                "start_time": start_time,
                "end_time": end_time,
                "sort": sort,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/perp/orders",
            params=params,
        )

    def subaccount_perp_open_orders(
        self,
        *,
        address: str,
        symbol: Optional[str] = None,
    ) -> Any:
        _require_value("address", address)
        params = _clean_params({"symbol": symbol})
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/perp/orders/open",
            params=params,
        )

    def subaccount_perp_trades(
        self,
        *,
        address: str,
        symbol: Optional[str] = None,
        limit: Optional[int] = None,
        from_id: Optional[int] = None,
        cursor: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        sort: Optional[str] = None,
    ) -> Any:
        _require_value("address", address)
        _validate_int_limit("limit", limit, 1, 500)
        _validate_sort(sort)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "symbol": symbol,
                "limit": limit,
                "from_id": from_id,
                "cursor": cursor,
                "start_time": start_time,
                "end_time": end_time,
                "sort": sort,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/perp/trades",
            params=params,
        )

    def subaccount_perp_funding_payments(
        self,
        *,
        address: str,
        symbol: Optional[str] = None,
        limit: Optional[int] = None,
        from_id: Optional[int] = None,
        cursor: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        sort: Optional[str] = None,
    ) -> Any:
        _require_value("address", address)
        _validate_int_limit("limit", limit, 1, 500)
        _validate_sort(sort)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "symbol": symbol,
                "limit": limit,
                "from_id": from_id,
                "cursor": cursor,
                "start_time": start_time,
                "end_time": end_time,
                "sort": sort,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/perp/funding-payments",
            params=params,
        )

    def subaccount_spot_orders(
        self,
        *,
        address: str,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        limit: Optional[int] = None,
        from_id: Optional[int] = None,
        cursor: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        sort: Optional[str] = None,
    ) -> Any:
        _require_value("address", address)
        _validate_int_limit("limit", limit, 1, 500)
        _validate_sort(sort)
        _validate_start_end(start_time, end_time)
        if side is not None:
            side = _normalize_order_side(side)
        params = _clean_params(
            {
                "symbol": symbol,
                "side": side,
                "limit": limit,
                "from_id": from_id,
                "cursor": cursor,
                "start_time": start_time,
                "end_time": end_time,
                "sort": sort,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/spot/orders",
            params=params,
        )

    def subaccount_spot_open_orders(
        self,
        *,
        address: str,
        symbol: Optional[str] = None,
    ) -> Any:
        _require_value("address", address)
        params = _clean_params({"symbol": symbol})
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/spot/orders/open",
            params=params,
        )

    def subaccount_spot_trades(
        self,
        *,
        address: str,
        symbol: Optional[str] = None,
        limit: Optional[int] = None,
        from_id: Optional[int] = None,
        cursor: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        sort: Optional[str] = None,
    ) -> Any:
        _require_value("address", address)
        _validate_int_limit("limit", limit, 1, 500)
        _validate_sort(sort)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "symbol": symbol,
                "limit": limit,
                "from_id": from_id,
                "cursor": cursor,
                "start_time": start_time,
                "end_time": end_time,
                "sort": sort,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/spot/trades",
            params=params,
        )

    def subaccount_balances(self, *, address: str) -> Any:
        _require_value("address", address)
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/balances",
        )

    def subaccount_portfolio(self, *, address: str) -> Any:
        _require_value("address", address)
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/portfolio",
        )

    def subaccount_balance_changes(
        self,
        *,
        address: str,
        limit: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        from_id: Optional[int] = None,
        cursor: Optional[int] = None,
        change_type: Optional[str] = None,
    ) -> Any:
        _require_value("address", address)
        _validate_int_limit("limit", limit, 1, 500)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
                "cursor": cursor if cursor is not None else from_id,
                "change_type": change_type,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/balance-events",
            params=params,
        )

    def subaccount_liquidations(
        self,
        *,
        address: str,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        liquidator: Optional[str] = None,
        liquidation_type: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> Any:
        _require_value("address", address)
        _validate_int_limit("limit", limit, 1, 500)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "limit": limit,
                "cursor": cursor,
                "start_time": start_time,
                "end_time": end_time,
                "liquidator": liquidator,
                "liquidation_type": liquidation_type,
                "symbol": symbol,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/liquidations",
            params=params,
        )

    def perp_order_by_tx(self, *, tx_hash: str) -> Any:
        _require_value("tx_hash", tx_hash)
        return self._client.request(
            "GET",
            f"/v1/account/perp/orders/tx/{tx_hash}",
        )

    def spot_order_by_tx(self, *, tx_hash: str) -> Any:
        _require_value("tx_hash", tx_hash)
        return self._client.request(
            "GET",
            f"/v1/account/spot/orders/tx/{tx_hash}",
        )

    def perp_order_by_id(self, *, address: str, order_id: int) -> Any:
        _require_value("address", address)
        _require_value("order_id", order_id)
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/perp/orders/{order_id}",
        )

    def spot_order_by_id(self, *, address: str, order_id: int) -> Any:
        _require_value("address", address)
        _require_value("order_id", order_id)
        return self._client.request(
            "GET",
            f"/v1/account/subaccounts/{address}/spot/orders/{order_id}",
        )

    # -------------------------------------------------------------------------
    # Quota (wallet-level): query + backend-executed claim
    # -------------------------------------------------------------------------

    def wallet_quota(self, *, address: str) -> Any:
        """GET /v1/account/wallets/{address}/quota -> {claimable, remaining}."""
        _require_value("address", address)
        return self._client.request("GET", f"/v1/account/wallets/{address}/quota")

    def quota_summary(self, *, wallet: str) -> Any:
        """GET /internal/v1/account/quota/summary — earned/granted/pending quota
        and trade volumes, aggregated across the wallet's subaccounts."""
        _require_value("wallet", wallet)
        return self._client.request(
            "GET",
            "/internal/v1/account/quota/summary",
            params=_clean_params({"wallet": wallet}),
        )

    def claim_quota(
        self,
        *,
        wallet: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        private_key: Optional[str] = None,
    ) -> Any:
        """Create a quota claim (POST /v1/account/quota/claim).

        The wallet personal-signs a fixed three-line message; the backend
        reserves the claimable quota and submits `Quota.sudo_add_quota`
        on-chain asynchronously. Poll `quota_claim` / `wait_quota_claim` for
        the result. Idempotent per `idempotency_key` (a uuid is generated when
        omitted); one active claim per wallet.

        Response (raw object, no envelope) `status`: "pending" (claim created,
        see `claim.id`), an active-claim status when one already exists, or
        "noop" when there is nothing claimable (`claim` is null, `created` is
        false).
        """
        key = private_key or self._client.private_key
        if not key:
            raise ValueError("private_key is required to sign the quota claim")
        from eth_account import Account
        from eth_account.messages import encode_defunct

        account = Account.from_key(key)
        resolved_wallet = wallet or normalize_address(account.address)
        _require_value("wallet", resolved_wallet)
        key_id = idempotency_key or str(uuid.uuid4())
        # The backend verifies this message byte-for-byte — keep it exact.
        message = (
            "DeepX quota claim\n"
            f"Wallet: {resolved_wallet}\n"
            f"Idempotency-Key: {key_id}"
        )
        signature = account.sign_message(encode_defunct(text=message)).signature.hex()
        if not signature.startswith("0x"):
            signature = "0x" + signature
        return self._client.request(
            "POST",
            "/v1/account/quota/claim",
            json_body={
                "wallet": resolved_wallet,
                "idempotencyKey": key_id,
                "message": message,
                "signature": signature,
            },
        )

    def quota_claim(self, *, claim_id: int) -> Any:
        """GET /internal/v1/account/quota/claim?id=... — claim task status."""
        _require_value("claim_id", claim_id)
        return self._client.request(
            "GET",
            "/internal/v1/account/quota/claim",
            params=_clean_params({"id": claim_id}),
        )

    def wait_quota_claim(
        self,
        *,
        claim_id: int,
        timeout_s: float = 60.0,
        interval_s: float = 2.0,
    ) -> Any:
        """Poll `quota_claim` until the claim is confirmed or fails on-chain.

        Transient REST errors (rate limits, 5xx) are retried until the
        deadline; only a terminal claim status or the timeout stops the loop.
        """
        from ._errors import RESTError, RPCError

        deadline = time.monotonic() + timeout_s
        last_status = "unknown"
        while True:
            try:
                result = self.quota_claim(claim_id=claim_id)
            except (RESTError, RPCError):
                result = None
            claim = result.get("data") if isinstance(result, dict) else None
            claim = claim or {}
            status = str(claim.get("status", "")).lower() or last_status
            last_status = status
            if status == "confirmed":
                return result
            if status == "failed":
                raise RuntimeError(
                    f"quota claim {claim_id} failed: {claim.get('lastError')}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"quota claim {claim_id} not confirmed within {timeout_s}s "
                    f"(last status: {last_status})"
                )
            time.sleep(interval_s)


@dataclass
class SpotV1Client:
    _client: ApiClient

    def markets(self, *, symbols: Optional[str] = None) -> Any:
        params = _clean_params({"symbols": symbols})
        return self._client.request("GET", "/v1/spot/markets", params=params)

    def market(self, *, symbol: str) -> Any:
        _require_value("symbol", symbol)
        return self._client.request("GET", f"/v1/spot/markets/{symbol}")

    def candles(
        self,
        *,
        symbol: str,
        interval: str,
        limit: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        price_type: Optional[str] = None,
    ) -> Any:
        _require_value("symbol", symbol)
        _require_value("interval", interval)
        _validate_candle_limit(limit)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "interval": interval,
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
                "price_type": price_type,
            }
        )
        return self._client.request("GET", f"/v1/spot/markets/{symbol}/candles", params=params)

    def trades(
        self,
        *,
        symbol: str,
        limit: Optional[int] = None,
        from_id: Optional[int] = None,
        cursor: Optional[int] = None,
    ) -> Any:
        _require_value("symbol", symbol)
        _validate_int_limit("limit", limit, 1, 500)
        params = _clean_params(
            {
                "limit": limit,
                "from_id": from_id,
                "cursor": cursor,
            }
        )
        return self._client.request("GET", f"/v1/spot/markets/{symbol}/trades", params=params)

    def orderbook(
        self,
        *,
        symbol: str,
        limit: Optional[int] = None,
        merge_level: Optional[int] = None,
    ) -> Any:
        _require_value("symbol", symbol)
        _validate_orderbook_limit(limit)
        _validate_merge_level(merge_level)
        params = _clean_params({"limit": limit, "merge_level": merge_level})
        return self._client.request(
            "GET",
            f"/v1/spot/markets/{symbol}/orderbook",
            params=params,
        )


@dataclass
class PerpV1Client:
    _client: ApiClient

    def markets(self) -> Any:
        return self._client.request("GET", "/v1/perp/markets")

    def market(self, *, symbol: str) -> Any:
        _require_value("symbol", symbol)
        return self._client.request("GET", f"/v1/perp/markets/{symbol}")

    def candles(
        self,
        *,
        symbol: str,
        interval: str,
        limit: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        price_type: Optional[str] = None,
    ) -> Any:
        _require_value("symbol", symbol)
        _require_value("interval", interval)
        _validate_candle_limit(limit)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "interval": interval,
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
                "price_type": price_type,
            }
        )
        return self._client.request("GET", f"/v1/perp/markets/{symbol}/candles", params=params)

    def trades(
        self,
        *,
        symbol: str,
        limit: Optional[int] = None,
        from_id: Optional[int] = None,
        cursor: Optional[int] = None,
    ) -> Any:
        _require_value("symbol", symbol)
        _validate_int_limit("limit", limit, 1, 500)
        params = _clean_params(
            {
                "limit": limit,
                "from_id": from_id,
                "cursor": cursor,
            }
        )
        return self._client.request("GET", f"/v1/perp/markets/{symbol}/trades", params=params)

    def orderbook(
        self,
        *,
        symbol: str,
        limit: Optional[int] = None,
        merge_level: Optional[int] = None,
    ) -> Any:
        _require_value("symbol", symbol)
        _validate_orderbook_limit(limit)
        _validate_merge_level(merge_level)
        params = _clean_params({"limit": limit, "merge_level": merge_level})
        return self._client.request(
            "GET",
            f"/v1/perp/markets/{symbol}/orderbook",
            params=params,
        )

    def open_interest(self, *, symbol: str) -> Any:
        _require_value("symbol", symbol)
        return self._client.request("GET", f"/v1/perp/markets/{symbol}/open-interest")

    def open_interest_history(
        self,
        *,
        symbol: str,
        interval: str,
        limit: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        sort: Optional[str] = None,
    ) -> Any:
        _require_value("symbol", symbol)
        _require_value("interval", interval)
        _validate_oi_limit(limit)
        _validate_sort(sort)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "interval": interval,
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
                "sort": sort,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/perp/markets/{symbol}/open-interest/history",
            params=params,
        )

    def funding_rate(self, *, symbol: str) -> Any:
        _require_value("symbol", symbol)
        return self._client.request("GET", f"/v1/perp/markets/{symbol}/funding-rate")

    def funding_rate_history(
        self,
        *,
        symbol: str,
        interval: Optional[str] = None,
        limit: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> Any:
        _require_value("symbol", symbol)
        if interval is None:
            interval = "1m"
        _validate_int_limit("limit", limit, 1, 5000)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "interval": interval,
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/perp/markets/{symbol}/funding-rate/history",
            params=params,
        )

    def long_short_ratio(self, *, symbol: str) -> Any:
        _require_value("symbol", symbol)
        return self._client.request("GET", f"/v1/perp/markets/{symbol}/long-short-ratio")

    def long_short_ratio_history(
        self,
        *,
        symbol: str,
        interval: Optional[str] = None,
        limit: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> Any:
        _require_value("symbol", symbol)
        if interval is None:
            interval = "1m"
        _validate_int_limit("limit", limit, 1, 5000)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "interval": interval,
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/perp/markets/{symbol}/long-short-ratio/history",
            params=params,
        )


@dataclass
class LendingV1Client:
    _client: ApiClient

    def markets(self) -> Any:
        return self._client.request("GET", "/v1/lending/markets")

    def market(self, *, asset: str) -> Any:
        _require_value("asset", asset)
        return self._client.request("GET", f"/v1/lending/markets/{asset}")

    def market_status(self, *, asset: Optional[str] = None) -> Any:
        if asset is None:
            return self._client.request("GET", "/v1/lending/markets/status")
        _require_value("asset", asset)
        return self._client.request("GET", f"/v1/lending/markets/{asset}/status")

    def market_status_history(
        self,
        *,
        asset: str,
        interval: Optional[str] = None,
        time_frame: Optional[str] = None,
        market_id: Optional[int] = None,
        limit: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        sort: Optional[str] = None,
    ) -> Any:
        _require_value("asset", asset)
        if interval is None and time_frame is None:
            interval = "1m"
        _validate_int_limit("limit", limit, 1, 5000)
        _validate_sort(sort)
        _validate_start_end(start_time, end_time)
        params = _clean_params(
            {
                "interval": interval,
                "time_frame": time_frame,
                "market_id": market_id,
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
                "sort": sort,
            }
        )
        return self._client.request(
            "GET",
            f"/v1/lending/markets/{asset}/status/history",
            params=params,
        )


@dataclass
class ChainTxV1Client:
    _client: ApiClient

    def __getattribute__(self, name: str):
        attr = object.__getattribute__(self, name)
        if name.startswith("_") or not callable(attr):
            return attr

        @wraps(attr)
        def wrapped(*args, **kwargs):
            kwargs = merge_tx_config_kwargs(attr, kwargs)
            return attr(*args, **kwargs)

        return wrapped

    @staticmethod
    def _resolve_required_str(
        *,
        name: str,
        override: Optional[str],
        fallback: Optional[str],
        default: Optional[str] = None,
    ) -> str:
        value = override
        if _is_blank(value):
            value = fallback
        if _is_blank(value):
            value = default
        _require_value(name, value)
        return str(value).strip()

    def _build_signed_tx(
        self,
        *,
        evm_rpc_url: str,
        private_key: str,
        precompile_address: str,
        data: bytes,
        chain_id: Optional[int],
        gas_limit: Optional[int],
        max_fee_per_gas: Optional[int],
        max_priority_fee_per_gas: Optional[int],
        use_legacy: bool,
        nonce_ms: Optional[int],
        use_timestamp_nonce: bool,
    ) -> Any:
        try:
            from ._native_py import build_signed_tx as build_signed_tx_py
        except Exception as exc:
            raise RuntimeError(
                "Python signing backend unavailable; install required Python deps "
                "(eth-account, eth-utils, and friends)"
            ) from exc

        with use_evm_rpc_config(
            user_agent=self._client.evm_rpc_user_agent,
            headers=self._client.evm_rpc_headers,
            timeout_s=self._client.evm_rpc_timeout,
            endpoint_pool=self._client._evm_rpc_pool,
        ):
            return build_signed_tx_py(
                evm_rpc_url=evm_rpc_url,
                private_key=private_key,
                precompile_address=precompile_address,
                data_hex="0x" + data.hex(),
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_timestamp_nonce=use_timestamp_nonce,
            )

    def _build_signed_extrinsic(
        self,
        *,
        signed_tx: str,
        signer: str,
        substrate_ws: str,
    ) -> str:
        try:
            from ._native_py import build_ethereum_transact_extrinsic
        except Exception as exc:
            raise RuntimeError(
                "Python substrate extrinsic builder unavailable; install required Python deps "
                "(substrate-interface, eth-account, eth-utils, and friends)"
            ) from exc
        return build_ethereum_transact_extrinsic(
            substrate_ws=substrate_ws,
            signed_tx_hex=signed_tx,
            signer=signer,
        )

    def _submit_signed_tx(self, *, path: str, signed_extrinsic: str) -> Any:
        _require_value("signed_extrinsic", signed_extrinsic)
        payload = _clean_params(
            {
                "signed_extrinsic": signed_extrinsic,
            }
        )
        return self._client.request(
            "POST",
            path,
            json_body=payload,
        )

    def _build_signed_pallet_call_extrinsic(
        self,
        *,
        private_key: str,
        call_module: str,
        call_function: str,
        call_params: dict[str, Any],
        nonce_ms: Optional[int] = None,
    ) -> str:
        try:
            from ._native_py import build_signed_pallet_call_extrinsic
        except Exception as exc:
            raise RuntimeError(
                "Python substrate extrinsic builder unavailable; install required Python deps "
                "(substrate-interface and friends)"
            ) from exc
        return build_signed_pallet_call_extrinsic(
            substrate_ws=self._client.substrate_ws,
            private_key=private_key,
            call_module=call_module,
            call_function=call_function,
            call_params=call_params,
            nonce_ms=nonce_ms,
        )

    def _sign_pallet_call_and_submit(
        self,
        *,
        path: str,
        private_key: Optional[str],
        call_module: str,
        call_function: str,
        call_params: dict[str, Any],
        nonce_ms: Optional[int] = None,
    ) -> Any:
        resolved_private_key = self._resolve_required_str(
            name="private_key",
            override=private_key,
            fallback=self._client.private_key,
        )
        signed_extrinsic = self._build_signed_pallet_call_extrinsic(
            private_key=resolved_private_key,
            call_module=call_module,
            call_function=call_function,
            call_params=call_params,
            nonce_ms=nonce_ms if nonce_ms is not None else self._client.nonce_ms,
        )
        return self._submit_signed_tx(path=path, signed_extrinsic=signed_extrinsic)

    def _sign_and_submit(
        self,
        *,
        path: str,
        data: bytes,
        evm_rpc_url: Optional[str],
        private_key: Optional[str],
        precompile_address: Optional[str],
        fallback_precompile: Optional[str],
        default_precompile: str,
        chain_id: Optional[int],
        gas_limit: Optional[int],
        max_fee_per_gas: Optional[int],
        max_priority_fee_per_gas: Optional[int],
        use_legacy: Optional[bool],
        nonce_ms: Optional[int],
        use_timestamp_nonce: bool,
    ) -> Any:
        resolved_evm_rpc_url = self._resolve_required_str(
            name="evm_rpc_url",
            override=evm_rpc_url,
            fallback=self._client.evm_rpc_url,
        )
        resolved_private_key = self._resolve_required_str(
            name="private_key",
            override=private_key,
            fallback=self._client.private_key,
        )
        resolved_precompile = self._resolve_required_str(
            name="precompile_address",
            override=precompile_address,
            fallback=fallback_precompile,
            default=default_precompile,
        )

        signed = self._build_signed_tx(
            evm_rpc_url=resolved_evm_rpc_url,
            private_key=resolved_private_key,
            precompile_address=resolved_precompile,
            data=data,
            chain_id=chain_id if chain_id is not None else self._client.chain_id,
            gas_limit=gas_limit if gas_limit is not None else self._client.gas_limit,
            max_fee_per_gas=(
                max_fee_per_gas if max_fee_per_gas is not None else self._client.max_fee_per_gas
            ),
            max_priority_fee_per_gas=(
                max_priority_fee_per_gas
                if max_priority_fee_per_gas is not None
                else self._client.max_priority_fee_per_gas
            ),
            use_legacy=self._client.use_legacy if use_legacy is None else use_legacy,
            nonce_ms=nonce_ms if nonce_ms is not None else self._client.nonce_ms,
            use_timestamp_nonce=use_timestamp_nonce,
        )

        return self._submit_signed_tx(
            path=path,
            signed_extrinsic=getattr(signed, "signed_extrinsic", None)
            or self._build_signed_extrinsic(
                signed_tx=signed.signed_tx,
                signer=signed.signer,
                substrate_ws=self._client.substrate_ws,
            ),
        )

    # -------------------------------------------------------------------------
    # Perp orders
    # -------------------------------------------------------------------------

    def place_perp_order_limit(
        self,
        *,
        market_id: int,
        is_long: bool,
        size: int,
        price: int,
        take_profit: Optional[int] = None,
        stop_loss: Optional[int] = None,
        reduce_only: bool = False,
        post_only: int = 0,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        return self.place_perp_order(
            market_id=market_id,
            is_long=is_long,
            size=size,
            price=price,
            order_type=0,
            take_profit=take_profit,
            stop_loss=stop_loss,
            reduce_only=reduce_only,
            post_only=post_only,
            cloid=cloid,
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile_address,
            subaccount=subaccount,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce_ms=nonce_ms,
        )

    def place_perp_order_market(
        self,
        *,
        market_id: int,
        is_long: bool,
        size: int,
        slippage: Optional[int] = None,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        return self.place_perp_order(
            market_id=market_id,
            is_long=is_long,
            size=size,
            price=0,
            order_type=1,
            slippage=slippage,
            take_profit=None,
            stop_loss=None,
            reduce_only=reduce_only,
            post_only=0,
            cloid=cloid,
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile_address,
            subaccount=subaccount,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce_ms=nonce_ms,
        )

    def place_perp_order_ioc(
        self,
        *,
        market_id: int,
        is_long: bool,
        size: int,
        price: int,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        return self.place_perp_order(
            market_id=market_id,
            is_long=is_long,
            size=size,
            price=price,
            order_type=3,
            take_profit=None,
            stop_loss=None,
            reduce_only=reduce_only,
            post_only=0,
            cloid=cloid,
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile_address,
            subaccount=subaccount,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce_ms=nonce_ms,
        )

    def place_perp_order(
        self,
        *,
        market_id: int,
        is_long: bool,
        size: int,
        price: int,
        order_type: int,
        slippage: Optional[int] = None,
        take_profit: Optional[int] = None,
        stop_loss: Optional[int] = None,
        reduce_only: bool = False,
        post_only: int = 0,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        # On-chain `place_order` takes a single `params: PerpPlaceParams` arg.
        # Leverage is not a per-order param — set it via set_global_leverage /
        # set_per_market_leverage first (chain client path).
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/placePerpOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="PerpMarket",
            call_function="place_order",
            call_params={
                "params": {
                    "subaccount": normalize_address(resolved_subaccount),
                    "market_id": int(market_id),
                    "is_long": bool(is_long),
                    "size": int(size),
                    "price": int(price),
                    "order_type": _perp_order_type(order_type, slippage),
                    "take_profit": _optional_u128(take_profit),
                    "stop_loss": _optional_u128(stop_loss),
                    "reduce_only": bool(reduce_only),
                    "post_only": _post_only_param(post_only),
                    "cloid": _optional_u64(cloid),
                }
            },
        )

    def cancel_perp_order(
        self,
        *,
        market_id: int,
        order_id: int,
        fast_cancel: bool = False,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        # On-chain `cancel_order` takes a single `params: PerpCancelParams` arg.
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/cancelPerpOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="PerpMarket",
            call_function="cancel_order",
            call_params={
                "params": {
                    "subaccount": normalize_address(resolved_subaccount),
                    "order_id": int(order_id),
                    "market_id": int(market_id),
                    "cancel_reason": "UserCanceled",
                    "fast_cancel": bool(fast_cancel),
                }
            },
        )

    def close_position_limit(
        self,
        *,
        market_id: int,
        price: int,
        slippage: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        return self.close_position(
            market_id=market_id,
            price=price,
            slippage=slippage,
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile_address,
            subaccount=subaccount,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce=nonce,
        )

    def close_position(
        self,
        *,
        market_id: int,
        price: int,
        slippage: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/closePosition",
            private_key=private_key,
            nonce_ms=nonce,
            call_module="PerpMarket",
            call_function="close_position",
            call_params={
                "subaccount": normalize_address(resolved_subaccount),
                "market_id": int(market_id),
                "price": int(price),
                "slippage": _optional_u64(slippage),
            },
        )

    def close_position_market(
        self,
        *,
        market_id: int,
        slippage: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        return self.close_position(
            market_id=market_id,
            price=0,
            slippage=slippage,
            evm_rpc_url=evm_rpc_url,
            private_key=private_key,
            precompile_address=precompile_address,
            subaccount=subaccount,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce=nonce,
        )

    # -------------------------------------------------------------------------
    # Spot orders
    # -------------------------------------------------------------------------

    def place_spot_order_buy(
        self,
        *,
        pair: str,
        quote_amount: int,
        base_amount: int,
        post_only: int = 0,
        reduce_only: bool = False,
        slippage: Optional[int] = None,
        auto_cancel: bool = False,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = slippage, auto_cancel, wait_for_finalized
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/placeSpotOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="SpotMarket",
            call_function="place_order",
            call_params=_spot_place_params(
                subaccount=resolved_subaccount,
                pair=pair,
                is_buy=True,
                quote_amount=quote_amount,
                base_amount=base_amount,
                order_type={"Limit": "GTC"},
                post_only=_post_only_param(post_only),
                reduce_only=reduce_only,
                cloid=cloid,
            ),
        )

    def place_spot_order_sell(
        self,
        *,
        pair: str,
        quote_amount: int,
        base_amount: int,
        post_only: int = 0,
        reduce_only: bool = False,
        slippage: Optional[int] = None,
        auto_cancel: bool = False,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = slippage, auto_cancel, wait_for_finalized
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/placeSpotOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="SpotMarket",
            call_function="place_order",
            call_params=_spot_place_params(
                subaccount=resolved_subaccount,
                pair=pair,
                is_buy=False,
                quote_amount=quote_amount,
                base_amount=base_amount,
                order_type={"Limit": "GTC"},
                post_only=_post_only_param(post_only),
                reduce_only=reduce_only,
                cloid=cloid,
            ),
        )

    def place_spot_order_buy_ioc(
        self,
        *,
        pair: str,
        quote_amount: int,
        base_amount: int,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/placeSpotOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="SpotMarket",
            call_function="place_order",
            call_params=_spot_place_params(
                subaccount=resolved_subaccount,
                pair=pair,
                is_buy=True,
                quote_amount=quote_amount,
                base_amount=base_amount,
                order_type={"Limit": "IOC"},
                post_only="None",
                reduce_only=reduce_only,
                cloid=cloid,
            ),
        )

    def place_spot_order_sell_ioc(
        self,
        *,
        pair: str,
        quote_amount: int,
        base_amount: int,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/placeSpotOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="SpotMarket",
            call_function="place_order",
            call_params=_spot_place_params(
                subaccount=resolved_subaccount,
                pair=pair,
                is_buy=False,
                quote_amount=quote_amount,
                base_amount=base_amount,
                order_type={"Limit": "IOC"},
                post_only="None",
                reduce_only=reduce_only,
                cloid=cloid,
            ),
        )

    def place_spot_market_order_buy_without_price(
        self,
        *,
        pair: str,
        quote_amount: int,
        base_amount: int,
        auto_cancel: bool = False,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        _ = auto_cancel  # no longer exists on-chain; kept for signature compatibility
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/placeSpotOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="SpotMarket",
            call_function="place_order",
            call_params=_spot_place_params(
                subaccount=resolved_subaccount,
                pair=pair,
                is_buy=True,
                quote_amount=quote_amount,
                base_amount=base_amount,
                order_type={"Market": None},
                post_only="None",
                reduce_only=reduce_only,
                cloid=cloid,
            ),
        )

    def place_spot_market_order_buy_with_price(
        self,
        *,
        pair: str,
        quote_amount: int,
        base_amount: int,
        slippage: int,
        auto_cancel: bool = False,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        _ = auto_cancel  # no longer exists on-chain; kept for signature compatibility
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/placeSpotOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="SpotMarket",
            call_function="place_order",
            call_params=_spot_place_params(
                subaccount=resolved_subaccount,
                pair=pair,
                is_buy=True,
                quote_amount=quote_amount,
                base_amount=base_amount,
                order_type={"Market": _spot_slippage_u8(slippage)},
                post_only="None",
                reduce_only=reduce_only,
                cloid=cloid,
            ),
        )

    def place_spot_market_order_sell_without_price(
        self,
        *,
        pair: str,
        quote_amount: int,
        base_amount: int,
        auto_cancel: bool = False,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        _ = auto_cancel  # no longer exists on-chain; kept for signature compatibility
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/placeSpotOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="SpotMarket",
            call_function="place_order",
            call_params=_spot_place_params(
                subaccount=resolved_subaccount,
                pair=pair,
                is_buy=False,
                quote_amount=quote_amount,
                base_amount=base_amount,
                order_type={"Market": None},
                post_only="None",
                reduce_only=reduce_only,
                cloid=cloid,
            ),
        )

    def place_spot_market_order_sell_with_price(
        self,
        *,
        pair: str,
        quote_amount: int,
        base_amount: int,
        slippage: int,
        auto_cancel: bool = False,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        _ = auto_cancel  # no longer exists on-chain; kept for signature compatibility
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/placeSpotOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="SpotMarket",
            call_function="place_order",
            call_params=_spot_place_params(
                subaccount=resolved_subaccount,
                pair=pair,
                is_buy=False,
                quote_amount=quote_amount,
                base_amount=base_amount,
                order_type={"Market": _spot_slippage_u8(slippage)},
                post_only="None",
                reduce_only=reduce_only,
                cloid=cloid,
            ),
        )

    def cancel_spot_order_buy(
        self,
        *,
        pair: str,
        order_id: int,
        fast_cancel: bool = False,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/cancelSpotOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="SpotMarket",
            call_function="cancel_order",
            call_params=_spot_cancel_params(
                subaccount=resolved_subaccount,
                pair=pair,
                order_id=order_id,
                is_buy=True,
                fast_cancel=fast_cancel,
            ),
        )

    def cancel_spot_order_sell(
        self,
        *,
        pair: str,
        order_id: int,
        fast_cancel: bool = False,
        evm_rpc_url: Optional[str] = None,
        private_key: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
    ) -> Any:
        _ = wait_for_finalized
        resolved_subaccount = self._resolve_required_str(
            name="subaccount",
            override=subaccount,
            fallback=self._client.subaccount,
        )
        return self._sign_pallet_call_and_submit(
            path="/v1/chain/tx/cancelSpotOrder",
            private_key=private_key,
            nonce_ms=nonce_ms,
            call_module="SpotMarket",
            call_function="cancel_order",
            call_params=_spot_cancel_params(
                subaccount=resolved_subaccount,
                pair=pair,
                order_id=order_id,
                is_buy=False,
                fast_cancel=fast_cancel,
            ),
        )
