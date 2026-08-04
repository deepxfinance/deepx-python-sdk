from __future__ import annotations

import json
from typing import Optional

from ._abi import decode_abi, encode_call, normalize_address, normalize_bytes32
from ._evm import evm_call
from ._native import build_signed_tx, submit_pallet_call, submit_pallet_call_wait_event, submit_signed_tx, submit_signed_tx_wait_event
from ._types import LendingAssetPoolState, LendingMarketState, TxResult


def deposit(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    asset: str | bytes,
    amount: int,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_lending_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="deposit",
        call_params={
            "from_subaccount": None,
            "subaccount": normalize_address(subaccount),
            "market_id": 1,
            "asset": _normalize_bytes(asset),
            "amount": int(amount),
        },
        event_name="Deposit",
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def deposit_from_subaccount(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    from_subaccount: str,
    subaccount: str,
    asset: str | bytes,
    amount: int,
    auto_borrow: bool = False,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_lending_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="deposit",
        call_params={
            "from_subaccount": (normalize_address(from_subaccount), bool(auto_borrow)),
            "subaccount": normalize_address(subaccount),
            "market_id": 1,
            "asset": _normalize_bytes(asset),
            "amount": int(amount),
        },
        event_name="Deposit",
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def bridge_invoke(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    uid: str,
    amount: int,
    custom_data: str | bytes,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    data = encode_call(
        "bridgeInvoke(bytes32,uint256,bytes)",
        ["bytes32", "uint256", "bytes"],
        [normalize_bytes32(uid), amount, _normalize_bytes(custom_data)],
    )
    return _submit_lending_tx(
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
        nonce=nonce,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def withdraw(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    asset: str | bytes,
    amount: int,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_lending_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="withdraw",
        call_params={
            "subaccount": normalize_address(subaccount),
            "to": _signer_address(private_key),
            "market_id": 1,
            "asset": _normalize_bytes(asset),
            "amount": int(amount),
            "mode": "OnlyWithdraw",
        },
        event_name="Withdraw",
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def withdraw_and_swap_evm(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    asset: str | bytes,
    amount: int,
    dst_chain_id: int,
    token_id: int,
    dst_recipient: str,
    refund_address: str,
    salt: str,
    custom_data: str | bytes,
    signature: str | bytes,
    consumer_address: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_lending_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="withdraw",
        call_params={
            "subaccount": normalize_address(subaccount),
            "to": _signer_address(private_key),
            "market_id": 1,
            "asset": _normalize_bytes(asset),
            "amount": int(amount),
            "mode": {
                "WithdrawAndSwap": {
                    "consumer_address": normalize_address(consumer_address),
                    "dst_chain_id": int(dst_chain_id),
                    "token_id": int(token_id),
                    "dst_recipient": "0x" + normalize_bytes32(dst_recipient).hex(),
                    "refund_address": normalize_address(refund_address),
                    "salt": "0x" + normalize_bytes32(salt).hex(),
                    "custom_data": _normalize_bytes(custom_data),
                    "signature": _normalize_bytes(signature),
                }
            },
        },
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def withdraw_and_swap(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    asset: str | bytes,
    amount: int,
    dst_chain_id: int,
    token_id: int,
    dst_recipient: str,
    refund_address: str,
    salt: str,
    custom_data: str | bytes,
    signature: str | bytes,
    consumer_address: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return withdraw_and_swap_evm(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile_address,
        subaccount=subaccount,
        asset=asset,
        amount=amount,
        dst_chain_id=dst_chain_id,
        token_id=token_id,
        dst_recipient=dst_recipient,
        refund_address=refund_address,
        salt=salt,
        custom_data=custom_data,
        signature=signature,
        consumer_address=consumer_address,
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        use_legacy=use_legacy,
        nonce=nonce,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def withdraw_and_swap_btc(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    subaccount: str,
    asset: str | bytes,
    amount: int,
    dst_recipient: str,
    refund_address: str,
    salt: str,
    signature: str | bytes,
    consumer_address: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_lending_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="withdraw",
        call_params={
            "subaccount": normalize_address(subaccount),
            "to": _signer_address(private_key),
            "market_id": 1,
            "asset": _normalize_bytes(asset),
            "amount": int(amount),
            "mode": {
                "WithdrawAndSwapBtc": {
                    "consumer_address": normalize_address(consumer_address),
                    "dst_recipient": dst_recipient,
                    "refund_address": normalize_address(refund_address),
                    "salt": "0x" + normalize_bytes32(salt).hex(),
                    "signature": _normalize_bytes(signature),
                }
            },
        },
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def borrow(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    borrower: str,
    market_id: int,
    asset: str | bytes,
    amount: int,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_lending_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="borrow",
        call_params={
            "borrower": normalize_address(borrower),
            "market_id": int(market_id),
            "asset": _normalize_bytes(asset),
            "amount": int(amount),
            "mode": "OnlyWithdraw",
        },
        event_name="Borrow",
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def borrow_and_swap_evm(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    borrower: str,
    market_id: int,
    asset: str | bytes,
    amount: int,
    dst_chain_id: int,
    token_id: int,
    dst_recipient: str,
    refund_address: str,
    salt: str,
    custom_data: str | bytes,
    signature: str | bytes,
    consumer_address: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_lending_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="borrow",
        call_params={
            "borrower": normalize_address(borrower),
            "market_id": int(market_id),
            "asset": _normalize_bytes(asset),
            "amount": int(amount),
            "mode": {
                "WithdrawAndSwap": {
                    "consumer_address": normalize_address(consumer_address),
                    "dst_chain_id": int(dst_chain_id),
                    "token_id": int(token_id),
                    "dst_recipient": "0x" + normalize_bytes32(dst_recipient).hex(),
                    "refund_address": normalize_address(refund_address),
                    "salt": "0x" + normalize_bytes32(salt).hex(),
                    "custom_data": _normalize_bytes(custom_data),
                    "signature": _normalize_bytes(signature),
                }
            },
        },
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def borrow_and_swap(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    borrower: str,
    market_id: int,
    asset: str | bytes,
    amount: int,
    dst_chain_id: int,
    token_id: int,
    dst_recipient: str,
    refund_address: str,
    salt: str,
    custom_data: str | bytes,
    signature: str | bytes,
    consumer_address: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return borrow_and_swap_evm(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile_address,
        borrower=borrower,
        market_id=market_id,
        asset=asset,
        amount=amount,
        dst_chain_id=dst_chain_id,
        token_id=token_id,
        dst_recipient=dst_recipient,
        refund_address=refund_address,
        salt=salt,
        custom_data=custom_data,
        signature=signature,
        consumer_address=consumer_address,
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        use_legacy=use_legacy,
        nonce=nonce,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def borrow_and_swap_btc(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    borrower: str,
    market_id: int,
    asset: str | bytes,
    amount: int,
    dst_recipient: str,
    refund_address: str,
    salt: str,
    signature: str | bytes,
    consumer_address: str,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_lending_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="borrow",
        call_params={
            "borrower": normalize_address(borrower),
            "market_id": int(market_id),
            "asset": _normalize_bytes(asset),
            "amount": int(amount),
            "mode": {
                "BorrowAndSwapBtc": {
                    "consumer_address": normalize_address(consumer_address),
                    "dst_recipient": dst_recipient,
                    "refund_address": normalize_address(refund_address),
                    "salt": "0x" + normalize_bytes32(salt).hex(),
                    "signature": _normalize_bytes(signature),
                }
            },
        },
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def repay(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    who: str,
    market_id: int,
    asset: str | bytes,
    amount: int,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    return _submit_lending_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="repay",
        call_params={
            "who": normalize_address(who),
            "market_id": int(market_id),
            "asset": _normalize_bytes(asset),
            "amount": int(amount),
        },
        event_name="Repay",
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def buy_quota(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    account: str,
    quota: int,
    from_subaccount: Optional[str] = None,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce: Optional[int] = None,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> TxResult:
    # Cost = QuoteAmountPerQuota * quota in QuoteAssetForQuota (USDC).
    # Payment: from the signer's wallet (deposit) by default, or from a
    # subaccount's spot balance when `from_subaccount` is given.
    return _submit_lending_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_function="buy_quota",
        call_params={
            "address": normalize_address(account),
            "quota": int(quota),
            "from_subaccount": None if from_subaccount is None else normalize_address(from_subaccount),
        },
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
        nonce=nonce,
    )


def lending_markets(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    market_id: int,
) -> LendingMarketState:
    data = encode_call("lendingMarkets(uint8)", ["uint8"], [market_id])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (market,) = decode_abi([_LENDING_MARKET_TUPLE], raw)
    return LendingMarketState(
        market_id=int(market[0]),
        market_name=_decode_bytes(market[1]),
        liquidation_bonus=int(market[2]),
    )


def asset_pools(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    market_id: int,
) -> list[LendingAssetPoolState]:
    data = encode_call("assetPools(uint8)", ["uint8"], [market_id])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    last_error: Exception | None = None
    for tuple_type in (_ASSET_POOL_TUPLE_V3, _ASSET_POOL_TUPLE_V2):
        try:
            (pools,) = decode_abi([f"{tuple_type}[]"], raw)
            return [_decode_asset_pool(pool) for pool in pools]
        except Exception as exc:  # pragma: no cover - exercised against different chain schemas
            last_error = exc
    raise RuntimeError("failed to decode assetPools response with known schemas") from last_error


def health_for(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    subaccount: str,
) -> int:
    data = encode_call("healthFor(address)", ["address"], [normalize_address(subaccount)])
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (value,) = decode_abi(["uint128"], raw)
    return int(value)


def max_borrow_amount_for(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    account: str,
    lending_market: int,
    asset: str | bytes,
) -> int:
    data = encode_call(
        "maxBorrowAmountFor(address,uint8,bytes)",
        ["address", "uint8", "bytes"],
        [normalize_address(account), lending_market, _normalize_bytes(asset)],
    )
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (value,) = decode_abi(["uint128"], raw)
    return int(value)


def max_withdraw_amount_for(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    account: str,
    lending_market: int,
    asset: str | bytes,
) -> int:
    data = encode_call(
        "maxWithdrawAmountFor(address,uint8,bytes)",
        ["address", "uint8", "bytes"],
        [normalize_address(account), lending_market, _normalize_bytes(asset)],
    )
    raw = evm_call(evm_rpc_url, precompile_address, data)
    (value,) = decode_abi(["uint128"], raw)
    return int(value)


def _submit_lending_tx(
    *,
    substrate_ws: str,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    data: bytes,
    event_name: Optional[str] = None,
    chain_id: Optional[int],
    gas_limit: Optional[int],
    max_fee_per_gas: Optional[int],
    max_priority_fee_per_gas: Optional[int],
    use_legacy: bool,
    nonce: Optional[int],
    wait_for_finalized: bool,
    timeout_ms: Optional[int],
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
        res = submit_signed_tx_wait_event(
            substrate_ws=substrate_ws,
            signed_tx_hex=signed.signed_tx,
            signer=signed.signer,
            pallet="Lending",
            event=event_name,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
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
    else:
        res = submit_signed_tx(
            substrate_ws=substrate_ws,
            signed_tx_hex=signed.signed_tx,
            signer=signed.signer,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
        return TxResult(tx_hash=res.tx_hash, event=None)


def _submit_lending_call(
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
) -> TxResult:
    if event_name:
        res = submit_pallet_call_wait_event(
            substrate_ws=substrate_ws,
            private_key=private_key,
            call_module="Lending",
            call_function=call_function,
            call_params=call_params,
            pallet="Lending",
            event=event_name,
            evm_rpc_url=evm_rpc_url,
            nonce_ms=nonce,
            use_timestamp_nonce=False,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )
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
        return TxResult(tx_hash=res.tx_hash, event=event_payload)

    res = submit_pallet_call(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module="Lending",
        call_function=call_function,
        call_params=call_params,
        evm_rpc_url=evm_rpc_url,
        nonce_ms=nonce,
        use_timestamp_nonce=False,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    return TxResult(tx_hash=res.tx_hash, event=None)


def _signer_address(private_key: str) -> str:
    try:
        from eth_account import Account
    except Exception as exc:  # pragma: no cover
        raise ImportError("Missing eth-account dependency") from exc
    return normalize_address(Account.from_key(private_key).address)


def _normalize_bytes(value: str | bytes) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    raw = value.strip()
    if raw.startswith("0x"):
        return bytes.fromhex(raw[2:])
    return raw.encode("utf-8")


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


def _decode_asset_pool(pool: tuple) -> LendingAssetPoolState:
    values = list(pool)
    if len(values) < 19:
        values.extend([0] * (19 - len(values)))
    return LendingAssetPoolState(
        market_id=int(values[0]),
        asset=_decode_bytes(values[1]),
        decimal=int(values[2]),
        total_deposits=int(values[3]),
        total_borrows=int(values[4]),
        cumulative_deposit_interest=int(values[5]),
        cumulative_borrow_interest=int(values[6]),
        last_updated_slot=int(values[7]),
        reserve_factor=int(values[8]),
        custom_liquidation_bonus=int(values[9]),
        initial_asset_weight=int(values[10]),
        maintenance_asset_weight=int(values[11]),
        initial_borrow_weight=int(values[12]),
        maintenance_borrow_weight=int(values[13]),
        apr_borrow=int(values[14]),
        apr_lend=int(values[15]),
        protocol_reserve=int(values[16]),
        supply_cap=int(values[17]),
        borrow_cap=int(values[18]),
    )


_LENDING_MARKET_TUPLE = "(uint8,bytes,uint128)"
_ASSET_POOL_TUPLE_V2 = (
    "(uint8,bytes,uint32,uint128,uint128,uint128,uint128,uint64,uint128,uint128,uint128,uint128,"
    "uint128,uint128,uint128,uint128,uint128,uint128,uint128)"
)
_ASSET_POOL_TUPLE_V3 = (
    "(uint8,bytes,uint32,uint128,uint128,uint128,uint128,uint64,uint128,uint128,uint128,uint128,"
    "uint128,uint128,uint128,uint128,uint128)"
)
