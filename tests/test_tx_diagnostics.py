from __future__ import annotations

import pytest

from deepx_sdk._errors import TxError
from deepx_sdk._tx_diagnostics import (
    InclusionTimeout,
    OutcomeCertainty,
    TransactionError,
    TransactionInvalid,
    TxStage,
)


def test_transaction_error_contains_actionable_context() -> None:
    error = InclusionTimeout(
        code="INCLUSION_TIMEOUT",
        stage=TxStage.INCLUSION,
        tx_hash="0x1234",
        cloid=2**31,
        nonce=1_785_312_345_678,
        elapsed_ms=5_001,
        certainty=OutcomeCertainty.UNKNOWN,
        retryable=False,
        suggested_action="Continue waiting or reconcile by tx hash/cloid.",
    )

    assert isinstance(error, TxError)
    assert error.to_dict()["stage"] == "inclusion"
    assert error.to_dict()["certainty"] == "unknown"
    assert error.to_dict()["safe_to_retry"] is False
    assert "may still execute" in str(error)
    assert "0x1234" in str(error)


def test_diagnostics_redact_signing_secrets_recursively() -> None:
    error = TransactionError(
        code="TRANSPORT_ERROR",
        stage=TxStage.SUBMISSION,
        elapsed_ms=1,
        certainty=OutcomeCertainty.UNKNOWN,
        retryable=False,
        suggested_action="Reconcile by transaction hash before retrying.",
        cause=RuntimeError(
            {
                "private_key": "secret",
                "nested": {
                    "signed_extrinsic": "0xdeadbeef",
                    "signature": "signature-secret",
                    "safe": "visible",
                },
            }
        ),
    )

    details = error.to_dict()["cause"]
    assert details["[REDACTED]"] == "[REDACTED]"
    assert details["nested"]["[REDACTED]"] == "[REDACTED]"
    assert details["nested"]["safe"] == "visible"
    assert "secret" not in str(error)


def test_diagnostics_redact_free_text_cause_that_names_a_signing_secret() -> None:
    error = TransactionError(
        code="TRANSPORT_ERROR",
        stage=TxStage.SUBMISSION,
        elapsed_ms=1,
        certainty=OutcomeCertainty.UNKNOWN,
        retryable=False,
        suggested_action="Reconcile by transaction hash before retrying.",
        cause=RuntimeError("transport rejected private_key free-text-secret"),
    )

    assert error.to_dict()["cause"] == "[REDACTED]"


def test_safe_rendering_redacts_nested_exceptions_containers_unknown_objects_and_strings() -> None:
    class UnknownObject:
        def __repr__(self) -> str:
            return "signature=unknown-secret"

        __str__ = __repr__

    error = TransactionError(
        code="NODE signature=code-secret",
        stage=TxStage.SUBMISSION,
        elapsed_ms=1,
        certainty=OutcomeCertainty.UNKNOWN,
        retryable=False,
        suggested_action="Reconcile using signature=action-secret.",
        node_status="ready signature=node-secret",
        invalid_reason="invalid signature=reason-secret",
        cause=RuntimeError(
            {
                "nested": [
                    RuntimeError("signature=nested-secret"),
                    {RuntimeError("signature=set-secret")},
                    UnknownObject(),
                ]
            }
        ),
    )

    rendered_details = repr(error.to_dict())
    rendered_message = str(error)
    for secret in (
        "code-secret",
        "action-secret",
        "node-secret",
        "reason-secret",
        "nested-secret",
        "set-secret",
        "unknown-secret",
    ):
        assert secret not in rendered_details
        assert secret not in rendered_message
    assert error.to_dict()["cause"]["nested"][2] == "<UnknownObject>"


def test_safe_rendering_redacts_secrets_embedded_in_mapping_keys() -> None:
    error = TransactionError(
        code="TRANSPORT_ERROR",
        stage=TxStage.SUBMISSION,
        elapsed_ms=1,
        certainty=OutcomeCertainty.UNKNOWN,
        retryable=False,
        suggested_action="Reconcile by transaction hash before retrying.",
        cause=RuntimeError({"signature=map-key-secret": "visible"}),
    )

    rendered_details = repr(error.to_dict())
    assert "map-key-secret" not in rendered_details
    assert "map-key-secret" not in str(error)
    assert error.to_dict()["cause"] == {"[REDACTED]": "visible"}


@pytest.mark.parametrize(
    ("raw_reason", "expected_text"),
    [
        ("ExceedPoolLimit", "50-transaction pool cap"),
        ("Payment: CallType::Timestamp(1)", "10-second quota-free interval"),
        ("TimeStale", "duplicate or older-than-retained timestamp nonce"),
        ("Future", "outside the allowed future range"),
        ("BadSigner", "inactive or frozen"),
    ],
)
def test_mapped_admission_failures_preserve_raw_reason_and_next_step(
    raw_reason: str, expected_text: str
) -> None:
    error = TransactionInvalid.from_node_reason(
        raw_reason,
        tx_hash="0x01",
        nonce=10,
        elapsed_ms=2,
    )

    rendered = str(error)
    assert error.to_dict()["invalid_reason"] == raw_reason
    assert expected_text in rendered
    assert "Suggested action:" in rendered


def test_non_timestamp_payment_uses_conservative_admission_guidance() -> None:
    raw_reason = "Payment: CallType::Transfer"
    error = TransactionInvalid.from_node_reason(raw_reason, elapsed_ms=2)

    assert error.to_dict()["invalid_reason"] == raw_reason
    assert "10-second quota-free interval" not in str(error)
    assert "payment-related admission" in str(error)


@pytest.mark.parametrize(
    ("certainty", "expected_phrase"),
    [
        (OutcomeCertainty.NOT_SUBMITTED, "was not submitted"),
        (OutcomeCertainty.REJECTED, "node rejected"),
        (OutcomeCertainty.REPLACED, "was replaced"),
        (OutcomeCertainty.EXECUTED_FAILED, "executed and failed"),
        (OutcomeCertainty.INCLUDED, "included but not final"),
        (OutcomeCertainty.FINALIZED, "is finalized"),
    ],
)
def test_transaction_error_describes_each_non_unknown_certainty(
    certainty: OutcomeCertainty, expected_phrase: str
) -> None:
    error = TransactionError(
        code="TRANSACTION_STATE",
        stage=TxStage.RECOVERY,
        elapsed_ms=1,
        certainty=certainty,
        retryable=False,
        suggested_action="Follow the stated recovery procedure.",
    )

    assert expected_phrase in str(error)


def test_unknown_node_reason_keeps_a_conservative_next_step() -> None:
    error = TransactionInvalid.from_node_reason("UnsupportedValidityRule", elapsed_ms=2)

    assert "Inspect the node reason" in str(error)


def test_timeout_reports_chain_slot_duration_and_ceil_elapsed_slots() -> None:
    error = InclusionTimeout(
        code="INCLUSION_TIMEOUT",
        stage=TxStage.INCLUSION,
        elapsed_ms=141,
        certainty=OutcomeCertainty.UNKNOWN,
        retryable=False,
        suggested_action="Continue waiting or reconcile by tx hash/cloid.",
    )

    assert error.to_dict()["slot_duration_ms"] == 70
    assert error.to_dict()["elapsed_slots"] == 3


def test_timeout_enforces_chain_slot_duration_when_callers_supply_another_value() -> None:
    error = InclusionTimeout(
        code="INCLUSION_TIMEOUT",
        stage=TxStage.INCLUSION,
        elapsed_ms=70,
        certainty=OutcomeCertainty.UNKNOWN,
        retryable=False,
        suggested_action="Continue waiting or reconcile by tx hash/cloid.",
        slot_duration_ms=1_000,
        elapsed_slots=1,
    )

    assert error.to_dict()["slot_duration_ms"] == 70
    assert error.to_dict()["elapsed_slots"] == 1
