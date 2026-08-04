import os
from typing import List

import deepx_sdk as dx
from _test_output import make_print

print = make_print()  # type: ignore[assignment]

# Simple end-to-end test for perp view calls via client.perp_market.*
# Configure via environment variables to avoid hardcoding secrets.
# Example:
#   export EVM_RPC_URL=http://127.0.0.1:8545
#   export PERP_PRECOMPILE=0x000000000000000000000000000000000000044E
#   export VIEW_SUBACCOUNT=0xYOUR_SUBACCOUNT
#   export VIEW_MARKET_ID=3
#   export VIEW_MARKET_IDS=3,4
#   export VIEW_ORDER_ID=33
#   export VIEW_DIRECTION=0
#   python tests/test_perp_views.py


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


def parse_market_ids(raw: str, fallback: int) -> List[int]:
    raw = raw.strip()
    if not raw:
        return [fallback]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return [parse_u16(p) for p in parts]


def main() -> None:
    evm_rpc_url = os.environ.get("EVM_RPC_URL", "").strip()
    if not evm_rpc_url:
        raise RuntimeError("EVM_RPC_URL is required")

    precompile = os.environ.get(
        "PERP_PRECOMPILE", "0x000000000000000000000000000000000000044E"
    ).strip()

    subaccount = os.environ.get("VIEW_SUBACCOUNT", "").strip()
    if not subaccount:
        subaccount = os.environ.get("ORDER_SUBACCOUNT", "").strip()
    if not subaccount:
        raise RuntimeError("VIEW_SUBACCOUNT is required")

    market_id = parse_u16(os.environ.get("VIEW_MARKET_ID", "3"))
    market_ids = parse_market_ids(os.environ.get("VIEW_MARKET_IDS", ""), market_id)

    order_id_raw = os.environ.get("VIEW_ORDER_ID", "").strip()
    order_id = int(order_id_raw) if order_id_raw else None

    direction = parse_u8(os.environ.get("VIEW_DIRECTION", "0"))

    client = dx.ChainClient(
        substrate_ws=os.environ.get("SUBSTRATE_WS", "ws://127.0.0.1:9944"),
        evm_rpc_url=evm_rpc_url,
        private_key=os.environ.get("PRIVATE_KEY", "0x" + "00" * 32),
        perp_precompile_address=precompile,
        subaccount=subaccount,
    )

    market = client.perp_market.perp_markets(market_id=market_id)
    print("perp_markets:", market)

    positions = client.perp_market.user_perp_positions(user=subaccount, market_ids=market_ids)
    print("user_perp_positions:", positions)

    active_pos = client.perp_market.active_pos_for_market(market_id=market_id)
    print("active_pos_for_market:", active_pos)

    active_orders = client.perp_market.user_active_orders(user=subaccount)
    print("user_active_orders:", active_orders)

    if order_id is not None:
        order = client.perp_market.order_info(user=subaccount, order_id=order_id)
        print("order_info:", order)
    else:
        print("order_info: skipped (VIEW_ORDER_ID not set)")

    free = client.perp_market.free_deposit_for(account=subaccount)
    print("free_deposit_for:", free)

    mark = client.perp_market.mark_price_for(market_id=market_id)
    print("mark_price_for:", mark)

    last = client.perp_market.last_trade_price_for(market_id=market_id)
    print("last_trade_price_for:", last)

    totals = client.perp_market.total_collateral_and_margin_required_for(
        account=subaccount,
        direction=direction,
    )
    print("total_collateral_and_margin_required_for:", totals)

    liq = client.perp_market.get_liquidate_price(account=subaccount, market_id=market_id)
    print("get_liquidate_price:", liq)

    oracle = client.perp_market.get_oracle_price_all()
    print("get_oracle_price_all:", oracle)


if __name__ == "__main__":
    main()
