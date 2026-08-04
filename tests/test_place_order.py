import os
from decimal import Decimal, getcontext

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Simple end-to-end test for client.perp_market.place_order.
# Configure via environment variables to avoid hardcoding secrets.
# Example:
#   export SUBSTRATE_WS=ws://127.0.0.1:9944
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export PRIVATE_KEY=0xYOUR_PRIVATE_KEY
#   export PERP_PRECOMPILE=0x000000000000000000000000000000000000044E
#   export ORDER_SUBACCOUNT=0xYOUR_SUBACCOUNT
#   export ORDER_MARKET_ID=3
#   export ORDER_IS_LONG=true
#   export ORDER_MODE=limit|market  # optional, uses new SDK methods
#   export ORDER_SIZE=0.01
#   export ORDER_SIZE_DECIMALS=18
#   export ORDER_PRICE=2011.61
#   export ORDER_PRICE_DECIMALS=6
#   export ORDER_TYPE=Limit        # Limit=0, Market=1, Stop=2
#   export ORDER_LEVERAGE=10
#   export ORDER_TAKE_PROFIT=0
#   export ORDER_TP_DECIMALS=6
#   export ORDER_STOP_LOSS=0
#   export ORDER_SL_DECIMALS=6
#   export ORDER_REDUCE_ONLY=false
#   export ORDER_POST_ONLY=None    # None=0, MustPostOnly=1, Adaptive=2
#   export AUTO_PRICE=true         # Use midpoint of allowed bounds
#   python tests/test_place_order.py

getcontext().prec = 80


def parse_bool(val: str) -> bool:
    return val.strip().lower() in {"1", "true", "yes", "y"}


def parse_u16(val: str) -> int:
    n = int(val)
    if n < 0 or n > 0xFFFF:
        raise ValueError(f"invalid u16: {val}")
    return n


def parse_u8(val: str) -> int:
    n = int(val)
    if n < 0 or n > 0xFF:
        raise ValueError(f"invalid u8: {val}")
    return n


def parse_scaled(raw_key: str, val_key: str, default_val: str, decimals_key: str) -> int:
    if raw_key in os.environ and os.environ[raw_key].strip():
        return int(os.environ[raw_key].strip())

    raw = os.environ.get(val_key, default_val).strip()
    decimals = int(os.environ.get(decimals_key, "18").strip())
    factor = Decimal(10) ** Decimal(decimals)
    return int(Decimal(raw) * factor)


def parse_order_type(val: str) -> int:
    val = val.strip().lower()
    if val in {"0", "limit"}:
        return 0
    if val in {"1", "market"}:
        return 1
    if val in {"2", "stop"}:
        return 2
    raise ValueError(f"invalid ORDER_TYPE: {val}")


def parse_post_only(val: str) -> int:
    val = val.strip().lower()
    if val in {"0", "none"}:
        return 0
    if val in {"1", "mustpostonly", "must"}:
        return 1
    if val in {"2", "adaptive"}:
        return 2
    raise ValueError(f"invalid ORDER_POST_ONLY: {val}")


def main() -> None:
    substrate_ws = os.environ.get("SUBSTRATE_WS", "ws://127.0.0.1:9944")
    evm_rpc_url = os.environ.get("EVM_RPC_URL", "http://127.0.0.1:8545")

    private_key = os.environ.get("PRIVATE_KEY", "").strip()
    if not private_key:
        raise RuntimeError("PRIVATE_KEY is required")

    precompile = os.environ.get(
        "PERP_PRECOMPILE", "0x000000000000000000000000000000000000044E"
    ).strip()

    subaccount = os.environ.get("ORDER_SUBACCOUNT", "").strip()
    if not subaccount:
        raise RuntimeError("ORDER_SUBACCOUNT is required")

    market_id = parse_u16(os.environ.get("ORDER_MARKET_ID", "3"))
    is_long = parse_bool(os.environ.get("ORDER_IS_LONG", "true"))

    size = parse_scaled("ORDER_SIZE_RAW", "ORDER_SIZE", "0.01", "ORDER_SIZE_DECIMALS")
    price = parse_scaled("ORDER_PRICE_RAW", "ORDER_PRICE", "2868.61", "ORDER_PRICE_DECIMALS")

    order_mode = os.environ.get("ORDER_MODE", "legacy").strip().lower()
    order_type = parse_order_type(os.environ.get("ORDER_TYPE", "Limit"))
    leverage = parse_u8(os.environ.get("ORDER_LEVERAGE", "10"))

    take_profit = parse_scaled("ORDER_TAKE_PROFIT_RAW", "ORDER_TAKE_PROFIT", "0", "ORDER_TP_DECIMALS")
    stop_loss = parse_scaled("ORDER_STOP_LOSS_RAW", "ORDER_STOP_LOSS", "0", "ORDER_SL_DECIMALS")

    reduce_only = parse_bool(os.environ.get("ORDER_REDUCE_ONLY", "false"))
    post_only = parse_post_only(os.environ.get("ORDER_POST_ONLY", "None"))

    # Use None for optional values when zero is intended as "not set".
    take_profit_opt = None if take_profit == 0 else take_profit
    stop_loss_opt = None if stop_loss == 0 else stop_loss

    client = dx.ChainClient(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        perp_precompile_address=precompile,
        subaccount=subaccount,
    )

    bounds = client.market.get_perp_price_bounds(market_id)
    print("mark_price =", bounds.mark_price)
    print("price_lower =", bounds.lower)
    print("price_upper =", bounds.upper)
    print("max_deviation_bps =", bounds.max_deviation_bps)
    print("base_decimal =", bounds.base_decimal)
    print("tick_size =", bounds.tick_size)
    print("step_size =", bounds.step_size)
    print("min_order_size =", bounds.min_order_size)
    print("price_raw =", price)

    auto_price = parse_bool(os.environ.get("AUTO_PRICE", "false"))
    if auto_price and order_mode != "market":
        mid = (bounds.lower + bounds.upper) // 2
        if bounds.tick_size > 0:
            price = (mid // bounds.tick_size) * bounds.tick_size
            if price < bounds.lower:
                price = ((bounds.lower + bounds.tick_size - 1) // bounds.tick_size) * bounds.tick_size
            if price > bounds.upper:
                price = (bounds.upper // bounds.tick_size) * bounds.tick_size
        else:
            price = mid
        print("auto_price_raw =", price)

    if order_mode != "market" and (price < bounds.lower or price > bounds.upper):
        raise RuntimeError(
            "price is outside allowed bounds; set ORDER_PRICE/ORDER_PRICE_DECIMALS or "
            "ORDER_PRICE_RAW within the range shown above"
        )

    if order_mode == "limit":
        res = client.perp_market.place_perp_order_limit(
            market_id=market_id,
            is_long=is_long,
            size=size,
            price=price,
            leverage=leverage,
            take_profit=take_profit_opt,
            stop_loss=stop_loss_opt,
            reduce_only=reduce_only,
            post_only=post_only,
        )
    elif order_mode == "market":
        if take_profit_opt is not None or stop_loss_opt is not None:
            print("warning: take_profit/stop_loss ignored for place_perp_order_market")
        res = client.perp_market.place_perp_order_market(
            market_id=market_id,
            is_long=is_long,
            size=size,
            leverage=leverage,
            reduce_only=reduce_only,
        )
    else:
        res = client.perp_market.place_perp_order(
            market_id=market_id,
            is_long=is_long,
            size=size,
            price=price,
            order_type=order_type,
            leverage=leverage,
            take_profit=take_profit_opt,
            stop_loss=stop_loss_opt,
            reduce_only=reduce_only,
            post_only=post_only,
        )

    print("order_id =", res.order_id)
    print("tx_hash  =", res.tx_hash)


if __name__ == "__main__":
    main()
