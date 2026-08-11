from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_NET = "testnet"


@dataclass(frozen=True)
class NetworkConfig:
    net: str
    api_base_url: str
    ws_base_url: str
    substrate_ws: str
    evm_rpc_url: str
    chain_id: int
    bridge_contract: str


# mainnet is not live yet; its endpoints read from env until the deployment is
# finalized, so the registry can carry the entry without committing dead URLs.
def _mainnet_config() -> NetworkConfig:
    return NetworkConfig(
        net="mainnet",
        api_base_url=os.environ.get("DEEPX_MAINNET_API_BASE_URL", ""),
        ws_base_url=os.environ.get("DEEPX_MAINNET_WS_BASE_URL", ""),
        substrate_ws=os.environ.get("DEEPX_MAINNET_SUBSTRATE_WS", ""),
        evm_rpc_url=os.environ.get("DEEPX_MAINNET_EVM_RPC_URL", ""),
        chain_id=int(os.environ.get("DEEPX_MAINNET_CHAIN_ID", "0")),
        bridge_contract=os.environ.get("DEEPX_MAINNET_BRIDGE_CONTRACT", ""),
    )


def _build_networks() -> dict[str, NetworkConfig]:
    return {
        "testnet": NetworkConfig(
            net="testnet",
            api_base_url="https://rest-api-testnet.deepx.fi",
            ws_base_url="wss://ws-api-testnet.deepx.fi",
            substrate_ws="wss://rpc-testnet.deepx.fi",
            evm_rpc_url="https://rpc-testnet.deepx.fi",
            chain_id=4846,
            bridge_contract="0x874c408fd66117a2edb953fe68cadccd675e5c2c",
        ),
        "mainnet": _mainnet_config(),
    }


def normalize_net(net: str) -> str:
    resolved = str(net).strip().lower()
    if resolved not in _build_networks():
        raise ValueError(f"net must be one of: {allowed_nets()}")
    return resolved


def allowed_nets() -> str:
    return ", ".join(sorted(_build_networks()))


def network_config(net: str) -> NetworkConfig:
    config = _build_networks()[normalize_net(net)]
    if not config.evm_rpc_url:
        raise ValueError(
            f"network {config.net!r} is not deployed yet "
            "(no endpoints configured)"
        )
    return config


def resolve_net(net: str | None = None) -> str:
    """Blank/None resolves to the default network."""
    candidate = "" if net is None else str(net).strip()
    return normalize_net(candidate or DEFAULT_NET)


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
