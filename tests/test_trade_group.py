from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from eth_abi import decode, encode
from eth_utils import keccak

from deepx_sdk import ChainClient, TradeGroupInfo, TradeGroupInvitation
from deepx_sdk import _subaccount, _trade_group

ACCOUNT = "0x" + "11" * 20
OTHER = "0x" + "22" * 20
OPERATIONS = [
    ("create_trade_group", {}, "TradeGroupCreated"),
    ("invite_trade_group", {"group_id": 7, "account": OTHER}, "TradeGroupInvited"),
    ("accept_trade_group", {"group_id": 7}, "TradeGroupJoined"),
    ("reject_trade_group", {"group_id": 7}, "TradeGroupInvitationRemoved"),
    ("revoke_trade_group_invitation", {"group_id": 7, "account": OTHER}, "TradeGroupInvitationRemoved"),
    ("leave_trade_group", {}, "TradeGroupLeft"),
    ("remove_trade_group_member", {"group_id": 7, "account": OTHER}, "TradeGroupLeft"),
    ("dissolve_trade_group", {"group_id": 7}, "TradeGroupDissolved"),
]


@pytest.mark.parametrize("function,params,event", OPERATIONS)
@pytest.mark.parametrize("nonce", [None, 5])
def test_group_operations_use_sequential_nonce_and_confirm_event(monkeypatch, function, params, event, nonce):
    calls = []
    def submit(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(pallet="Subaccount", event=event, fields_json=json.dumps({"group_id":7}), tx_hash="0xtx")
    monkeypatch.setattr(_subaccount, "submit_pallet_call_wait_event", submit)
    client = ChainClient(private_key="0x"+"33"*32, subaccount=ACCOUNT)
    try:
        result = getattr(client.subaccount_client, function)(**params, nonce=nonce, wait_for_finalized=False, timeout_ms=123)
    finally:
        client.close()
    call = calls[0]
    assert call["call_module"] == "Subaccount" and call["call_function"] == function
    assert call["call_params"] == params
    assert call["use_timestamp_nonce"] is False
    assert call["nonce_ms"] == nonce
    assert call["wait_for_finalized"] is False and call["timeout_ms"] == 123
    assert call["event"] == event
    assert result.tx_hash == "0xtx" and result.event["group_id"] == 7


@pytest.mark.parametrize("group_id", [-1, 2**64, True, "7"])
def test_group_id_validation_happens_before_submission(monkeypatch, group_id):
    monkeypatch.setattr(_trade_group, "_submit_subaccount_call", lambda **kwargs: pytest.fail("invalid id submitted"))
    with pytest.raises(ValueError, match="uint64"):
        _trade_group.submit_trade_group_call(substrate_ws="wss://node", evm_rpc_url="https://rpc", private_key="key",
            call_function="accept_trade_group", group_id=group_id)


@pytest.mark.parametrize("kind", ["membership", "group", "invitations"])
def test_group_views_match_node_abi(monkeypatch, kind):
    calls=[]
    if kind == "membership":
        signature, arg_types, args = "tradeGroupFor(address)", ["address"], (ACCOUNT,)
        raw = encode(["uint64"], [0])
    elif kind == "group":
        signature, arg_types, args = "tradeGroup(uint64)", ["uint64"], (7,)
        raw = encode(["(address,address[])"], [(ACCOUNT, [ACCOUNT, OTHER])])
    else:
        signature, arg_types, args = "tradeGroupInvitations(uint64)", ["uint64"], (7,)
        raw = encode(["(address,uint64)[]"], [[(OTHER, 1792576000000)]])
    def call(url, address, data):
        assert url == "https://rpc.test"
        assert data[:4] == keccak(text=signature)[:4]
        assert decode(arg_types, data[4:]) == args
        calls.append(address)
        return raw
    monkeypatch.setattr(_trade_group, "evm_call", call)
    client = ChainClient(private_key="0x"+"33"*32, subaccount=ACCOUNT, evm_rpc_url="https://rpc.test")
    try:
        if kind == "membership":
            assert client.subaccount_client.trade_group_for(account=ACCOUNT) == 0
        elif kind == "group":
            assert client.subaccount_client.trade_group(group_id=7) == TradeGroupInfo(creator=ACCOUNT, members=[ACCOUNT, OTHER])
        else:
            assert client.subaccount_client.trade_group_invitations(group_id=7) == [TradeGroupInvitation(account=OTHER, expires_at=1792576000000)]
    finally:
        client.close()
    assert len(calls) == 1


def test_group_backend_errors_are_not_reported_as_success(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("TradeGroupInvitationExpired")
    monkeypatch.setattr(_subaccount, "submit_pallet_call_wait_event", fail)
    with pytest.raises(RuntimeError, match="TradeGroupInvitationExpired"):
        _trade_group.submit_trade_group_call(substrate_ws="wss://node", evm_rpc_url="https://rpc", private_key="key",
            call_function="accept_trade_group", group_id=7)
