"""Wallet-level trade-group operations; management uses sequential nonces."""
from __future__ import annotations

from ._abi import decode_abi, encode_call, normalize_address
from ._evm import evm_call
from ._subaccount import _submit_subaccount_call
from ._types import TradeGroupInfo, TradeGroupInvitation, TxResult


_GROUP_EVENTS = {
    "create_trade_group": "TradeGroupCreated",
    "invite_trade_group": "TradeGroupInvited",
    "accept_trade_group": "TradeGroupJoined",
    "reject_trade_group": "TradeGroupInvitationRemoved",
    "revoke_trade_group_invitation": "TradeGroupInvitationRemoved",
    "leave_trade_group": "TradeGroupLeft",
    "remove_trade_group_member": "TradeGroupLeft",
    "dissolve_trade_group": "TradeGroupDissolved",
}


def validate_group_id(group_id: int) -> int:
    if isinstance(group_id, bool) or not isinstance(group_id, int) or not 0 <= group_id < 2**64:
        raise ValueError("group_id must be a uint64 integer")
    return group_id


def submit_trade_group_call(
    *, substrate_ws: str, evm_rpc_url: str, private_key: str,
    call_function: str, group_id: int | None = None, account: str | None = None,
    nonce: int | None = None, wait_for_finalized: bool = True,
    timeout_ms: int | None = None,
) -> TxResult:
    params = {}
    if group_id is not None:
        params["group_id"] = validate_group_id(group_id)
    if account is not None:
        params["account"] = normalize_address(account)
    return _submit_subaccount_call(
        substrate_ws=substrate_ws, evm_rpc_url=evm_rpc_url, private_key=private_key,
        call_function=call_function, call_params=params, nonce=nonce,
        wait_for_finalized=wait_for_finalized, timeout_ms=timeout_ms,
        event_name=_GROUP_EVENTS[call_function],
    )


def trade_group_for(*, evm_rpc_url: str, precompile_address: str, account: str) -> int:
    data = encode_call("tradeGroupFor(address)", ["address"], [normalize_address(account)])
    (group_id,) = decode_abi(["uint64"], evm_call(evm_rpc_url, precompile_address, data))
    return int(group_id)


def trade_group(*, evm_rpc_url: str, precompile_address: str, group_id: int) -> TradeGroupInfo:
    data = encode_call("tradeGroup(uint64)", ["uint64"], [validate_group_id(group_id)])
    (group,) = decode_abi(["(address,address[])"], evm_call(evm_rpc_url, precompile_address, data))
    return TradeGroupInfo(creator=group[0], members=list(group[1]))


def trade_group_invitations(
    *, evm_rpc_url: str, precompile_address: str, group_id: int,
) -> list[TradeGroupInvitation]:
    data = encode_call("tradeGroupInvitations(uint64)", ["uint64"], [validate_group_id(group_id)])
    (invitations,) = decode_abi(["(address,uint64)[]"], evm_call(evm_rpc_url, precompile_address, data))
    return [TradeGroupInvitation(account=account, expires_at=int(expiry)) for account, expiry in invitations]
