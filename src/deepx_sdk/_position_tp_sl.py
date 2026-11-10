"""Position TP/SL parameters. Prices use 1e6; quantities use base-asset units."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ._abi import normalize_address


def _positive(value: int | None, bits: int, name: str) -> int:
    if type(value) is not int or not 0 < value < 2**bits:
        raise ValueError(f"{name} must be a positive uint{bits}")
    return value


@dataclass(frozen=True)
class TpSlConfig:
    condition: Literal["TakeProfit", "StopLoss"]
    trigger_price: int
    source: Literal["Mark", "Index"] = "Mark"
    quantity: int | None = None
    limit_price: int | None = None
    slippage: int | None = None

    def to_scale(self) -> dict[str, Any]:
        if self.condition not in ("TakeProfit", "StopLoss"):
            raise ValueError("condition must be TakeProfit or StopLoss")
        if self.source not in ("Mark", "Index"):
            raise ValueError("source must be Mark or Index")
        if self.limit_price is not None and self.slippage is not None:
            raise ValueError("slippage applies only to market execution")
        if self.slippage is not None and (
            type(self.slippage) is not int or not 0 <= self.slippage < 2**64
        ):
            raise ValueError("slippage must be a uint64")
        return {
            "condition": self.condition,
            "source": self.source,
            "trigger_price": _positive(self.trigger_price, 128, "trigger_price"),
            "quantity": "Entire"
            if self.quantity is None
            else {"Partial": _positive(self.quantity, 128, "quantity")},
            "execution": {"Market": self.slippage}
            if self.limit_price is None
            else {"Limit": _positive(self.limit_price, 128, "limit_price")},
        }


@dataclass(frozen=True)
class TpSlAction:
    kind: Literal["Create", "Update", "Cancel"]
    config: TpSlConfig | None = None
    setting_id: int | None = None

    def to_scale(self) -> dict[str, Any]:
        if (
            self.kind == "Create"
            and self.setting_id is None
            and isinstance(self.config, TpSlConfig)
        ):
            return {"Create": self.config.to_scale()}
        if self.kind in ("Update", "Cancel"):
            setting_id = _positive(self.setting_id, 64, "setting_id")
            if self.kind == "Cancel" and self.config is None:
                return {"Cancel": {"id": setting_id}}
            if self.kind == "Update" and isinstance(self.config, TpSlConfig):
                return {"Update": {"id": setting_id, "config": self.config.to_scale()}}
        raise ValueError(
            "Create needs config; Update needs id and config; Cancel needs id only"
        )


def tp_sl_params(
    subaccount: str, market_id: int, actions: Sequence[TpSlAction]
) -> dict:
    _positive(market_id, 16, "market_id")
    if not 1 <= len(actions) <= 128:
        raise ValueError("actions must contain 1..128 settings")
    if not all(isinstance(action, TpSlAction) for action in actions):
        raise ValueError("actions must contain TpSlAction objects")
    return {
        "subaccount": normalize_address(subaccount),
        "market_id": market_id,
        "actions": [action.to_scale() for action in actions],
    }
