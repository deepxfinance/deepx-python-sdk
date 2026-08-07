from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_NET = "devnet"


@dataclass(frozen=True)
class NetworkConfig:
    net: str
    api_base_url: str
    ws_base_url: str
    substrate_ws: str
    evm_rpc_url: str
    chain_id: int
    bridge_contract: str


_NETWORKS: dict[str, NetworkConfig] = {
    "devnet": NetworkConfig(
        net="devnet",
        api_base_url="https://rest-api-devnet.deepx.fi",
        ws_base_url="wss://ws-api-devnet.deepx.fi",
        substrate_ws="wss://devnet-rpc-new.deepx.fi",
        evm_rpc_url="https://devnet-rpc-new.deepx.fi",
        chain_id=4845,
        bridge_contract="0xa32408eD9f1dFa1e2dc30143F9133Af31E8514ed",
    ),
    "testnet": NetworkConfig(
        net="testnet",
        api_base_url="https://rest-api-testnet.deepx.fi",
        ws_base_url="wss://ws-api-testnet.deepx.fi",
        substrate_ws="wss://rpc-testnet.deepx.fi",
        evm_rpc_url="https://rpc-testnet.deepx.fi",
        chain_id=4846,
        bridge_contract="0x7db17a464c6ca9c1a81a25b4364d4f8e673f0049",
    ),
}


def normalize_net(net: str) -> str:
    resolved = str(net).strip().lower()
    if resolved not in _NETWORKS:
        raise ValueError(f"net must be one of: {allowed_nets()}")
    return resolved


def resolve_net(net: str | None = None) -> str:
    """Blank/None resolves to the default network; `net` is an escape hatch
    for SDK development, not part of the user-facing surface."""
    candidate = "" if net is None else str(net).strip()
    return normalize_net(candidate or DEFAULT_NET)


def allowed_nets() -> str:
    return ", ".join(sorted(_NETWORKS))


def network_config(net: str) -> NetworkConfig:
    return _NETWORKS[normalize_net(net)]


def resolve_substrate_ws_endpoints(
    substrate_ws: str | None,
    endpoints: Sequence[str] | None,
    *,
    default: str,
) -> tuple[str, ...]:
    return resolve_ordered_endpoints(
        substrate_ws,
        endpoints,
        default=default,
        name="substrate_ws_endpoints",
    )


def resolve_ordered_endpoints(
    primary: str | None,
    endpoints: Sequence[str] | None,
    *,
    default: str,
    name: str,
) -> tuple[str, ...]:
    configured = str(primary or "").strip()
    if endpoints is None:
        return (configured or default,)

    resolved: list[str] = []
    for endpoint in endpoints:
        value = str(endpoint).strip()
        if not value:
            raise ValueError(f"{name} must contain non-empty URLs")
        if value not in resolved:
            resolved.append(value)
    if not resolved:
        raise ValueError(f"{name} must not be empty")
    if configured and configured != resolved[0]:
        raise ValueError(
            f"the primary endpoint must match the first {name} entry"
        )
    return tuple(resolved)
