from __future__ import annotations

import json
from typing import Optional

from ._abi import decode_abi, encode_call, normalize_address
from ._evm import evm_call
from ._native import build_signed_tx, submit_pallet_call, submit_pallet_call_wait_event, submit_signed_tx, submit_signed_tx_wait_event
from ._types import (
    DelegateInfo,
    OneClickTradingInfo,
    SubaccountBorrowPosition,
    SubaccountInfo,
    SubaccountSpotPosition,
    SubaccountSummary,
    SubaccountUserStats,
    TxResult,
)


def initialize_subaccount(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    name: str | bytes,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="initialize_subaccount",
        call_params={"name": _normalize_bytes(name)},
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
        event_name="NewUserRecord",
    )


def delete_subaccount(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="delete_subaccount",
        call_params={"subaccount": normalize_address(subaccount)},
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
        event_name="SubaccountDeleted",
    )


def no_op(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:    # `no_op` is CallType::Timestamp(1) — unlike the other Subaccount calls
    # (sequential nonce). No params, no event: it only consumes the timestamp
    # nonce. With the same nonce_ms as a stuck pending tx it replaces that tx
    # in the mempool (no_op has the highest pool priority).
    _ = evm_rpc_url, precompile_address, chain_id, gas_limit, max_fee_per_gas
    _ = max_priority_fee_per_gas, use_legacy
    res = submit_pallet_call(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="Subaccount",
        call_function="no_op",
        call_params={},
        nonce_ms=nonce_ms,
        use_timestamp_nonce=True,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    return TxResult(tx_hash=res.tx_hash, event=None)


def create_one_click_trading_account(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    new_account: str,
    quota: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    _ = quota  # Kept for backward compatibility; current precompile signature does not accept quota.
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="create_one_click_trading_account",
        call_params={"new": normalize_address(new_account)},
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def delete_one_click_trading_account(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    account: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="delete_one_click_trading_account",
        call_params={"oct_account": normalize_address(account)},
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def disable_one_click_trading_account(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    account: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="update_oct_mode",
        call_params={"address": normalize_address(account), "new_mode": "Disable"},
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def enable_one_click_trading_account(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    account: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="update_oct_mode",
        call_params={"address": normalize_address(account), "new_mode": "PlaceOrCancelOrder"},
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def set_delegate_account(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    delegate: str,
    name: str | bytes,
    valid_until: int,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    # `valid_until` is a wall-clock millisecond timestamp; the chain rejects
    # past values with 19_34 DelegateExpiry. Re-setting an existing delegate
    # updates its name/valid_until in place.
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="set_delegate_account",
        call_params={
            "subaccount": normalize_address(subaccount),
            "delegate": normalize_address(delegate),
            "name": _normalize_bytes(name),
            "valid_until": int(valid_until),
        },
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
        event_name="SubaccountDelegated",
    )


def remove_delegate_account(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    delegate: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="remove_delegate_account",
        call_params={"subaccount": normalize_address(subaccount), "delegate": normalize_address(delegate)},
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
        event_name="SubaccountDelegateRemoved",
    )


def set_spot_margin(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    enable_spot_margin: bool,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="set_spot_margin",
        call_params={"address": normalize_address(subaccount), "enable_spot_margin": bool(enable_spot_margin)},
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def rename_subaccount(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    new_name: str | bytes,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="rename_subaccount",
        call_params={"subaccount": normalize_address(subaccount), "new_name": _normalize_bytes(new_name)},
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def liquidate_perp_by_transfer(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    market_index: int,
    liquidator_max_base_amount: int,
    target_subaccount: str,
    liquidator: str,
    limit_price: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="liquidate_perp_by_transfer",
        call_params={
            "market_index": int(market_index),
            "liquidator_max_base_amount": int(liquidator_max_base_amount),
            "limit_price": None if limit_price is None else int(limit_price),
            "target_subaccount": normalize_address(target_subaccount),
            "liquidator": normalize_address(liquidator),
        },
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
        event_name="LiquidationRecord",
        event_required=False,
    )


def liquidate_spot_by_transfer(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    asset_symbol: str | bytes,
    liability_symbol: str | bytes,
    target_account_addr: str,
    liquidator: str,
    liquidator_max_liability_transfer: int,
    lending_market_id: int,
    limit_price: Optional[int] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="liquidate_spot_by_transfer",
        call_params={
            "asset_symbol": _normalize_bytes(asset_symbol),
            "liability_symbol": _normalize_bytes(liability_symbol),
            "target_account_addr": normalize_address(target_account_addr),
            "liquidator": normalize_address(liquidator),
            "limit_price": None if limit_price is None else int(limit_price),
            "liquidator_max_liability_transfer": int(liquidator_max_liability_transfer),
            "lending_market_id": int(lending_market_id),
        },
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
        event_name="LiquidationRecord",
        event_required=False,
    )


def liquidate_by_market(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    target_subaccount: str,
    liquidator: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_subaccount_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="liquidate_by_market",
        call_params={"target_subaccount": normalize_address(target_subaccount), "liquidator": normalize_address(liquidator)},
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
        event_name="LiquidationRecord",
        event_required=False,
    )


def user_stats(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    address: str,
) -> SubaccountUserStats:
    data = encode_call("userStats(address)", ["address"], [normalize_address(address)])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (stats,) = decode_abi([_USER_STATS_TUPLE], raw)
    return _decode_user_stats(stats)


def subaccount_info(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    address: str,
) -> SubaccountInfo:
    data = encode_call("subaccountInfo(address)", ["address"], [normalize_address(address)])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    layout, info = _decode_subaccount_info_tuple(raw)
    return _decode_subaccount_info(info, layout)


def one_click_trading_accounts_for(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    owner: str,
) -> list[OneClickTradingInfo]:
    data = encode_call(
        "oneClickTradingAccountsFor(address)",
        ["address"],
        [normalize_address(owner)],
    )
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (accounts,) = decode_abi([f"{_ONE_CLICK_TRADING_TUPLE}[]"], raw)
    return [
        OneClickTradingInfo(
            address=_decode_address(item[0]),
            mode=int(item[1]),
            create_time=int(item[2]),
        )
        for item in accounts
    ]


def delegate_accounts(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    user: str,
) -> list[SubaccountSummary]:
    data = encode_call("delegateAccounts(address)", ["address"], [normalize_address(user)])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (accounts,) = decode_abi([f"{_SUMMARY_TUPLE}[]"], raw)
    return [_decode_subaccount_summary(item) for item in accounts]


def _submit_subaccount_tx(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    data: bytes,
    chain_id: Optional[int],
    gas_limit: Optional[int],
    max_fee_per_gas: Optional[int],
    max_priority_fee_per_gas: Optional[int],
    use_legacy: bool,
    nonce: Optional[int],
    wait_for_finalized: bool,
    timeout_ms: Optional[int],
    event_name: Optional[str] = None,
    event_required: bool = True,
) -> TxResult:
    signed = build_signed_tx(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile_address,
        data=data,
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        use_legacy=use_legacy,
        nonce_ms=nonce,
        use_timestamp_nonce=False,
    )
    if event_name:
        try:
            res = submit_signed_tx_wait_event(
                substrate_ws=substrate_ws,
                signed_tx_hex=signed.signed_tx,
                signer=signed.signer,
                pallet="Subaccount",
                event=event_name,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            )
        except RuntimeError as exc:
            if not event_required and str(exc).startswith("event not found:"):
                return TxResult(tx_hash=signed.tx_hash, event=None)
            raise
        event_fields: dict | None = None
        if res.fields_json:
            try:
                decoded = json.loads(res.fields_json)
                if isinstance(decoded, dict):
                    event_fields = decoded
            except Exception:
                event_fields = None
        event_payload: dict = {
            "pallet": res.pallet,
            "name": res.event,
        }
        if event_fields:
            event_payload.update(event_fields)
        return TxResult(
            tx_hash=res.tx_hash,
            event=event_payload,
        )

    res = submit_signed_tx(
        substrate_ws=substrate_ws,
        signed_tx_hex=signed.signed_tx,
        signer=signed.signer,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    return TxResult(tx_hash=res.tx_hash, event=None)


def _submit_subaccount_call(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    call_function: str,
    call_params: dict,
    wait_for_finalized: bool,
    timeout_ms: Optional[int],
    nonce: Optional[int],
    event_name: Optional[str] = None,
    event_required: bool = True,
) -> TxResult:
    if event_name:
        if not event_required:
            res = submit_pallet_call(
                substrate_ws=substrate_ws,
                private_key=private_key,
                call_module="Subaccount",
                call_function=call_function,
                call_params=call_params,
                evm_rpc_url=evm_rpc_url,
                nonce_ms=nonce,
                use_timestamp_nonce=False,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            )
            return TxResult(tx_hash=res.tx_hash, event=None)
        try:
            res = submit_pallet_call_wait_event(
                substrate_ws=substrate_ws,
                private_key=private_key,
                call_module="Subaccount",
                call_function=call_function,
                call_params=call_params,
                pallet="Subaccount",
                event=event_name,
                evm_rpc_url=evm_rpc_url,
                nonce_ms=nonce,
                use_timestamp_nonce=False,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            )
        except RuntimeError as exc:
            raise
        return _tx_result_from_event(res)

    res = submit_pallet_call(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="Subaccount",
        call_function=call_function,
        call_params=call_params,
        evm_rpc_url=evm_rpc_url,
        nonce_ms=nonce,
        use_timestamp_nonce=False,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    return TxResult(tx_hash=res.tx_hash, event=None)


def _submit_modify_orders(
    *,
    substrate_ws: str,
    private_key: str,
    ops: list,
    pallet: str,
    event: str,
    nonce_ms: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
):
    # Shared submit for the atomic modify (cancel+place) path. `modify_orders`
    # is CallType::Timestamp(1) and transactional — the whole extrinsic rolls
    # back if either op fails. The new order id is read from the market
    # pallet's OrderPlaced / StateOrder{Buy,Sell} event fired by the same tx.
    return submit_pallet_call_wait_event(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="Subaccount",
        call_function="modify_orders",
        call_params={"ops": ops},
        pallet=pallet,
        event=event,
        nonce_ms=nonce_ms,
        use_timestamp_nonce=True,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def _tx_result_from_event(res: object) -> TxResult:
    event_fields: dict | None = None
    fields_json = getattr(res, "fields_json", "")
    if fields_json:
        try:
            decoded = json.loads(fields_json)
            if isinstance(decoded, dict):
                event_fields = decoded
        except Exception:
            event_fields = None
    event_payload: dict = {
        "pallet": getattr(res, "pallet", None),
        "name": getattr(res, "event", None),
    }
    if event_fields:
        event_payload.update(event_fields)
    return TxResult(tx_hash=getattr(res, "tx_hash", ""), event=event_payload)


def _decode_user_stats(stats: tuple) -> SubaccountUserStats:
    return SubaccountUserStats(
        subaccounts=[_decode_subaccount_summary(item) for item in stats[0]],
        if_staked_quote_asset_amount=int(stats[1]),
        number_of_sub_accounts=int(stats[2]),
        number_of_sub_accounts_created=int(stats[3]),
    )


def _decode_subaccount_summary(item: tuple) -> SubaccountSummary:
    return SubaccountSummary(subaccount=_decode_address(item[0]), name=_decode_bytes(item[1]))


def _decode_subaccount_info(info: tuple, layout: str) -> SubaccountInfo:
    if layout == "delegates_vec":
        # 8-field layout: authority, delegates vec, name, spot, borrow,
        # next_order_id, status, margin flag. Current devnet precompile.
        spot_positions = [
            SubaccountSpotPosition(symbol=_decode_bytes(pos[0]), token_amount=int(pos[1]))
            for pos in info[3]
        ]
        borrow_positions = [
            SubaccountBorrowPosition(
                lending_market_id=int(borrow[0]),
                asset=_decode_bytes(borrow[1]),
                amount=int(borrow[2]),
                interest=int(borrow[3]),
            )
            for borrow in info[4]
        ]
        delegates = [
            DelegateInfo(
                delegate_address=_decode_address(d[0]),
                delegate_name=_decode_bytes(d[1]),
                valid_until=int(d[2]),
            )
            for d in info[1]
        ]
        return SubaccountInfo(
            authority=_decode_address(info[0]),
            delegate="",
            name=_decode_bytes(info[2]),
            spot_positions=spot_positions,
            borrow_positions=borrow_positions,
            next_order_id=int(info[5]),
            status=int(info[6]),
            is_margin_trading_enabled=bool(info[7]),
            delegates=delegates,
        )
    if layout == "latest":
        spot_positions = [
            SubaccountSpotPosition(symbol=_decode_bytes(pos[0]), token_amount=int(pos[1]))
            for pos in info[5]
        ]
        return SubaccountInfo(
            authority=_decode_address(info[0]),
            address=_decode_address(info[1]),
            delegate=_decode_address(info[2]),
            name=_decode_bytes(info[3]),
            status=int(info[4]),
            spot_positions=spot_positions,
            borrow_positions=[],
            next_order_id=int(info[6]),
            is_margin_trading_enabled=bool(info[7]),
            liquidation_start_at=_decode_optional_u64(info[8]),
            next_liquidation_id=int(info[9]),
            margin_strategy=int(info[10]),
        )

    spot_positions = [
        SubaccountSpotPosition(symbol=_decode_bytes(pos[0]), token_amount=int(pos[1]))
        for pos in info[3]
    ]
    borrow_positions = [
        SubaccountBorrowPosition(
            lending_market_id=int(borrow[0]),
            asset=_decode_bytes(borrow[1]),
            amount=int(borrow[2]),
            interest=int(borrow[3]),
        )
        for borrow in info[4]
    ]
    return SubaccountInfo(
        authority=_decode_address(info[0]),
        delegate=_decode_address(info[1]),
        name=_decode_bytes(info[2]),
        spot_positions=spot_positions,
        borrow_positions=borrow_positions,
        next_order_id=int(info[5]),
        status=int(info[6]),
        is_margin_trading_enabled=bool(info[7]),
    )


def _decode_subaccount_info_tuple(raw: bytes) -> tuple[str, tuple]:
    candidates = (
        ("delegates_vec", _ACCOUNT_INFO_DELEGATES_TUPLE),
        ("latest", _ACCOUNT_INFO_TUPLE),
        ("legacy_user", _USER_TUPLE_V1),
    )
    last_error: Exception | None = None
    for layout, tuple_type in candidates:
        try:
            (info,) = decode_abi([tuple_type], raw)
            return layout, info
        except Exception as exc:
            last_error = exc
    raise RuntimeError("unable to decode subaccountInfo response with supported ABI layouts") from last_error


def _decode_optional_u64(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 2:
        return int(value[1]) if bool(value[0]) else None
    return int(value)


def _normalize_bytes(value: str | bytes) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    raw = value.strip()
    if raw.startswith("0x"):
        return bytes.fromhex(raw[2:])
    return raw.encode("utf-8")


def _decode_address(value: bytes) -> str:
    if isinstance(value, str):
        return value
    return "0x" + value.hex()


def _decode_bytes(value: bytes) -> str:
    if isinstance(value, str):
        return value
    try:
        decoded = value.decode("utf-8")
        if decoded.isprintable():
            return decoded
    except Exception:
        pass
    return "0x" + value.hex()


_SUMMARY_TUPLE = "(address,bytes)"
_ONE_CLICK_TRADING_TUPLE = "(address,uint8,uint32)"
_BORROW_POSITION_TUPLE = "(uint8,bytes,uint128,uint128)"
_SPOT_POSITION_TUPLE = "(bytes,uint128)"
_OPTION_U64_TUPLE = "(bool,uint64)"
_ACCOUNT_INFO_TUPLE = (
    f"(address,address,address,bytes,uint8,{_SPOT_POSITION_TUPLE}[],uint32,bool,{_OPTION_U64_TUPLE},uint32,uint8)"
)
# Precompile layout with delegates vec (delegate -> DelegateInfo[]):
# (authority, delegates, name, spot_positions, borrow_positions,
#  next_order_id, status, is_margin_trading_enabled)
_DELEGATE_TUPLE = "(address,bytes,uint64)"
_ACCOUNT_INFO_DELEGATES_TUPLE = (
    f"(address,{_DELEGATE_TUPLE}[],bytes,{_SPOT_POSITION_TUPLE}[],{_BORROW_POSITION_TUPLE}[],uint32,uint8,bool)"
)
_USER_TUPLE_V1 = (
    f"(address,address,bytes,{_SPOT_POSITION_TUPLE}[],{_BORROW_POSITION_TUPLE}[],uint32,uint8,bool)"
)
_USER_STATS_TUPLE = f"({_SUMMARY_TUPLE}[],uint64,uint16,uint16)"
