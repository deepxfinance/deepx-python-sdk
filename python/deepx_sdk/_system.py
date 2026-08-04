from __future__ import annotations

from ._abi import decode_abi, encode_call, normalize_address
from ._evm import evm_call
from ._types import SystemAccountInfo


def system_account(
    *,
    evm_rpc_url: str,
    precompile_address: str,
    address: str,
) -> SystemAccountInfo:
    account = normalize_address(address)
    v2_error: Exception | None = None

    # Prefer V2 when available because it includes `is_exist`.
    try:
        data = encode_call("systemAccountV2(address)", ["address"], [account])
        raw = evm_call(evm_rpc_url, precompile_address, data)
        (info,) = decode_abi([_SYSTEM_ACCOUNT_TUPLE_V2], raw)
        return SystemAccountInfo(
            nonce=int(info[0]),
            update=int(info[1]),
            time_nonce=[int(v) for v in info[2]],
            quota=int(info[3]),
            is_exist=bool(info[4]),
        )
    except Exception as exc:  # pragma: no cover - fallback path depends on runtime precompile.
        v2_error = exc

    # Fallback for runtimes that expose only the legacy 4-field schema.
    try:
        data = encode_call("systemAccount(address)", ["address"], [account])
        raw = evm_call(evm_rpc_url, precompile_address, data)
        (info,) = decode_abi([_SYSTEM_ACCOUNT_TUPLE], raw)
    except Exception as exc:
        if v2_error is None:  # pragma: no cover - defensive; V1 fallback is reached only after V2 failed.
            raise
        raise RuntimeError(
            f"systemAccount decode failed (V2: {v2_error}; V1: {exc})"
        ) from exc

    nonce = int(info[0])
    update = int(info[1])
    time_nonce = [int(v) for v in info[2]]
    quota = int(info[3])
    return SystemAccountInfo(
        nonce=nonce,
        update=update,
        time_nonce=time_nonce,
        quota=quota,
        is_exist=bool(nonce or update or time_nonce or quota),
    )




_SYSTEM_ACCOUNT_TUPLE = "(uint64,uint64,uint64[],uint32)"
_SYSTEM_ACCOUNT_TUPLE_V2 = "(uint64,uint64,uint64[],uint32,bool)"
