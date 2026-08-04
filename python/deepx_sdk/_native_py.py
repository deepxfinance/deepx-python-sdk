from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from ._rpc_transport import (
    rpc_request_endpoints,
    rpc_request_options,
    substrate_ws_request_endpoints,
)
from ._errors import RPCError, parse_chain_error_code

_Account = None
_keccak = None
_to_canonical_address = None
_to_checksum_address = None
_SubstrateInterface = None
_SubstrateKeypair = None
_SubstrateKeypairType = None
DEFAULT_PRECOMPILE_GAS_LIMIT = 500_000
DEFAULT_SYSTEM_PRECOMPILE_ADDRESS = "0x0000000000000000000000000000000000000452"
DEFAULT_SUBMIT_TIMEOUT_MS = 120_000
# Default per-operation timeout (connect + per-message read) for substrate
# WebSocket connections, used when no explicit timeout_ms is supplied. Bounds
# ws_options so a dead/flaky substrate endpoint fails fast instead of hanging
# the connect/metadata/nonce fetch indefinitely. Generous enough not to trip a
# healthy finalization poll (whose per-read errors are swallowed and retried
# up to DEFAULT_SUBMIT_TIMEOUT_MS anyway).
DEFAULT_WS_TIMEOUT_MS = 60_000
POLL_INTERVAL_S = 1.0


def _native_debug_enabled() -> bool:
    raw = os.environ.get("DEEPX_SDK_NATIVE_DEBUG", "")
    return raw.strip().lower() in {"1", "true", "yes", "on", "debug"}


def _native_debug(message: str) -> None:
    if not _native_debug_enabled():
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[deepx_sdk._native_py {ts}] {message}", file=sys.stderr, flush=True)


@dataclass
class SignedTxPayload:
    signed_tx: str
    signer: str
    tx_hash: str
    nonce: int
    gas_limit: int


@dataclass
class SubmitTxResult:
    tx_hash: str
    extrinsic_hash: str


@dataclass
class SubmitEventResult:
    tx_hash: str
    extrinsic_hash: str
    pallet: str
    event: str
    fields_json: str


def build_signed_tx(
    *,
    substrate_ws: Optional[str] = None,
    evm_rpc_url: str,
    private_key: str,
    precompile_address: str,
    data_hex: str,
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
    account_lib, keccak, to_canonical_address, to_checksum_address = _get_signing_libs()
    _ = max_fee_per_gas
    _ = max_priority_fee_per_gas

    data = _decode_hex_bytes(data_hex)
    to_addr = _normalize_address(precompile_address, to_checksum_address, field="precompile_address")
    account = _parse_account(private_key, account_lib)
    signer_checksum = to_checksum_address(account.address)

    resolved_chain_id = chain_id if chain_id is not None else _rpc_get_chain_id(evm_rpc_url)
    if nonce_ms is not None:
        resolved_nonce = int(nonce_ms)
    elif use_timestamp_nonce:
        resolved_nonce = int(time.time() * 1000)
    else:
        resolved_nonce = _get_native_account_nonce(
            substrate_ws=substrate_ws,
            evm_rpc_url=evm_rpc_url,
            address=signer_checksum,
            system_precompile_address=system_precompile_address,
        )

    if use_legacy:
        tx: dict[str, Any] = {
            "to": to_addr,
            "value": int(value),
            "data": "0x" + data.hex(),
            "nonce": resolved_nonce,
            "chainId": int(resolved_chain_id),
            "gasPrice": 0,
        }
    else:
        tx = {
            "type": 2,
            "to": to_addr,
            "value": int(value),
            "data": "0x" + data.hex(),
            "nonce": resolved_nonce,
            "chainId": int(resolved_chain_id),
            "maxFeePerGas": 0,
            "maxPriorityFeePerGas": 0,
        }

    if gas_limit is not None:
        tx["gas"] = int(gas_limit)
    else:
        # Use a minimal object for estimateGas; some RPCs reject fields like chainId/type.
        estimate = _estimate_tx_for_rpc(tx=tx, from_addr=signer_checksum)
        try:
            estimated_gas = _rpc_estimate_gas(evm_rpc_url, estimate)
            tx["gas"] = (
                estimated_gas if int(estimated_gas) > 0 else DEFAULT_PRECOMPILE_GAS_LIMIT
            )
        except RuntimeError:
            tx["gas"] = DEFAULT_PRECOMPILE_GAS_LIMIT

    try:
        signed = account_lib.sign_transaction(tx, account.key)
    except Exception as exc:
        raise RuntimeError(f"sign transaction failed: {exc}") from exc

    raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raw = getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("sign transaction returned no raw transaction bytes")
    raw_bytes = bytes(raw)

    return SignedTxPayload(
        signed_tx="0x" + raw_bytes.hex(),
        signer="0x" + to_canonical_address(signer_checksum).hex(),
        tx_hash="0x" + keccak(raw_bytes).hex(),
        nonce=resolved_nonce,
        gas_limit=int(tx.get("gas", 0)),
    )


def submit_signed_tx(
    *,
    substrate_ws: str,
    signed_tx_hex: str,
    signer: str,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SubmitTxResult:
    signed_tx = _decode_hex_bytes(signed_tx_hex)
    signer_hex = _normalize_h160_hex(signer, field="signer")
    tx = _decode_signed_rlp_bytes_to_transaction_v2(signed_tx)
    receipt = _submit_ethereum_transact(
        substrate_ws=substrate_ws,
        tx=tx,
        signer=signer_hex,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    _ensure_receipt_success(receipt)
    return SubmitTxResult(
        tx_hash=_eth_tx_hash(signed_tx),
        extrinsic_hash=str(getattr(receipt, "extrinsic_hash", "")),
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
    _native_debug(
        "submit_signed_tx_wait_event:start "
        f"pallet={pallet} event={event} wait_for_finalized={wait_for_finalized} timeout_ms={timeout_ms}"
    )
    signed_tx = _decode_hex_bytes(signed_tx_hex)
    signer_hex = _normalize_h160_hex(signer, field="signer")
    tx = _decode_signed_rlp_bytes_to_transaction_v2(signed_tx)
    receipt = _submit_ethereum_transact(
        substrate_ws=substrate_ws,
        tx=tx,
        signer=signer_hex,
        # Query events from the inclusion block as early as possible; on
        # non-archive nodes historical state can disappear quickly.
        wait_for_finalized=False,
        timeout_ms=timeout_ms,
    )
    _ensure_receipt_success(receipt, allow_unknown=True)
    _native_debug(
        "submit_signed_tx_wait_event:receipt "
        f"extrinsic_hash={getattr(receipt, 'extrinsic_hash', None)} "
        f"block_hash={getattr(receipt, 'block_hash', None)} "
        f"status={getattr(receipt, 'is_success', None)}"
    )

    receipt_events, receipt_events_err = _safe_triggered_events(receipt)
    receipt_matches = _filter_matching_events(receipt_events, pallet=pallet, event=event)
    _native_debug(
        "submit_signed_tx_wait_event:path=receipt "
        f"events_len={len(receipt_events)} match_count={len(receipt_matches)} "
        f"receipt_events_err={receipt_events_err}"
    )
    if receipt_matches:
        attrs = receipt_matches[0].get("attributes")
        _native_debug(
            "submit_signed_tx_wait_event:path=receipt "
            f"hit>=1 using_first match_count={len(receipt_matches)}"
        )
        return SubmitEventResult(
            tx_hash=_eth_tx_hash(signed_tx),
            extrinsic_hash=str(getattr(receipt, "extrinsic_hash", "")),
            pallet=pallet,
            event=event,
            fields_json=json.dumps(_json_ready(attrs), ensure_ascii=False),
        )

    extrinsic_idx = _receipt_extrinsic_idx(receipt)
    _native_debug(f"submit_signed_tx_wait_event:path=block extrinsic_idx={extrinsic_idx}")
    block_events, block_events_err = _safe_block_events(receipt)
    scoped_events = _filter_events_for_extrinsic(block_events, extrinsic_idx=extrinsic_idx)
    block_matches = _filter_matching_events(scoped_events, pallet=pallet, event=event)

    _native_debug(
        "submit_signed_tx_wait_event:path=block "
        f"block_events_len={len(block_events)} scoped_len={len(scoped_events)} "
        f"match_count={len(block_matches)} block_events_err={block_events_err}"
    )
    if block_matches:
        attrs = block_matches[0].get("attributes")
        _native_debug(
            "submit_signed_tx_wait_event:path=block "
            f"hit>=1 using_first match_count={len(block_matches)}"
        )
        return SubmitEventResult(
            tx_hash=_eth_tx_hash(signed_tx),
            extrinsic_hash=str(getattr(receipt, "extrinsic_hash", "")),
            pallet=pallet,
            event=event,
            fields_json=json.dumps(_json_ready(attrs), ensure_ascii=False),
        )

    _native_debug("submit_signed_tx_wait_event:path=none hit=0 raising")
    raise RuntimeError(
        "event not found: "
        f"{pallet}::{event}, "
        f"extrinsic_hash={getattr(receipt, 'extrinsic_hash', None)}, "
        f"block_hash={getattr(receipt, 'block_hash', None)}, "
        f"extrinsic_idx={extrinsic_idx}, "
        f"block_match_count={len(block_matches)}, "
        f"block_events_sample={_event_overview_from_values(block_matches or scoped_events or block_events)}, "
        f"block_events_error={block_events_err}"
    )


def submit_pallet_call(
    *,
    substrate_ws: str,
    private_key: str,
    call_module: str,
    call_function: str,
    call_params: dict[str, Any],
    evm_rpc_url: Optional[str] = None,
    nonce_ms: Optional[int] = None,
    use_timestamp_nonce: bool = True,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SubmitTxResult:
    receipt = _submit_signed_pallet_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_module=call_module,
        call_function=call_function,
        call_params=call_params,
        nonce_ms=nonce_ms,
        use_timestamp_nonce=use_timestamp_nonce,
        wait_for_finalized=wait_for_finalized,
        timeout_ms=timeout_ms,
    )
    _ensure_receipt_success(receipt)
    extrinsic_hash = str(getattr(receipt, "extrinsic_hash", ""))
    return SubmitTxResult(tx_hash=extrinsic_hash, extrinsic_hash=extrinsic_hash)


def submit_pallet_call_wait_event(
    *,
    substrate_ws: str,
    private_key: str,
    call_module: str,
    call_function: str,
    call_params: dict[str, Any],
    pallet: str,
    event: str,
    evm_rpc_url: Optional[str] = None,
    nonce_ms: Optional[int] = None,
    use_timestamp_nonce: bool = True,
    wait_for_finalized: bool = True,
    timeout_ms: Optional[int] = None,
) -> SubmitEventResult:
    _native_debug(
        "submit_pallet_call_wait_event:start "
        f"call={call_module}.{call_function} pallet={pallet} event={event} "
        f"wait_for_finalized={wait_for_finalized} timeout_ms={timeout_ms}"
    )
    receipt = _submit_signed_pallet_call(
        substrate_ws=substrate_ws,
        evm_rpc_url=evm_rpc_url,
        private_key=private_key,
        call_module=call_module,
        call_function=call_function,
        call_params=call_params,
        nonce_ms=nonce_ms,
        use_timestamp_nonce=use_timestamp_nonce,
        # Keep parity with submit_signed_tx_wait_event: inclusion is enough to
        # read scoped events from the block and avoids archive-node sensitivity.
        wait_for_finalized=False,
        timeout_ms=timeout_ms,
    )
    _ensure_receipt_success(receipt, allow_unknown=True)
    extrinsic_hash = str(getattr(receipt, "extrinsic_hash", ""))

    receipt_events, receipt_events_err = _safe_triggered_events(receipt)
    receipt_matches = _filter_matching_events(receipt_events, pallet=pallet, event=event)
    _native_debug(
        "submit_pallet_call_wait_event:path=receipt "
        f"events_len={len(receipt_events)} match_count={len(receipt_matches)} "
        f"receipt_events_err={receipt_events_err}"
    )
    if receipt_matches:
        attrs = receipt_matches[0].get("attributes")
        return SubmitEventResult(
            tx_hash=extrinsic_hash,
            extrinsic_hash=extrinsic_hash,
            pallet=pallet,
            event=event,
            fields_json=json.dumps(_json_ready(attrs), ensure_ascii=False),
        )

    extrinsic_idx = _receipt_extrinsic_idx(receipt)
    block_events, block_events_err = _safe_block_events(receipt)
    scoped_events = _filter_events_for_extrinsic(block_events, extrinsic_idx=extrinsic_idx)
    block_matches = _filter_matching_events(scoped_events, pallet=pallet, event=event)

    _native_debug(
        "submit_pallet_call_wait_event:path=block "
        f"block_events_len={len(block_events)} scoped_len={len(scoped_events)} "
        f"match_count={len(block_matches)} block_events_err={block_events_err}"
    )
    if block_matches:
        attrs = block_matches[0].get("attributes")
        return SubmitEventResult(
            tx_hash=extrinsic_hash,
            extrinsic_hash=extrinsic_hash,
            pallet=pallet,
            event=event,
            fields_json=json.dumps(_json_ready(attrs), ensure_ascii=False),
        )

    failed_attrs = _system_extrinsic_failed_attrs(scoped_events)
    if failed_attrs is not None:
        _ctx = (
            f"submit extrinsic failed (block events): call={call_module}.{call_function}, "
            f"expected_event={pallet}::{event}, extrinsic_hash={extrinsic_hash}"
        )
        _cerr = _chain_error_from_failed_attrs(failed_attrs, _ctx)
        if _cerr is not None:
            raise _cerr
        raise RuntimeError(
            "submit extrinsic failed (block events): "
            f"call={call_module}.{call_function}, "
            f"expected_event={pallet}::{event}, "
            f"extrinsic_hash={extrinsic_hash}, "
            f"block_hash={getattr(receipt, 'block_hash', None)}, "
            f"extrinsic_idx={extrinsic_idx}, "
            f"attributes={_json_ready(failed_attrs)}"
        )

    raise RuntimeError(
        "event not found: "
        f"{pallet}::{event}, "
        f"extrinsic_hash={extrinsic_hash}, "
        f"block_hash={getattr(receipt, 'block_hash', None)}, "
        f"extrinsic_idx={extrinsic_idx}, "
        f"block_events_sample={_event_overview_from_values(scoped_events or block_events)}, "
        f"block_events_error={block_events_err}"
    )


def _get_signing_libs() -> tuple[Any, Any, Any, Any]:
    global _Account
    global _keccak
    global _to_canonical_address
    global _to_checksum_address
    if _Account is None:
        try:
            from eth_account import Account
            from eth_utils import keccak, to_canonical_address, to_checksum_address
        except Exception as exc:
            raise ImportError(
                "Missing Python signing deps. Install with 'pip install eth-account eth-utils'."
            ) from exc
        _Account = Account
        _keccak = keccak
        _to_canonical_address = to_canonical_address
        _to_checksum_address = to_checksum_address
    return _Account, _keccak, _to_canonical_address, _to_checksum_address


def _parse_account(private_key: str, account_lib: Any) -> Any:
    key = private_key.strip()
    try:
        return account_lib.from_key(key)
    except Exception as exc:
        raise RuntimeError(f"invalid private_key: {exc}") from exc


def _normalize_address(value: str, to_checksum_address: Any, *, field: str) -> str:
    raw = value.strip()
    if not raw.startswith("0x"):
        raw = "0x" + raw
    try:
        return to_checksum_address(raw)
    except Exception as exc:
        raise RuntimeError(f"invalid {field}: {exc}") from exc


def _rpc_get_chain_id(evm_rpc_url: str) -> int:
    result = _rpc_call(evm_rpc_url, "eth_chainId", [])
    if not isinstance(result, str):
        raise RuntimeError(f"eth_chainId invalid result: {result}")
    return int(result, 16)


def _rpc_get_transaction_count(evm_rpc_url: str, address: str) -> int:
    result = _rpc_call(evm_rpc_url, "eth_getTransactionCount", [address, "pending"])
    if not isinstance(result, str):
        raise RuntimeError(f"eth_getTransactionCount invalid result: {result}")
    return int(result, 16)


def _get_native_account_nonce(
    *,
    substrate_ws: Optional[str],
    evm_rpc_url: str,
    address: str,
    system_precompile_address: Optional[str] = None,
) -> int:
    pending_nonces: list[int] = []
    if system_precompile_address is None:
        system_precompile_address = DEFAULT_SYSTEM_PRECOMPILE_ADDRESS
    if system_precompile_address:
        try:
            pending_nonces.append(
                _rpc_get_deepx_system_account_nonce(
                    evm_rpc_url=evm_rpc_url,
                    system_precompile_address=system_precompile_address,
                    address=address,
                )
            )
        except Exception:
            pass
    if substrate_ws:
        try:
            substrate_cls = _get_substrate_interface_cls()
            substrate = _create_substrate(substrate_cls, substrate_ws)
            try:
                value = substrate.rpc_request("system_accountNextIndex", [address])
                result = value.get("result") if isinstance(value, dict) else None
                if result is not None:
                    pending_nonces.append(int(result))
            except Exception:
                pass
        except Exception:
            pass
    try:
        pending_nonces.append(_rpc_get_transaction_count(evm_rpc_url, address))
    except Exception:
        if not pending_nonces:
            raise
    return max(pending_nonces)


def _rpc_get_deepx_system_account_nonce(
    *,
    evm_rpc_url: str,
    system_precompile_address: str,
    address: str,
) -> int:
    _, keccak, _, to_checksum_address = _get_signing_libs()
    try:
        from eth_abi import decode as abi_decode
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("eth_abi is required to decode systemAccountV2") from exc

    to_addr = _normalize_address(
        system_precompile_address,
        to_checksum_address,
        field="system_precompile_address",
    )
    account = _normalize_address(address, to_checksum_address, field="address")
    selector = keccak(text="systemAccountV2(address)")[:4]
    encoded_addr = bytes.fromhex(account[2:].lower()).rjust(32, b"\x00")
    raw = _rpc_call(
        evm_rpc_url,
        "eth_call",
        [
            {
                "to": to_addr,
                "data": "0x" + (selector + encoded_addr).hex(),
            },
            "latest",
        ],
    )
    if not isinstance(raw, str):
        raise RuntimeError(f"eth_call invalid result: {raw}")
    payload = bytes.fromhex(raw[2:] if raw.startswith("0x") else raw)
    try:
        (info,) = abi_decode(["(uint64,uint64,uint64[],uint32,bool)"], payload)
    except Exception as exc:
        raise RuntimeError(f"systemAccountV2 decode failed: {exc}") from exc
    return int(info[0])


def _rpc_estimate_gas(evm_rpc_url: str, tx: dict[str, Any]) -> int:
    result = _rpc_call(evm_rpc_url, "eth_estimateGas", [tx])
    if isinstance(result, str):
        return int(result, 16)
    if isinstance(result, int):
        return result
    raise RuntimeError(f"eth_estimateGas invalid result: {result}")


def _rpc_call(evm_rpc_url: str, method: str, params: list[Any]) -> Any:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }
    headers, timeout_s = rpc_request_options()
    endpoints, endpoint_pool = rpc_request_endpoints(evm_rpc_url)
    for index, endpoint in enumerate(endpoints):
        _native_debug(
            f"rpc_call:start method={method} url={endpoint} timeout={timeout_s}s"
        )
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 and index + 1 < len(endpoints):
                exc.close()
                continue
            _native_debug(
                f"rpc_call:http_error method={method} "
                f"{time.monotonic()-t0:.2f}s code={exc.code}"
            )
            detail = f"HTTP {exc.code} {exc.reason}"
            raw = ""
            if exc.fp:
                try:
                    raw = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    raw = ""
            raw = raw.replace("\n", " ").strip()
            if raw:
                if len(raw) > 240:
                    raw = raw[:237] + "..."
                detail = f"{detail} body={raw}"
            raise RuntimeError(f"{method} request failed: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if index + 1 < len(endpoints):
                continue
            _native_debug(
                f"rpc_call:url_error method={method} "
                f"{time.monotonic()-t0:.2f}s {exc}"
            )
            raise RuntimeError(f"{method} request failed: {exc}") from exc
        except Exception as exc:
            _native_debug(
                f"rpc_call:error method={method} "
                f"{time.monotonic()-t0:.2f}s {type(exc).__name__}: {exc}"
            )
            raise RuntimeError(f"{method} request failed: {exc}") from exc
        if endpoint_pool is not None:
            endpoint_pool.mark_success(endpoint)
        break
    _native_debug(
        f"rpc_call:ok method={method} {time.monotonic()-t0:.2f}s"
    )

    if "error" in body:
        raise RuntimeError(f"{method} error: {body['error']}")
    if "result" not in body:
        raise RuntimeError(f"{method} invalid response: {body}")
    return body["result"]


def _decode_hex_bytes(raw: str) -> bytes:
    data = raw.strip()
    if data.startswith("0x"):
        data = data[2:]
    if data == "":
        return b""
    try:
        return bytes.fromhex(data)
    except Exception as exc:
        raise RuntimeError(f"invalid hex data: {exc}") from exc


def _to_rpc_quantity(value: int) -> str:
    if value < 0:
        raise RuntimeError(f"invalid negative quantity: {value}")
    return hex(value)


def _tx_for_rpc(tx: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in tx.items():
        if isinstance(value, int):
            out[key] = _to_rpc_quantity(value)
        else:
            out[key] = value
    return out


def _estimate_tx_for_rpc(*, tx: dict[str, Any], from_addr: str) -> dict[str, Any]:
    estimate: dict[str, Any] = {"from": from_addr}
    for key in ("to", "data", "value"):
        if key in tx:
            estimate[key] = tx[key]
    return _tx_for_rpc(estimate)


def _get_substrate_interface_cls() -> Any:
    global _SubstrateInterface
    if _SubstrateInterface is None:
        try:
            from substrateinterface import SubstrateInterface
        except Exception as exc:
            raise ImportError(
                "Missing dependency for Substrate submit. Install with "
                "'pip install substrate-interface'."
            ) from exc
        _SubstrateInterface = SubstrateInterface
    return _SubstrateInterface


def _get_substrate_keypair_libs() -> tuple[Any, Any]:
    global _SubstrateKeypair
    global _SubstrateKeypairType
    if _SubstrateKeypair is None:
        try:
            from substrateinterface import Keypair, KeypairType
        except Exception as exc:
            raise ImportError(
                "Missing dependency for Substrate signing. Install with "
                "'pip install substrate-interface'."
            ) from exc
        _SubstrateKeypair = Keypair
        _SubstrateKeypairType = KeypairType
    return _SubstrateKeypair, _SubstrateKeypairType


def _create_ecdsa_keypair(private_key: str) -> Any:
    keypair_cls, keypair_type = _get_substrate_keypair_libs()
    raw = private_key.strip()
    if raw.startswith("0x"):
        raw = raw[2:]
    try:
        bytes.fromhex(raw)
    except Exception as exc:
        raise RuntimeError(f"invalid private_key: {exc}") from exc

    key_hex = "0x" + raw
    crypto_type = getattr(keypair_type, "ECDSA", 2)
    errors: list[str] = []
    for kwargs in (
        {"private_key": key_hex, "crypto_type": crypto_type},
        {"private_key": bytes.fromhex(raw), "crypto_type": crypto_type},
        {"private_key": key_hex},
    ):
        try:
            return keypair_cls.create_from_private_key(**kwargs)
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("create ECDSA substrate keypair failed: " + " | ".join(errors))


def _submit_signed_pallet_call(
    *,
    substrate_ws: str,
    evm_rpc_url: Optional[str],
    private_key: str,
    call_module: str,
    call_function: str,
    call_params: dict[str, Any],
    nonce_ms: Optional[int],
    use_timestamp_nonce: bool,
    wait_for_finalized: bool,
    timeout_ms: Optional[int],
) -> Any:
    if not substrate_ws.startswith(("ws://", "wss://")):
        raise RuntimeError("substrate_ws must be ws:// or wss://")

    substrate_cls = _get_substrate_interface_cls()
    keypair = _create_ecdsa_keypair(private_key)
    account_lib, _, _, to_checksum_address = _get_signing_libs()
    signer = to_checksum_address(account_lib.from_key(private_key).address)

    def _submit() -> Any:
        last_nonce: int | None = None
        attempts = 3 if nonce_ms is None and not use_timestamp_nonce else 1
        last_error: BaseException | None = None
        for _attempt in range(attempts):
            _native_debug(f"_submit_signed_pallet_call:connect ws={substrate_ws} timeout_ms={timeout_ms}")
            substrate = _create_substrate(substrate_cls, substrate_ws, timeout_ms=timeout_ms)
            _native_debug("_submit_signed_pallet_call:compose_call:start")
            t0 = time.monotonic()
            call = substrate.compose_call(
                call_module=call_module,
                call_function=call_function,
                call_params=call_params,
            )
            _native_debug(f"_submit_signed_pallet_call:compose_call:ok {time.monotonic()-t0:.2f}s")
            if nonce_ms is not None:
                nonce = int(nonce_ms)
            elif use_timestamp_nonce:
                nonce = int(time.time() * 1000)
            else:
                resolved = _get_native_account_nonce(
                    substrate_ws=substrate_ws,
                    evm_rpc_url=evm_rpc_url or substrate_ws,
                    address=signer,
                )
                nonce = resolved if last_nonce is None else max(resolved, last_nonce + 1)
            _native_debug(f"_submit_signed_pallet_call:create_signed_extrinsic:start nonce={nonce}")
            t0 = time.monotonic()
            extrinsic = substrate.create_signed_extrinsic(call=call, keypair=keypair, nonce=nonce)
            _native_debug(f"_submit_signed_pallet_call:create_signed_extrinsic:ok {time.monotonic()-t0:.2f}s")
            try:
                _native_debug(
                    f"_submit_signed_pallet_call:submit_extrinsic:start "
                    f"wait_for_finalized={wait_for_finalized} timeout_ms={timeout_ms}"
                )
                t0 = time.monotonic()
                result = _submit_extrinsic_with_timeout(
                    substrate,
                    extrinsic,
                    wait_for_inclusion=not wait_for_finalized,
                    wait_for_finalization=wait_for_finalized,
                    timeout_ms=timeout_ms,
                )
                _native_debug(f"_submit_signed_pallet_call:submit_extrinsic:ok {time.monotonic()-t0:.2f}s")
                return result
            except BaseException as exc:
                _native_debug(
                    f"_submit_signed_pallet_call:submit_extrinsic:error {time.monotonic()-t0:.2f}s "
                    f"{type(exc).__name__}: {str(exc)[:120]}"
                )
                last_error = exc
                if nonce_ms is None and not use_timestamp_nonce and _is_outdated_transaction_error(exc):
                    last_nonce = nonce
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("submit pallet call failed without an error")

    return _submit()


def _submit_ethereum_transact(
    *,
    substrate_ws: str,
    tx: dict[str, Any],
    signer: str,
    wait_for_finalized: bool,
    timeout_ms: Optional[int],
) -> Any:
    if not substrate_ws.startswith(("ws://", "wss://")):
        raise RuntimeError("substrate_ws must be ws:// or wss://")

    substrate_cls = _get_substrate_interface_cls()

    def _submit() -> Any:
        substrate = _create_substrate(substrate_cls, substrate_ws, timeout_ms=timeout_ms)
        call = _compose_ethereum_transact_call(substrate, tx, signer)
        extrinsic = substrate.create_unsigned_extrinsic(call=call)
        return _submit_extrinsic_with_timeout(
            substrate,
            extrinsic,
            wait_for_inclusion=not wait_for_finalized,
            wait_for_finalization=wait_for_finalized,
            timeout_ms=timeout_ms,
        )

    return _submit()


def _submit_extrinsic_with_timeout(
    substrate: Any,
    extrinsic: Any,
    *,
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
    timeout_ms: Optional[int],
) -> Any:
    effective_timeout_ms = timeout_ms if timeout_ms is not None and timeout_ms > 0 else DEFAULT_SUBMIT_TIMEOUT_MS
    _native_debug(
        f"_submit_extrinsic_with_timeout:start effective_timeout_ms={effective_timeout_ms} "
        f"wait_for_inclusion={wait_for_inclusion} wait_for_finalization={wait_for_finalization}"
    )
    start_t = time.monotonic()

    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            result = substrate.submit_extrinsic(
                extrinsic,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
            )
        except BaseException as exc:
            _native_debug(f"_submit_extrinsic_with_timeout:runner:error {type(exc).__name__}: {str(exc)[:120]}")
            try:
                result_queue.put_nowait(("error", exc))
            except queue.Full:
                pass
            return
        _native_debug("_submit_extrinsic_with_timeout:runner:ok")
        try:
            result_queue.put_nowait(("result", result))
        except queue.Full:
            pass

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        kind, value = result_queue.get(timeout=effective_timeout_ms / 1000.0)
    except queue.Empty as exc:
        _native_debug(f"_submit_extrinsic_with_timeout:TIMEOUT after {effective_timeout_ms}ms")
        try:
            substrate.close()
        except Exception:
            pass
        raise TimeoutError(
            "submit_extrinsic timed out after "
            f"{effective_timeout_ms}ms "
            f"(wait_for_inclusion={wait_for_inclusion}, "
            f"wait_for_finalization={wait_for_finalization})"
        ) from exc
    if kind == "error":
        raise value

    # wait_for_inclusion/finalization uses author_submitAndWatchExtrinsic: the
    # node pushes inclusion/finalization instead of us scanning blocks, and the
    # receipt comes back with block_hash set. If it doesn't (subscription
    # quirk), fall back to block-scan polling so downstream event reading works.
    if (wait_for_inclusion or wait_for_finalization) and not getattr(value, "block_hash", None):
        extrinsic_hash = str(getattr(value, "extrinsic_hash", ""))
        if extrinsic_hash:
            remaining_ms = max(
                int((start_t + effective_timeout_ms / 1000.0 - time.monotonic()) * 1000), 1000
            )
            _native_debug(
                f"_submit_extrinsic_with_timeout:no block_hash from subscription, "
                f"fall back to scan remaining_ms={remaining_ms}"
            )
            return _scan_for_inclusion(
                substrate,
                extrinsic_hash=extrinsic_hash,
                receipt_cls=type(value),
                wait_for_finalization=wait_for_finalization,
                timeout_ms=remaining_ms,
            )
    return value


def _scan_for_inclusion(
    substrate: Any,
    *,
    extrinsic_hash: str,
    receipt_cls: Any,
    wait_for_finalization: bool,
    timeout_ms: int,
) -> Any:
    # Fallback path: locate the block containing `extrinsic_hash` by scanning
    # recent blocks. Only used when the author_submitAndWatchExtrinsic
    # subscription returned without a block_hash.
    deadline = time.monotonic() + (max(timeout_ms, 1) / 1000.0)
    seen_block_numbers: set[int] = set()
    next_block_number = _safe_current_block_number(substrate)
    _native_debug(
        f"_scan_for_inclusion:start extrinsic_hash={extrinsic_hash} "
        f"wait_for_finalization={wait_for_finalization} timeout_ms={timeout_ms} "
        f"start_block={next_block_number}"
    )
    poll_iter = 0

    while time.monotonic() < deadline:
        poll_iter += 1
        if wait_for_finalization:
            block_hash = _safe_finalized_head(substrate)
            if poll_iter <= 3 or poll_iter % 10 == 0:
                _native_debug(f"_scan_for_inclusion:poll iter={poll_iter} finalized_head={block_hash}")
            if block_hash:
                receipt = _receipt_if_block_contains(
                    substrate=substrate,
                    receipt_cls=receipt_cls,
                    extrinsic_hash=extrinsic_hash,
                    block_hash=block_hash,
                    finalized=True,
                )
                if receipt is not None:
                    _native_debug(f"_scan_for_inclusion:found finalized iter={poll_iter}")
                    return receipt
        else:
            current = _safe_current_block_number(substrate)
            if poll_iter <= 3 or poll_iter % 10 == 0:
                _native_debug(f"_scan_for_inclusion:poll iter={poll_iter} current_block={current}")
            if current is not None:
                start = next_block_number if next_block_number is not None else max(current - 6, 0)
                for block_number in range(start, current + 1):
                    if block_number in seen_block_numbers:
                        continue
                    seen_block_numbers.add(block_number)
                    receipt = _receipt_if_block_contains(
                        substrate=substrate,
                        receipt_cls=receipt_cls,
                        extrinsic_hash=extrinsic_hash,
                        block_number=block_number,
                        finalized=False,
                    )
                    if receipt is not None:
                        _native_debug(f"_scan_for_inclusion:found iter={poll_iter} block={block_number}")
                        return receipt
                next_block_number = current + 1
        time.sleep(POLL_INTERVAL_S)

    _native_debug(f"_scan_for_inclusion:TIMEOUT iter={poll_iter} extrinsic_hash={extrinsic_hash}")
    raise TimeoutError(
        "submit_extrinsic inclusion poll timed out after "
        f"{timeout_ms}ms (extrinsic_hash={extrinsic_hash})"
    )


def _safe_current_block_number(substrate: Any) -> int | None:
    try:
        head = substrate.get_chain_head()
        if not head:
            return None
        return int(substrate.get_block_number(head))
    except Exception:
        return None


def _safe_finalized_head(substrate: Any) -> str | None:
    try:
        head = substrate.get_chain_finalised_head()
        return str(head) if head else None
    except Exception:
        return None


def _receipt_if_block_contains(
    *,
    substrate: Any,
    receipt_cls: Any,
    extrinsic_hash: str,
    block_hash: str | None = None,
    block_number: int | None = None,
    finalized: bool,
) -> Any | None:
    try:
        if block_hash is None:
            block_hash = substrate.get_block_hash(block_number)
        block = substrate.get_block(block_hash=block_hash)
    except Exception:
        return None
    extrinsics = (block or {}).get("extrinsics", [])
    for idx, item in enumerate(extrinsics):
        item_hash = getattr(item, "extrinsic_hash", None)
        if item_hash is None:
            continue
        if "0x" + item_hash.hex() == extrinsic_hash:
            return receipt_cls(
                substrate=substrate,
                extrinsic_hash=extrinsic_hash,
                block_hash=block_hash,
                block_number=block_number,
                extrinsic_idx=idx,
                finalized=finalized,
            )
    return None


def _is_outdated_transaction_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "transaction is outdated" in text or "invalidtransaction::stale" in text


def build_ethereum_transact_extrinsic(
    *,
    substrate_ws: str,
    signed_tx_hex: str,
    signer: str,
) -> str:
    if not substrate_ws.startswith(("ws://", "wss://")):
        raise RuntimeError("substrate_ws must be ws:// or wss://")

    signed_tx = _decode_hex_bytes(signed_tx_hex)
    signer_hex = _normalize_h160_hex(signer, field="signer")
    tx = _decode_signed_rlp_bytes_to_transaction_v2(signed_tx)
    substrate_cls = _get_substrate_interface_cls()
    substrate = _create_substrate(substrate_cls, substrate_ws)
    call = _compose_ethereum_transact_call(substrate, tx, signer_hex)
    extrinsic = substrate.create_unsigned_extrinsic(call=call)
    data = getattr(extrinsic, "data", extrinsic)
    if hasattr(data, "to_hex"):
        return data.to_hex()
    if hasattr(data, "hex"):
        return "0x" + data.hex()
    text = str(data)
    return text if text.startswith("0x") else "0x" + text


def build_signed_pallet_call_extrinsic(
    *,
    substrate_ws: str,
    private_key: str,
    call_module: str,
    call_function: str,
    call_params: dict[str, Any],
    nonce_ms: Optional[int] = None,
) -> str:
    if not substrate_ws.startswith(("ws://", "wss://")):
        raise RuntimeError("substrate_ws must be ws:// or wss://")

    substrate_cls = _get_substrate_interface_cls()
    keypair = _create_ecdsa_keypair(private_key)
    substrate = _create_substrate(substrate_cls, substrate_ws)
    call = substrate.compose_call(
        call_module=call_module,
        call_function=call_function,
        call_params=call_params,
    )
    nonce = int(nonce_ms) if nonce_ms is not None else int(time.time() * 1000)
    extrinsic = substrate.create_signed_extrinsic(call=call, keypair=keypair, nonce=nonce)
    data = getattr(extrinsic, "data", extrinsic)
    if hasattr(data, "to_hex"):
        return data.to_hex()
    if hasattr(data, "hex"):
        return "0x" + data.hex()
    text = str(data)
    return text if text.startswith("0x") else "0x" + text


def _ws_proxy_kwargs(substrate_ws: str) -> dict[str, Any]:
    # websocket-client does NOT read http_proxy/https_proxy env vars, so honor
    # them manually to keep the substrate WS transport consistent with the EVM
    # (urllib) transport, which already follows them. Returns websocket-client
    # create_connection kwargs (http_proxy_host/port[/auth]) or {} when no proxy
    # is configured — zero impact for users who haven't set a proxy.
    scheme = substrate_ws.split("://", 1)[0].lower() if "://" in substrate_ws else ""
    env_key = "https_proxy" if scheme == "wss" else "http_proxy"
    raw = os.environ.get(env_key) or os.environ.get(env_key.upper())
    if not raw:
        return {}
    parsed = urllib.parse.urlparse(raw)
    host = parsed.hostname
    if not host:
        return {}
    kwargs: dict[str, Any] = {"http_proxy_host": host}
    if parsed.port:
        kwargs["http_proxy_port"] = parsed.port
    if parsed.username or parsed.password:
        kwargs["http_proxy_auth"] = (parsed.username or "", parsed.password or "")
    return kwargs


def _create_substrate(
    substrate_cls: Any,
    substrate_ws: str,
    timeout_ms: Optional[int] = None,
) -> Any:
    effective_ms = (
        timeout_ms if timeout_ms is not None and timeout_ms > 0 else DEFAULT_WS_TIMEOUT_MS
    )
    endpoints, endpoint_pool = substrate_ws_request_endpoints(substrate_ws)
    last_error: BaseException | None = None
    connection_errors: list[tuple[str, BaseException]] = []
    substrate: Any = None
    for endpoint in endpoints:
        ws_options: dict[str, Any] = {"timeout": effective_ms / 1000.0}
        ws_options.update(_ws_proxy_kwargs(endpoint))
        kwargs: dict[str, Any] = {
            "url": endpoint,
            "ws_options": ws_options,
        }
        try:
            try:
                substrate = substrate_cls(**kwargs)
            except TypeError:
                # Older substrate-interface builds don't accept ws_options.
                substrate = substrate_cls(url=endpoint)
        except BaseException as exc:
            last_error = exc
            connection_errors.append((endpoint, exc))
            continue
        if endpoint_pool is not None:
            endpoint_pool.mark_success(endpoint)
        break
    if substrate is None:
        if last_error is not None:
            if endpoint_pool is not None and len(connection_errors) > 1:
                details = "; ".join(
                    f"{endpoint_pool.display(endpoint)}: "
                    f"{type(exc).__name__}: "
                    f"{str(exc).replace(endpoint, endpoint_pool.display(endpoint))[:160]}"
                    for endpoint, exc in connection_errors
                )
                raise RPCError(
                    "Unable to connect to any configured Substrate WebSocket "
                    f"endpoint ({len(connection_errors)} attempted): {details}"
                ) from last_error
            raise last_error
        raise RuntimeError("Unable to connect to a Substrate WebSocket endpoint.")
    # DeepX AccountNonceApi returns an 8-byte AccountIndex on devnet, while
    # substrate-interface 1.8 decodes account_nonce as U32 in strict mode.
    # Non-strict mode preserves the decoded nonce and ignores the trailing bytes.
    try:
        substrate.config["strict_scale_decode"] = False
    except Exception:
        pass
    return substrate


def _compose_ethereum_transact_call(substrate: Any, tx: dict[str, Any], signer: str) -> Any:
    errors: list[str] = []
    for call_module in ("Ethereum", "ethereum"):
        for source_key in ("source", "signer"):
            try:
                return substrate.compose_call(
                    call_module=call_module,
                    call_function="transact",
                    call_params={"transaction": tx, source_key: signer},
                )
            except Exception as exc:
                errors.append(f"{call_module}.{source_key}: {exc}")
    raise RuntimeError("compose Ethereum.transact call failed: " + " | ".join(errors))


def _ensure_receipt_success(receipt: Any, *, allow_unknown: bool = False) -> None:
    try:
        success = getattr(receipt, "is_success", None)
    except Exception as exc:
        raise RuntimeError(f"unable to determine extrinsic status: {exc}") from exc

    if success is True:
        return

    err: Any = None
    try:
        err = getattr(receipt, "error_message", None)
    except Exception:
        err = None

    if success is False:
        if err:
            raise RuntimeError(str(err))
        events, events_err = _safe_triggered_events(receipt)
        raise RuntimeError(
            "submit extrinsic failed: "
            f"extrinsic_hash={getattr(receipt, 'extrinsic_hash', None)}, "
            f"block_hash={getattr(receipt, 'block_hash', None)}, "
            f"events={_event_overview(events)}, "
            f"triggered_events_error={events_err}"
        )

    # Some runtimes/clients may not expose System::ExtrinsicSuccess/Failed in a way
    # substrate-interface can infer; keep going and rely on event matching in caller.
    if allow_unknown:
        return

    # For submit_signed_tx (no expected pallet/event), try block-level events as a
    # secondary status source. This is required on runtimes where triggered_events is
    # empty but System.EventsMap still contains decodable events.
    block_events, block_events_err = _safe_block_events(receipt)
    extrinsic_idx = _receipt_extrinsic_idx(receipt)
    scoped_events = _filter_events_for_extrinsic(block_events, extrinsic_idx=extrinsic_idx)

    has_failed = False
    has_success = False
    failed_attrs: Any = None
    for ev in scoped_events:
        value = _event_value(ev)
        if value is None:
            continue
        module_id = value.get("module_id")
        event_id = value.get("event_id")
        if module_id == "System" and event_id == "ExtrinsicFailed":
            has_failed = True
            failed_attrs = value.get("attributes")
            break
        if module_id == "System" and event_id == "ExtrinsicSuccess":
            has_success = True

    _native_debug(
        "_ensure_receipt_success:status_unknown "
        f"extrinsic_hash={getattr(receipt, 'extrinsic_hash', None)} "
        f"block_hash={getattr(receipt, 'block_hash', None)} "
        f"extrinsic_idx={extrinsic_idx} "
        f"block_events_len={len(block_events)} scoped_len={len(scoped_events)} "
        f"has_success={has_success} has_failed={has_failed} block_events_err={block_events_err}"
    )

    if has_failed:
        _ctx = (
            f"submit extrinsic failed (block events): "
            f"extrinsic_hash={getattr(receipt, 'extrinsic_hash', None)}, "
            f"block_hash={getattr(receipt, 'block_hash', None)}, "
            f"extrinsic_idx={extrinsic_idx}"
        )
        _cerr = _chain_error_from_failed_attrs(failed_attrs, _ctx)
        if _cerr is not None:
            raise _cerr
        raise RuntimeError(
            "submit extrinsic failed (block events): "
            f"extrinsic_hash={getattr(receipt, 'extrinsic_hash', None)}, "
            f"block_hash={getattr(receipt, 'block_hash', None)}, "
            f"extrinsic_idx={extrinsic_idx}, "
            f"attributes={_json_ready(failed_attrs)}"
        )

    # If we can scope events to the extrinsic and saw either an explicit
    # ExtrinsicSuccess or any scoped events without ExtrinsicFailed, treat it as success.
    if extrinsic_idx is not None and scoped_events and (has_success or not has_failed):
        return

    # Some devnet nodes return enough block data to locate the extrinsic but
    # too few decodable events to scope System::ExtrinsicSuccess. For no-event
    # calls, inclusion plus absence of an explicit failure is the strongest
    # status available.
    if extrinsic_idx is not None and getattr(receipt, "block_hash", None) and not has_failed:
        return

    if err:
        raise RuntimeError(str(err))
    events, events_err = _safe_triggered_events(receipt)
    raise RuntimeError(
        "submit extrinsic status unknown: "
        f"extrinsic_hash={getattr(receipt, 'extrinsic_hash', None)}, "
        f"block_hash={getattr(receipt, 'block_hash', None)}, "
        f"events={_event_overview(events)}, "
        f"triggered_events_error={events_err}, "
        f"extrinsic_idx={extrinsic_idx}, "
        f"block_events_len={len(block_events)}, "
        f"scoped_events_len={len(scoped_events)}, "
        f"block_events_error={block_events_err}"
    )


def _safe_triggered_events(receipt: Any) -> tuple[list[Any], str | None]:
    try:
        events = getattr(receipt, "triggered_events", [])
    except Exception as exc:
        return [], str(exc)
    return list(events or []), None


def _safe_block_events(receipt: Any) -> tuple[list[Any], str | None]:
    substrate = getattr(receipt, "substrate", None)
    block_hash = getattr(receipt, "block_hash", None)
    if substrate is None or not block_hash:
        return [], "missing substrate or block_hash"
    return _load_block_events(substrate=substrate, block_hash=block_hash)


def _load_block_events(*, substrate: Any, block_hash: str) -> tuple[list[Any], str | None]:
    _native_debug(f"_load_block_events:start block_hash={block_hash}")
    errors: list[str] = []
    diagnostics: list[str] = []
    try:
        map_events = _events_from_system_events_map(substrate=substrate, block_hash=block_hash)
        diagnostics.append(f"events_map_normalized_len={len(map_events)}")
        _native_debug(f"_load_block_events:System.EventsMap normalized_len={len(map_events)}")
        if map_events:
            return map_events, None
    except Exception as exc:
        errors.append(f"rpc-decode(System.EventsMap): {exc}")
        _native_debug(f"_load_block_events:System.EventsMap error={exc}")

    try:
        events = list(substrate.get_events(block_hash=block_hash) or [])
        diagnostics.append(f"get_events_len={len(events)}")
        _native_debug(f"_load_block_events:get_events len={len(events)}")
        if events:
            return events, None
    except Exception as exc:
        errors.append(f"get_events: {exc}")
        _native_debug(f"_load_block_events:get_events error={exc}")

    try:
        # Compatibility fallback: on some runtimes/nodes, `get_events()` can
        # return an empty list while `System.Events` query still carries decodable
        # records in a different shape.
        query_obj = substrate.query(
            module="System",
            storage_function="Events",
            block_hash=block_hash,
        )
        q_value = getattr(query_obj, "value", None)
        q_elements = getattr(query_obj, "elements", None)
        diagnostics.append(
            "query_system_events="
            f"value_type={type(q_value).__name__},"
            f"value_len={_container_len(q_value)},"
            f"elements_type={type(q_elements).__name__},"
            f"elements_len={_container_len(q_elements)}"
        )
        fallback_events = _events_from_system_events_query(query_obj)
        diagnostics.append(f"query_system_events_normalized_len={len(fallback_events)}")
        _native_debug(
            "_load_block_events:query(System.Events) "
            f"value_type={type(q_value).__name__} value_len={_container_len(q_value)} "
            f"elements_type={type(q_elements).__name__} elements_len={_container_len(q_elements)} "
            f"normalized_len={len(fallback_events)}"
        )
        if fallback_events:
            return fallback_events, None
    except Exception as exc:
        errors.append(f"query(System.Events): {exc}")
        _native_debug(f"_load_block_events:query(System.Events) error={exc}")

    try:
        diagnostics.append(_rpc_system_events_raw_diag(substrate=substrate, block_hash=block_hash))
        _native_debug(f"_load_block_events:{diagnostics[-1]}")
    except Exception as exc:
        errors.append(f"rpc(System.Events raw): {exc}")
        _native_debug(f"_load_block_events:rpc(System.Events raw) error={exc}")

    try:
        rpc_events = _events_from_system_events_rpc(substrate=substrate, block_hash=block_hash)
        diagnostics.append(f"rpc_system_events_normalized_len={len(rpc_events)}")
        _native_debug(f"_load_block_events:rpc(System.Events) normalized_len={len(rpc_events)}")
        if rpc_events:
            return rpc_events, None
    except Exception as exc:
        errors.append(f"rpc-decode(System.Events): {exc}")
        _native_debug(f"_load_block_events:rpc(System.Events) error={exc}")

    try:
        qs_events = _events_from_system_events_query_storage_at(substrate=substrate, block_hash=block_hash)
        diagnostics.append(f"query_storage_at_system_events_len={len(qs_events)}")
        _native_debug(f"_load_block_events:queryStorageAt(System.Events) len={len(qs_events)}")
        if qs_events:
            return qs_events, None
    except Exception as exc:
        errors.append(f"queryStorageAt-decode(System.Events): {exc}")
        _native_debug(f"_load_block_events:queryStorageAt(System.Events) error={exc}")

    if errors:
        _native_debug(
            "_load_block_events:done with errors "
            f"errors={' | '.join(errors)} diagnostics={'; '.join(diagnostics)}"
        )
        if diagnostics:
            return [], " | ".join(errors + diagnostics)
        return [], " | ".join(errors)
    if diagnostics:
        _native_debug(f"_load_block_events:done diagnostics={'; '.join(diagnostics)}")
        return [], "; ".join(diagnostics)
    _native_debug("_load_block_events:done no events")
    return [], "no events from get_events/query/rpc"


def _rpc_result_or_none(substrate: Any, method: str, params: list[Any]) -> Any:
    try:
        response = substrate.rpc_request(method, params)
    except Exception:
        return None
    if not isinstance(response, dict):
        return None
    if "error" in response:
        return None
    return response.get("result")


def _block_number(*, substrate: Any, block_hash: Any) -> int | None:
    if not isinstance(block_hash, str) or not block_hash:
        return None
    header = _rpc_result_or_none(substrate, "chain_getHeader", [block_hash])
    if not isinstance(header, dict):
        return None
    return _parse_int_like(header.get("number"))


def _receipt_extrinsic_idx(receipt: Any) -> int | None:
    for key in ("extrinsic_idx", "extrinsic_index", "extrinsic_id"):
        raw = getattr(receipt, key, None)
        if raw is None:
            continue
        idx = _parse_int_like(raw)
        if idx is not None:
            return idx
    return None


def _filter_events_for_extrinsic(events: list[Any], *, extrinsic_idx: int | None) -> list[Any]:
    if extrinsic_idx is None:
        return []
    filtered: list[Any] = []
    for ev in events:
        value = _event_value(ev)
        if value is None:
            continue
        idx = _event_extrinsic_idx(value)
        if idx == extrinsic_idx:
            filtered.append(ev)
    return filtered


def _event_extrinsic_idx(value: dict[str, Any]) -> int | None:
    idx = _parse_int_like(value.get("extrinsic_idx"))
    if idx is not None:
        return idx
    phase = value.get("phase")
    if isinstance(phase, dict):
        idx = _parse_int_like(phase.get("ApplyExtrinsic"))
        if idx is not None:
            return idx
        idx = _parse_int_like(phase.get("apply_extrinsic"))
        if idx is not None:
            return idx
    if isinstance(phase, str):
        marker = "ApplyExtrinsic("
        pos = phase.find(marker)
        if pos >= 0:
            start = pos + len(marker)
            end = phase.find(")", start)
            if end > start:
                idx = _parse_int_like(phase[start:end])
                if idx is not None:
                    return idx
    return None


def _parse_int_like(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            if s.startswith(("0x", "0X")):
                return int(s, 16)
            return int(s)
        except ValueError:
            return None
    return None


def _filter_matching_events(events: list[Any], *, pallet: str, event: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for ev in events:
        value = _event_value(ev)
        if value is None:
            continue
        if value.get("module_id") == pallet and value.get("event_id") == event:
            matches.append(value)
    return matches


def _system_extrinsic_failed_attrs(events: list[Any]) -> Any | None:
    for ev in events:
        value = _event_value(ev)
        if value is None:
            continue
        if value.get("module_id") == "System" and value.get("event_id") == "ExtrinsicFailed":
            return value.get("attributes")
    return None


def _decode_dispatch_error_index(raw: Any) -> int | None:
    """Decode a Substrate `ModuleError.error` ([u8; 4]) value into its index."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        return int.from_bytes(bytes(raw), "little")
    if isinstance(raw, str):
        h = raw[2:] if raw.startswith("0x") else raw
        try:
            return int.from_bytes(bytes.fromhex(h), "little")
        except ValueError:
            return None
    if isinstance(raw, (list, tuple)) and raw:
        try:
            return int.from_bytes(bytes(raw), "little")
        except (ValueError, TypeError):
            return None
    return None


def _chain_error_from_failed_attrs(failed_attrs: Any, context: str) -> Any | None:
    """Build a typed ChainError from System::ExtrinsicFailed attributes.

    Returns None for non-Module dispatch errors (BadOrigin/Other/...) or
    undecodable shapes so the caller falls back to a RuntimeError carrying the
    full attributes.
    """
    if not isinstance(failed_attrs, dict):
        return None
    dispatch_error = failed_attrs.get("dispatch_error")
    if not isinstance(dispatch_error, dict):
        return None
    module = dispatch_error.get("Module")
    if not isinstance(module, dict):
        return None
    pallet_index = module.get("index")
    error_index = _decode_dispatch_error_index(module.get("error"))
    if pallet_index is None or error_index is None:
        return None
    return parse_chain_error_code(f"{int(pallet_index)}_{int(error_index)}", context)


def _event_value(event_like: Any) -> dict[str, Any] | None:
    value = getattr(event_like, "value", event_like)
    normalized = _normalize_event_record_item(value)
    if normalized is not None:
        return normalized
    if isinstance(value, dict):
        return value
    return None


def _events_from_system_events_query(query_obj: Any) -> list[dict[str, Any]]:
    raw_value = getattr(query_obj, "value", None)
    events = _normalize_system_events_value(raw_value)
    if events:
        return events

    elements = getattr(query_obj, "elements", None)
    if isinstance(elements, list):
        collected: list[dict[str, Any]] = []
        for item in elements:
            value = getattr(item, "value", item)
            normalized = _normalize_event_record_item(value)
            if normalized is not None:
                collected.append(normalized)
        return collected
    return []


def _events_from_system_events_rpc(*, substrate: Any, block_hash: str) -> list[dict[str, Any]]:
    storage_key = substrate.create_storage_key("System", "Events").to_hex()
    if not storage_key:
        raise RuntimeError("empty storage key for System.Events")

    method = _pick_system_events_storage_method(substrate)
    response = substrate.rpc_request(method, [storage_key, block_hash])
    if "error" in response:
        raise RuntimeError(str(response["error"]))

    raw_hex = response.get("result")
    if not raw_hex:
        return []
    return _decode_system_events_offline(
        substrate=substrate,
        block_hash=block_hash,
        raw_hex=raw_hex,
    )


def _events_from_system_events_query_storage_at(*, substrate: Any, block_hash: str) -> list[dict[str, Any]]:
    storage_key = substrate.create_storage_key("System", "Events").to_hex()
    if not storage_key:
        raise RuntimeError("empty storage key for System.Events")

    response = substrate.rpc_request("state_queryStorageAt", [[storage_key], block_hash])
    if "error" in response:
        raise RuntimeError(str(response["error"]))
    result = response.get("result")
    if not isinstance(result, list):
        return []

    target_key = storage_key.lower()
    for item in result:
        if not isinstance(item, dict):
            continue
        changes = item.get("changes")
        if not isinstance(changes, list):
            continue
        for pair in changes:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            key, value = pair
            if not isinstance(key, str) or key.lower() != target_key:
                continue
            if not value:
                return []
            if not isinstance(value, str):
                continue
            return _decode_system_events_offline(
                substrate=substrate,
                block_hash=block_hash,
                raw_hex=value,
            )
    return []


def _events_from_system_events_map(*, substrate: Any, block_hash: str) -> list[dict[str, Any]]:
    number = _block_number(substrate=substrate, block_hash=block_hash)
    if number is None:
        raise RuntimeError("unable to resolve block number")
    _native_debug(f"_events_from_system_events_map:block_hash={block_hash} block_number={number}")

    thread_key = _system_threads_storage_key_hex(block_number=number)
    thread_raw = _rpc_storage_raw_hex(substrate=substrate, key_hex=thread_key, block_hash=block_hash)

    # Default thread=0 when storage is absent/empty.
    thread_count = 0
    if isinstance(thread_raw, str) and thread_raw:
        thread_count = _decode_thread_count(substrate=substrate, block_hash=block_hash, raw_hex=thread_raw)
    _native_debug(
        "_events_from_system_events_map:threads "
        f"thread_key={thread_key[:18]}... thread_raw={'yes' if bool(thread_raw) else 'no'} thread_count={thread_count}"
    )

    batch_raw_values: list[str] = []
    for thread_id in range(max(0, int(thread_count)) + 1):
        ev_key = _system_events_map_storage_key_hex(block_number=number, thread_id=thread_id)
        ev_raw = _rpc_storage_raw_hex(substrate=substrate, key_hex=ev_key, block_hash=block_hash)
        if not isinstance(ev_raw, str) or not ev_raw:
            _native_debug(f"_events_from_system_events_map:thread={thread_id} no_raw")
            continue
        _native_debug(
            "_events_from_system_events_map:thread "
            f"id={thread_id} raw_len={len(ev_raw)}"
        )
        batch_raw_values.append(ev_raw)
    if not batch_raw_values:
        _native_debug("_events_from_system_events_map:collected_len=0")
        return []
    combined_raw = _combine_events_map_batches(
        substrate=substrate,
        block_hash=block_hash,
        raw_hex_values=batch_raw_values,
    )
    collected = _decode_event_record_vec(
        substrate=substrate,
        block_hash=block_hash,
        data=combined_raw,
    )
    _native_debug(f"_events_from_system_events_map:collected_len={len(collected)}")
    return collected


def _rpc_storage_raw_hex(*, substrate: Any, key_hex: str, block_hash: str) -> str | None:
    method = _pick_system_events_storage_method(substrate)
    response = substrate.rpc_request(method, [key_hex, block_hash])
    if not isinstance(response, dict):
        return None
    if "error" in response:
        raise RuntimeError(str(response["error"]))
    value = response.get("result")
    _native_debug(
        "_rpc_storage_raw_hex "
        f"method={method} key={key_hex[:18]}... block_hash={str(block_hash)[:18]}... "
        f"has_value={'yes' if isinstance(value, str) and bool(value) else 'no'}"
    )
    if isinstance(value, str):
        return value
    return None


def _system_threads_storage_key_hex(*, block_number: int) -> str:
    from substrateinterface.utils import hasher

    key = bytes(hasher.xxh128(b"System")) + bytes(hasher.xxh128(b"Threads")) + _u64_scale(block_number)
    return "0x" + key.hex()


def _system_events_map_storage_key_hex(*, block_number: int, thread_id: int) -> str:
    from substrateinterface.utils import hasher

    num_enc = _u64_scale(block_number)
    tid_enc = _u8_scale(thread_id)
    key = (
        bytes(hasher.xxh128(b"System"))
        + bytes(hasher.xxh128(b"EventsMap"))
        + bytes(hasher.blake2_128(num_enc))
        + bytes(hasher.blake2_128(tid_enc))
    )
    return "0x" + key.hex()


def _u32_scale(value: int) -> bytes:
    if value < 0 or value > 0xFFFFFFFF:
        raise RuntimeError(f"invalid u32 value: {value}")
    return int(value).to_bytes(4, byteorder="little", signed=False)


def _u64_scale(value: int) -> bytes:
    if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        raise RuntimeError(f"invalid u64 value: {value}")
    return int(value).to_bytes(8, byteorder="little", signed=False)


def _u8_scale(value: int) -> bytes:
    if value < 0 or value > 0xFF:
        raise RuntimeError(f"invalid u8 value: {value}")
    return int(value).to_bytes(1, byteorder="little", signed=False)


def _decode_thread_count(*, substrate: Any, block_hash: str, raw_hex: str) -> int:
    # Storage value is expected to be a SCALE-encoded u8.
    try:
        value = substrate.decode_scale("u8", raw_hex, block_hash=block_hash)
        parsed = _parse_int_like(value)
        if parsed is not None:
            return parsed
    except Exception:
        pass

    raw = _decode_hex_bytes(raw_hex)
    return raw[0] if raw else 0


def _decode_events_map_batch(*, substrate: Any, block_hash: str, raw_hex: str) -> list[dict[str, Any]]:
    batch = _normalize_events_map_batch_bytes(
        substrate=substrate,
        block_hash=block_hash,
        raw_hex=raw_hex,
    )
    if not batch:
        return []
    return _decode_event_record_vec(substrate=substrate, block_hash=block_hash, data=batch)


def _combine_events_map_batches(*, substrate: Any, block_hash: str, raw_hex_values: list[str]) -> bytes:
    total_num_events = 0
    combined_payload = bytearray()
    for raw_hex in raw_hex_values:
        batch = _normalize_events_map_batch_bytes(
            substrate=substrate,
            block_hash=block_hash,
            raw_hex=raw_hex,
        )
        if not batch:
            continue
        num_events, payload_offset = _decode_compact_u32(batch, 0)
        total_num_events += num_events
        combined_payload.extend(batch[payload_offset:])
    return _encode_compact_u32(total_num_events) + bytes(combined_payload)


def _normalize_events_map_batch_bytes(*, substrate: Any, block_hash: str, raw_hex: str) -> bytes:
    raw = _decode_hex_bytes(raw_hex)
    if not raw:
        return b""

    # First treat storage as direct event-batch bytes.
    try:
        direct = _decode_event_record_vec(substrate=substrate, block_hash=block_hash, data=raw)
        if direct:
            return raw
    except Exception:
        pass

    # Some runtimes wrap the batch as SCALE Vec<u8>; unwrap and validate that shape.
    try:
        inner_len, inner_off = _decode_compact_u32(raw, 0)
        end = inner_off + inner_len
        if inner_off > 0 and end == len(raw):
            inner = raw[inner_off:end]
            unwrapped = _decode_event_record_vec(
                substrate=substrate,
                block_hash=block_hash,
                data=inner,
            )
            if unwrapped:
                return inner
    except Exception:
        pass
    return b""


def _encode_compact_u32(value: int) -> bytes:
    if value < 0:
        raise RuntimeError(f"invalid compact u32 value: {value}")
    if value < 1 << 6:
        return bytes([(value << 2) | 0b00])
    if value < 1 << 14:
        encoded = (value << 2) | 0b01
        return encoded.to_bytes(2, byteorder="little", signed=False)
    if value < 1 << 30:
        encoded = (value << 2) | 0b10
        return encoded.to_bytes(4, byteorder="little", signed=False)
    raw = int(value).to_bytes((value.bit_length() + 7) // 8, byteorder="little", signed=False)
    if len(raw) < 4:
        raw = raw + (b"\x00" * (4 - len(raw)))
    if len(raw) > 67:
        raise RuntimeError(f"compact u32 too large: {value}")
    prefix = bytes([((len(raw) - 4) << 2) | 0b11])
    return prefix + raw


def _decode_event_record_vec(*, substrate: Any, block_hash: str, data: bytes) -> list[dict[str, Any]]:
    raw_hex = "0x" + data.hex()
    candidates = (
        "Vec<EventRecord>",
        "Vec<EventRecord<T::Hash>>",
        "Vec<frame_system::EventRecord<RuntimeEvent, H256>>",
    )
    last_err: Exception | None = None
    for type_string in candidates:
        try:
            decoded = substrate.decode_scale(type_string, raw_hex, block_hash=block_hash)
            events = _normalize_system_events_value(decoded)
            if events:
                return events
        except Exception as exc:
            last_err = exc
            continue

    # Last-resort iterative decode if Vec<EventRecord> aliases are unavailable.
    try:
        from scalecodec.base import ScaleBytes
    except Exception as exc:
        raise RuntimeError(
            "Missing dependency for SCALE decode. Install with 'pip install scalecodec'."
        ) from exc

    try:
        count, offset = _decode_compact_u32(data, 0)
        collected: list[dict[str, Any]] = []
        for _ in range(count):
            obj = substrate.runtime_config.create_scale_object(
                type_string="EventRecord",
                data=ScaleBytes("0x" + data[offset:].hex()),
                metadata=substrate.metadata,
            )
            obj.decode(check_remaining=False)
            consumed = getattr(getattr(obj, "data", None), "offset", None)
            if not isinstance(consumed, int) or consumed <= 0:
                break
            normalized = _normalize_event_record_item(getattr(obj, "value", None))
            if normalized is not None:
                collected.append(normalized)
            offset += consumed
        if collected:
            return collected
    except Exception as exc:
        last_err = exc

    if last_err is not None:
        raise RuntimeError(f"decode event-record vec failed: {last_err}")
    return []


def _decode_compact_u32(data: bytes, offset: int = 0) -> tuple[int, int]:
    if offset < 0 or offset >= len(data):
        raise RuntimeError("compact decode out of range")
    first = data[offset]
    mode = first & 0b11
    if mode == 0:
        return first >> 2, offset + 1
    if mode == 1:
        if offset + 1 >= len(data):
            raise RuntimeError("compact decode requires 2 bytes")
        val = int.from_bytes(data[offset:offset + 2], "little")
        return val >> 2, offset + 2
    if mode == 2:
        if offset + 3 >= len(data):
            raise RuntimeError("compact decode requires 4 bytes")
        val = int.from_bytes(data[offset:offset + 4], "little")
        return val >> 2, offset + 4
    byte_len = (first >> 2) + 4
    start = offset + 1
    end = start + byte_len
    if end > len(data):
        raise RuntimeError("compact decode big-integer out of range")
    val = int.from_bytes(data[start:end], "little")
    if val > 0xFFFFFFFF:
        raise RuntimeError(f"compact value does not fit u32: {val}")
    return val, end


def _decode_system_events_raw(*, substrate: Any, block_hash: str, raw_hex: str) -> list[dict[str, Any]]:
    if not isinstance(raw_hex, str) or not raw_hex:
        return []

    try:
        from scalecodec.base import ScaleBytes
    except Exception as exc:
        raise ImportError(
            "Missing dependency for SCALE decode. Install with 'pip install scalecodec'."
        ) from exc

    substrate.init_runtime(block_hash=block_hash)
    metadata_pallet = substrate.metadata.get_metadata_pallet("System")
    if not metadata_pallet:
        raise RuntimeError("metadata pallet System not found")
    storage_item = metadata_pallet.get_storage_function("Events")
    if not storage_item:
        raise RuntimeError("metadata storage function System.Events not found")
    value_scale_type = storage_item.get_value_type_string()
    if not value_scale_type:
        raise RuntimeError("unable to resolve value type for System.Events")

    scale_obj = substrate.runtime_config.create_scale_object(
        type_string=value_scale_type,
        data=ScaleBytes(raw_hex),
        metadata=substrate.metadata,
    )
    scale_obj.decode(check_remaining=substrate.config.get("strict_scale_decode"))

    events = _normalize_system_events_offline(getattr(scale_obj, "value", None))
    if events:
        return events

    elements = getattr(scale_obj, "elements", None)
    if isinstance(elements, list):
        collected: list[dict[str, Any]] = []
        for item in elements:
            value = getattr(item, "value", item)
            normalized = _normalize_event_record_item(value)
            if normalized is not None:
                collected.append(normalized)
        return collected
    return []


def _decode_system_events_offline(
    *,
    substrate: Any,
    raw_hex: str,
    block_hash: str | None = None,
) -> list[dict[str, Any]]:
    """Decode raw System.Events storage using only a frozen runtime snapshot."""
    resolved_block_hash = block_hash or getattr(substrate, "block_hash", None)
    if not isinstance(resolved_block_hash, str) or not resolved_block_hash:
        raise RuntimeError("frozen substrate snapshot is missing block_hash")
    return _decode_system_events_raw(
        substrate=substrate,
        block_hash=resolved_block_hash,
        raw_hex=raw_hex,
    )


def _decode_events_map_offline(
    *,
    substrate: Any,
    raw_hex_values: list[str],
    block_hash: str | None = None,
) -> list[dict[str, Any]]:
    """Decode System.EventsMap thread batches using only a frozen runtime snapshot.

    The multi-threaded runtime moves System.Events into System.EventsMap(number,
    thread) at block finalize, so post-block events must be read per-thread and
    combined. raw_hex_values are the raw storage values, one per thread.
    """
    resolved_block_hash = block_hash or getattr(substrate, "block_hash", None)
    if not isinstance(resolved_block_hash, str) or not resolved_block_hash:
        raise RuntimeError("frozen substrate snapshot is missing block_hash")
    if not raw_hex_values:
        return []
    combined = _combine_events_map_batches(
        substrate=substrate,
        block_hash=resolved_block_hash,
        raw_hex_values=raw_hex_values,
    )
    return _decode_event_record_vec(
        substrate=substrate,
        block_hash=resolved_block_hash,
        data=combined,
    )


def _rpc_system_events_raw_diag(*, substrate: Any, block_hash: str) -> str:
    storage_key = substrate.create_storage_key("System", "Events").to_hex()
    if not storage_key:
        return "rpc_system_events_raw=empty_storage_key"

    method = _pick_system_events_storage_method(substrate)
    response = substrate.rpc_request(method, [storage_key, block_hash])
    if not isinstance(response, dict):
        return f"rpc_system_events_raw=invalid_response_type:{type(response).__name__}"
    if "error" in response:
        return f"rpc_system_events_raw_error={response['error']}"

    raw_hex = response.get("result")
    if raw_hex is None:
        return f"rpc_system_events_raw={method}:None"
    if not isinstance(raw_hex, str):
        return f"rpc_system_events_raw={method}:type={type(raw_hex).__name__}"
    return f"rpc_system_events_raw={method}:len={len(raw_hex)},head={raw_hex[:64]}"


def _pick_system_events_storage_method(substrate: Any) -> str:
    # Match subxt behavior first: it uses state_getStorage(key, at_hash).
    try:
        if substrate.supports_rpc_method("state_getStorage"):
            return "state_getStorage"
    except Exception:
        pass
    try:
        if substrate.supports_rpc_method("state_getStorageAt"):
            return "state_getStorageAt"
    except Exception:
        pass
    # Keep a deterministic fallback even if method probing fails.
    return "state_getStorage"


def _normalize_system_events_value(raw_value: Any) -> list[dict[str, Any]]:
    raw_value = _unwrap_scale_value(raw_value)
    if isinstance(raw_value, dict):
        for key in ("events", "records", "value"):
            nested = raw_value.get(key)
            nested = _unwrap_scale_value(nested)
            if isinstance(nested, (list, tuple)):
                raw_value = list(nested)
                break
        else:
            # Sometimes a single event record is wrapped in dict form.
            single = _normalize_event_record_item(raw_value)
            return [single] if single is not None else []
    if not isinstance(raw_value, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    for item in list(raw_value):
        normalized = _normalize_event_record_item(item)
        if normalized is not None:
            out.append(normalized)
    return out


def _normalize_system_events_offline(raw_value: Any) -> list[dict[str, Any]]:
    """Normalize decoded System.Events records without transport access."""
    return _normalize_system_events_value(raw_value)


def _normalize_event_record_item(item: Any) -> dict[str, Any] | None:
    item = _unwrap_scale_value(item)
    if isinstance(item, (list, tuple)):
        seq = list(item)
        if len(seq) >= 2:
            item = {"phase": seq[0], "event": seq[1]}
            if len(seq) >= 3:
                item["topics"] = seq[2]
        elif len(seq) == 1:
            item = _unwrap_scale_value(seq[0])
        else:
            return None

    if not isinstance(item, dict):
        return None

    # Already in expected shape.
    if "module_id" in item and "event_id" in item:
        return item

    event_obj = _unwrap_scale_value(item.get("event"))
    phase_obj = item.get("phase")
    extrinsic_idx = _event_extrinsic_idx({"phase": phase_obj, "extrinsic_idx": item.get("extrinsic_idx")})

    module_id: Any = None
    event_id: Any = None
    attributes: Any = None

    if isinstance(event_obj, dict):
        event_obj = {k: _unwrap_scale_value(v) for k, v in event_obj.items()}
        module_id = (
            event_obj.get("module_id")
            or event_obj.get("pallet")
            or event_obj.get("pallet_name")
            or event_obj.get("module")
        )
        event_id = (
            event_obj.get("event_id")
            or event_obj.get("variant")
            or event_obj.get("variant_name")
            or event_obj.get("event")
            or event_obj.get("name")
            or event_obj.get("event_name")
        )
        attributes = (
            event_obj.get("attributes")
            if "attributes" in event_obj
            else event_obj.get("data")
        )
        if attributes is None and "fields" in event_obj:
            attributes = event_obj.get("fields")
        if attributes is None and "args" in event_obj:
            attributes = event_obj.get("args")
        if attributes is None and "values" in event_obj:
            attributes = event_obj.get("values")
    elif isinstance(event_obj, str):
        # e.g. "SpotMarket.OrderCancelled" or "SpotMarket::OrderCancelled"
        text = event_obj.strip()
        if "::" in text:
            module_id, event_id = text.split("::", 1)
        elif "." in text:
            module_id, event_id = text.split(".", 1)
        attributes = item.get("attributes")

    if not (module_id and event_id):
        return None

    normalized: dict[str, Any] = {
        "module_id": module_id,
        "event_id": event_id,
        "attributes": attributes,
    }
    if phase_obj is not None:
        normalized["phase"] = phase_obj
    if extrinsic_idx is not None:
        normalized["extrinsic_idx"] = extrinsic_idx
    return normalized


def _unwrap_scale_value(value: Any) -> Any:
    # Unwrap substrate/scalecodec wrappers exposing `.value`.
    seen_ids: set[int] = set()
    cur = value
    while hasattr(cur, "value"):
        ident = id(cur)
        if ident in seen_ids:
            break
        seen_ids.add(ident)
        try:
            nxt = getattr(cur, "value")
        except Exception:
            break
        if nxt is cur:
            break
        cur = nxt
    return cur


def _container_len(value: Any) -> int | None:
    val = _unwrap_scale_value(value)
    if isinstance(val, (list, tuple, dict, set)):
        return len(val)
    return None


def _event_overview(events: list[Any], limit: int = 8) -> list[str]:
    out: list[str] = []
    for ev in events[:limit]:
        value = _event_value(ev)
        if value is not None:
            module_id = value.get("module_id")
            event_id = value.get("event_id")
            if module_id and event_id:
                out.append(f"{module_id}.{event_id}")
                continue
        out.append(str(value))
    if len(events) > limit:
        out.append(f"...(+{len(events) - limit})")
    return out


def _event_overview_from_values(values: list[Any], limit: int = 8) -> list[str]:
    out: list[str] = []
    for value in values[:limit]:
        if isinstance(value, dict):
            module_id = value.get("module_id")
            event_id = value.get("event_id")
            if module_id and event_id:
                out.append(f"{module_id}.{event_id}")
                continue
        out.append(str(value))
    if len(values) > limit:
        out.append(f"...(+{len(values) - limit})")
    return out


def _decode_signed_rlp_bytes_to_transaction_v2(raw_signed_tx: bytes) -> dict[str, Any]:
    if not raw_signed_tx:
        raise RuntimeError("empty signed tx")

    try:
        from eth_account._utils.legacy_transactions import Transaction
        from eth_account.typed_transactions import TypedTransaction
        from hexbytes import HexBytes
    except Exception as exc:
        raise ImportError(
            "Missing tx decode dependencies. Install with 'pip install eth-account'."
        ) from exc

    raw = HexBytes(raw_signed_tx)
    first = raw[0]
    if first in (0x01, 0x02):
        typed = TypedTransaction.from_bytes(raw)
        tx = typed.as_dict()
        tx_type = int(tx.get("type", first))
        if tx_type == 1:
            return {"EIP2930": _map_eip2930_tx(tx)}
        if tx_type == 2:
            return {"EIP1559": _map_eip1559_tx(tx)}
        raise RuntimeError(f"unsupported typed transaction type: {tx_type}")

    legacy = Transaction.from_bytes(raw)
    return {"Legacy": _map_legacy_tx(legacy)}


def _map_legacy_tx(tx: Any) -> dict[str, Any]:
    signature_v = int(getattr(tx, "v"))
    return {
        "nonce": int(getattr(tx, "nonce")),
        "gas_price": int(getattr(tx, "gasPrice")),
        "gas_limit": int(getattr(tx, "gas")),
        "action": _map_action(getattr(tx, "to")),
        "value": int(getattr(tx, "value")),
        "input": _bytes_to_hex(getattr(tx, "data")),
        "signature": {
            "v": _legacy_recovery_id(signature_v),
            "r": _u256_to_h256_hex(int(getattr(tx, "r"))),
            "s": _u256_to_h256_hex(int(getattr(tx, "s"))),
        },
    }


def _map_eip2930_tx(tx: dict[str, Any]) -> dict[str, Any]:
    return {
        "chain_id": int(tx.get("chainId", 0)),
        "nonce": int(tx.get("nonce", 0)),
        "gas_price": int(tx.get("gasPrice", 0)),
        "gas_limit": int(tx.get("gas", 0)),
        "action": _map_action(tx.get("to")),
        "value": int(tx.get("value", 0)),
        "input": _bytes_to_hex(tx.get("data")),
        "access_list": _map_access_list(tx.get("accessList", ())),
        "odd_y_parity": bool(int(tx.get("v", 0))),
        "r": _u256_to_h256_hex(int(tx.get("r", 0))),
        "s": _u256_to_h256_hex(int(tx.get("s", 0))),
    }


def _map_eip1559_tx(tx: dict[str, Any]) -> dict[str, Any]:
    return {
        "chain_id": int(tx.get("chainId", 0)),
        "nonce": int(tx.get("nonce", 0)),
        "max_priority_fee_per_gas": int(tx.get("maxPriorityFeePerGas", 0)),
        "max_fee_per_gas": int(tx.get("maxFeePerGas", 0)),
        "gas_limit": int(tx.get("gas", 0)),
        "action": _map_action(tx.get("to")),
        "value": int(tx.get("value", 0)),
        "input": _bytes_to_hex(tx.get("data")),
        "access_list": _map_access_list(tx.get("accessList", ())),
        "odd_y_parity": bool(int(tx.get("v", 0))),
        "r": _u256_to_h256_hex(int(tx.get("r", 0))),
        "s": _u256_to_h256_hex(int(tx.get("s", 0))),
    }


def _map_access_list(raw_access_list: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if raw_access_list in (None, (), []):
        return items

    for item in list(raw_access_list):
        address: Any = None
        storage_keys: Any = None
        if isinstance(item, dict):
            address = item.get("address")
            storage_keys = item.get("storageKeys", item.get("storage_keys"))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            address, storage_keys = item
        else:
            raise RuntimeError(f"invalid access list item: {item}")

        keys = storage_keys or []
        items.append(
            {
                "address": _bytes_to_h160_hex(address),
                "storage_keys": [_bytes_to_h256_hex(k) for k in list(keys)],
            }
        )
    return items


def _map_action(to_value: Any) -> dict[str, Any]:
    if to_value in (None, b"", "0x", "0X"):
        return {"Create": None}
    return {"Call": _bytes_to_h160_hex(to_value)}


def _legacy_recovery_id(v: int) -> int:
    if v in (27, 28):
        return v - 27
    if v >= 35:
        return (v - 35) % 2
    return v & 1


def _u256_to_h256_hex(value: int) -> str:
    if value < 0 or value >= (1 << 256):
        raise RuntimeError(f"invalid U256 value: {value}")
    return "0x" + value.to_bytes(32, "big").hex()


def _bytes_to_hex(value: Any) -> str:
    if value in (None, "", "0x", "0X"):
        return "0x"
    if isinstance(value, str):
        return value if value.startswith("0x") else "0x" + value
    return "0x" + bytes(value).hex()


def _bytes_to_h160_hex(value: Any) -> str:
    raw = bytes(value) if not isinstance(value, str) else bytes.fromhex(value[2:] if value.startswith("0x") else value)
    if len(raw) != 20:
        raise RuntimeError(f"invalid H160 bytes length: {len(raw)}")
    return "0x" + raw.hex()


def _bytes_to_h256_hex(value: Any) -> str:
    raw = bytes(value) if not isinstance(value, str) else bytes.fromhex(value[2:] if value.startswith("0x") else value)
    if len(raw) != 32:
        raise RuntimeError(f"invalid H256 bytes length: {len(raw)}")
    return "0x" + raw.hex()


def _normalize_h160_hex(value: str, *, field: str) -> str:
    _, _, to_canonical_address, _ = _get_signing_libs()
    raw = value.strip()
    if not raw.startswith("0x"):
        raw = "0x" + raw
    try:
        return "0x" + to_canonical_address(raw).hex()
    except Exception as exc:
        raise RuntimeError(f"invalid {field}: {exc}") from exc


def _normalize_h256_hex(value: str) -> str:
    raw = value.strip()
    if raw.startswith(("0x", "0X")):
        raw = raw[2:]
    if len(raw) != 64:
        raise RuntimeError(f"invalid H256 hex length: {len(raw)}")
    try:
        bytes.fromhex(raw)
    except Exception as exc:
        raise RuntimeError(f"invalid H256 hex: {exc}") from exc
    return "0x" + raw.lower()


def _eth_tx_hash(raw_signed_tx: bytes) -> str:
    _, keccak, _, _ = _get_signing_libs()
    return "0x" + keccak(raw_signed_tx).hex()


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(v) for v in value]
    if hasattr(value, "value"):
        return _json_ready(getattr(value, "value"))
    try:
        return str(value)
    except Exception:
        return repr(value)
