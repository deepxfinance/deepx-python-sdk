from __future__ import annotations

import json
from typing import Optional

from ._abi import (
    decode_abi,
    encode_call,
    normalize_address,
    normalize_bytes32,
)
from ._evm import evm_call
from ._native import submit_pallet_call, submit_pallet_call_wait_event
from ._subaccount import _submit_modify_orders
from ._types import (
    ModifyOrderResult,
    SpotCancelOrderResult,
    SpotMarketSpec,
    SpotOrderInfo,
    SpotPlaceOrderResult,
)


def _spot_slippage_bps(value: int) -> int:
    # Chain runtime 187+: a market order's slippage is validated on-chain
    # against the pair's max_deviation_bps (default 500, per-pair sudo
    # config, hard bound 10000) instead of the old fixed 0-99 percentage.
    # Keep only the chain's hard bound here; the per-pair check happens
    # on-chain (20_x InvalidSlippage).
    slippage = int(value)
    if slippage < 0 or slippage > 10000:
        raise ValueError("spot market slippage must be between 0 and 10000")
    return slippage


def _spot_place_params(
    *,
    subaccount: str,
    pair: str,
    is_buy: bool,
    quote_amount: int,
    base_amount: int,
    order_type: object,
    post_only: str,
    reduce_only: bool,
    cloid: Optional[int],
) -> dict:
    # On-chain `place_order` takes a single `params: SpotPlaceParams` arg.
    # `order_type` shares the perp OrderType enum: Limit(TimeInForce) |
    # Market(Option<u64> slippage). The current runtime derives the system
    # order id from the timestamp nonce; `cloid` remains accepted for source
    # compatibility but is no longer part of the on-chain params.
    _ = cloid
    return {
        "params": {
            "subaccount": normalize_address(subaccount),
            "pair": "0x" + normalize_bytes32(pair).hex(),
            "is_buy": bool(is_buy),
            "quote_amount": int(quote_amount),
            "base_amount": int(base_amount),
            "order_type": order_type,
            "post_only": post_only,
            "reduce_only": bool(reduce_only),
        }
    }


def subaccount_place_order_buy_b(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    post_only: int = 0,
    reduce_only: bool = False,
    slippage: Optional[int] = None,
    auto_cancel: bool = False,
    cloid: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotPlaceOrderResult:
    _ = slippage, auto_cancel
    return _submit_spot_order(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_params=_spot_place_params(
            subaccount=subaccount,
            pair=pair,
            is_buy=True,
            quote_amount=quote_amount,
            base_amount=base_amount,
            order_type={"Limit": "GTC"},
            post_only=_post_only_param(post_only),
            reduce_only=reduce_only,
            cloid=cloid,
        ),
        is_buy=True,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def subaccount_place_order_sell_b(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    post_only: int = 0,
    reduce_only: bool = False,
    slippage: Optional[int] = None,
    auto_cancel: bool = False,
    cloid: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotPlaceOrderResult:
    _ = slippage, auto_cancel
    return _submit_spot_order(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_params=_spot_place_params(
            subaccount=subaccount,
            pair=pair,
            is_buy=False,
            quote_amount=quote_amount,
            base_amount=base_amount,
            order_type={"Limit": "GTC"},
            post_only=_post_only_param(post_only),
            reduce_only=reduce_only,
            cloid=cloid,
        ),
        is_buy=False,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def subaccount_place_order_buy_ioc_b(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    reduce_only: bool = False,
    cloid: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotPlaceOrderResult:
    return _submit_spot_order(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_params=_spot_place_params(
            subaccount=subaccount,
            pair=pair,
            is_buy=True,
            quote_amount=quote_amount,
            base_amount=base_amount,
            order_type={"Limit": "IOC"},
            post_only="None",
            reduce_only=reduce_only,
            cloid=cloid,
        ),
        is_buy=True,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def subaccount_place_order_sell_ioc_b(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    reduce_only: bool = False,
    cloid: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotPlaceOrderResult:
    return _submit_spot_order(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_params=_spot_place_params(
            subaccount=subaccount,
            pair=pair,
            is_buy=False,
            quote_amount=quote_amount,
            base_amount=base_amount,
            order_type={"Limit": "IOC"},
            post_only="None",
            reduce_only=reduce_only,
            cloid=cloid,
        ),
        is_buy=False,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def subaccount_place_market_order_buy_b_without_price(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    auto_cancel: bool = False,
    reduce_only: bool = False,
    cloid: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotPlaceOrderResult:
    _ = auto_cancel  # no longer exists on-chain; kept for signature compatibility
    return _submit_spot_order(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_params=_spot_place_params(
            subaccount=subaccount,
            pair=pair,
            is_buy=True,
            quote_amount=quote_amount,
            base_amount=base_amount,
            order_type={"Market": None},
            post_only="None",
            reduce_only=reduce_only,
            cloid=cloid,
        ),
        is_buy=True,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def subaccount_place_market_order_buy_b_with_price(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    slippage: int,
    auto_cancel: bool = False,
    reduce_only: bool = False,
    cloid: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotPlaceOrderResult:
    _ = auto_cancel  # no longer exists on-chain; kept for signature compatibility
    return _submit_spot_order(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_params=_spot_place_params(
            subaccount=subaccount,
            pair=pair,
            is_buy=True,
            quote_amount=quote_amount,
            base_amount=base_amount,
            order_type={"Market": _spot_slippage_bps(slippage)},
            post_only="None",
            reduce_only=reduce_only,
            cloid=cloid,
        ),
        is_buy=True,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def subaccount_place_market_order_sell_b_without_price(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    auto_cancel: bool = False,
    reduce_only: bool = False,
    cloid: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotPlaceOrderResult:
    _ = auto_cancel  # no longer exists on-chain; kept for signature compatibility
    return _submit_spot_order(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_params=_spot_place_params(
            subaccount=subaccount,
            pair=pair,
            is_buy=False,
            quote_amount=quote_amount,
            base_amount=base_amount,
            order_type={"Market": None},
            post_only="None",
            reduce_only=reduce_only,
            cloid=cloid,
        ),
        is_buy=False,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def subaccount_place_market_order_sell_b_with_price(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    pair: str,
    quote_amount: int,
    base_amount: int,
    slippage: int,
    auto_cancel: bool = False,
    reduce_only: bool = False,
    cloid: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotPlaceOrderResult:
    _ = auto_cancel  # no longer exists on-chain; kept for signature compatibility
    return _submit_spot_order(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_params=_spot_place_params(
            subaccount=subaccount,
            pair=pair,
            is_buy=False,
            quote_amount=quote_amount,
            base_amount=base_amount,
            order_type={"Market": _spot_slippage_bps(slippage)},
            post_only="None",
            reduce_only=reduce_only,
            cloid=cloid,
        ),
        is_buy=False,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def modify_spot_order(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    pair: str,
    order_id: int,
    is_buy: bool,
    quote_amount: int,
    base_amount: int,
    order_type: int = 0,
    slippage: Optional[int] = None,
    post_only: int = 0,
    reduce_only: bool = False,
    cloid: Optional[int] = None,
    fast_cancel: bool = False,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> ModifyOrderResult:
    _ = evm_rpc_url, precompile_address, chain_id, gas_limit, max_fee_per_gas
    _ = max_priority_fee_per_gas, use_legacy
    # Atomic cancel+place via Subaccount.modify_orders (transactional): the old
    # order stays untouched if the new one fails admission. The new order is a
    # fresh SpotPlaceParams — all params are explicit.
    ops = [
        {
            "Cancel": {
                "Spot": {
                    "subaccount": normalize_address(subaccount),
                    "pair": "0x" + normalize_bytes32(pair).hex(),
                    "order_id": int(order_id),
                    "is_buy": bool(is_buy),
                    "cancel_reason": "UserCanceled",
                    "fast_cancel": bool(fast_cancel),
                }
            }
        },
        {
            "Place": {
                "Spot": _spot_place_params(
                    subaccount=subaccount,
                    pair=pair,
                    is_buy=is_buy,
                    quote_amount=quote_amount,
                    base_amount=base_amount,
                    order_type=_spot_order_type_param(order_type, slippage),
                    post_only=_post_only_param(post_only),
                    reduce_only=reduce_only,
                    cloid=cloid,
                )["params"]
            }
        },
    ]
    ev = _submit_modify_orders(
        substrate_ws=substrate_ws,
        private_key=private_key,
        ops=ops,
        pallet="SpotMarket",
        event="StateOrderBuy" if is_buy else "StateOrderSell",
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    fields = json.loads(ev.fields_json)
    new_order_id = _parse_int_field(fields, "order_id")
    return ModifyOrderResult(
        order_id=new_order_id,
        tx_hash=ev.tx_hash,
        extrinsic_hash=ev.extrinsic_hash,
        canceled_order_id=int(order_id),
    )


def _spot_order_type_param(value: int, slippage: Optional[int]) -> object:
    mapping = {
        0: lambda: {"Limit": "GTC"},
        1: lambda: {"Market": None if slippage is None else _spot_slippage_bps(slippage)},
        3: lambda: {"Limit": "IOC"},
    }
    try:
        return mapping[int(value)]()
    except KeyError as exc:
        raise ValueError(f"invalid spot order_type: {value}") from exc


def _submit_spot_order(
    *,
    substrate_ws: str,
    private_key: str,
    call_params: dict,
    is_buy: bool,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotPlaceOrderResult:
    event_name = "StateOrderBuy" if is_buy else "StateOrderSell"
    ev = submit_pallet_call_wait_event(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="SpotMarket",
        call_function="place_order",
        call_params=call_params,
        pallet="SpotMarket",
        event=event_name,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    fields = json.loads(ev.fields_json)
    order_id = _parse_int_field(fields, "order_id")
    return SpotPlaceOrderResult(order_id=order_id, tx_hash=ev.tx_hash, extrinsic_hash=ev.extrinsic_hash)


def subaccount_cancel_order_buy_b(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    pair: str,
    order_id: int,
    fast_cancel: bool = False,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotCancelOrderResult:
    return _submit_spot_cancel(
        substrate_ws=substrate_ws,
        private_key=private_key,
        subaccount=subaccount,
        pair=pair,
        order_id=order_id,
        is_buy=True,
        fast_cancel=fast_cancel,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def subaccount_cancel_order_sell_b(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    pair: str,
    order_id: int,
    fast_cancel: bool = False,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotCancelOrderResult:
    return _submit_spot_cancel(
        substrate_ws=substrate_ws,
        private_key=private_key,
        subaccount=subaccount,
        pair=pair,
        order_id=order_id,
        is_buy=False,
        fast_cancel=fast_cancel,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def _submit_spot_cancel(
    *,
    substrate_ws: str,
    private_key: str,
    subaccount: str,
    pair: str,
    order_id: int,
    is_buy: bool,
    fast_cancel: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SpotCancelOrderResult:
    # On-chain `cancel_order` takes a single `params: SpotCancelParams` arg.
    # With fast_cancel=True the pallet skips the OrderCancelled event (higher
    # priority), so wait for inclusion only and echo the requested order_id.
    call_params = {
        "params": {
            "subaccount": normalize_address(subaccount),
            "pair": "0x" + normalize_bytes32(pair).hex(),
            "order_id": int(order_id),
            "is_buy": bool(is_buy),
            "cancel_reason": "UserCanceled",
            "fast_cancel": bool(fast_cancel),
        }
    }
    if fast_cancel:
        tx = submit_pallet_call(
            substrate_ws=substrate_ws,
            private_key=private_key,
            call_module="SpotMarket",
            call_function="cancel_order",
            call_params=call_params,
            nonce_ms=nonce_ms,
            wait_for_finalized=False,
            timeout_ms=timeout_ms,
        )
        return SpotCancelOrderResult(
            order_id=int(order_id),
            tx_hash=tx.tx_hash,
            extrinsic_hash=tx.extrinsic_hash,
        )
    try:
        ev = submit_pallet_call_wait_event(
            substrate_ws=substrate_ws,
            private_key=private_key,
            call_module="SpotMarket",
            call_function="cancel_order",
            call_params=call_params,
            pallet="SpotMarket",
            event="OrderCancelled",
            nonce_ms=nonce_ms,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "spot cancel submitted but SpotMarket::OrderCancelled event could not be confirmed: "
            f"order_id={order_id}, error={exc}"
        ) from exc
    fields = json.loads(ev.fields_json)
    parsed_order_id = _parse_int_field(fields, "order_id")
    return SpotCancelOrderResult(
        order_id=parsed_order_id,
        tx_hash=ev.tx_hash,
        extrinsic_hash=ev.extrinsic_hash,
    )


def _post_only_u8(value: int) -> int:
    post_only = int(value)
    if post_only not in {0, 1, 2}:
        raise ValueError(f"invalid post_only: {value}")
    return post_only


def _post_only_param(value: int) -> str:
    mapping = {0: "None", 1: "MustPostOnly", 2: "Adaptive"}
    try:
        return mapping[int(value)]
    except KeyError as exc:
        raise ValueError(f"invalid post_only: {value}") from exc


def _parse_int_field(fields: object, key: str) -> int:
    if isinstance(fields, dict) and key in fields:
        try:
            return _parse_int_value(fields[key])
        except Exception as exc:
            raise RuntimeError(
                f"event field '{key}' not an int-like value: {fields[key]}"
            ) from exc
    if key == "order_id" and isinstance(fields, dict):
        order = fields.get("order")
        if isinstance(order, dict) and "id" in order:
            try:
                return _parse_int_value(order["id"])
            except Exception as exc:
                raise RuntimeError(
                    f"event field 'order.id' not an int-like value: {order['id']}"
                ) from exc
        if "id" in fields:
            try:
                return _parse_int_value(fields["id"])
            except Exception as exc:
                raise RuntimeError(
                    f"event field 'id' not an int-like value: {fields['id']}"
                ) from exc
    raise RuntimeError(f"event field '{key}' not found: {fields}")


def _parse_int_value(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        val = value.strip()
        if val.startswith("0x"):
            return int(val, 16)
        return int(val)
    if isinstance(value, dict):
        if "value" in value:
            return _parse_int_value(value["value"])
        if "values" in value:
            return _parse_int_value(value["values"])
    if isinstance(value, list):
        if not value:
            raise ValueError("empty list")
        try:
            items = [_parse_int_value(v) for v in value]
        except Exception as exc:
            raise ValueError(f"unsupported list value: {value}") from exc
        if all(0 <= v <= 0xFF for v in items) and len(items) in {16, 32}:
            return int.from_bytes(bytes(items), "little")
        total = 0
        for i, limb in enumerate(items):
            total |= int(limb) << (64 * i)
        return total
    raise ValueError(f"unsupported int value: {value}")


def user_active_spot_orders(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    user: str,
    pair: str | None = None,
) -> list[SpotOrderInfo]:
    pair_bytes = normalize_bytes32(pair) if pair else bytes(32)
    data = encode_call(
        "userActiveSpotOrders(address,bytes32)",
        ["address", "bytes32"],
        [normalize_address(user), pair_bytes],
    )
    raw = evm_call(evm_rpc_url, precompile_address, data)
    return [_decode_spot_order(order) for order in _decode_spot_orders_tuple(raw)]


def get_spot_market_spec(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    pair: str,
) -> SpotMarketSpec:
    data = encode_call("getSpotMarketSpec(bytes32)", ["bytes32"], [normalize_bytes32(pair)])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (spec,) = decode_abi([_SPOT_MARKET_SPEC_TUPLE], raw)
    return SpotMarketSpec(
        min_order_size=int(spec[0]),
        tick_size=int(spec[1]),
        step_size=int(spec[2]),
    )


_SPOT_ORDER_TUPLE = "(bytes32,uint64,address,uint256,uint256,uint256,uint32,uint8,bool,uint8,uint8)"
# `id` was U256 before the chain moved spot order ids to u64.
_SPOT_ORDER_TUPLE_LEGACY = "(bytes32,uint256,address,uint256,uint256,uint256,uint32,uint8,bool,uint8,uint8)"
_SPOT_MARKET_SPEC_TUPLE = "(uint128,uint128,uint128)"


def _decode_spot_orders_tuple(raw: bytes) -> tuple:
    for tuple_type in (_SPOT_ORDER_TUPLE, _SPOT_ORDER_TUPLE_LEGACY):
        try:
            (orders,) = decode_abi([f"{tuple_type}[]"], raw)
            return orders
        except Exception:
            continue
    raise RuntimeError("unable to decode userActiveSpotOrders response with supported ABI layouts")


def _decode_spot_order(order: tuple) -> SpotOrderInfo:
    return SpotOrderInfo(
        pair=_decode_bytes32(order[0]),
        id=int(order[1]),
        maker=_decode_address(order[2]),
        price=int(order[3]),
        quote_amount=int(order[4]),
        base_amount=int(order[5]),
        create_time=int(order[6]),
        status=int(order[7]),
        is_buy=bool(order[8]),
        order_type=int(order[9]),
        slippage=int(order[10]),
    )


def _decode_address(value: bytes) -> str:
    if isinstance(value, str):
        return value
    return "0x" + value.hex()


def _decode_bytes32(value: bytes) -> str:
    if isinstance(value, str):
        return value
    return "0x" + value.hex()
