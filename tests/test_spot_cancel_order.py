import os

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Simple end-to-end test for client.spot_market subaccount_cancel_order_*_b.
# Configure via environment variables to avoid hardcoding secrets.
# Example:
#   export SUBSTRATE_WS=ws://127.0.0.1:9944
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export PRIVATE_KEY=0xYOUR_PRIVATE_KEY
#   export SPOT_PRECOMPILE=0x000000000000000000000000000000000000044D
#   export SPOT_SUBACCOUNT=0xYOUR_SUBACCOUNT
#   export SPOT_PAIR=0x...32bytes...
#   export SPOT_IS_BUY=true
#   export SPOT_CANCEL_ORDER_ID=12345
#   python tests/test_spot_cancel_order.py


def parse_bool(val: str) -> bool:
    return val.strip().lower() in {"1", "true", "yes", "y"}


def parse_non_negative(val: str, label: str) -> int:
    n = int(val)
    if n < 0:
        raise ValueError(f"{label} must be non-negative: {val}")
    return n


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

    order_id_raw = os.environ.get("SPOT_CANCEL_ORDER_ID", "0").strip()
    order_id = parse_non_negative(order_id_raw, "SPOT_CANCEL_ORDER_ID")
    if order_id == 0:
        raise RuntimeError("SPOT_CANCEL_ORDER_ID is required")

    client = dx.ChainClient(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        spot_precompile_address=precompile,
        subaccount=subaccount,
    )

    if is_buy:
        res = client.spot_market.subaccount_cancel_order_buy_b(pair=pair, order_id=order_id)
    else:
        res = client.spot_market.subaccount_cancel_order_sell_b(pair=pair, order_id=order_id)

    print("order_id =", res.order_id)
    print("tx_hash  =", res.tx_hash)


if __name__ == "__main__":
    main()
