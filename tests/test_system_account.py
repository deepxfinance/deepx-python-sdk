import os

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Simple end-to-end test for system account quota via client.system.system_account
# Example:
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export SYSTEM_PRECOMPILE=0x0000000000000000000000000000000000000452
#   export VIEW_SUBACCOUNT=0xYOUR_ADDRESS
#   python tests/test_system_account.py


def main() -> None:
    evm_rpc_url = os.environ.get("EVM_RPC_URL", "").strip()
    if not evm_rpc_url:
        raise RuntimeError("EVM_RPC_URL is required")

    precompile = os.environ.get(
        "SYSTEM_PRECOMPILE", "0x0000000000000000000000000000000000000452"
    ).strip()

    address = os.environ.get("VIEW_SUBACCOUNT", "").strip()
    if not address:
        address = os.environ.get("ORDER_SUBACCOUNT", "").strip()
    if not address:
        raise RuntimeError("VIEW_SUBACCOUNT is required")

    client = dx.ChainClient(
        substrate_ws=os.environ.get("SUBSTRATE_WS", "ws://127.0.0.1:9944"),
        evm_rpc_url=evm_rpc_url,
        private_key=os.environ.get("PRIVATE_KEY", "0x" + "00" * 32),
        system_precompile_address=precompile,
        subaccount=address,
    )

    print("address:", address)
    info = client.system.system_account(address=address)
    print("system_account:", info)


if __name__ == "__main__":
    main()
