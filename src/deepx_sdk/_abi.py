from __future__ import annotations

from typing import Optional, Sequence

try:
    from eth_abi import decode as abi_decode
    from eth_abi import encode as abi_encode
    from eth_utils import keccak
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Missing dependencies for ABI encoding. Install with 'pip install eth-abi eth-utils'."
    ) from exc


def encode_perp_place_order(
    *,
    subaccount: str,
    market_id: int,
    is_long: bool,
    size: int,
    price: int,
    order_type: int,
    leverage: int,
    take_profit: Optional[int],
    stop_loss: Optional[int],
    reduce_only: bool,
    post_only: int,
) -> bytes:
    signature = (
        "placePerpOrder(address,uint16,bool,uint128,uint128,uint8,uint8,uint128,uint128,bool,uint8)"
    )
    types = [
        "address",
        "uint16",
        "bool",
        "uint128",
        "uint128",
        "uint8",
        "uint8",
        "uint128",
        "uint128",
        "bool",
        "uint8",
    ]
    args = [
        normalize_address(subaccount),
        market_id,
        is_long,
        size,
        price,
        order_type,
        leverage,
        take_profit or 0,
        stop_loss or 0,
        reduce_only,
        post_only,
    ]
    return _encode_call(signature, types, args)


def encode_perp_cancel_order(*, subaccount: str, market_id: int, order_id: int) -> bytes:
    signature = "cancelOrder(address,uint16,uint32)"
    types = ["address", "uint16", "uint32"]
    args = [normalize_address(subaccount), market_id, order_id]
    return _encode_call(signature, types, args)


def encode_perp_close_position(
    *,
    subaccount: str,
    market_id: int,
    price: int,
    slippage: Optional[int],
) -> bytes:
    signature = "closePosition(address,uint16,uint128,uint64)"
    types = ["address", "uint16", "uint128", "uint64"]
    args = [normalize_address(subaccount), market_id, price, slippage or 0]
    return _encode_call(signature, types, args)


def encode_perp_set_profit_and_loss_point(
    *,
    subaccount: str,
    market_id: int,
    take_profit_point: Optional[int],
    stop_loss_point: Optional[int],
) -> bytes:
    signature = "setProfitAndLossPoint(address,uint16,uint128,uint128)"
    types = ["address", "uint16", "uint128", "uint128"]
    args = [
        normalize_address(subaccount),
        market_id,
        take_profit_point or 0,
        stop_loss_point or 0,
    ]
    return _encode_call(signature, types, args)


def encode_spot_place_order(
    *,
    subaccount: str,
    pair: str,
    is_buy: bool,
    quote_amount: int,
    base_amount: int,
    order_type: int,
    post_only: int,
    reduce_only: bool,
    slippage: Optional[int],
    auto_cancel: bool,
) -> bytes:
    pair_bytes = normalize_bytes32(pair)
    subaccount = normalize_address(subaccount)

    if order_type == 0:  # limit
        if is_buy:
            signature = "subaccountPlaceOrderBuyB(address,bytes32,uint256,uint256,uint8,bool)"
        else:
            signature = "subaccountPlaceOrderSellB(address,bytes32,uint256,uint256,uint8,bool)"
        types = ["address", "bytes32", "uint256", "uint256", "uint8", "bool"]
        args = [subaccount, pair_bytes, quote_amount, base_amount, post_only, reduce_only]
        return _encode_call(signature, types, args)

    if order_type != 1:
        raise ValueError(f"invalid spot order_type: {order_type}")

    if slippage is None:
        if is_buy:
            signature = (
                "subaccountPlaceMarketOrderBuyBWithoutPrice(address,bytes32,uint256,uint256,bool,bool)"
            )
        else:
            signature = (
                "subaccountPlaceMarketOrderSellBWithoutPrice(address,bytes32,uint256,uint256,bool,bool)"
            )
        types = ["address", "bytes32", "uint256", "uint256", "bool", "bool"]
        args = [subaccount, pair_bytes, quote_amount, base_amount, auto_cancel, reduce_only]
        return _encode_call(signature, types, args)

    if is_buy:
        signature = (
            "subaccountPlaceMarketOrderBuyBWithPrice(address,bytes32,uint256,uint256,uint8,bool,bool)"
        )
    else:
        signature = (
            "subaccountPlaceMarketOrderSellBWithPrice(address,bytes32,uint256,uint256,uint8,bool,bool)"
        )
    types = ["address", "bytes32", "uint256", "uint256", "uint8", "bool", "bool"]
    args = [
        subaccount,
        pair_bytes,
        quote_amount,
        base_amount,
        slippage,
        auto_cancel,
        reduce_only,
    ]
    return _encode_call(signature, types, args)


def encode_spot_cancel_order(*, subaccount: str, pair: str, order_id: int, is_buy: bool) -> bytes:
    pair_bytes = normalize_bytes32(pair)
    subaccount = normalize_address(subaccount)
    if is_buy:
        signature = "subaccountCancelOrderBuyB(address,bytes32,uint256)"
    else:
        signature = "subaccountCancelOrderSellB(address,bytes32,uint256)"
    types = ["address", "bytes32", "uint256"]
    args = [subaccount, pair_bytes, order_id]
    return _encode_call(signature, types, args)


def encode_spot_place_order_sell_b(
    *, subaccount: str, pair: str, quote_amount: int, base_amount: int, post_only: int, reduce_only: bool
) -> bytes:
    signature = "subaccountPlaceOrderSellB(address,bytes32,uint256,uint256,uint8,bool)"
    types = ["address", "bytes32", "uint256", "uint256", "uint8", "bool"]
    args = [normalize_address(subaccount), normalize_bytes32(pair), quote_amount, base_amount, post_only, reduce_only]
    return _encode_call(signature, types, args)


def encode_spot_place_order_buy_b(
    *, subaccount: str, pair: str, quote_amount: int, base_amount: int, post_only: int, reduce_only: bool
) -> bytes:
    signature = "subaccountPlaceOrderBuyB(address,bytes32,uint256,uint256,uint8,bool)"
    types = ["address", "bytes32", "uint256", "uint256", "uint8", "bool"]
    args = [normalize_address(subaccount), normalize_bytes32(pair), quote_amount, base_amount, post_only, reduce_only]
    return _encode_call(signature, types, args)


def encode_spot_place_market_order_sell_b_without_price(
    *,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    auto_cancel: bool,
    reduce_only: bool,
) -> bytes:
    signature = "subaccountPlaceMarketOrderSellBWithoutPrice(address,bytes32,uint256,uint256,bool,bool)"
    types = ["address", "bytes32", "uint256", "uint256", "bool", "bool"]
    args = [normalize_address(subaccount), normalize_bytes32(pair), quote_amount, base_amount, auto_cancel, reduce_only]
    return _encode_call(signature, types, args)


def encode_spot_place_market_order_sell_b_with_price(
    *,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    slippage: int,
    auto_cancel: bool,
    reduce_only: bool,
) -> bytes:
    signature = "subaccountPlaceMarketOrderSellBWithPrice(address,bytes32,uint256,uint256,uint8,bool,bool)"
    types = ["address", "bytes32", "uint256", "uint256", "uint8", "bool", "bool"]
    args = [
        normalize_address(subaccount),
        normalize_bytes32(pair),
        quote_amount,
        base_amount,
        slippage,
        auto_cancel,
        reduce_only,
    ]
    return _encode_call(signature, types, args)


def encode_spot_place_market_order_buy_b_without_price(
    *,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    auto_cancel: bool,
    reduce_only: bool,
) -> bytes:
    signature = "subaccountPlaceMarketOrderBuyBWithoutPrice(address,bytes32,uint256,uint256,bool,bool)"
    types = ["address", "bytes32", "uint256", "uint256", "bool", "bool"]
    args = [normalize_address(subaccount), normalize_bytes32(pair), quote_amount, base_amount, auto_cancel, reduce_only]
    return _encode_call(signature, types, args)


def encode_spot_place_market_order_buy_b_with_price(
    *,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    slippage: int,
    auto_cancel: bool,
    reduce_only: bool,
) -> bytes:
    signature = "subaccountPlaceMarketOrderBuyBWithPrice(address,bytes32,uint256,uint256,uint8,bool,bool)"
    types = ["address", "bytes32", "uint256", "uint256", "uint8", "bool", "bool"]
    args = [
        normalize_address(subaccount),
        normalize_bytes32(pair),
        quote_amount,
        base_amount,
        slippage,
        auto_cancel,
        reduce_only,
    ]
    return _encode_call(signature, types, args)


def encode_spot_cancel_order_sell_b(*, subaccount: str, pair: str, order_id: int) -> bytes:
    signature = "subaccountCancelOrderSellB(address,bytes32,uint256)"
    types = ["address", "bytes32", "uint256"]
    args = [normalize_address(subaccount), normalize_bytes32(pair), order_id]
    return _encode_call(signature, types, args)


def encode_spot_cancel_order_buy_b(*, subaccount: str, pair: str, order_id: int) -> bytes:
    signature = "subaccountCancelOrderBuyB(address,bytes32,uint256)"
    types = ["address", "bytes32", "uint256"]
    args = [normalize_address(subaccount), normalize_bytes32(pair), order_id]
    return _encode_call(signature, types, args)


def _encode_call(signature: str, types: Sequence[str], args: Sequence[object]) -> bytes:
    selector = keccak(text=signature)[:4]
    encoded = abi_encode(types, list(args))
    return selector + encoded


def encode_call(signature: str, types: Sequence[str], args: Sequence[object]) -> bytes:
    return _encode_call(signature, types, args)


def decode_abi(types: Sequence[str], data: bytes) -> tuple:
    if data is None:
        return tuple()
    return abi_decode(types, data)


def normalize_address(addr: str) -> str:
    addr = addr.strip()
    if not addr.startswith("0x"):
        addr = "0x" + addr
    return addr


def normalize_bytes32(value: str) -> bytes:
    raw = value.strip()
    if raw.startswith("0x"):
        raw = raw[2:]
    if len(raw) != 64:
        raise ValueError("pair must be 32-byte hex string")
    return bytes.fromhex(raw)
