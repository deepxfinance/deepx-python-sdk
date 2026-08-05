from __future__ import annotations

from typing import Any, Optional

try:
    from substrateinterface import SubstrateInterface
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Missing dependency for Substrate access. Install with 'pip install substrate-interface'."
    ) from exc

from ._types import PerpPriceBounds


def get_perp_price_bounds(substrate_ws: str, market_id: int) -> PerpPriceBounds:
    """Fetch perp price bounds and trading constraints for a market."""
    # Use the shared factory: proxy env injection, ws timeouts, endpoint pools.
    # A bare SubstrateInterface(url=...) ignores http_proxy/https_proxy and
    # fails outright on flaky routes (devnet direct path drops the TLS
    # handshake with SSLEOFError).
    from . import _native_py

    substrate = _native_py._create_substrate(
        _native_py._get_substrate_interface_cls(), substrate_ws
    )
    market = _query_perp_market(substrate, market_id)

    mark_price = _get_int(market, "mark_price", "markPrice")
    max_bps = _get_int(market, "max_deviation_bps", "maxDeviationBps")
    base_decimal = _get_int(market, "base_decimal", "baseDecimal")
    order_spec = _get_field(market, "order_spec", "orderSpec")

    tick_size = _get_int(order_spec, "tick_size", "tickSize")
    step_size = _get_int(order_spec, "step_size", "stepSize")
    min_order_size = _get_int(order_spec, "min_qty", "minQty")
    min_notional = _get_optional_int(order_spec, "min_notional", "minNotional")

    lower = (mark_price * (10_000 - max_bps)) // 10_000
    upper = (mark_price * (10_000 + max_bps)) // 10_000

    return PerpPriceBounds(
        mark_price=mark_price,
        lower=lower,
        upper=upper,
        max_deviation_bps=max_bps,
        base_decimal=base_decimal,
        tick_size=tick_size,
        step_size=step_size,
        min_order_size=min_order_size,
        min_notional=min_notional,
    )


def _query_perp_market(substrate: SubstrateInterface, market_id: int) -> Any:
    module_candidates = [
        "PerpMarket",
        "perp_market",
        "PerpMarketPallet",
        "Perpmarket",
    ]
    storage_name = "PerpMarkets"
    last_err: Optional[Exception] = None
    for module in module_candidates:
        try:
            result = substrate.query(module, storage_name, [market_id])
            value = getattr(result, "value", None)
            if value is None:
                raise RuntimeError(f"perp market not found: {market_id}")
            return value
        except Exception as exc:  # pragma: no cover - best-effort fallbacks
            last_err = exc
            continue
    raise RuntimeError(f"failed to query PerpMarkets: {last_err}")


def _get_field(obj: Any, *keys: str) -> Any:
    if not isinstance(obj, dict):
        raise RuntimeError(f"unexpected storage value type: {type(obj)}")
    for key in keys:
        if key in obj:
            return obj[key]
    raise RuntimeError(f"missing field {keys} in {obj}")


def _get_int(obj: Any, *keys: str) -> int:
    value = _get_field(obj, *keys)
    return int(value)


def _get_optional_int(obj: Any, *keys: str) -> int | None:
    if not isinstance(obj, dict):
        raise RuntimeError(f"unexpected storage value type: {type(obj)}")
    for key in keys:
        if key in obj and obj[key] is not None:
            return int(obj[key])
    return None
