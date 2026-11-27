from __future__ import annotations

import gzip
from pathlib import Path
from types import SimpleNamespace

import pytest
from scalecodec.base import ScaleBytes

from deepx_sdk._perp_market import _perp_cancel_params, _perp_place_params
from deepx_sdk._vendor_substrate import SubstrateInterface


SUBACCOUNT = "0x" + "22" * 20
SIZE = 10_000_000
TP = 55_000_000
SL = 45_000_000


@pytest.fixture(scope="module", params=["testnet-380", "devnet-230"])
def runtime(request):
    source = Path(__file__).with_name("fixtures") / f"{request.param}.scale.gz"
    substrate = SubstrateInterface(url="http://localhost", auto_discover=False)
    substrate.metadata = substrate.runtime_config.create_scale_object("MetadataVersioned")
    substrate.metadata.decode(ScaleBytes("0x" + gzip.decompress(source.read_bytes()).hex()))
    substrate.runtime_config.add_portable_registry(substrate.metadata)
    substrate.block_hash = "0x" + "11" * 32
    yield request.param == "devnet-230", substrate
    substrate.close()


@pytest.mark.parametrize("take_profit,stop_loss", [(None, None), (TP, None), (None, SL), (TP, SL)])
@pytest.mark.parametrize("replace", [False, True], ids=["place", "replace"])
def test_attached_tp_sl_round_trips_real_scale_metadata(
    runtime, monkeypatch, take_profit, stop_loss, replace,
):
    upgraded, substrate = runtime
    monkeypatch.setattr(substrate, "init_runtime", lambda **_: None)
    monkeypatch.setattr(substrate, "rpc_request", lambda *_: pytest.fail("Unexpected RPC request"))

    def query(module, storage, params, *, block_hash):
        assert upgraded and replace
        assert (module, storage, params, block_hash) == (
            "PerpMarket", "ConditionalOrders", [SUBACCOUNT, 111], substrate.block_hash,
        )
        return SimpleNamespace(value={"kind": "Parent", "fills": {"size": 5_000_000}})

    monkeypatch.setattr(substrate, "query", query)
    place = _perp_place_params(
        subaccount=SUBACCOUNT, market_id=1, is_long=True, size=SIZE, price=50_000_000,
        order_type=0, slippage=None, take_profit=take_profit, stop_loss=stop_loss,
        reduce_only=False, post_only=0, cloid=None,
    )
    if replace:
        cancel = _perp_cancel_params(
            subaccount=SUBACCOUNT, market_id=1, order_id=111, fast_cancel=False,
        )["params"]
        call = substrate.compose_call("Subaccount", "modify_orders", {
            "ops": [{"Cancel": {"Perp": cancel}}, {"Place": {"Perp": place}}],
        })
    else:
        call = substrate.compose_call("PerpMarket", "place_order", {"params": place})

    decoder = substrate.runtime_config.create_scale_object(
        "Call", data=ScaleBytes(call.data.to_hex()), metadata=substrate.metadata,
    )
    decoded = decoder.decode()
    assert decoded["call_function"] == ("modify_orders" if replace else "place_order")
    args = {arg["name"]: arg["value"] for arg in decoded["call_args"]}
    params = args["ops"][1]["Place"]["Perp"] if replace else args["params"]
    size = SIZE + 5_000_000 if upgraded and replace else SIZE
    assert params["size"] == size
    assert params["stp_mode"] == "None"
    for field, condition, price in (
        ("take_profit", "TakeProfit", take_profit), ("stop_loss", "StopLoss", stop_loss),
    ):
        expected = price
        if upgraded and price is not None:
            expected = {
                "condition": condition,
                "source": "Mark",
                "trigger_price": price,
                "quantity": {"Partial": size},
                "execution": {"Market": None},
            }
        assert params[field] == expected
