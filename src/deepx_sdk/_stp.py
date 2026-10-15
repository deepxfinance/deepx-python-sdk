"""Per-order STP encoding and metadata-based compatibility."""
from __future__ import annotations

from typing import Any


_STP_MODES = {
    "NONE": "None",
    "EXPIRE_MAKER": "ExpireMaker",
    "EXPIRE_TAKER": "ExpireTaker",
    "EXPIRE_BOTH": "ExpireBoth",
}


def normalize_stp_mode(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value.upper() not in _STP_MODES:
        raise ValueError("stp_mode must be NONE, EXPIRE_MAKER, EXPIRE_TAKER, EXPIRE_BOTH, or None")
    return _STP_MODES[value.upper()]


def stp_mode_name(value: int) -> str:
    if value not in range(4):
        raise ValueError(f"invalid on-chain stp_mode: {value}")
    return tuple(_STP_MODES)[value]


def compose_order_call(substrate: Any, **kwargs: Any) -> Any:
    """Keep explicit STP from being silently discarded by a pre-STP codec.

    All order builders include stp_mode, including None for the chain default.
    Pin metadata and composition to the same block, and adapt nested modify /
    batch order parameters without mutating the caller's values.
    """
    params = kwargs["call_params"]

    def has_stp(value: Any) -> bool:
        if isinstance(value, dict):
            return "stp_mode" in value or any(has_stp(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return any(has_stp(v) for v in value)
        return False

    if not has_stp(params) or not callable(getattr(substrate, "get_metadata_call_function", None)):
        return substrate.compose_call(**kwargs)

    substrate.init_runtime(block_hash=kwargs.get("block_hash"))
    block_hash = substrate.block_hash
    supported: dict[str, bool] = {}

    def adapt(value: Any) -> Any:
        if isinstance(value, dict):
            result = {k: adapt(v) for k, v in value.items()}
            if "stp_mode" in result:
                module = "PerpMarket" if "market_id" in result else "SpotMarket"
                if module not in supported:
                    call = substrate.get_metadata_call_function(module, "place_order", block_hash=block_hash)
                    field = next(f for f in call.value["fields"] if f["name"] == "params")
                    codec = substrate.runtime_config.create_scale_object(
                        f"scale_info::{field['type']}", metadata=substrate.metadata,
                    )
                    supported[module] = any(name == "stp_mode" for name, _ in codec.type_mapping)
                if not supported[module]:
                    if result["stp_mode"] is not None:
                        raise ValueError(f"{module} on this runtime does not support stp_mode")
                    result.pop("stp_mode")
            return result
        if isinstance(value, (list, tuple)):
            return [adapt(v) for v in value]
        return value

    return substrate.compose_call(**{**kwargs, "call_params": adapt(params), "block_hash": block_hash})
