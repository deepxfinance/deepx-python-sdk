import os

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Simple end-to-end test for lending view calls via client.lending.*
# Configure via environment variables to avoid hardcoding secrets.
# Example:
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export LENDING_PRECOMPILE=0x0000000000000000000000000000000000000450
#   export LENDING_MARKET_ID=1
#   export LENDING_ACCOUNT=0xYOUR_SUBACCOUNT
#   export LENDING_ASSET=usdc
#   python tests/test_lending_views.py


def parse_u8(val: str) -> int:
    n = int(val)
    if n < 0 or n > 0xFF:
        raise ValueError(f"invalid u8: {val}")
    return n


def main() -> None:
    evm_rpc_url = os.environ.get("EVM_RPC_URL", "").strip()
    if not evm_rpc_url:
        raise RuntimeError("EVM_RPC_URL is required")

    precompile = os.environ.get(
        "LENDING_PRECOMPILE", "0x0000000000000000000000000000000000000450"
    ).strip()

    market_id = parse_u8(os.environ.get("LENDING_MARKET_ID", "1"))

    account = (
        os.environ.get("LENDING_ACCOUNT", "").strip()
        or os.environ.get("VIEW_SUBACCOUNT", "").strip()
        or os.environ.get("ORDER_SUBACCOUNT", "").strip()
    )

    asset = os.environ.get("LENDING_ASSET", "").strip()

    client = dx.ChainClient(
        substrate_ws=os.environ.get("SUBSTRATE_WS", "ws://127.0.0.1:9944"),
        evm_rpc_url=evm_rpc_url,
        private_key=os.environ.get("PRIVATE_KEY", "0x" + "00" * 32),
        lending_precompile_address=precompile,
        subaccount=account or ("0x" + "00" * 20),
    )

    market = client.lending.lending_markets(market_id=market_id)
    print("lending_markets:", market)

    pools = client.lending.asset_pools(market_id=market_id)
    print("asset_pools:", pools)

    if account:
        health = client.lending.health_for(subaccount=account)
        print("health_for:", health)
    else:
        print("health_for: skipped (LENDING_ACCOUNT not set)")

    if account and asset:
        max_borrow = client.lending.max_borrow_amount_for(
            account=account,
            lending_market=market_id,
            asset=asset,
        )
        print("max_borrow_amount_for:", max_borrow)

        max_withdraw = client.lending.max_withdraw_amount_for(
            account=account,
            lending_market=market_id,
            asset=asset,
        )
        print("max_withdraw_amount_for:", max_withdraw)
    else:
        print("max_*_amount_for: skipped (LENDING_ACCOUNT or LENDING_ASSET not set)")


if __name__ == "__main__":
    main()
