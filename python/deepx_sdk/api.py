from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import Any, Optional, TYPE_CHECKING

from ._errors import RESTError, RPCError, parse_api_error_code
from ._network import (
    network_config,
    normalize_net,
    resolve_ordered_endpoints,
)
from ._rpc_transport import RpcEndpointPool

if TYPE_CHECKING:
    from .api_v1 import V1Client


def _normalize_optional_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


@dataclass
class ApiClient:
    base_url: Optional[str] = None
    net: str = "devnet"
    timeout: int = 30
    user_agent: str = "deepx-python-sdk/0.1.0"
    ws_base_url: Optional[str] = None
    substrate_ws: Optional[str] = None
    evm_rpc_url: Optional[str] = None
    private_key: Optional[str] = None
    perp_precompile_address: Optional[str] = None
    spot_precompile_address: Optional[str] = None
    lending_precompile_address: Optional[str] = None
    subaccount_precompile_address: Optional[str] = None
    system_precompile_address: Optional[str] = None
    subaccount: Optional[str] = None
    chain_id: Optional[int] = None
    gas_limit: Optional[int] = None
    max_fee_per_gas: Optional[int] = None
    max_priority_fee_per_gas: Optional[int] = None
    use_legacy: bool = False
    nonce_ms: Optional[int] = None
    evm_rpc_user_agent: str = "deepx-python-sdk/0.1.0"
    evm_rpc_headers: Optional[dict[str, str]] = None
    evm_rpc_timeout: Optional[float] = None
    base_urls: Sequence[str] | None = None
    evm_rpc_endpoints: Sequence[str] | None = None
    v1: "V1Client" = field(init=False, repr=False)

    def __post_init__(self) -> None:
        from .api_v1 import V1Client

        resolved_net = normalize_net(self.net)
        config = network_config(resolved_net)
        self.net = resolved_net

        self.base_urls = resolve_ordered_endpoints(
            self.base_url,
            self.base_urls,
            default=config.api_base_url,
            name="base_urls",
        )
        self.base_url = self.base_urls[0]
        self._api_endpoint_pool = RpcEndpointPool(tuple(self.base_urls))

        if self.ws_base_url is None or str(self.ws_base_url).strip() == "":
            self.ws_base_url = config.ws_base_url
        else:
            self.ws_base_url = str(self.ws_base_url).strip()

        if self.substrate_ws is None or str(self.substrate_ws).strip() == "":
            self.substrate_ws = config.substrate_ws
        else:
            self.substrate_ws = str(self.substrate_ws).strip()

        self.evm_rpc_endpoints = resolve_ordered_endpoints(
            self.evm_rpc_url,
            self.evm_rpc_endpoints,
            default=config.evm_rpc_url,
            name="evm_rpc_endpoints",
        )
        self.evm_rpc_url = self.evm_rpc_endpoints[0]
        self._evm_rpc_pool = RpcEndpointPool(tuple(self.evm_rpc_endpoints))

        self.perp_precompile_address = _normalize_optional_str(self.perp_precompile_address)
        self.spot_precompile_address = _normalize_optional_str(self.spot_precompile_address)
        self.lending_precompile_address = _normalize_optional_str(self.lending_precompile_address)
        self.subaccount_precompile_address = _normalize_optional_str(
            self.subaccount_precompile_address
        )
        self.system_precompile_address = _normalize_optional_str(self.system_precompile_address)

        self.v1 = V1Client(self)

    @property
    def active_api_endpoint(self) -> str:
        return self._api_endpoint_pool.active_display

    @property
    def active_evm_rpc_endpoint(self) -> str:
        return self._evm_rpc_pool.active_display

    def _make_url(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        *,
        base_url: str | None = None,
    ) -> str:
        base = str(base_url or self.base_url).rstrip("/")
        path = path.lstrip("/")
        url = f"{base}/{urllib.parse.quote(path, safe='/:')}"
        if params:
            query = urllib.parse.urlencode(params, doseq=True)
            url = f"{url}?{query}"
        return url

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        method_upper = method.upper()
        # Avoid urllib default UA ("Python-urllib/x.y"), which may be blocked by WAF.
        hdrs = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        if headers:
            hdrs.update(headers)

        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")

        retryable = method_upper in {"GET", "HEAD", "OPTIONS"}
        endpoints = (
            self._api_endpoint_pool.ordered()
            if retryable
            else (self._api_endpoint_pool.active,)
        )
        for index, endpoint in enumerate(endpoints):
            url = self._make_url(path, params, base_url=endpoint)
            req = urllib.request.Request(
                url,
                data=data,
                headers=hdrs,
                method=method_upper,
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                if (
                    retryable
                    and exc.code >= 500
                    and index + 1 < len(endpoints)
                ):
                    exc.close()
                    continue
                raw = exc.read().decode("utf-8", errors="replace")
                payload: Any
                try:
                    payload = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    payload = {}
                if isinstance(payload, dict):
                    message = str(
                        payload.get("message")
                        or payload.get("msg")
                        or payload.get("error")
                        or exc.reason
                    )
                    code = payload.get("code")
                    error_type = payload.get("errorType") or payload.get("error_type")
                    details = payload.get("details")
                    if isinstance(code, int) and not isinstance(code, bool):
                        err = parse_api_error_code(code, message)
                        err.status_code = exc.code
                        err.message = message
                        if error_type:
                            err.error_type = error_type
                        err.details = details
                        raise err from exc
                    raise RESTError(
                        status_code=exc.code,
                        message=message,
                        code=code,
                        error_type=error_type,
                        details=details,
                    ) from exc
                raise RESTError(
                    status_code=exc.code,
                    message=raw or str(exc.reason),
                ) from exc
            except (
                TimeoutError,
                socket.timeout,
                urllib.error.URLError,
                OSError,
            ) as exc:
                if retryable and index + 1 < len(endpoints):
                    continue
                raise RPCError(
                    f"REST request failed: {method_upper} {url}: {exc}"
                ) from exc
            self._api_endpoint_pool.mark_success(endpoint)
            break
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw


class AsyncApiClient(ApiClient):
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        sync_request = super().request
        return await asyncio.to_thread(
            sync_request,
            method,
            path,
            params=params,
            json_body=json_body,
            headers=headers,
        )
