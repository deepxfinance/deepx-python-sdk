from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._error_codes import (
    APIErrorCode,
    ChainErrorCode,
    format_msg,
    lookup_api_error,
    lookup_chain_error,
)


class DeepXSDKError(Exception):
    """Base class for SDK-specific errors."""


class ValidationError(DeepXSDKError, ValueError):
    """Raised when SDK-side validation fails."""


class MarketNotFoundError(ValidationError):
    """Raised when a symbol or market cannot be resolved."""


class RPCError(DeepXSDKError, RuntimeError):
    """Raised when an RPC or transport call fails."""


class TxError(DeepXSDKError, RuntimeError):
    """Raised when a transaction build, submit, or finalization step fails."""


@dataclass
class RESTError(DeepXSDKError, RuntimeError):
    status_code: int | None
    message: str
    code: int | None = None
    error_type: str | None = None
    details: Any = None

    def __str__(self) -> str:
        parts = []
        if self.status_code is not None:
            parts.append(f"HTTP {self.status_code}")
        if self.error_type:
            parts.append(self.error_type)
        if self.code is not None:
            parts.append(str(self.code))
        prefix = " ".join(parts)
        return f"{prefix}: {self.message}" if prefix else self.message


# ---------------------------------------------------------------------------
# Typed error variants backed by the ErrorCodes.yaml / ApiErrorCodes.yaml
# registries in _error_codes.py.
# ---------------------------------------------------------------------------


@dataclass
class ChainError(TxError):
    """Raised when a transaction was executed on-chain and reverted.

    The ``code`` field is the canonical ``"<pallet_index>_<error_index>"``
    identifier (e.g. ``"22_17"``) from ``ErrorCodes.yaml``. ``name`` and
    ``pallet`` are populated from the registry when the code is recognized;
    otherwise they default to empty strings.
    """

    code: str = ""
    name: str = ""
    pallet: str = ""
    message: str = ""

    @property
    def pallet_index(self) -> int:
        return int(self.code.split("_", 1)[0])

    @property
    def error_index(self) -> int:
        return int(self.code.split("_", 1)[1])

    def __str__(self) -> str:
        head = f"chain error {self.code}"
        if self.name:
            head = f"{head} ({self.name})"
        return f"{head}: {self.message}" if self.message else head


@dataclass
class APIError(RESTError):
    """Raised when the REST API rejects a request before it reaches the chain.

    The ``category`` field comes from ``ApiErrorCodes.yaml`` (VALIDATION,
    AUTH, NOT_FOUND, RATE_LIMIT, CONFLICT, INTERNAL).
    """

    category: str = ""

    def __post_init__(self) -> None:
        # Keep RESTError.__str__ behavior — append category to the prefix when
        # no error_type was provided so existing callers don't change shape.
        if self.error_type is None and self.category:
            self.error_type = self.category


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_chain_error_code(code: str | int | None, message: str = "") -> ChainError:
    """Build a ``ChainError`` from a raw code coming off the wire.

    ``code`` may be the canonical ``"<pallet>_<error>"`` string or an integer
    ``(pallet_index * 1000 + error_index)`` shorthand used by some
    pre-aggregation layers. If the code is unknown, the resulting exception
    still carries the raw values so debugging is possible.
    """
    if code is None:
        return ChainError(message=message)
    if isinstance(code, int):
        # Treat as pallet_index * 1000 + error_index, matching the legacy
        # numeric encoding used in some internal error aggregators.
        return ChainError(
            code=f"{code // 1000}_{code % 1000}",
            message=message,
        )
    code_str = str(code)
    registry_hit = lookup_chain_error(code_str)
    return ChainError(
        code=code_str,
        name=registry_hit.name if registry_hit else "",
        pallet=registry_hit.pallet if registry_hit else "",
        message=message,
    )


def parse_api_error_code(code: int | None, message: str = "", **params: Any) -> APIError:
    """Build an ``APIError`` from a raw integer code.

    ``params`` are substituted into the registry's message template; missing
    placeholders fall back to the raw template (no ``KeyError``).
    """
    if code is None:
        return APIError(status_code=None, message=message, category="")
    registry_hit = lookup_api_error(code)
    if registry_hit is None:
        return APIError(status_code=None, message=message, code=code, category="")
    rendered = format_msg_safe(registry_hit.msg, params)
    return APIError(
        status_code=None,
        message=rendered,
        code=code,
        category=registry_hit.category,
    )


def format_msg_safe(template: str, params: dict[str, Any]) -> str:
    """``str.format`` that leaves unfilled placeholders intact instead of raising."""
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


__all__ = [
    "APIError",
    "APIErrorCode",
    "ChainError",
    "ChainErrorCode",
    "DeepXSDKError",
    "MarketNotFoundError",
    "RESTError",
    "RPCError",
    "TxError",
    "ValidationError",
    "format_msg",
    "format_msg_safe",
    "parse_api_error_code",
    "parse_chain_error_code",
]
