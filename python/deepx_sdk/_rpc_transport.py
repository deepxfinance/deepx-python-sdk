from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import threading
from typing import Iterator, Optional
from urllib.parse import urlsplit, urlunsplit

DEFAULT_USER_AGENT = "deepx-python-sdk/0.1.0"

# Default per-request timeout for EVM JSON-RPC HTTP calls (eth_call,
# eth_estimateGas, eth_chainId, ...). Bounds urlopen so a dead/flaky RPC
# endpoint fails fast instead of blocking forever. Overridable per call site
# via use_evm_rpc_config(timeout_s=...).
DEFAULT_RPC_TIMEOUT_S = 30.0


@dataclass
class RpcTransportConfig:
    user_agent: str = DEFAULT_USER_AGENT
    headers: dict[str, str] | None = None
    timeout_s: float | None = DEFAULT_RPC_TIMEOUT_S
    endpoint_pool: "RpcEndpointPool | None" = None


class RpcEndpointPool:
    def __init__(self, endpoints: tuple[str, ...]) -> None:
        if not endpoints:
            raise ValueError("RPC endpoint pool must not be empty")
        self._endpoints = endpoints
        self._active_index = 0
        self._lock = threading.Lock()

    @property
    def active(self) -> str:
        with self._lock:
            return self._endpoints[self._active_index]

    @property
    def active_display(self) -> str:
        return _safe_http_endpoint(self.active)

    def display(self, endpoint: str) -> str:
        return _safe_http_endpoint(endpoint)

    def ordered(self) -> tuple[str, ...]:
        with self._lock:
            index = self._active_index
            return self._endpoints[index:] + self._endpoints[:index]

    def mark_success(self, endpoint: str) -> None:
        with self._lock:
            try:
                self._active_index = self._endpoints.index(endpoint)
            except ValueError:
                pass


_CURRENT_EVM_RPC_CONFIG: ContextVar[RpcTransportConfig | None] = ContextVar(
    "deepx_sdk_current_evm_rpc_config",
    default=None,
)
_CURRENT_SUBSTRATE_WS_POOL: ContextVar[RpcEndpointPool | None] = ContextVar(
    "deepx_sdk_current_substrate_ws_pool",
    default=None,
)


def _normalize_headers(headers: Optional[dict[str, str]]) -> dict[str, str] | None:
    if not headers:
        return None

    normalized_headers: dict[str, str] = {}
    for key, value in headers.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        normalized_headers[normalized_key] = str(value)
    return normalized_headers or None


@contextmanager
def use_evm_rpc_config(
    *,
    user_agent: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
    timeout_s: float | None = None,
    endpoint_pool: RpcEndpointPool | None = None,
) -> Iterator[None]:
    resolved_user_agent = str(user_agent).strip() if user_agent is not None else DEFAULT_USER_AGENT
    if not resolved_user_agent:
        resolved_user_agent = DEFAULT_USER_AGENT

    token = _CURRENT_EVM_RPC_CONFIG.set(
        RpcTransportConfig(
            user_agent=resolved_user_agent,
            headers=_normalize_headers(headers),
            timeout_s=timeout_s,
            endpoint_pool=endpoint_pool,
        )
    )
    try:
        yield
    finally:
        _CURRENT_EVM_RPC_CONFIG.reset(token)


def rpc_request_options(
    *,
    default_user_agent: str = DEFAULT_USER_AGENT,
) -> tuple[dict[str, str], float | None]:
    config = _CURRENT_EVM_RPC_CONFIG.get()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": default_user_agent,
    }
    if config is None:
        return headers, DEFAULT_RPC_TIMEOUT_S

    headers["User-Agent"] = config.user_agent or default_user_agent
    if config.headers:
        headers.update(config.headers)
    timeout_s = config.timeout_s if config.timeout_s is not None else DEFAULT_RPC_TIMEOUT_S
    return headers, timeout_s


def rpc_request_endpoints(
    default_url: str,
) -> tuple[tuple[str, ...], RpcEndpointPool | None]:
    config = _CURRENT_EVM_RPC_CONFIG.get()
    pool = config.endpoint_pool if config is not None else None
    if pool is None:
        return (default_url,), None
    return pool.ordered(), pool


@contextmanager
def use_substrate_ws_config(
    *,
    endpoint_pool: RpcEndpointPool | None = None,
) -> Iterator[None]:
    token = _CURRENT_SUBSTRATE_WS_POOL.set(endpoint_pool)
    try:
        yield
    finally:
        _CURRENT_SUBSTRATE_WS_POOL.reset(token)


def substrate_ws_request_endpoints(
    default_url: str,
) -> tuple[tuple[str, ...], RpcEndpointPool | None]:
    pool = _CURRENT_SUBSTRATE_WS_POOL.get()
    if pool is None:
        return (default_url,), None
    return pool.ordered(), pool


def _safe_http_endpoint(url: str) -> str:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if not parsed.scheme or hostname is None:
            return "<configured endpoint>"
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))
    except (TypeError, ValueError):
        return "<configured endpoint>"
