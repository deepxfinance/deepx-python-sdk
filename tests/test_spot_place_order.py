import os
from decimal import Decimal, getcontext

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Simple end-to-end test for client.spot_market subaccount_* spot order calls.
# Configure via environment variables to avoid hardcoding secrets.
# Example:
#   export SUBSTRATE_WS=ws://127.0.0.1:9944
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export PRIVATE_KEY=0xYOUR_PRIVATE_KEY
#   export SPOT_PRECOMPILE=0x000000000000000000000000000000000000044D
#   export SPOT_SUBACCOUNT=0xYOUR_SUBACCOUNT
#   export SPOT_PAIR=0x...32bytes...
#   export SPOT_IS_BUY=true
#   export SPOT_QUOTE_AMOUNT=21.01
#   export SPOT_QUOTE_DECIMALS=6
#   export SPOT_BASE_AMOUNT=0.01
#   export SPOT_BASE_DECIMALS=18
#   export SPOT_ORDER_TYPE=Limit      # Limit=0, Market=1
#   export SPOT_POST_ONLY=None        # None=0, MustPostOnly=1, Adaptive=2
#   export SPOT_REDUCE_ONLY=false
#   export SPOT_SLIPPAGE=0
#   export SPOT_AUTO_CANCEL=false
#   python tests/test_spot_place_order.py

getcontext().prec = 80


def parse_bool(val: str) -> bool:
    return val.strip().lower() in {"1", "true", "yes", "y"}


def parse_non_negative(val: str, label: str) -> int:
    n = int(val)
    if n < 0:
        raise ValueError(f"{label} must be non-negative: {val}")
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
    raise ValueError(f"invalid SPOT_ORDER_TYPE: {val}")


def parse_post_only(val: str) -> int:
    val = val.strip().lower()
    if val in {"0", "none"}:
        return 0
    if val in {"1", "mustpostonly", "must"}:
        return 1
    if val in {"2", "adaptive"}:
        return 2
    raise ValueError(f"invalid SPOT_POST_ONLY: {val}")


def parse_pair(val: str) -> str:
    s = val.strip()
    if not s:
        raise RuntimeError("SPOT_PAIR is required")
    raw = s[2:] if s.startswith("0x") else s
    if len(raw) != 64:
        raise ValueError("SPOT_PAIR must be 32 bytes hex (64 hex chars)")
    try:
        bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError("SPOT_PAIR must be valid hex") from exc
    return "0x" + raw


def main() -> None:
    substrate_ws = os.environ.get("SUBSTRATE_WS", "ws://127.0.0.1:9944")
    evm_rpc_url = os.environ.get("EVM_RPC_URL", "http://127.0.0.1:8545")

    private_key = os.environ.get("PRIVATE_KEY", "").strip()
    if not private_key:
        raise RuntimeError("PRIVATE_KEY is required")

    precompile = os.environ.get(
        "SPOT_PRECOMPILE", "0x000000000000000000000000000000000000044D"
    ).strip()

    subaccount = os.environ.get("SPOT_SUBACCOUNT", "").strip()
    if not subaccount:
        subaccount = os.environ.get("ORDER_SUBACCOUNT", "").strip()
    if not subaccount:
        raise RuntimeError("SPOT_SUBACCOUNT is required")

    pair = parse_pair(os.environ.get("SPOT_PAIR", ""))
    is_buy = parse_bool(os.environ.get("SPOT_IS_BUY", "true"))

    quote_amount = parse_scaled(
        "SPOT_QUOTE_AMOUNT_RAW",
        "SPOT_QUOTE_AMOUNT",
        "0",
        "SPOT_QUOTE_DECIMALS",
    )
    base_amount = parse_scaled(
        "SPOT_BASE_AMOUNT_RAW",
        "SPOT_BASE_AMOUNT",
        "0",
        "SPOT_BASE_DECIMALS",
    )

    if quote_amount == 0 and base_amount == 0:
        raise RuntimeError("SPOT_QUOTE_AMOUNT or SPOT_BASE_AMOUNT must be non-zero")

    order_type = parse_order_type(os.environ.get("SPOT_ORDER_TYPE", "Limit"))
    post_only = parse_post_only(os.environ.get("SPOT_POST_ONLY", "None"))
    reduce_only = parse_bool(os.environ.get("SPOT_REDUCE_ONLY", "false"))

    slippage_raw = os.environ.get("SPOT_SLIPPAGE", "0").strip()
    slippage_val = parse_non_negative(slippage_raw, "SPOT_SLIPPAGE")
    slippage = None if slippage_val == 0 else slippage_val

    auto_cancel = parse_bool(os.environ.get("SPOT_AUTO_CANCEL", "false"))

    client = dx.ChainClient(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        spot_precompile_address=precompile,
        subaccount=subaccount,
    )

    if order_type == 0:
        if is_buy:
            res = client.spot_market.subaccount_place_order_buy_b(
                pair=pair,
                quote_amount=quote_amount,
                base_amount=base_amount,
                post_only=post_only,
                reduce_only=reduce_only,
            )
        else:
            res = client.spot_market.subaccount_place_order_sell_b(
                pair=pair,
                quote_amount=quote_amount,
                base_amount=base_amount,
                post_only=post_only,
                reduce_only=reduce_only,
            )
    else:
        if slippage is None:
            if is_buy:
                res = client.spot_market.subaccount_place_market_order_buy_b_without_price(
                    pair=pair,
                    quote_amount=quote_amount,
                    base_amount=base_amount,
                    auto_cancel=auto_cancel,
                    reduce_only=reduce_only,
                )
            else:
                res = client.spot_market.subaccount_place_market_order_sell_b_without_price(
                    pair=pair,
                    quote_amount=quote_amount,
                    base_amount=base_amount,
                    auto_cancel=auto_cancel,
                    reduce_only=reduce_only,
                )
        else:
            if is_buy:
                res = client.spot_market.subaccount_place_market_order_buy_b_with_price(
                    pair=pair,
                    quote_amount=quote_amount,
                    base_amount=base_amount,
                    slippage=slippage,
                    auto_cancel=auto_cancel,
                    reduce_only=reduce_only,
                )
            else:
                res = client.spot_market.subaccount_place_market_order_sell_b_with_price(
                    pair=pair,
                    quote_amount=quote_amount,
                    base_amount=base_amount,
                    slippage=slippage,
                    auto_cancel=auto_cancel,
                    reduce_only=reduce_only,
                )

    print("order_id =", res.order_id)
    print("tx_hash  =", res.tx_hash)


if __name__ == "__main__":
    main()
