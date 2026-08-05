from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from ._native_py import _native_debug
from ._rpc_transport import rpc_request_endpoints, rpc_request_options


def evm_call(evm_rpc_url: str, to: str, data: bytes) -> bytes:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [
            {
                "to": to,
                "data": "0x" + data.hex(),
            },
            "latest",
        ],
    }
    headers, timeout_s = rpc_request_options()
    endpoints, endpoint_pool = rpc_request_endpoints(evm_rpc_url)
    for index, endpoint in enumerate(endpoints):
        _native_debug(
            f"evm_call:start method=eth_call url={endpoint} "
            f"timeout={timeout_s}s to={to}"
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
                "evm_call:http_error method=eth_call "
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
            raise RuntimeError(f"eth_call request failed: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if index + 1 < len(endpoints):
                continue
            _native_debug(
                "evm_call:url_error method=eth_call "
                f"{time.monotonic()-t0:.2f}s {exc}"
            )
            raise RuntimeError(f"eth_call request failed: {exc}") from exc
        except Exception as exc:
            _native_debug(
                "evm_call:error method=eth_call "
                f"{time.monotonic()-t0:.2f}s {type(exc).__name__}: {exc}"
            )
            raise RuntimeError(f"eth_call request failed: {exc}") from exc
        if endpoint_pool is not None:
            endpoint_pool.mark_success(endpoint)
        break
    _native_debug(f"evm_call:ok method=eth_call {time.monotonic()-t0:.2f}s")
    if "error" in body:
        raise RuntimeError(f"eth_call error: {body['error']}")
    result = body.get("result")
    if not isinstance(result, str):
        raise RuntimeError(f"eth_call invalid result: {result}")
    result = result[2:] if result.startswith("0x") else result
    if result == "":
        return b""
    return bytes.fromhex(result)
