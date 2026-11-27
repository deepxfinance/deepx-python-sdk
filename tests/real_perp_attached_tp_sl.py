"""Submit a test parent order and verify the child configs in its chain event."""

from __future__ import annotations

import json
import os
import time

from deepx_sdk import _native_py
from deepx_sdk._native import submit_pallet_call, submit_pallet_call_wait_event
from deepx_sdk._perp_market import _perp_cancel_params, _perp_place_params


def main() -> None:
    private_key = os.environ.get("PRIVATE_KEY", "").strip()
    if not private_key:
        raise RuntimeError("PRIVATE_KEY must be a dedicated test wallet key")
    substrate_ws = os.environ["SUBSTRATE_WS"]
    subaccount = os.environ["ORDER_SUBACCOUNT"]
    market_id = int(os.environ["ORDER_MARKET_ID"])
    size = int(os.environ["ORDER_SIZE_RAW"])
    price = int(os.environ["ORDER_PRICE_RAW"])
    take_profit = int(os.environ["ORDER_TAKE_PROFIT_RAW"])
    stop_loss = int(os.environ["ORDER_STOP_LOSS_RAW"])
    if size <= 0 or not 0 < stop_loss < price < take_profit:
        raise ValueError("Supply a positive size and stop_loss < limit_price < take_profit")

    params = _perp_place_params(
        subaccount=subaccount, market_id=market_id, is_long=True, size=size, price=price,
        order_type=0, slippage=None, take_profit=take_profit, stop_loss=stop_loss,
        reduce_only=False, post_only=0, cloid=None,
    )
    with _native_py._create_substrate(
        _native_py._get_substrate_interface_cls(), substrate_ws, timeout_ms=30_000,
    ) as substrate:
        substrate.init_runtime()
        pallet = substrate.metadata.get_metadata_pallet("PerpMarket")
        if pallet is None or not any(
            event.name == "ConditionalOrdersSubmitted" for event in pallet.events
        ):
            raise RuntimeError("This acceptance test requires conditional order metadata")

    parent_id = time.time_ns() // 1_000_000
    print(f"Submitting test parent order {parent_id}", flush=True)
    event = submit_pallet_call_wait_event(
        substrate_ws=substrate_ws, private_key=private_key,
        call_module="PerpMarket", call_function="place_order", call_params={"params": params},
        pallet="PerpMarket", event="ConditionalOrdersSubmitted",
        nonce_ms=parent_id, timeout_ms=60_000,
    )
    try:
        fields = json.loads(event.fields_json)
        assert fields["order_id"] == parent_id
        assert fields["owner"].lower() == params["subaccount"].lower()
        assert fields["market_id"] == market_id
        children = [record for record in fields["records"] if record["kind"] == "AttachedTpSl"]
        assert len(children) == 2
        assert sorted(record["order_id"] for record in children) == sorted(fields["children"])
        assert all(record["parent_order_id"] == parent_id for record in children)
        configs = {record["config"]["condition"]: record["config"] for record in children}
        assert configs == {
            condition: {
                "condition": condition, "source": "Mark", "trigger_price": trigger_price,
                "quantity": {"Partial": size}, "execution": {"Market": None},
            }
            for condition, trigger_price in (("TakeProfit", take_profit), ("StopLoss", stop_loss))
        }
    finally:
        submit_pallet_call(
            substrate_ws=substrate_ws, private_key=private_key,
            call_module="PerpMarket", call_function="cancel_order",
            call_params=_perp_cancel_params(
                subaccount=subaccount, market_id=market_id, order_id=parent_id, fast_cancel=False,
            ),
            timeout_ms=60_000,
        )
    print(f"Verified both attached TP/SL children for {event.extrinsic_hash}")


if __name__ == "__main__":
    main()
