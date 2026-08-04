import os

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Simple end-to-end test for client.perp_market.cancel_order.
# Configure via environment variables to avoid hardcoding secrets.
# Example:
#   export SUBSTRATE_WS=ws://127.0.0.1:9944
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export PRIVATE_KEY=0xYOUR_PRIVATE_KEY
#   export PERP_PRECOMPILE=0x000000000000000000000000000000000000044E
#   export ORDER_SUBACCOUNT=0xYOUR_SUBACCOUNT
#   export ORDER_MARKET_ID=3
#   export CANCEL_ORDER_ID=12345
#   python tests/test_cancel_order.py


def parse_u16(val: str) -> int:
    n = int(val)
    if n < 0 or n > 0xFFFF:
        raise ValueError(f"invalid u16: {val}")
    return n


def parse_u32(val: str) -> int:
    n = int(val)
    if n < 0 or n > 0xFFFF_FFFF:
        raise ValueError(f"invalid u32: {val}")
    return n


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
    order_id = parse_u32(os.environ.get("CANCEL_ORDER_ID", "0"))
    if order_id == 0:
        raise RuntimeError("CANCEL_ORDER_ID is required")

    client = dx.ChainClient(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        perp_precompile_address=precompile,
        subaccount=subaccount,
    )

    res = client.perp_market.cancel_order(market_id=market_id, order_id=order_id)

    print("order_id =", res.order_id)
    print("tx_hash  =", res.tx_hash)


if __name__ == "__main__":
    main()
