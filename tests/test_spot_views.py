import os

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Simple end-to-end test for spot view calls via client.spot_market.*
# Configure via environment variables to avoid hardcoding secrets.
# Example:
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export SPOT_PRECOMPILE=0x000000000000000000000000000000000000044D
#   export VIEW_SUBACCOUNT=0xYOUR_SUBACCOUNT
#   export SPOT_PAIR=0x...32bytes...
#   python tests/test_spot_views.py


def main() -> None:
    evm_rpc_url = os.environ.get("EVM_RPC_URL", "").strip()
    if not evm_rpc_url:
        raise RuntimeError("EVM_RPC_URL is required")

    precompile = os.environ.get(
        "SPOT_PRECOMPILE", "0x000000000000000000000000000000000000044D"
    ).strip()

    subaccount = os.environ.get("VIEW_SUBACCOUNT", "").strip()
    if not subaccount:
        subaccount = os.environ.get("ORDER_SUBACCOUNT", "").strip()
    if not subaccount:
        raise RuntimeError("VIEW_SUBACCOUNT is required")

    pair = os.environ.get("SPOT_PAIR", "").strip()
    if not pair:
        raise RuntimeError("SPOT_PAIR is required")

    client = dx.ChainClient(
        substrate_ws=os.environ.get("SUBSTRATE_WS", "ws://127.0.0.1:9944"),
        evm_rpc_url=evm_rpc_url,
        private_key=os.environ.get("PRIVATE_KEY", "0x" + "00" * 32),
        spot_precompile_address=precompile,
        subaccount=subaccount,
    )

    orders = client.spot_market.user_active_spot_orders(user=subaccount, pair=pair)
    print("user_active_spot_orders:", orders)

    spec = client.spot_market.get_spot_market_spec(pair=pair)
    print("get_spot_market_spec:", spec)


if __name__ == "__main__":
    main()
