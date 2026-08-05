import os
import pathlib
import sys
import json
from decimal import Decimal, getcontext

_sys_root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_sys_root / "src"))

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Simple end-to-end test for client.perp_market.set_profit_and_loss_point.
# Configure via environment variables to avoid hardcoding secrets.
# Example:
#   export SUBSTRATE_WS=ws://127.0.0.1:9944
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export PRIVATE_KEY=0xYOUR_PRIVATE_KEY
#   export PERP_PRECOMPILE=0x000000000000000000000000000000000000044E
#   export ORDER_SUBACCOUNT=0xYOUR_SUBACCOUNT
#   export ORDER_MARKET_ID=3
#   export TAKE_PROFIT=2500
#   export STOP_LOSS=2100
#   export PRICE_DECIMALS=6
#   python tests/test_set_profit_and_loss_point.py

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
    decimals = int(os.environ.get(decimals_key, "18").strip())
    factor = Decimal(10) ** Decimal(decimals)
    return int(Decimal(raw) * factor)


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

    take_profit = parse_scaled(
        "TAKE_PROFIT_RAW",
        "TAKE_PROFIT",
        "0",
        "PRICE_DECIMALS",
    )
    stop_loss = parse_scaled(
        "STOP_LOSS_RAW",
        "STOP_LOSS",
        "0",
        "PRICE_DECIMALS",
    )

    take_profit_opt = None if take_profit == 0 else take_profit
    stop_loss_opt = None if stop_loss == 0 else stop_loss
    

    client = dx.ChainClient(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        perp_precompile_address=precompile,
        subaccount=subaccount,
    )

    pos = client.perp_market.user_perp_positions(user=subaccount, market_ids=[market_id])
    mark = client.perp_market.mark_price_for(market_id=market_id)
    print(pos)
    print("mark_price =", mark)
    print("take_profit =", take_profit_opt)
    print("stop_loss =", stop_loss_opt)

    res = client.perp_market.set_profit_and_loss_point(
        market_id=market_id,
        take_profit_point=take_profit_opt,
        stop_loss_point=stop_loss_opt,
    )

    print("tx_hash       =", res.tx_hash)
    print("extrinsic_hash=", res.extrinsic_hash)
    print("event_fields  =", json.dumps(res.fields, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
