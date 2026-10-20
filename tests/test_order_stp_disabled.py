from __future__ import annotations

import pytest
from scalecodec.base import RuntimeConfigurationObject
from scalecodec.type_registry import load_type_registry_preset

from deepx_sdk import ApiClient, _perp_market, _spot_market


@pytest.mark.parametrize("path", ["chain_perp", "chain_spot", "api_perp", "api_spot"])
def test_order_stp_encodes_some_none_instead_of_chain_default(monkeypatch, path):
    address = "0x" + "11" * 20
    pair = "0x" + "22" * 32
    if path == "chain_perp":
        params = _perp_market._perp_place_params(
            subaccount=address, market_id=3, is_long=True, size=100, price=200,
            order_type=0, slippage=None, take_profit=None, stop_loss=None,
            reduce_only=False, post_only=0, cloid=None,
        )
    elif path == "chain_spot":
        params = _spot_market._spot_place_params(
            subaccount=address, pair=pair, is_buy=True, quote_amount=100,
            base_amount=200, order_type={"Limit": "GTC"}, post_only="None",
            reduce_only=False, cloid=None,
        )["params"]
    else:
        tx = ApiClient(subaccount=address).v1.chain_tx
        monkeypatch.setattr(
            tx, "_sign_pallet_call_and_submit",
            lambda **kwargs: kwargs["call_params"]["params"],
        )
        if path == "api_perp":
            params = tx.place_perp_order(market_id=3, is_long=True, size=100, price=200, order_type=0)
        else:
            params = tx.place_spot_order_buy(pair=pair, quote_amount=100, base_amount=200)

    runtime = RuntimeConfigurationObject()
    runtime.update_type_registry(load_type_registry_preset("core"))
    # Node StpMode discriminants: None=0, ExpireMaker=1, ExpireTaker=2, ExpireBoth=3.
    runtime.update_type_registry({"types": {"StpMode": {
        "type": "enum", "value_list": ["None", "ExpireMaker", "ExpireTaker", "ExpireBoth"],
    }}})
    option = runtime.create_scale_object("Option<StpMode>")
    assert option.encode(params["stp_mode"]).to_hex() == "0x0100"
    assert runtime.create_scale_object("Option<StpMode>").encode(None).to_hex() == "0x00"
