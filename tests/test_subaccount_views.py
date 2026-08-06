import os

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Simple end-to-end test for subaccount view calls via client.subaccount_client.*
# Configure via environment variables to avoid hardcoding secrets.
# Example:
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export SUBACCOUNT_PRECOMPILE=0x0000000000000000000000000000000000000451
#   export VIEW_SUBACCOUNT=0xYOUR_SUBACCOUNT
#   export VIEW_OWNER=0xYOUR_OWNER
#   python tests/test_subaccount_views.py


def main() -> None:
    evm_rpc_url = os.environ.get("EVM_RPC_URL", "").strip()
    if not evm_rpc_url:
        raise RuntimeError("EVM_RPC_URL is required")

    precompile = os.environ.get(
        "SUBACCOUNT_PRECOMPILE", "0x0000000000000000000000000000000000000451"
    ).strip()

    subaccount = os.environ.get("VIEW_SUBACCOUNT", "").strip()
    if not subaccount:
        subaccount = os.environ.get("ORDER_SUBACCOUNT", "").strip()
    if not subaccount:
        raise RuntimeError("VIEW_SUBACCOUNT is required")

    owner = os.environ.get("VIEW_OWNER", "").strip()

    client = dx.ChainClient(
        substrate_ws=os.environ.get("SUBSTRATE_WS", "ws://127.0.0.1:9944"),
        evm_rpc_url=evm_rpc_url,
        private_key=os.environ.get("PRIVATE_KEY", "0x" + "00" * 32),
        subaccount_precompile_address=precompile,
        subaccount=subaccount,
    )

    info = client.subaccount_client.subaccount_info(address=subaccount)
    print("subaccount_info:", info)

    resolved_owner = owner or info.authority or subaccount
    if not owner and info.authority:
        print(f"user_stats.owner: auto from subaccount_info.authority={resolved_owner}")

    try:
        stats = client.subaccount_client.user_stats(address=resolved_owner)
        print("user_stats:", stats)
    except RuntimeError as exc:
        msg = str(exc)
        if "UserStats does not exist" in msg:
            print(f"user_stats: skipped ({msg})")
        else:
            raise

    delegates = client.subaccount_client.delegate_accounts_for(owner=resolved_owner)
    print("delegate_accounts_for:", delegates)


if __name__ == "__main__":
    main()
