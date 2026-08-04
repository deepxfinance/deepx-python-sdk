from __future__ import annotations

import time
from typing import Optional

# Pure Python backend.
from . import _native_py as _native  # type: ignore[assignment]

SignedTxPayload = _native.SignedTxPayload
SubmitTxResult = _native.SubmitTxResult
SubmitEventResult = _native.SubmitEventResult


def build_signed_tx(
    *,
    substrate_ws: Optional[str] = None,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    data: bytes,
    value: int = 0,
    chain_id: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    max_priority_fee_per_gas: Optional[int] = None,
    use_legacy: bool = False,
    nonce_ms: Optional[int] = None,
    use_timestamp_nonce: bool = True,
    system_precompile_address: Optional[str] = None,
) -> SignedTxPayload:
    if nonce_ms is None and use_timestamp_nonce:
        nonce_ms = int(time.time() * 1000)
    data_hex = "0x" + data.hex()
    return _native.build_signed_tx(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        precompile_address=precompile_address,
        data_hex=data_hex,
        value=value,
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        use_legacy=use_legacy,
        nonce_ms=nonce_ms,
        use_timestamp_nonce=use_timestamp_nonce,
        system_precompile_address=system_precompile_address,
    )


def submit_signed_tx(
    *,
    substrate_ws: str,
    signed_tx_hex: str,
    signer: str,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SubmitTxResult:
    return _native.submit_signed_tx(
        substrate_ws=substrate_ws,
        signed_tx_hex=signed_tx_hex,
        signer=signer,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def submit_signed_tx_wait_event(
    *,
    substrate_ws: str,
    signed_tx_hex: str,
    signer: str,
    pallet: str,
    event: str,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SubmitEventResult:
    return _native.submit_signed_tx_wait_event(
        substrate_ws=substrate_ws,
        signed_tx_hex=signed_tx_hex,
        signer=signer,
        pallet=pallet,
        event=event,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def submit_pallet_call(
    *,
    substrate_ws: str,
    private_key: str,
    call_module: str,
    call_function: str,
    call_params: dict,
    evm_rpc_url: Optional[str] = None,
    nonce_ms: Optional[int] = None,
    use_timestamp_nonce: bool = True,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SubmitTxResult:
    return _native.submit_pallet_call(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module=call_module,
        call_function=call_function,
        call_params=call_params,
        evm_rpc_url=evm_rpc_url,
        nonce_ms=nonce_ms,
        use_timestamp_nonce=use_timestamp_nonce,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )


def submit_pallet_call_wait_event(
    *,
    substrate_ws: str,
    private_key: str,
    call_module: str,
    call_function: str,
    call_params: dict,
    pallet: str,
    event: str,
    evm_rpc_url: Optional[str] = None,
    nonce_ms: Optional[int] = None,
    use_timestamp_nonce: bool = True,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SubmitEventResult:
    return _native.submit_pallet_call_wait_event(
        substrate_ws=substrate_ws,
        private_key=private_key,
        call_module=call_module,
        call_function=call_function,
        call_params=call_params,
        pallet=pallet,
        event=event,
        evm_rpc_url=evm_rpc_url,
        nonce_ms=nonce_ms,
        use_timestamp_nonce=use_timestamp_nonce,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
