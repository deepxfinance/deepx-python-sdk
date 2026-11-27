from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from eth_abi import encode
from eth_utils import keccak

from deepx_sdk import ChainClient, ValidationError, _perp_market, _subaccount
from deepx_sdk._perp_runtime import adapt_perp_call


ADDR = "0x" + "22" * 20
ORDER_ID = 2**40 + 1


class _Runtime:
    block_hash = "0x99"

    def __init__(self, *, conditional: bool, record: dict | None = None) -> None:
        extra_call = "update_tp_sl" if conditional else "set_profit_and_loss_point"
        pallet = SimpleNamespace(calls=[
            SimpleNamespace(name="place_order", value={"fields": [{"type": 0}]}),
            SimpleNamespace(name=extra_call),
        ])
        attachment_type = "Option<TpSlConfig>" if conditional else "Option<u128>"
        fields = [{"name": "take_profit", "typeName": attachment_type}]
        registry = [{"type": {"def": {"composite": {"fields": fields}}}}]
        self.metadata = SimpleNamespace(
            get_metadata_pallet=lambda name: pallet if name == "PerpMarket" else None,
            portable_registry=SimpleNamespace(value={"types": registry}),
        )
        self.record = record
        self.queries: list[tuple] = []

    def query(self, pallet, storage, params, *, block_hash):
        self.queries.append((pallet, storage, params, block_hash))
        return SimpleNamespace(value=self.record)


def test_attached_prices_are_adapted_only_for_the_new_metadata() -> None:
    params = {
        "params": {"size": 7, "take_profit": 110, "stop_loss": 90, "stp_mode": "None"}
    }
    old = adapt_perp_call(_Runtime(conditional=False), "PerpMarket", "place_order", params)
    new = adapt_perp_call(_Runtime(conditional=True), "PerpMarket", "place_order", params)

    assert old is params
    assert new["params"] == {
        "size": 7,
        "take_profit": {
            "condition": "TakeProfit", "source": "Mark", "trigger_price": 110,
            "quantity": {"Partial": 7}, "execution": {"Market": None},
        },
        "stop_loss": {
            "condition": "StopLoss", "source": "Mark", "trigger_price": 90,
            "quantity": {"Partial": 7}, "execution": {"Market": None},
        },
        "stp_mode": "None",
    }
    assert params["params"]["take_profit"] == 110
    plain = {"params": {**params["params"], "take_profit": None, "stop_loss": None}}
    assert adapt_perp_call(_Runtime(conditional=True), "PerpMarket", "place_order", plain) == plain


@pytest.mark.parametrize(("conditional", "kind", "reduce_only", "total", "encoded_size"), [
    (False, None, False, 13, 7),
    (True, None, False, None, 7),
    (True, "Parent", False, None, 11),
    (True, "Parent", False, 13, 13),
    (True, "Entry", False, None, 11),
    (True, "PositionTpSl", True, None, 17),
])
def test_perp_modify_uses_the_correct_quantity(
    conditional, kind, reduce_only, total, encoded_size,
) -> None:
    record = None if kind is None else {
        "kind": kind, "fills": {"size": 4}, "chain_fills": {"size": 10},
    }
    substrate = _Runtime(conditional=conditional, record=record)
    cancel = {"Cancel": {"Perp": {"subaccount": ADDR, "order_id": ORDER_ID}}}
    place = {
        "size": 7, "take_profit": None if reduce_only or kind == "Entry" else 110,
        "stop_loss": None, "reduce_only": reduce_only,
    }
    params = {"ops": [cancel, {"Place": {"Perp": place}}]}
    if total is not None:
        params["_perp_total_quantity"] = total

    encoded = adapt_perp_call(substrate, "Subaccount", "modify_orders", params)

    assert set(encoded) == {"ops"}
    assert encoded["ops"][0] == cancel
    assert encoded["ops"][1]["Place"]["Perp"]["size"] == encoded_size
    if conditional and place["take_profit"] is not None:
        assert encoded["ops"][1]["Place"]["Perp"]["take_profit"]["quantity"] == {
            "Partial": encoded_size,
        }
    expected = [("PerpMarket", "ConditionalOrders", [ADDR, ORDER_ID], "0x99")] if conditional else []
    assert substrate.queries == expected
    assert place["size"] == 7


@pytest.mark.parametrize(("conditional", "unsupported"), [
    (False, "update_tp_sl"),
    (True, "set_profit_and_loss_point"),
])
def test_position_tp_sl_checks_runtime_support(conditional, unsupported) -> None:
    with pytest.raises(ValidationError, match=unsupported):
        adapt_perp_call(_Runtime(conditional=conditional), "PerpMarket", unsupported, {})


def test_modify_order_handles_cancel_only_batch(monkeypatch) -> None:
    calls = []
    fields = {"op_results": [[0, {"Ok": {"Perp": {"OrderCancelled": {"order_id": ORDER_ID}}}}]]}
    monkeypatch.setattr(
        _subaccount, "submit_pallet_call_wait_event",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(
            tx_hash="0xtx", extrinsic_hash="0xext", fields_json=json.dumps(fields),
        ),
    )
    monkeypatch.setattr(_perp_market, "order_info", lambda **_: SimpleNamespace(size_filled=5))

    result = _client().perp_market.modify_order(
        market_id=3, order_id=ORDER_ID, is_long=True, price=1, new_total_quantity=6,
    )

    assert result.order_id == result.canceled_order_id == ORDER_ID
    assert calls[0]["event"] == "BatchOps"
    assert calls[0]["call_params"]["_perp_total_quantity"] == 6


def _client() -> ChainClient:
    return ChainClient(
        substrate_ws="wss://node", evm_rpc_url="https://rpc", private_key="0xpk", subaccount=ADDR,
    )


def test_update_tp_sl_submits_one_timestamp_call(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        _perp_market, "submit_pallet_call",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(tx_hash="0xtx"),
    )
    market = _client().perp_market
    actions = (
        {"Cancel": {"id": ORDER_ID}},
        {"Create": {
            "condition": "TakeProfit", "source": "Mark", "trigger_price": 110,
            "quantity": "Entire", "execution": {"Market": None},
        }},
    )
    assert market.update_tp_sl(
        market_id=3, actions=actions, nonce_ms=100, wait_for_finalized=False,
    ).tx_hash == "0xtx"
    assert calls[0]["call_module"] == "PerpMarket"
    assert calls[0]["call_function"] == "update_tp_sl"
    assert calls[0]["call_params"] == {"subaccount": ADDR, "market_id": 3, "actions": list(actions)}
    assert calls[0]["nonce_ms"] == 100
    assert calls[0]["wait_for_finalized"] is False
    for invalid in ([], [actions[0]] * 129, [{"Update": {"id": ORDER_ID}}]):
        with pytest.raises(ValidationError):
            market.update_tp_sl(market_id=3, actions=invalid)
    assert len(calls) == 1


def test_new_evm_views_use_the_runtime_selectors_and_abi(monkeypatch) -> None:
    responses = {
        "activePositionTpSl(address,uint16)": encode(["uint64[]"], [[ORDER_ID]]),
        "activeConditionalOrders(address,uint16)": encode(["uint64[]"], [[ORDER_ID, ORDER_ID + 1]]),
        "totalCollateralAndMarginRequiredForV2(address,uint8)": encode(["(int128,uint128)"], [(-8, 3)]),
    }
    by_selector = {keccak(text=signature)[:4]: raw for signature, raw in responses.items()}
    monkeypatch.setattr(_perp_market, "evm_call", lambda _url, _to, data: by_selector[data[:4]])
    market = _client().perp_market
    assert market.active_position_tp_sl(market_id=3) == [ORDER_ID]
    assert market.active_conditional_orders(market_id=3) == [ORDER_ID, ORDER_ID + 1]
    result = market.total_collateral_and_margin_required_for(
        account=ADDR, direction=0, signed_collateral=True,
    )
    assert (result.collateral, result.margin_required) == (-8, 3)
