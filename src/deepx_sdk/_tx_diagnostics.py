"""Safe, actionable diagnostics for asynchronous transaction tracking."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Any

from ._errors import TxError

if TYPE_CHECKING:
    from ._pending_tx import PendingTransaction


class TxStage(str, Enum):
    VALIDATION = "validation"
    ENCODING = "encoding"
    SUBMISSION = "submission"
    INCLUSION = "inclusion"
    FINALIZATION = "finalization"
    RECOVERY = "recovery"
    CLIENT = "client"


class OutcomeCertainty(str, Enum):
    NOT_SUBMITTED = "not_submitted"
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    REPLACED = "replaced"
    EXECUTED_FAILED = "executed_failed"
    NOT_INCLUDED = "not_included"
    INCLUDED = "included"
    FINALIZED = "finalized"


_SECRET_KEYS = frozenset({"private_key", "signed_extrinsic", "signature"})
_SECRET_ASSIGNMENT = re.compile(r"(?i)(private_key|signed_extrinsic|signature)")


def _safe_render(value: Any) -> Any:
    """Return diagnostics-safe data without invoking unknown object renderers."""
    if isinstance(value, BaseException):
        return {
            "type": type(value).__name__,
            "args": _safe_render(value.args),
        }
    if isinstance(value, Mapping):
        return {
            _safe_mapping_key(key): "[REDACTED]"
            if isinstance(key, str) and key.lower() in _SECRET_KEYS
            else _safe_render(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_render(item) for item in value]
    if isinstance(value, str):
        if _SECRET_ASSIGNMENT.search(value):
            return "[REDACTED]"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


def _safe_mapping_key(key: Any) -> str:
    return _safe_text(key)


def _safe_text(value: Any) -> str:
    rendered = _safe_render(value)
    return rendered if isinstance(rendered, str) else f"<{type(value).__name__}>"


class TransactionError(TxError):
    """An exception with enough context to safely decide the next action."""

    def __init__(
        self,
        *,
        code: str,
        stage: TxStage,
        elapsed_ms: int,
        certainty: OutcomeCertainty,
        retryable: bool,
        suggested_action: str,
        tx_hash: str | None = None,
        cloid: int | None = None,
        nonce: int | None = None,
        pending: PendingTransaction[Any] | None = None,
        cause: BaseException | None = None,
        node_status: str | None = None,
        invalid_reason: str | None = None,
        slot_duration_ms: int | None = None,
        elapsed_slots: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.code = code
        self.stage = stage
        self.tx_hash = tx_hash
        self.cloid = cloid
        self.nonce = nonce
        self.elapsed_ms = elapsed_ms
        self.certainty = certainty
        self.retryable = retryable
        self.suggested_action = suggested_action
        self.pending = pending
        self.cause = cause
        self.node_status = node_status
        self.invalid_reason = invalid_reason
        self.slot_duration_ms = slot_duration_ms
        self.elapsed_slots = elapsed_slots
        self.details = dict(details) if details is not None else None

    def to_dict(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "code": self.code,
            "stage": self.stage.value,
            "tx_hash": self.tx_hash,
            "cloid": self.cloid,
            "nonce": self.nonce,
            "elapsed_ms": self.elapsed_ms,
            "certainty": self.certainty.value,
            "safe_to_retry": self.retryable,
            "suggested_action": self.suggested_action,
        }
        if self.node_status is not None:
            details["node_status"] = self.node_status
        if self.invalid_reason is not None:
            details["invalid_reason"] = self.invalid_reason
        if self.slot_duration_ms is not None:
            details["slot_duration_ms"] = self.slot_duration_ms
        if self.elapsed_slots is not None:
            details["elapsed_slots"] = self.elapsed_slots
        if self.cause is not None:
            details["cause"] = _safe_render(
                self.cause.args[0] if len(self.cause.args) == 1 else self.cause
            )
        if self.details is not None:
            details["details"] = _safe_render(self.details)
        return _safe_render(details)

    def __str__(self) -> str:
        outcome = self.certainty.value
        if self.certainty is OutcomeCertainty.UNKNOWN:
            outcome += "; the transaction may still execute later"
        elif self.certainty is OutcomeCertainty.NOT_SUBMITTED:
            outcome += "; the transaction was not submitted"
        elif self.certainty is OutcomeCertainty.REJECTED:
            outcome += "; the node rejected the transaction"
        elif self.certainty is OutcomeCertainty.REPLACED:
            outcome += "; the transaction was replaced"
        elif self.certainty is OutcomeCertainty.EXECUTED_FAILED:
            outcome += "; the transaction executed and failed"
        elif self.certainty is OutcomeCertainty.NOT_INCLUDED:
            outcome += "; the transaction was not included through the observed finalized head"
        elif self.certainty is OutcomeCertainty.INCLUDED:
            outcome += "; the transaction is included but not final"
        else:
            outcome += "; the transaction is finalized"

        parts = [
            _safe_text(self.code),
            f"Stage: {self.stage.value}",
            f"Outcome certainty: {outcome}",
        ]
        if self.tx_hash is not None:
            parts.append(f"Tx hash: {_safe_text(self.tx_hash)}")
        if self.cloid is not None:
            parts.append(f"Cloid: {self.cloid}")
        if self.nonce is not None:
            parts.append(f"Nonce: {self.nonce}")
        if self.node_status is not None:
            parts.append(f"Node status: {_safe_text(self.node_status)}")
        if self.invalid_reason is not None:
            parts.append(f"Node reason: {_safe_text(self.invalid_reason)}")
        parts.append(f"Safe to retry immediately: {'yes' if self.retryable else 'no'}")
        parts.append(f"Suggested action: {_safe_text(self.suggested_action)}")
        return ". ".join(parts) + "."


class _TimeoutError(TransactionError):
    SLOT_DURATION_MS = 70

    def __init__(self, **kwargs: Any) -> None:
        elapsed_ms = int(kwargs["elapsed_ms"])
        kwargs["slot_duration_ms"] = self.SLOT_DURATION_MS
        kwargs["elapsed_slots"] = math.ceil(elapsed_ms / self.SLOT_DURATION_MS)
        super().__init__(**kwargs)


class SubmissionTimeout(_TimeoutError):
    pass


class InclusionTimeout(_TimeoutError):
    pass


class FinalizationTimeout(_TimeoutError):
    pass


_ADMISSION_FAILURES = {
    "ExceedPoolLimit": (
        "The account has reached the node's 50-transaction pool cap.",
        "Wait for an existing transaction to leave the pool, then submit again.",
    ),
    "TimeStale": (
        "The timestamp nonce is duplicate or older-than-retained timestamp nonce.",
        "Use a fresh timestamp nonce and rebuild the transaction.",
    ),
    "Future": (
        "The timestamp is outside the allowed future range.",
        "Synchronize time and use a timestamp nonce within the allowed range.",
    ),
    "BadSigner": (
        "The signing account is inactive or frozen.",
        "Activate or unfreeze the account before rebuilding the transaction.",
    ),
}


class TransactionInvalid(TransactionError):
    @classmethod
    def from_node_reason(
        cls,
        raw_reason: str,
        *,
        tx_hash: str | None = None,
        cloid: int | None = None,
        nonce: int | None = None,
        elapsed_ms: int = 0,
        pending: PendingTransaction[Any] | None = None,
        node_status: str | None = None,
    ) -> TransactionInvalid:
        if "Payment" in raw_reason:
            if "CallType::Timestamp(1)" in raw_reason:
                explanation = (
                    "Quota is exhausted or reserved, or the account is inside its "
                    "10-second quota-free interval."
                )
                action = (
                    "Wait for usable quota or the quota-free interval to end, then "
                    "rebuild and submit."
                )
            else:
                explanation = "The node reported a payment-related admission failure."
                action = "Inspect payment requirements for this call type before rebuilding."
        else:
            reason = next(
                (key for key in _ADMISSION_FAILURES if raw_reason.startswith(key)),
                None,
            )
            if reason is None:
                explanation = "The node rejected the transaction validity."
                action = "Inspect the node reason, correct the transaction, then rebuild it."
            else:
                explanation, action = _ADMISSION_FAILURES[reason]
        return cls(
            code="TRANSACTION_INVALID",
            stage=TxStage.SUBMISSION,
            tx_hash=tx_hash,
            cloid=cloid,
            nonce=nonce,
            elapsed_ms=elapsed_ms,
            certainty=OutcomeCertainty.REJECTED,
            retryable=False,
            suggested_action=f"{explanation} {action}",
            pending=pending,
            node_status=node_status,
            invalid_reason=raw_reason,
        )


class TransactionDropped(TransactionError):
    pass


class TransactionNotIncluded(TransactionError):
    pass


class TransactionUsurped(TransactionError):
    pass


class ReconciliationRequired(TransactionError):
    pass


class ClientBackpressure(TransactionError):
    pass


class ClientNotConnected(TransactionError):
    pass


class ReplacementUnsupported(TransactionError):
    pass


__all__ = [
    "ClientBackpressure",
    "ClientNotConnected",
    "FinalizationTimeout",
    "InclusionTimeout",
    "OutcomeCertainty",
    "ReconciliationRequired",
    "ReplacementUnsupported",
    "SubmissionTimeout",
    "TransactionDropped",
    "TransactionError",
    "TransactionInvalid",
    "TransactionNotIncluded",
    "TransactionUsurped",
    "TxStage",
]
