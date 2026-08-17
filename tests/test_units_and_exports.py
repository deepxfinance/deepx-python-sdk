from __future__ import annotations

import sys
import types
from decimal import Decimal

import pytest

if "substrateinterface" not in sys.modules:
    substrate_stub = types.ModuleType("substrateinterface")

    class _SubstrateInterfacePlaceholder:
        pass

    substrate_stub.SubstrateInterface = _SubstrateInterfacePlaceholder
    sys.modules["substrateinterface"] = substrate_stub

import deepx_sdk as dx


def test_decimal_unit_helpers() -> None:
    assert dx.to_base_unit("0.01", 18) == 10_000_000_000_000_000
    assert dx.to_quote_unit(Decimal("12.34"), 6) == 12_340_000
    assert dx.from_base_unit(123, 2) == Decimal("1.23")
    assert dx.from_quote_unit(12_340_000, 6) == Decimal("12.34")
    assert dx.to_base_unit("123456789123456789.123456789", 18) == (
        123456789123456789123456789000000000
    )

    with pytest.raises(ValueError, match="fractional precision"):
        dx.to_base_unit("0.001", 2)

    with pytest.raises(ValueError, match="non-negative"):
        dx.to_base_unit("1", -1)

    with pytest.raises(ValueError, match="invalid decimal amount"):
        dx.to_base_unit(object(), 2)

    with pytest.raises(ValueError, match="finite"):
        dx.to_base_unit("NaN", 2)


def test_public_type_exports_and_alias_bases() -> None:
    assert dx.AsyncChainClient is not None
    assert dx.PendingTransaction is not None
    assert dx.TxStatus.SUBMITTED.value == "submitted"
    assert dx.TxUpdate is not None
    assert dx.TxTimings is not None
    assert dx.TxTimeouts is not None
    assert dx.RecoveryConfig is not None
    assert dx.OutcomeCertainty.UNKNOWN.value == "unknown"
    assert issubclass(dx.TransactionError, dx.TxError)
    assert issubclass(dx.InclusionTimeout, dx.TxError)
    assert issubclass(dx.SubmissionTimeout, dx.TransactionError)
    assert issubclass(dx.FinalizationTimeout, dx.TransactionError)
    assert issubclass(dx.TransactionInvalid, dx.TransactionError)
    assert issubclass(dx.TransactionDropped, dx.TransactionError)
    assert issubclass(dx.TransactionUsurped, dx.TransactionError)
    assert issubclass(dx.ReconciliationRequired, dx.TransactionError)
    assert issubclass(dx.ClientBackpressure, dx.TransactionError)
    assert issubclass(dx.ClientNotConnected, dx.TransactionError)
    assert issubclass(dx.ReplacementUnsupported, dx.TransactionError)
    assert issubclass(dx.AsyncApiClient, dx.ApiClient)
    assert hasattr(dx, "BridgeApi")
    assert hasattr(dx, "BridgeServiceClient")
    assert hasattr(dx, "get_sign_bridge_out_url")
    assert hasattr(dx, "get_withdraw_dst_recipient")
    assert issubclass(dx.PlaceOrderResult, dx.OrderResult)
    assert issubclass(dx.SpotPlaceOrderResult, dx.OrderResult)
    assert issubclass(dx.PerpOrderSpec, dx.MarketSpec)
    assert issubclass(dx.SpotMarketSpec, dx.MarketSpec)
    assert issubclass(dx.ValidationError, dx.DeepXSDKError)
    assert issubclass(dx.MarketNotFoundError, dx.ValidationError)
    assert issubclass(dx.RPCError, dx.DeepXSDKError)
    assert issubclass(dx.TxError, dx.DeepXSDKError)
    assert issubclass(dx.RESTError, dx.DeepXSDKError)

    result = dx.PlaceOrderResult(
        order_id=1,
        tx_hash="0xtx",
        extrinsic_hash="0xext",
    )
    assert isinstance(result, dx.OrderResult)
    sdk = dx.SDK(chain=object(), api=object())
    assert sdk.bridge is None
    assert sdk.bridge_service is None
    assert dx.TxConfig(gas_limit=123).gas_limit == 123
    assert dx.parse_ws_message({"method": "ping"}).method == "ping"
    assert dx.WsMessage(channel="test").channel == "test"


def test_transaction_manager_exports() -> None:
    assert dx.ExecutionState.ACCEPTED.value == "accepted"
    assert dx.TransactionSnapshot is not None
    assert dx.TransactionEvent is not None
    assert dx.TransactionManager is not None
