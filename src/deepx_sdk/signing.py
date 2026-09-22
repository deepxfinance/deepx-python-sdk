"""Standalone order signing. Reads metadata but never submits a transaction."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from ._async_encoder import (
    ExtrinsicEncoder,
    TimestampNonceAllocator,
    _encode_pallet_call_sync,
)
from ._errors import ValidationError
from ._perp_market import _perp_cancel_params, _perp_place_params
from ._spot_market import (
    _post_only_param,
    _spot_cancel_params,
    _spot_order_type_param,
    _spot_place_params,
)
from .client import _normalize_order_type, _normalize_spot_side


@dataclass(frozen=True)
class SignedExtrinsic:
    """Locally signed transaction; its existence does not imply chain acceptance."""

    signed_extrinsic: str
    tx_hash: str
    nonce: int
    runtime_version: int


_nonce_lock = threading.Lock()
_nonce_high_water: dict[tuple[str, bytes], int] = {}


def _build_signed(
    *, substrate_ws: str, private_key: str, call_module: str,
    call_function: str, call_params: dict, nonce_ms: int | None,
    timeout_ms: int | None,
) -> SignedExtrinsic:
    # Reuse the ticket encoder's frozen metadata and signing implementation.
    # Loading closes the RPC connection before any local signing takes place.
    encoder = ExtrinsicEncoder(substrate_ws, private_key, timeout_ms=timeout_ms)
    snapshot = encoder._load_snapshot()
    chain_time = snapshot.estimated_chain_time_ms(time.monotonic_ns())
    allocator = TimestampNonceAllocator(lambda: chain_time)
    key = (snapshot.substrate.get_block_hash(0), bytes(snapshot.keypair.public_key))
    with _nonce_lock:
        if nonce_ms is None:
            candidate = max(chain_time, _nonce_high_water.get(key, -1) + 1)
        else:
            if isinstance(nonce_ms, bool) or not isinstance(nonce_ms, int):
                raise ValidationError("nonce_ms must be an integer")
            candidate = nonce_ms
        nonce = allocator.reserve(candidate)
        _nonce_high_water[key] = max(nonce, _nonce_high_water.get(key, -1))
    encoded = _encode_pallet_call_sync(snapshot, call_module, call_function, call_params, nonce)
    return SignedExtrinsic(
        signed_extrinsic=encoded.data_hex, tx_hash=encoded.tx_hash,
        nonce=encoded.nonce, runtime_version=encoded.runtime_version,
    )


def build_signed_perp_order(
    *, substrate_ws: str, private_key: str, subaccount: str, market_id: int,
    is_long: bool, size: int, price: int | None = None,
    order_type: str | int = "limit", slippage: int | None = None,
    take_profit: int | None = None, stop_loss: int | None = None,
    reduce_only: bool = False, post_only: int = 0,
    nonce_ms: int | None = None, timeout_ms: int | None = None,
) -> SignedExtrinsic:
    """Sign a perp order using chain integer units; do not submit it.

    Market orders use price=0; limit, IOC and stop orders require price.
    Nonces are timestamp milliseconds, not sequential account nonces.
    """
    kind = _normalize_order_type(order_type)
    if kind == "market":
        if price not in (None, 0):
            raise ValueError("market orders require price=0 or None")
        price = 0
    elif price is None:
        raise ValueError("price is required for limit, IOC and stop orders")
    if kind != "limit" and post_only != 0:
        raise ValueError("post_only is only supported for limit orders")
    params = _perp_place_params(
        subaccount=subaccount, market_id=market_id, is_long=is_long, size=size,
        price=price, order_type={"limit": 0, "market": 1, "stop": 2, "ioc": 3}[kind],
        slippage=slippage, take_profit=take_profit, stop_loss=stop_loss,
        reduce_only=reduce_only, post_only=post_only, cloid=None,
    )
    return _build_signed(
        substrate_ws=substrate_ws, private_key=private_key, call_module="PerpMarket",
        call_function="place_order", call_params={"params": params},
        nonce_ms=nonce_ms, timeout_ms=timeout_ms,
    )


def build_signed_spot_order(
    *, substrate_ws: str, private_key: str, subaccount: str, pair: str,
    side: str | bool, quote_amount: int, base_amount: int,
    order_type: str | int = "limit", slippage: int | None = None,
    reduce_only: bool = False, post_only: int = 0,
    nonce_ms: int | None = None, timeout_ms: int | None = None,
) -> SignedExtrinsic:
    """Sign a spot order using base/quote token base units; do not submit it."""
    kind = _normalize_order_type(order_type)
    if kind != "limit" and post_only != 0:
        raise ValueError("post_only is only supported for limit orders")
    params = _spot_place_params(
        subaccount=subaccount, pair=pair, is_buy=_normalize_spot_side(side) == "buy",
        quote_amount=quote_amount, base_amount=base_amount,
        order_type=_spot_order_type_param(
            {"limit": 0, "market": 1, "stop": 2, "ioc": 3}[kind], slippage,
        ),
        post_only=_post_only_param(post_only), reduce_only=reduce_only, cloid=None,
    )
    return _build_signed(
        substrate_ws=substrate_ws, private_key=private_key, call_module="SpotMarket",
        call_function="place_order", call_params=params,
        nonce_ms=nonce_ms, timeout_ms=timeout_ms,
    )


def build_signed_perp_cancel(
    *, substrate_ws: str, private_key: str, subaccount: str, market_id: int,
    order_id: int, fast_cancel: bool = False,
    nonce_ms: int | None = None, timeout_ms: int | None = None,
) -> SignedExtrinsic:
    """Sign a perp cancellation for a u64 order ID; do not submit it."""
    return _build_signed(
        substrate_ws=substrate_ws, private_key=private_key, call_module="PerpMarket",
        call_function="cancel_order", call_params=_perp_cancel_params(
            subaccount=subaccount, market_id=market_id,
            order_id=order_id, fast_cancel=fast_cancel,
        ),
        nonce_ms=nonce_ms, timeout_ms=timeout_ms,
    )


def build_signed_spot_cancel(
    *, substrate_ws: str, private_key: str, subaccount: str, pair: str,
    side: str | bool, order_id: int, fast_cancel: bool = False,
    nonce_ms: int | None = None, timeout_ms: int | None = None,
) -> SignedExtrinsic:
    """Sign a spot cancellation for a u64 order ID; do not submit it."""
    return _build_signed(
        substrate_ws=substrate_ws, private_key=private_key, call_module="SpotMarket",
        call_function="cancel_order", call_params=_spot_cancel_params(
            subaccount=subaccount, pair=pair, order_id=order_id,
            is_buy=_normalize_spot_side(side) == "buy", fast_cancel=fast_cancel,
        ),
        nonce_ms=nonce_ms, timeout_ms=timeout_ms,
    )


__all__ = [
    "SignedExtrinsic", "build_signed_perp_order", "build_signed_spot_order",
    "build_signed_perp_cancel", "build_signed_spot_cancel",
]
