from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Union


Amount = Union[str, int, float, Decimal]

__all__ = [
    "from_base_unit",
    "from_quote_unit",
    "to_base_unit",
    "to_quote_unit",
]


def _validate_decimals(decimals: int) -> int:
    value = int(decimals)
    if value < 0:
        raise ValueError("decimals must be non-negative")
    return value


def _to_decimal(amount: Amount) -> Decimal:
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal amount: {amount!r}") from exc
    if not value.is_finite():
        raise ValueError("amount must be finite")
    return value


def to_base_unit(amount: Amount, decimals: int) -> int:
    decimals = _validate_decimals(decimals)
    decimal_amount = _to_decimal(amount)
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, len(decimal_amount.as_tuple().digits) + decimals + 10)
        scale = Decimal(10) ** decimals
        value = decimal_amount * scale
        integral = value.to_integral_value()
    if value != integral:
        raise ValueError("amount has more fractional precision than decimals allows")
    return int(integral)


def from_base_unit(amount: int, decimals: int) -> Decimal:
    decimals = _validate_decimals(decimals)
    amount_int = int(amount)
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, len(str(abs(amount_int))) + decimals + 10)
        scale = Decimal(10) ** decimals
        return Decimal(amount_int) / scale


def to_quote_unit(amount: Amount, decimals: int) -> int:
    return to_base_unit(amount, decimals)


def from_quote_unit(amount: int, decimals: int) -> Decimal:
    return from_base_unit(amount, decimals)
