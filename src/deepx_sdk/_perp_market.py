from __future__ import annotations

import json
from typing import Any, Optional

from ._abi import (
    decode_abi,
    encode_call,
    encode_perp_set_profit_and_loss_point,
    normalize_address,
)
from ._evm import evm_call
from ._native import (
    build_signed_tx,
    submit_pallet_call,
    submit_pallet_call_wait_event,
    submit_signed_tx_wait_event,
)
from ._subaccount import _submit_modify_orders
from ._types import (
    ActiveOrderInfo,
    CancelOrderResult,
    ModifyOrderResult,
    OraclePriceInfo,
    PerpMarketInfo,
    PerpOrderInfo,
    PerpOrderSpec,
    PerpPositionInfo,
    PlaceOrderResult,
    PositionUpdatedResult,
    SettlePnlResult,
    TotalCollateralAndMarginInfo,
    TxResult,
)


def place_perp_order(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
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
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> PlaceOrderResult:
    _ = evm_rpc_url, precompile_address, chain_id, gas_limit, max_fee_per_gas
    _ = max_priority_fee_per_gas, use_legacy
    # On-chain `place_order` takes a single `params: PerpPlaceParams` arg.
    # Leverage is no longer a per-order param — set it via set_global_leverage
    # / set_per_market_leverage first. `slippage` only applies to market orders
    # (folds into OrderType::Market(Option<u64>)). The current runtime derives
    # the system order id from the timestamp nonce; `cloid` is retained in the
    # Python signature for source compatibility but is no longer serialized.
    ev = submit_pallet_call_wait_event(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="PerpMarket",
        call_function="place_order",
        call_params={
            "params": _perp_place_params(
                subaccount=subaccount,
                market_id=market_id,
                is_long=is_long,
                size=size,
                price=price,
                order_type=order_type,
                slippage=slippage,
                take_profit=take_profit,
                stop_loss=stop_loss,
                reduce_only=reduce_only,
                post_only=post_only,
                cloid=cloid,
            )
        },
        pallet="PerpMarket",
        event="OrderPlaced",
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    fields = json.loads(ev.fields_json)
    order_id = _parse_int_field(fields, "order_id")
    return PlaceOrderResult(order_id=order_id, tx_hash=ev.tx_hash, extrinsic_hash=ev.extrinsic_hash)


def place_perp_order_limit(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
    is_long: bool,
    size: int,
    price: int,
    take_profit: Optional[int] = None,
    stop_loss: Optional[int] = None,
    reduce_only: bool = False,
    post_only: int = 0,
    cloid: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> PlaceOrderResult:
    return place_perp_order(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile_address,
        subaccount=subaccount,
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
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        use_legacy=use_legacy,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def place_perp_order_ioc(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
    is_long: bool,
    size: int,
    price: int,
    take_profit: Optional[int] = None,
    stop_loss: Optional[int] = None,
    reduce_only: bool = False,
    post_only: int = 0,
    cloid: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> PlaceOrderResult:
    return place_perp_order(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile_address,
        subaccount=subaccount,
        market_id=market_id,
        is_long=is_long,
        size=size,
        price=price,
        order_type=3,
        take_profit=take_profit,
        stop_loss=stop_loss,
        reduce_only=reduce_only,
        post_only=post_only,
        cloid=cloid,
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        use_legacy=use_legacy,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def place_perp_order_market(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
    is_long: bool,
    size: int,
    slippage: Optional[int] = None,
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
) -> PlaceOrderResult:
    return place_perp_order(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile_address,
        subaccount=subaccount,
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
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        use_legacy=use_legacy,
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def cancel_perp_order(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
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
) -> CancelOrderResult:
    _ = evm_rpc_url, precompile_address, chain_id, gas_limit, max_fee_per_gas
    _ = max_priority_fee_per_gas, use_legacy
    # On-chain `cancel_order` takes a single `params: PerpCancelParams` arg.
    # With fast_cancel=True the pallet skips the OrderCancelled event (higher
    # priority), so wait for inclusion only and echo the requested order_id.
    call_params = {
        "params": {
            "subaccount": normalize_address(subaccount),
            "order_id": int(order_id),
            "market_id": int(market_id),
            "cancel_reason": "UserCanceled",
            "fast_cancel": bool(fast_cancel),
        }
    }
    if fast_cancel:
        tx = submit_pallet_call(
            substrate_ws=substrate_ws,
            private_key=private_key,
            call_module="PerpMarket",
            call_function="cancel_order",
            call_params=call_params,
            nonce_ms=nonce_ms,
            wait_for_finalized=False,
            timeout_ms=timeout_ms,
        )
        return CancelOrderResult(order_id=int(order_id), tx_hash=tx.tx_hash, extrinsic_hash=tx.extrinsic_hash)
    ev = submit_pallet_call_wait_event(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="PerpMarket",
        call_function="cancel_order",
        call_params=call_params,
        pallet="PerpMarket",
        event="OrderCancelled",
        nonce_ms=nonce_ms,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    fields = json.loads(ev.fields_json)
    order_id = _parse_int_field(fields, "order_id")
    return CancelOrderResult(order_id=order_id, tx_hash=ev.tx_hash, extrinsic_hash=ev.extrinsic_hash)


def modify_perp_order(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
    order_id: int,
    is_long: bool,
    price: int,
    size: Optional[int] = None,
    new_total_quantity: Optional[int] = None,
    order_type: int = 0,
    slippage: Optional[int] = None,
    take_profit: Optional[int] = None,
    stop_loss: Optional[int] = None,
    reduce_only: bool = False,
    post_only: int = 0,
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
    _ = chain_id, gas_limit, max_fee_per_gas, max_priority_fee_per_gas, use_legacy
    # Atomic cancel+place via Subaccount.modify_orders (transactional): the old
    # order stays untouched if the new one fails admission. Exactly one of
    # `size` / `new_total_quantity` is required:
    # - size: explicit remaining size of the new order (chain semantics).
    # - new_total_quantity: product semantics — total including the filled part;
    #   the new size is new_total_quantity - filled. == filled degrades to a
    #   plain cancel; < filled is rejected.
    if (size is None) == (new_total_quantity is None):
        raise ValueError("exactly one of size / new_total_quantity is required")
    if new_total_quantity is not None:
        current = order_info(
            evm_rpc_url=evm_rpc_url,
            precompile_address=precompile_address,
            user=subaccount,
            order_id=order_id,
        )
        size = int(new_total_quantity) - current.size_filled
        if size < 0:
            raise ValueError(
                f"new_total_quantity {new_total_quantity} < filled {current.size_filled}"
            )
        if size == 0:
            cancelled = cancel_perp_order(
                substrate_ws=substrate_ws,
                evm_rpc_url=evm_rpc_url,
                private_key=private_key,
                precompile_address=precompile_address,
                subaccount=subaccount,
                market_id=market_id,
                order_id=order_id,
                fast_cancel=fast_cancel,
                nonce_ms=nonce_ms,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            )
            return ModifyOrderResult(
                order_id=cancelled.order_id,
                tx_hash=cancelled.tx_hash,
                extrinsic_hash=cancelled.extrinsic_hash,
                canceled_order_id=int(order_id),
            )
    ops = [
        {
            "Cancel": {
                "Perp": {
                    "subaccount": normalize_address(subaccount),
                    "order_id": int(order_id),
                    "market_id": int(market_id),
                    "cancel_reason": "UserCanceled",
                    "fast_cancel": bool(fast_cancel),
                }
            }
        },
        {
            "Place": {
                "Perp": _perp_place_params(
                    subaccount=subaccount,
                    market_id=market_id,
                    is_long=is_long,
                    size=size,
                    price=price,
                    order_type=order_type,
                    slippage=slippage,
                    take_profit=take_profit,
                    stop_loss=stop_loss,
                    reduce_only=reduce_only,
                    post_only=post_only,
                    cloid=cloid,
                )
            }
        },
    ]
    ev = _submit_modify_orders(
        substrate_ws=substrate_ws,
        private_key=private_key,
        ops=ops,
        pallet="PerpMarket",
        event="OrderPlaced",
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


def settle_pnl(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SettlePnlResult | TxResult:
    _ = evm_rpc_url, precompile_address, chain_id, gas_limit, max_fee_per_gas
    _ = max_priority_fee_per_gas, use_legacy
    # On-chain `settle_pnl` is a Nonce-type call (sequential nonce), permissionless
    # (any signer may settle any subaccount). market_id=None settles ALL markets;
    # that path emits one SettlePnl event per non-zero settlement — possibly none —
    # so it must wait for inclusion only.
    call_params = {
        "subaccount": normalize_address(subaccount),
        "market_id": None if market_id is None else int(market_id),
    }
    if market_id is None:
        tx = submit_pallet_call(
            substrate_ws=substrate_ws,
            private_key=private_key,
            call_module="PerpMarket",
            call_function="settle_pnl",
            call_params=call_params,
            nonce_ms=nonce,
            use_timestamp_nonce=False,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        return TxResult(tx_hash=tx.tx_hash, event=None)
    ev = submit_pallet_call_wait_event(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="PerpMarket",
        call_function="settle_pnl",
        call_params=call_params,
        pallet="PerpMarket",
        event="SettlePnl",
        nonce_ms=nonce,
        use_timestamp_nonce=False,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    fields = json.loads(ev.fields_json)
    return SettlePnlResult(
        tx_hash=ev.tx_hash,
        extrinsic_hash=ev.extrinsic_hash,
        market_id=_parse_int_field(fields, "market_id"),
        unrealized=_parse_int_field(fields, "unrealized"),
        funding=_parse_int_field(fields, "funding"),
        total=_parse_int_field(fields, "total"),
    )


def close_position_limit(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
    price: int,
    slippage: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> PlaceOrderResult:
    return _close_position_inner(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile_address,
        subaccount=subaccount,
        market_id=market_id,
        price=price,
        slippage=slippage,
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        use_legacy=use_legacy,
        nonce=nonce,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def close_position(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
    price: int,
    slippage: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> PlaceOrderResult:
    return _close_position_inner(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile_address,
        subaccount=subaccount,
        market_id=market_id,
        price=price,
        slippage=slippage,
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        use_legacy=use_legacy,
        nonce=nonce,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def close_position_market(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
    slippage: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> PlaceOrderResult:
    return _close_position_inner(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile_address,
        subaccount=subaccount,
        market_id=market_id,
        price=0,
        slippage=slippage,
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        use_legacy=use_legacy,
        nonce=nonce,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def _close_position_inner(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
    price: int,
    slippage: Optional[int],
    chain_id: Optional[int],
    gas_limit: Optional[int],
    max_fee_per_gas: Optional[int],
    max_priority_fee_per_gas: Optional[int],
    use_legacy: bool,
    nonce: Optional[int],
    wait_for_finalized: bool,
    timeout_ms: Optional[int],
) -> PlaceOrderResult:
    _ = precompile_address, chain_id, gas_limit, max_fee_per_gas
    _ = max_priority_fee_per_gas, use_legacy
    ev = submit_pallet_call_wait_event(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="PerpMarket",
        call_function="close_position",
        call_params={
            "subaccount": normalize_address(subaccount),
            "market_id": int(market_id),
            "price": int(price),
            "slippage": _optional_u64(slippage),
        },
        pallet="PerpMarket",
        event="OrderPlaced",
        nonce_ms=nonce,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    fields = json.loads(ev.fields_json)
    order_id = _parse_int_field(fields, "order_id")
    return PlaceOrderResult(order_id=order_id, tx_hash=ev.tx_hash, extrinsic_hash=ev.extrinsic_hash)


def set_profit_and_loss_point(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
    take_profit_point: Optional[int] = None,
    stop_loss_point: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> PositionUpdatedResult:
    _ = evm_rpc_url, precompile_address, chain_id, gas_limit, max_fee_per_gas
    _ = max_priority_fee_per_gas, use_legacy, nonce
    ev = submit_pallet_call_wait_event(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="PerpMarket",
        call_function="set_profit_and_loss_point",
        call_params={
            "subaccount": normalize_address(subaccount),
            "market_id": int(market_id),
            "take_profit_point": 0 if take_profit_point is None else int(take_profit_point),
            "stop_loss_point": 0 if stop_loss_point is None else int(stop_loss_point),
        },
        pallet="PerpMarket",
        event="PositionUpdated",
        evm_rpc_url=evm_rpc_url,
        nonce_ms=nonce,
        use_timestamp_nonce=False,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    fields = json.loads(ev.fields_json)
    parsed_fields = _parse_position_updated_fields(fields)
    return PositionUpdatedResult(
        tx_hash=ev.tx_hash,
        extrinsic_hash=ev.extrinsic_hash,
        fields=parsed_fields,
    )


def set_global_leverage(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    max_leverage: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    """Set the per-subaccount global leverage cap (applies to all markets).

    Leverage is configured per subaccount, not per order — call this before
    trading. ``max_leverage`` is scaled by LEVERAGE_PRECISION (1000):
    10x = 10000. ``None`` clears the override and falls back to the protocol
    max.
    """
    _ = evm_rpc_url, precompile_address, chain_id, gas_limit, max_fee_per_gas
    _ = max_priority_fee_per_gas, use_legacy
    ev = submit_pallet_call_wait_event(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="PerpMarket",
        call_function="set_global_leverage",
        call_params={
            "subaccount": normalize_address(subaccount),
            "max_leverage": _optional_u64(max_leverage),
        },
        pallet="PerpMarket",
        event="GlobalLeverageSet",
        nonce_ms=nonce,
        use_timestamp_nonce=False,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    return _tx_result_from_event(ev)


def set_per_market_leverage(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
    max_leverage: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    """Set a per-subaccount, per-market leverage override.

    Overrides the global cap for one market (the more conservative wins).
    ``max_leverage`` is scaled by LEVERAGE_PRECISION (1000): 10x = 10000.
    ``None`` clears the override for this market.
    """
    _ = evm_rpc_url, precompile_address, chain_id, gas_limit, max_fee_per_gas
    _ = max_priority_fee_per_gas, use_legacy
    ev = submit_pallet_call_wait_event(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="PerpMarket",
        call_function="set_per_market_leverage",
        call_params={
            "subaccount": normalize_address(subaccount),
            "market_id": int(market_id),
            "max_leverage": _optional_u64(max_leverage),
        },
        pallet="PerpMarket",
        event="PerMarketLeverageSet",
        nonce_ms=nonce,
        use_timestamp_nonce=False,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    return _tx_result_from_event(ev)


def _tx_result_from_event(ev: Any) -> TxResult:
    event: dict[str, Any] | None = None
    if getattr(ev, "fields_json", ""):
        try:
            decoded = json.loads(ev.fields_json)
            if isinstance(decoded, dict):
                event = {"pallet": getattr(ev, "pallet", None), "name": getattr(ev, "event", None)}
                event.update(decoded)
        except Exception:
            event = None
    return TxResult(tx_hash=getattr(ev, "tx_hash", ""), event=event)


def _parse_int_field(fields: object, key: str) -> int:
    if isinstance(fields, dict) and key in fields:
        try:
            return _parse_int_value(fields[key])
        except Exception as exc:
            raise RuntimeError(
                f"event field '{key}' not an int-like value: {fields[key]}"
            ) from exc
    raise RuntimeError(f"event field '{key}' not found: {fields}")


def _perp_place_params(
    *,
    subaccount: str,
    market_id: int,
    is_long: bool,
    size: int,
    price: int,
    order_type: int,
    slippage: Optional[int],
    take_profit: Optional[int],
    stop_loss: Optional[int],
    reduce_only: bool,
    post_only: int,
    cloid: Optional[int],
) -> dict:
    _ = cloid
    return {
        "subaccount": normalize_address(subaccount),
        "market_id": int(market_id),
        "is_long": bool(is_long),
        "size": int(size),
        "price": int(price),
        "order_type": _perp_order_type_param(order_type, slippage),
        "take_profit": _optional_u128(take_profit),
        "stop_loss": _optional_u128(stop_loss),
        "reduce_only": bool(reduce_only),
        "post_only": _post_only_param(post_only),
    }


def _optional_u128(value: Optional[int]) -> int | None:
    return None if value is None else int(value)


def _optional_u64(value: Optional[int]) -> int | None:
    return None if value is None else int(value)


def _perp_order_type_u8(value: int) -> int:
    order_type = int(value)
    if order_type not in {0, 1, 2, 3}:
        raise ValueError(f"invalid perp order_type: {value}")
    return order_type


def _perp_order_type_param(value: int, slippage: Optional[int] = None) -> Any:
    # On-chain OrderType is `Limit(TimeInForce) | Market(Option<u64>) | Stop`
    # (primitives/src/types.rs). scalecodec encodes a payload-carrying variant
    # as {"Variant": payload} and a bare variant as the variant name string.
    #   0 Limit (GTC) -> {"Limit": "GTC"}
    #   1 Market      -> {"Market": <slippage u64 or None>}   (slippage in bps)
    #   2 Stop        -> "Stop"
    #   3 IOC         -> {"Limit": "IOC"}  (Limit with TimeInForce::IOC)
    mapping: dict[int, Any] = {
        0: lambda: {"Limit": "GTC"},
        1: lambda: {"Market": _optional_u64(slippage)},
        2: lambda: "Stop",
        3: lambda: {"Limit": "IOC"},
    }
    try:
        return mapping[int(value)]()
    except KeyError as exc:
        raise ValueError(f"invalid perp order_type: {value}") from exc


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


def _parse_position_updated_fields(fields: object) -> dict:
    if isinstance(fields, dict):
        owner = fields.get("owner")
        market_id = fields.get("market_id")
        pos = fields.get("pos")
        pnl = fields.get("pnl")
        if owner is not None and market_id is not None and pos is not None and pnl is not None:
            return {
                "owner": _parse_address_value(owner),
                "market_id": _parse_int_value(market_id),
                "pos": _parse_perp_position_value(pos),
                "pnl": _parse_int_value(pnl),
            }
        return fields
    if isinstance(fields, list) and len(fields) >= 4:
        return {
            "owner": _parse_address_value(fields[0]),
            "market_id": _parse_int_value(fields[1]),
            "pos": _parse_perp_position_value(fields[2]),
            "pnl": _parse_int_value(fields[3]),
        }
    return {"raw": fields}


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


def _parse_address_value(value: object) -> str:
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("0x"):
            return raw.lower()
        return "0x" + raw.lower()
    if isinstance(value, dict):
        if "value" in value:
            return _parse_address_value(value["value"])
        if "id" in value:
            return _parse_address_value(value["id"])
    if isinstance(value, list):
        items = [_parse_int_value(v) for v in value]
        if all(0 <= v <= 0xFF for v in items):
            return _decode_address(bytes(items))
    if isinstance(value, (bytes, bytearray)):
        return _decode_address(bytes(value))
    raise ValueError(f"unsupported address value: {value}")


def _parse_optional_u128_value(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, dict):
        if "None" in value:
            return None
        if "Some" in value:
            return _parse_int_value(value["Some"])
        if "value" in value:
            return _parse_optional_u128_value(value["value"])
    if isinstance(value, str) and value.strip().lower() == "none":
        return None
    return _parse_int_value(value)


def _parse_perp_position_value(value: object) -> dict:
    if isinstance(value, dict):
        keys = {
            "market_id",
            "is_long",
            "base_asset_amount",
            "entry_price",
            "leverage",
            "last_funding_rate",
            "version",
            "realized_pnl",
            "funding_payment",
            "owner",
            "take_profit",
            "stop_loss",
        }
        if keys.issubset(value.keys()):
            return {
                "market_id": _parse_int_value(value["market_id"]),
                "is_long": bool(value["is_long"]),
                "base_asset_amount": _parse_int_value(value["base_asset_amount"]),
                "entry_price": _parse_int_value(value["entry_price"]),
                "leverage": _parse_int_value(value["leverage"]),
                "last_funding_rate": _parse_int_value(value["last_funding_rate"]),
                "version": _parse_int_value(value["version"]),
                "realized_pnl": _parse_int_value(value["realized_pnl"]),
                "funding_payment": _parse_int_value(value["funding_payment"]),
                "owner": _parse_address_value(value["owner"]),
                "take_profit": _parse_optional_u128_value(value["take_profit"]),
                "stop_loss": _parse_optional_u128_value(value["stop_loss"]),
            }
    if isinstance(value, list) and len(value) >= 12:
        return {
            "market_id": _parse_int_value(value[0]),
            "is_long": bool(value[1]),
            "base_asset_amount": _parse_int_value(value[2]),
            "entry_price": _parse_int_value(value[3]),
            "leverage": _parse_int_value(value[4]),
            "last_funding_rate": _parse_int_value(value[5]),
            "version": _parse_int_value(value[6]),
            "realized_pnl": _parse_int_value(value[7]),
            "funding_payment": _parse_int_value(value[8]),
            "owner": _parse_address_value(value[9]),
            "take_profit": _parse_optional_u128_value(value[10]),
            "stop_loss": _parse_optional_u128_value(value[11]),
        }
    return {"raw": value}


def perp_markets(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    market_id: int,
) -> PerpMarketInfo:
    data = encode_call("perpMarkets(uint16)", ["uint16"], [market_id])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    market = _decode_perp_market_tuple(raw)
    return _decode_perp_market(market)


def user_perp_positions(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    user: str,
    market_ids: list[int],
) -> list[PerpPositionInfo]:
    data = encode_call(
        "userPerpPositions(address,uint16[])",
        ["address", "uint16[]"],
        [normalize_address(user), market_ids],
    )
    raw = evm_call(evm_rpc_url, precompile_address, data)
    return [_decode_perp_position(pos) for pos in _decode_perp_positions(raw)]


def active_pos_for_market(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    market_id: int,
) -> list[PerpPositionInfo]:
    data = encode_call("activePosForMarket(uint16)", ["uint16"], [market_id])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    return [_decode_perp_position(pos) for pos in _decode_perp_positions(raw)]


def user_active_orders(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    user: str,
) -> list[ActiveOrderInfo]:
    data = encode_call("userActiveOrders(address)", ["address"], [normalize_address(user)])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (orders,) = decode_abi([f"{_ACTIVE_ORDER_TUPLE}[]"], raw)
    return [_decode_active_order(order) for order in orders]


def order_info(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    user: str,
    order_id: int,
) -> PerpOrderInfo:
    # The order_id argument widened u32 -> u64, which changes the selector.
    # Deployments trailing that runtime still only expose the uint32 form.
    for arg_type in ("uint64", "uint32"):
        data = encode_call(
            f"orderInfo(address,{arg_type})",
            ["address", arg_type],
            [normalize_address(user), order_id],
        )
        try:
            raw = evm_call(evm_rpc_url, precompile_address, data)
        except Exception as exc:
            error_text = str(exc).lower()
            selector_error = any(
                marker in error_text
                for marker in (
                    "unknown selector",
                    "function selector",
                    "selector was not recognized",
                    "method not found",
                    "no matching function",
                )
            )
            if arg_type == "uint64" and selector_error:
                continue
            raise
        (order,) = decode_abi([_PERP_ORDER_TUPLE], raw)
        return _decode_perp_order(order)
    raise RuntimeError("orderInfo is not exposed by the perp precompile at this endpoint")


def free_deposit_for(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    account: str,
) -> int:
    data = encode_call("freeDepositFor(address)", ["address"], [normalize_address(account)])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (value,) = decode_abi(["uint128"], raw)
    return int(value)


def mark_price_for(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    market_id: int,
) -> int:
    data = encode_call("markPriceFor(uint16)", ["uint16"], [market_id])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (value,) = decode_abi(["uint128"], raw)
    return int(value)


def global_max_leverage_for(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    subaccount: str,
) -> int:
    # Scaled by LEVERAGE_PRECISION (1000): 10x = 10000.
    data = encode_call(
        "globalMaxLeverage(address)",
        ["address"],
        [normalize_address(subaccount)],
    )
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (value,) = decode_abi(["uint64"], raw)
    return int(value)


def per_market_max_leverage_for(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
) -> int:
    # Scaled by LEVERAGE_PRECISION (1000). Returns 0 when no override is set.
    data = encode_call(
        "perMarketMaxLeverage(address,uint16)",
        ["address", "uint16"],
        [normalize_address(subaccount), int(market_id)],
    )
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (value,) = decode_abi(["uint64"], raw)
    return int(value)


def effective_leverage_for(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    subaccount: str,
    market_id: int,
) -> int:
    # min(global, per_market_override.unwrap_or(global)), scaled x1000.
    data = encode_call(
        "effectiveLeverage(address,uint16)",
        ["address", "uint16"],
        [normalize_address(subaccount), int(market_id)],
    )
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (value,) = decode_abi(["uint64"], raw)
    return int(value)


def last_trade_price_for(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    market_id: int,
) -> int:
    data = encode_call("lastTradePriceFor(uint16)", ["uint16"], [market_id])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (value,) = decode_abi(["uint128"], raw)
    return int(value)


def total_collateral_and_margin_required_for(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    account: str,
    direction: int,
) -> TotalCollateralAndMarginInfo:
    data = encode_call(
        "totalCollateralAndMarginRequiredFor(address,uint8)",
        ["address", "uint8"],
        [normalize_address(account), direction],
    )
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (value,) = decode_abi([_TOTAL_COLLATERAL_TUPLE], raw)
    return TotalCollateralAndMarginInfo(collateral=int(value[0]), margin_required=int(value[1]))


def get_liquidate_price(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    account: str,
    market_id: int,
) -> int | None:
    data = encode_call(
        "getLiquidatePrice(address,uint16)",
        ["address", "uint16"],
        [normalize_address(account), market_id],
    )
    try:
        raw = evm_call(evm_rpc_url, precompile_address, data)
    except RuntimeError as exc:
        if "return none" in str(exc):
            return None
        raise
    (value,) = decode_abi(["uint128"], raw)
    return int(value)


def get_oracle_price_all(
    *,
    evm_rpc_url: str,
    precompile_address: str,
) -> list[OraclePriceInfo]:
    data = encode_call("getOraclePriceAll()", [], [])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (prices,) = decode_abi([f"{_ORACLE_PRICE_TUPLE}[]"], raw)
    return [OraclePriceInfo(symbol=_decode_bytes(p[0]), price=int(p[1])) for p in prices]


_ORDER_SPEC_TUPLE = "(uint128,uint128,uint128)"
_PERP_MARKET_TUPLE = (
    f"(uint16,bytes,bytes,int32,uint16,bytes,uint64,int128,uint64,uint128,uint128,uint64,"
    f"uint128,uint32,int32,{_ORDER_SPEC_TUPLE},uint128,uint128,uint128,int128,uint128,int128,int128)"
)
# Newer precompiles append cumulative_funding_index to the market view.
_PERP_MARKET_TUPLE_V2 = (
    f"(uint16,bytes,bytes,int32,uint16,bytes,uint64,int128,uint64,uint128,uint128,uint64,"
    f"uint128,uint32,int32,{_ORDER_SPEC_TUPLE},uint128,uint128,uint128,int128,uint128,int128,int128,int128)"
)
# `order_id` widened u32 -> u64 (chain: order ids are now derived from the
# extrinsic's millisecond timestamp nonce, so they exceed 2^32). Decoding as
# uint64 also reads the pre-widening layout, since both occupy one ABI word.
_ACTIVE_ORDER_TUPLE = "(address,uint16,uint8,uint8,uint64,uint128,uint64)"
_PERP_ORDER_TUPLE = (
    "(uint64,address,uint16,bool,uint128,uint128,uint8,uint64,uint64,uint64,uint8,uint128,uint128,uint128,uint128)"
)
_PERP_POSITION_TUPLE = (
    "(uint16,bool,uint128,uint128,uint64,int128,uint64,int128,int128,address,uint128,uint128,uint128)"
)
# Newer precompiles append last_settle_price to the position view.
_PERP_POSITION_TUPLE_V2 = (
    "(uint16,bool,uint128,uint128,uint64,int128,uint64,int128,int128,address,uint128,uint128,uint128,uint128)"
)
_TOTAL_COLLATERAL_TUPLE = "(uint128,uint128)"
_ORACLE_PRICE_TUPLE = "(bytes,uint128)"


def _decode_order_spec(order_spec: tuple) -> PerpOrderSpec:
    return PerpOrderSpec(
        min_order_size=int(order_spec[0]),
        tick_size=int(order_spec[1]),
        step_size=int(order_spec[2]),
    )


def _decode_perp_market(market: tuple) -> PerpMarketInfo:
    if len(market) not in (23, 24):
        raise RuntimeError(f"unexpected perpMarkets layout length: {len(market)}")
    order_spec = _decode_order_spec(market[15])
    return PerpMarketInfo(
        id=int(market[0]),
        name=_decode_bytes(market[1]),
        base_symbol=_decode_bytes(market[2]),
        base_decimal=int(market[3]),
        quote_market_id=int(market[4]),
        network=_decode_bytes(market[5]),
        height=int(market[6]),
        funding_rate=int(market[7]),
        last_cacl_funding_rate_time=int(market[8]),
        oracle_price=int(market[9]),
        mark_price=int(market[10]),
        max_deviation_bps=int(market[11]),
        maintenance_margin_ratio=int(market[12]),
        taker_fee_rate=int(market[13]),
        maker_fee_rate=int(market[14]),
        order_spec=order_spec,
        open_interest=int(market[16]),
        long_open_pos_num=int(market[17]),
        short_open_pos_num=int(market[18]),
        base_interest_rate=int(market[19]),
        impact_margin_value=int(market[20]),
        funding_rate_clamp_upper_bound=int(market[21]),
        funding_rate_clamp_lower_bound=int(market[22]),
        base_address=None,
        quote_symbol=None,
        quote_address=None,
        quote_decimal=None,
        initial_margin_ratio=None,
        max_active_orders=None,
        is_quote_market=None,
        liquidation_spec=None,
        cumulative_funding_index=int(market[23]) if len(market) == 24 else None,
    )


def _decode_perp_market_tuple(raw: bytes) -> tuple:
    # Prefer the newer 24-field layout (with cumulative_funding_index),
    # fall back to the legacy 23-field one.
    for tuple_type in (_PERP_MARKET_TUPLE_V2, _PERP_MARKET_TUPLE):
        try:
            (market,) = decode_abi([tuple_type], raw)
            return market
        except Exception:
            continue
    raise RuntimeError("unable to decode perpMarkets response with supported ABI layouts")


def _decode_active_order(order: tuple) -> ActiveOrderInfo:
    return ActiveOrderInfo(
        owner=_decode_address(order[0]),
        market_id=int(order[1]),
        order_side=int(order[2]),
        order_type=int(order[3]),
        order_id=int(order[4]),
        price=int(order[5]),
        created_at=int(order[6]),
    )


def _decode_perp_order(order: tuple) -> PerpOrderInfo:
    return PerpOrderInfo(
        order_id=int(order[0]),
        owner=_decode_address(order[1]),
        market_id=int(order[2]),
        is_long=bool(order[3]),
        size=int(order[4]),
        price=int(order[5]),
        order_type=int(order[6]),
        create_time=int(order[7]),
        leverage=int(order[8]),
        slippage=int(order[9]),
        status=int(order[10]),
        size_filled=int(order[11]),
        size_remain=int(order[12]),
        take_profit=int(order[13]),
        stop_loss=int(order[14]),
    )


def _decode_perp_positions(raw: bytes) -> list:
    # Prefer the newer 14-field layout (with last_settle_price),
    # fall back to the legacy 13-field one.
    for tuple_type in (_PERP_POSITION_TUPLE_V2, _PERP_POSITION_TUPLE):
        try:
            (positions,) = decode_abi([f"{tuple_type}[]"], raw)
            return positions
        except Exception:
            continue
    raise RuntimeError("unable to decode perp positions response with supported ABI layouts")


def _decode_perp_position(pos: tuple) -> PerpPositionInfo:
    return PerpPositionInfo(
        market_id=int(pos[0]),
        is_long=bool(pos[1]),
        base_asset_amount=int(pos[2]),
        entry_price=int(pos[3]),
        leverage=int(pos[4]),
        last_funding_rate=int(pos[5]),
        version=int(pos[6]),
        realized_pnl=int(pos[7]),
        funding_payment=int(pos[8]),
        owner=_decode_address(pos[9]),
        take_profit=int(pos[10]),
        stop_loss=int(pos[11]),
        liquidate_price=int(pos[12]),
        last_settle_price=int(pos[13]) if len(pos) > 13 else None,
    )


def _decode_address(value: bytes) -> str:
    if isinstance(value, str):
        return value
    return "0x" + value.hex()


def _decode_bytes(value: bytes) -> str:
    if isinstance(value, str):
        return value
    try:
        decoded = value.decode("utf-8")
        if decoded.isprintable():
            return decoded
    except Exception:
        pass
    return "0x" + value.hex()
