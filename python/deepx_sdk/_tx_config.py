from __future__ import annotations

from inspect import signature
from typing import Any

from ._types import TxConfig


_TX_CONFIG_DIRECT_FIELDS = (
    "chain_id",
    "gas_limit",
    "max_fee_per_gas",
    "max_priority_fee_per_gas",
    "use_legacy",
    "wait_for_finalized",
    "timeout_ms",
)
_TX_CONFIG_SUPPORTED_FIELDS = frozenset((*_TX_CONFIG_DIRECT_FIELDS, "nonce_ms", "nonce"))


def _has_tx_kwarg(kwargs: dict[str, Any], name: str) -> bool:
    return name in kwargs and kwargs[name] is not None


def merge_tx_config_kwargs(method: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    tx_config = kwargs.pop("tx_config", None)
    if tx_config is None:
        return kwargs
    if not isinstance(tx_config, TxConfig):
        raise TypeError("tx_config must be a TxConfig instance")

    accepted = set(signature(method).parameters)
    if not accepted.intersection(_TX_CONFIG_SUPPORTED_FIELDS):
        raise TypeError(f"{method.__name__}() does not accept tx_config")

    for field_name in _TX_CONFIG_DIRECT_FIELDS:
        if field_name in accepted and not _has_tx_kwarg(kwargs, field_name):
            value = getattr(tx_config, field_name)
            if value is not None:
                kwargs[field_name] = value

    if "nonce_ms" in accepted and not _has_tx_kwarg(kwargs, "nonce_ms"):
        nonce_ms = tx_config.nonce_ms if tx_config.nonce_ms is not None else tx_config.nonce
        if nonce_ms is not None:
            kwargs["nonce_ms"] = nonce_ms

    if "nonce" in accepted and not _has_tx_kwarg(kwargs, "nonce"):
        nonce = tx_config.nonce if tx_config.nonce is not None else tx_config.nonce_ms
        if nonce is not None:
            kwargs["nonce"] = nonce

    return kwargs
