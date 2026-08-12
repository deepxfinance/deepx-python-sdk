"""Tests for the ErrorCodes / ApiErrorCodes registries and parser helpers."""

from __future__ import annotations

import pytest

from deepx_sdk import _error_codes as codes
from deepx_sdk._errors import (
    APIError,
    ChainError,
    RESTError,
    format_msg,
    format_msg_safe,
    parse_api_error_code,
    parse_chain_error_code,
)


# ---------------------------------------------------------------------------
# Registry sanity tests
# ---------------------------------------------------------------------------


def test_chain_registry_count_matches_yaml() -> None:
    # The on-chain registry should have at least one entry per pallet listed
    # in the runtime composition (19, 20, 21, 22, 23, 24, 26).
    pallets = {entry.pallet_index for entry in codes.CHAIN_ERROR_CODES.values()}
    assert {19, 20, 21, 22, 23, 24, 26}.issubset(pallets)


def test_chain_registry_codes_are_well_formed() -> None:
    for code, entry in codes.CHAIN_ERROR_CODES.items():
        assert code == entry.code
        pallet_index_str, error_index_str = code.split("_")
        assert int(pallet_index_str) in codes.PALLET_NAMES
        assert int(error_index_str) >= 0
        assert entry.pallet == codes.PALLET_NAMES[int(pallet_index_str)]
        assert entry.category == codes.ON_CHAIN
        assert entry.name  # non-empty
        assert entry.msg  # non-empty


def test_api_registry_codes_are_sequential_from_10001() -> None:
    codes_sorted = sorted(codes.API_ERROR_CODES)
    assert codes_sorted[0] == 10001
    assert codes_sorted == list(range(10001, 10001 + len(codes_sorted)))


def test_api_registry_names_are_upper_snake() -> None:
    for entry in codes.API_ERROR_CODES.values():
        assert entry.name.isupper()
        assert " " not in entry.name


# ---------------------------------------------------------------------------
# Lookup tests
# ---------------------------------------------------------------------------


def test_lookup_chain_error_known() -> None:
    entry = codes.lookup_chain_error("20_17")
    assert entry is not None
    assert entry.name == "InsufficientBalance"
    assert entry.pallet == "SpotMarket"
    assert entry.pallet_index == 20
    assert entry.error_index == 17


@pytest.mark.parametrize("code", ["20_43", "22_75"])
def test_client_order_id_range_matches_chain(code: str) -> None:
    entry = codes.lookup_chain_error(code)
    assert entry is not None
    assert "[2^31, 2^32-1]" in entry.msg


def test_lookup_chain_error_unknown() -> None:
    assert codes.lookup_chain_error("99_99") is None
    assert codes.lookup_chain_error("not-a-code") is None


def test_lookup_api_error_known() -> None:
    entry = codes.lookup_api_error(10001)
    assert entry is not None
    assert entry.name == "INVALID_PARAMETER"
    assert entry.category == codes.VALIDATION


def test_lookup_api_error_unknown() -> None:
    assert codes.lookup_api_error(99999) is None
    assert codes.lookup_api_error(0) is None


# ---------------------------------------------------------------------------
# format_msg tests
# ---------------------------------------------------------------------------


def test_format_msg_substitutes_placeholders() -> None:
    assert format_msg("Hello, {name}!", name="Alice") == "Hello, Alice!"


def test_format_msg_multiple_placeholders() -> None:
    rendered = format_msg(
        "'{value}' is not a valid value for '{param}'. Allowed: {allowed}.",
        value="X",
        param="side",
        allowed="buy, sell",
    )
    assert rendered == "'X' is not a valid value for 'side'. Allowed: buy, sell."


def test_format_msg_missing_placeholder_raises() -> None:
    with pytest.raises(KeyError):
        format_msg("Hello, {name}!")


def test_format_msg_safe_leaves_missing_placeholders() -> None:
    rendered = format_msg_safe("Hello, {name}!", {})
    assert rendered == "Hello, {name}!"

    rendered = format_msg_safe(
        "Rate limit exceeded. Retry after {retryAfter} seconds.",
        {"retryAfter": 30},
    )
    assert rendered == "Rate limit exceeded. Retry after 30 seconds."


def test_format_msg_safe_handles_positional_placeholder_index_error() -> None:
    # str.format raises IndexError on missing positional placeholders like {0};
    # format_msg_safe must catch that too and return the template unchanged.
    # Pass non-empty params so the early-return is skipped and the try block runs.
    rendered = format_msg_safe("Hello {0}", {"foo": "bar"})
    assert rendered == "Hello {0}"


def test_api_error_code_format_substitutes() -> None:
    entry = codes.lookup_api_error(10010)
    assert entry is not None
    assert entry.format(retryAfter=30) == "Rate limit exceeded. Retry after 30 seconds."

    market_entry = codes.lookup_api_error(10007)
    assert market_entry is not None
    assert market_entry.format(symbol="ETH-USDC") == "Market 'ETH-USDC' does not exist or is not active."


# ---------------------------------------------------------------------------
# parse_chain_error_code tests
# ---------------------------------------------------------------------------


def test_parse_chain_error_known_code() -> None:
    err = parse_chain_error_code("20_17", "wrapped msg")
    assert isinstance(err, ChainError)
    assert err.code == "20_17"
    assert err.name == "InsufficientBalance"
    assert err.pallet == "SpotMarket"
    assert err.pallet_index == 20
    assert err.error_index == 17
    assert err.message == "wrapped msg"


def test_parse_chain_error_unknown_code() -> None:
    err = parse_chain_error_code("99_99", "msg")
    assert err.code == "99_99"
    assert err.name == ""
    assert err.pallet == ""


def test_parse_chain_error_none() -> None:
    err = parse_chain_error_code(None, "msg")
    assert err.code == ""
    assert err.name == ""


def test_parse_chain_error_int_shorthand() -> None:
    # 20 * 1000 + 17 == 20017
    err = parse_chain_error_code(20017, "msg")
    assert err.code == "20_17"
    assert err.pallet_index == 20
    assert err.error_index == 17


def test_chain_error_str_format() -> None:
    err = parse_chain_error_code("20_17", "some message")
    rendered = str(err)
    assert "20_17" in rendered
    assert "InsufficientBalance" in rendered
    assert "some message" in rendered


def test_chain_error_str_format_unknown() -> None:
    err = parse_chain_error_code("99_99", "raw")
    rendered = str(err)
    assert "99_99" in rendered
    assert "raw" in rendered


# ---------------------------------------------------------------------------
# parse_api_error_code tests
# ---------------------------------------------------------------------------


def test_parse_api_error_known_code() -> None:
    err = parse_api_error_code(10001, "ignored", param="side")
    assert isinstance(err, APIError)
    assert err.code == 10001
    assert err.category == codes.VALIDATION
    # The registry's template is rendered with the provided params
    assert err.message == "Invalid parameter: side."
    assert err.error_type == codes.VALIDATION


def test_parse_api_error_unknown_code() -> None:
    err = parse_api_error_code(99999, "raw msg")
    assert err.code == 99999
    assert err.category == ""
    assert err.message == "raw msg"


def test_parse_api_error_none() -> None:
    err = parse_api_error_code(None, "msg")
    assert err.code is None
    assert err.category == ""
    assert err.message == "msg"


def test_parse_api_error_with_rate_limit_template() -> None:
    err = parse_api_error_code(10010, "", retryAfter=60)
    assert err.message == "Rate limit exceeded. Retry after 60 seconds."
    assert err.category == codes.RATE_LIMIT


def test_parse_api_error_safe_missing_placeholder() -> None:
    # No params supplied for a template that has {retryAfter} — the message
    # should still be present (un-rendered), not raise KeyError.
    err = parse_api_error_code(10010, "fallback")
    assert err.code == 10010
    assert "Retry after" in err.message


def test_api_error_str_includes_category() -> None:
    err = parse_api_error_code(10010, "msg", retryAfter=10)
    rendered = str(err)
    assert "RATE_LIMIT" in rendered
    assert "10010" in rendered


# ---------------------------------------------------------------------------
# RESTError backward compatibility
# ---------------------------------------------------------------------------


def test_rest_error_str_unchanged() -> None:
    err = RESTError(status_code=400, message="bad", code=-1001, error_type="VALIDATION")
    assert str(err) == "HTTP 400 VALIDATION -1001: bad"


def test_rest_error_str_no_code() -> None:
    err = RESTError(status_code=500, message="boom")
    assert str(err) == "HTTP 500: boom"


def test_rest_error_str_no_fields() -> None:
    err = RESTError(status_code=None, message="just a message")
    assert str(err) == "just a message"
