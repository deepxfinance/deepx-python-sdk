from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from eth_abi import encode

from deepx_sdk import ApiClient, ChainClient, signing
from deepx_sdk import _perp_market as perp, _spot_market as spot
from deepx_sdk._abi import decode_abi
from deepx_sdk._stp import compose_order_call, normalize_stp_mode
from deepx_sdk.async_client import AsyncPerpMarketClient, AsyncSpotMarketClient

ADDRESS = "0x" + "11" * 20
PAIR = "0x" + "22" * 32
MODES = [(None, None), ("NONE", "None"), ("EXPIRE_MAKER", "ExpireMaker"),
         ("EXPIRE_TAKER", "ExpireTaker"), ("EXPIRE_BOTH", "ExpireBoth")]


@pytest.mark.parametrize("value,expected", MODES)
def test_stp_modes(value, expected):
    assert normalize_stp_mode(value) == expected


@pytest.mark.parametrize("value", [False, 1, "", "expire", "ExpireMaker"])
def test_invalid_stp_modes(value):
    with pytest.raises(ValueError, match="stp_mode"):
        normalize_stp_mode(value)


class MetadataRuntime:
    def __init__(self, supported):
        self.supported = supported
        self.block_hash = "0xfrozen"
        self.metadata = object()
        self.calls = []
        self.runtime_config = SimpleNamespace(create_scale_object=self.codec)

    def init_runtime(self, *, block_hash):
        assert block_hash in (None, self.block_hash)

    def get_metadata_call_function(self, module, function, *, block_hash):
        assert module in ("SpotMarket", "PerpMarket") and function == "place_order"
        assert block_hash == self.block_hash
        return SimpleNamespace(value={"fields": [{"name": "params", "type": 256}]})

    def codec(self, type_name, *, metadata):
        assert type_name == "scale_info::256" and metadata is self.metadata
        return SimpleNamespace(type_mapping=[("subaccount", "H160")] +
                               ([("stp_mode", "Option<StpMode>")] if self.supported else []))

    def compose_call(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["call_params"]


@pytest.mark.parametrize("module,key", [("PerpMarket", "market_id"), ("SpotMarket", "pair")])
@pytest.mark.parametrize("mode,encoded", MODES)
@pytest.mark.parametrize("supported", [True, False])
def test_metadata_guard_preserves_explicit_protection(module, key, mode, encoded, supported):
    runtime = MetadataRuntime(supported)
    params = {"params": {key: 3 if key == "market_id" else PAIR, "stp_mode": encoded}}
    original = copy.deepcopy(params)
    if not supported and mode is not None:
        with pytest.raises(ValueError, match="does not support stp_mode"):
            compose_order_call(runtime, call_module=module, call_function="place_order", call_params=params)
        assert runtime.calls == []
    else:
        result = compose_order_call(runtime, call_module=module, call_function="place_order", call_params=params)
        assert ("stp_mode" in result["params"]) is supported
        if supported:
            assert result["params"]["stp_mode"] == encoded
        assert runtime.calls[0]["block_hash"] == runtime.block_hash
    assert params == original


def test_metadata_guard_handles_atomic_modify_and_batch_orders():
    runtime = MetadataRuntime(False)
    params = {"ops": [{"Place": {"Perp": {"market_id": 3, "stp_mode": None}}},
                      {"Place": {"Spot": {"pair": PAIR, "stp_mode": "ExpireBoth"}}}]}
    with pytest.raises(ValueError, match="SpotMarket.*does not support"):
        compose_order_call(runtime, call_module="Subaccount", call_function="modify_orders", call_params=params)
    assert params["ops"][0]["Place"]["Perp"]["stp_mode"] is None
    assert runtime.calls == []


@pytest.fixture
def native_orders(monkeypatch):
    calls = []
    def submit(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(fields_json=json.dumps({"order_id": 99}), tx_hash="0xtx", extrinsic_hash="0xtx")
    monkeypatch.setattr(perp, "submit_pallet_call_wait_event", submit)
    monkeypatch.setattr(spot, "submit_pallet_call_wait_event", submit)
    monkeypatch.setattr(perp, "_submit_modify_orders", submit)
    monkeypatch.setattr(spot, "_submit_modify_orders", submit)
    client = ChainClient(private_key="0x"+"33"*32, subaccount=ADDRESS)
    yield client, calls
    client.close()


@pytest.mark.parametrize("kind", ["limit", "market", "ioc", "stop"])
@pytest.mark.parametrize("mode,encoded", MODES)
def test_sync_perp_order_mode(native_orders, kind, mode, encoded):
    client, calls = native_orders
    client.perp_market.place_order(market_id=3, side="buy", size=10,
        price=None if kind == "market" else 2000, order_type=kind, stp_mode=mode)
    assert calls[-1]["call_params"]["params"]["stp_mode"] == encoded


@pytest.mark.parametrize("kind,slippage", [("limit", None), ("ioc", None), ("market", None), ("market", 25)])
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_sync_spot_order_mode(native_orders, kind, slippage, side):
    client, calls = native_orders
    client.spot_market.place_order(pair=PAIR, side=side, quote_amount=100, base_amount=10,
                                  order_type=kind, slippage=slippage, stp_mode="NONE")
    assert calls[-1]["call_params"]["params"]["stp_mode"] == "None"


def test_modify_modes_reach_nested_order(native_orders):
    client, calls = native_orders
    client.perp_market.modify_order(market_id=3, order_id=1, is_long=True, price=2000,
                                    size=10, stp_mode="EXPIRE_TAKER")
    assert calls[-1]["ops"][1]["Place"]["Perp"]["stp_mode"] == "ExpireTaker"
    client.spot_market.modify_order(pair=PAIR, order_id=1, side="sell", quote_amount=100,
                                    base_amount=10, stp_mode="EXPIRE_BOTH")
    assert calls[-1]["ops"][1]["Place"]["Spot"]["stp_mode"] == "ExpireBoth"


@pytest.mark.parametrize("market", ["perp", "spot"])
@pytest.mark.parametrize("mode,encoded", MODES)
def test_async_order_modes(market, mode, encoded):
    calls = []
    async def submit(**kwargs):
        calls.append(kwargs)
        return "pending"
    client = SimpleNamespace(subaccount=ADDRESS, _submit=submit)
    if market == "perp":
        api = AsyncPerpMarketClient(client)
        kwargs = dict(market_id=3, size=10, price=2000)
    else:
        api = AsyncSpotMarketClient(client)
        kwargs = dict(pair=PAIR, quote_amount=100, base_amount=10)
    assert asyncio.run(api.place_order(side="buy", stp_mode=mode, **kwargs)) == "pending"
    assert calls[-1]["call_params"]["params"]["stp_mode"] == encoded


@pytest.mark.parametrize("market", ["perp", "spot"])
def test_standalone_order_signing_forwards_stp(monkeypatch, market):
    calls = []
    monkeypatch.setattr(signing, "_build_signed", lambda **kwargs: calls.append(kwargs))
    common = dict(substrate_ws="wss://node", private_key="key", subaccount=ADDRESS, stp_mode="EXPIRE_BOTH")
    if market == "perp":
        signing.build_signed_perp_order(**common, market_id=3, is_long=True, size=10, price=2000)
    else:
        signing.build_signed_spot_order(**common, pair=PAIR, side="sell", quote_amount=100, base_amount=10)
    assert calls[-1]["call_params"]["params"]["stp_mode"] == "ExpireBoth"


@pytest.mark.parametrize("market", ["perp", "spot"])
def test_rest_order_signing_forwards_stp(monkeypatch, market):
    api = ApiClient(subaccount=ADDRESS).v1.chain_tx
    calls = []
    monkeypatch.setattr(api, "_sign_pallet_call_and_submit", lambda **kwargs: calls.append(kwargs))
    if market == "perp":
        api.place_perp_order_limit(market_id=3, is_long=True, size=10, price=2000, stp_mode="NONE")
    else:
        api.place_spot_order_buy(pair=PAIR, quote_amount=100, base_amount=10, stp_mode="NONE")
    assert calls[-1]["call_params"]["params"]["stp_mode"] == "None"


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_new_perp_order_view_exposes_mode(monkeypatch, mode):
    value = (1, ADDRESS, 3, True, 100, 2000, 0, 123, 10, 0, 0, 0, 100, 0, 0, mode)
    raw = encode([perp._PERP_ORDER_TUPLE_STP], [value])
    monkeypatch.setattr(perp, "evm_call", lambda *args: raw)
    result = perp.order_info(evm_rpc_url="https://rpc", precompile_address=ADDRESS, user=ADDRESS, order_id=1)
    assert result.stp_mode == MODES[mode+1][0]


@pytest.mark.parametrize("layout,has_mode", [(spot._SPOT_ORDER_TUPLE_STP, True),
    (spot._SPOT_ORDER_TUPLE, False), (spot._SPOT_ORDER_TUPLE_LEGACY, False)])
@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_spot_array_stride_is_correct_for_every_order(layout, has_mode, count):
    values=[]
    for index in range(count):
        order_id = 2**80 + index if layout == spot._SPOT_ORDER_TUPLE_LEGACY else index + 1
        order = (bytes.fromhex(PAIR[2:]), order_id, ADDRESS, 2000+index, 100, 10, 123, 0, True, 0, 0)
        values.append(order + (index % 4,) if has_mode else order)
    raw = encode([layout+"[]"], [values])
    orders = [spot._decode_spot_order(order) for order in spot._decode_spot_orders_tuple(raw)]
    assert [o.id for o in orders] == [v[1] for v in values]
    assert [o.price for o in orders] == [2000+i for i in range(count)]
    assert [o.stp_mode for o in orders] == [MODES[i % 4+1][0] if has_mode else None for i in range(count)]
    assert decode_abi([layout+"[]"], raw)[0] == tuple(values)


@pytest.mark.parametrize("market", ["perp", "spot"])
def test_sync_ticket_forwards_mode_to_async_submission(native_orders, monkeypatch, market):
    client, _ = native_orders
    calls = []
    def place_order(**kwargs):
        calls.append(kwargs)
        return "pending"
    inner = SimpleNamespace(perp_market=SimpleNamespace(place_order=place_order),
                            spot_market=SimpleNamespace(place_order=place_order))
    runtime = SimpleNamespace(submit=lambda callback: callback(inner))
    monkeypatch.setattr(client, "_get_ticket_runtime", lambda: runtime)
    if market == "perp":
        result = client.perp_market.submit_order(side="buy", size=10, price=2000,
                                                 market_id=3, stp_mode="EXPIRE_TAKER")
    else:
        result = client.spot_market.submit_order(side="sell", pair=PAIR,
            quote_amount=100, base_amount=10, stp_mode="EXPIRE_TAKER")
    assert result == "pending" and calls[-1]["stp_mode"] == "EXPIRE_TAKER"


@pytest.mark.parametrize("value,expected_bytes", [(None, b"\x00"), ("NONE", b"\x01\x00"),
    ("EXPIRE_MAKER", b"\x01\x01"), ("EXPIRE_TAKER", b"\x01\x02"), ("EXPIRE_BOTH", b"\x01\x03")])
def test_optional_stp_scale_discriminants_match_node(value, expected_bytes):
    from scalecodec.base import RuntimeConfigurationObject
    runtime = RuntimeConfigurationObject()
    runtime.update_type_registry({"types": {"StpMode": {"type": "enum", "value_list": [
        "None", "ExpireMaker", "ExpireTaker", "ExpireBoth",
    ]}}})
    codec = runtime.create_scale_object("Option<StpMode>")
    assert bytes(codec.encode(normalize_stp_mode(value)).data) == expected_bytes
