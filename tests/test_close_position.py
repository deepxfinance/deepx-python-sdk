import os
from decimal import Decimal, getcontext

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Simple end-to-end test for client.perp_market.close_position_*.
# Configure via environment variables to avoid hardcoding secrets.
# Example:
#   export SUBSTRATE_WS=ws://127.0.0.1:9944
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export PRIVATE_KEY=0xYOUR_PRIVATE_KEY
#   export PERP_PRECOMPILE=0x000000000000000000000000000000000000044E
#   export ORDER_SUBACCOUNT=0xYOUR_SUBACCOUNT
#   export ORDER_MARKET_ID=3
#   export CLOSE_PRICE=2200.5
#   export CLOSE_PRICE_DECIMALS=6
#   export CLOSE_MARKET=true
#   export CLOSE_AUTO_PRICE=true
#   export CLOSE_SLIPPAGE=0
#   python tests/test_close_position.py

getcontext().prec = 80


def parse_u16(val: str) -> int:
    n = int(val)
    if n < 0 or n > 0xFFFF:
        raise ValueError(f"invalid u16: {val}")
    return n


def parse_scaled(raw_key: str, val_key: str, default_val: str, decimals_key: str) -> int:
    if raw_key in os.environ and os.environ[raw_key].strip():
        return int(os.environ[raw_key].strip())

    raw = os.environ.get(val_key, default_val).strip()
    decimals = int(os.environ.get(decimals_key, "6").strip())
    factor = Decimal(10) ** Decimal(decimals)
    return int(Decimal(raw) * factor)


def parse_bool(val: str) -> bool:
    return val.strip().lower() in {"1", "true", "yes", "y"}


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
    price = parse_scaled("CLOSE_PRICE_RAW", "CLOSE_PRICE", "2200", "CLOSE_PRICE_DECIMALS")

    slippage_raw = os.environ.get("CLOSE_SLIPPAGE", "0").strip()
    slippage_val = int(slippage_raw)
    slippage = None if slippage_val == 0 else slippage_val
    market_close = parse_bool(os.environ.get("CLOSE_MARKET", "false"))

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
    print("price_raw =", price)

    auto_price = parse_bool(os.environ.get("CLOSE_AUTO_PRICE", os.environ.get("AUTO_PRICE", "false")))
    if auto_price:
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

    if market_close:
        price = 0
        print("market_close: price=0")
    elif price < bounds.lower or price > bounds.upper:
        raise RuntimeError(
            "price is outside allowed bounds; set CLOSE_PRICE/CLOSE_PRICE_DECIMALS or "
            "CLOSE_PRICE_RAW within the range shown above"
        )

    if market_close:
        res = client.perp_market.close_position_market(
            market_id=market_id,
            slippage=slippage,
        )
    else:
        res = client.perp_market.close_position_limit(
            market_id=market_id,
            price=price,
            slippage=slippage,
        )

    print("order_id =", res.order_id)
    print("tx_hash  =", res.tx_hash)


if __name__ == "__main__":
    main()
