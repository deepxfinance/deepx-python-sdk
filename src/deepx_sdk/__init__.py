"""DeepX Python SDK."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("deepx-python-sdk")
except PackageNotFoundError:  # running from an uninstalled source tree
    __version__ = "0.0.0"

from ._errors import (
    APIError,
    ChainError,
    DeepXSDKError,
    MarketNotFoundError,
    RESTError,
    RPCError,
    TxError,
    ValidationError,
)
from ._types import (
    ActiveOrderInfo,
    CancelOrderResult,
    DelegateInfo,
    LendingAssetPoolState,
    LendingMarketState,
    MarketSpec,
    ModifyOrderResult,
    OraclePriceInfo,
    OrderResult,
    PerpLiquidationFeeRate,
    PerpLiquidationSpec,
    PerpMarketInfo,
    PerpOrderInfo,
    PerpOrderSpec,
    PerpPositionInfo,
    PerpPriceBounds,
    PlaceOrderResult,
    PositionUpdatedResult,
    SettlePnlResult,
    SpotCancelOrderResult,
    SpotMarketSpec,
    SpotOrderInfo,
    SpotPlaceOrderResult,
    SubaccountBorrowPosition,
    SubaccountInfo,
    SubaccountSpotPosition,
    SubaccountSummary,
    SubaccountUserStats,
    SystemAccountInfo,
    TotalCollateralAndMarginInfo,
    TxConfig,
    TxResult,
)
from .api import ApiClient, AsyncApiClient
from .async_client import AsyncChainClient
from .bridge import *  # noqa: F401,F403
from .bridge import __all__ as _bridge_all
from .client import ChainClient
from ._pending_tx import (
    ExecutionState,
    PendingTransaction,
    TransactionSnapshot,
    TxStatus,
    TxTimings,
    TxTimeouts,
    TxUpdate,
)
from ._sync_ticket import SyncTransactionTicket
from ._transaction_manager import TransactionEvent, TransactionManager
from .sdk import SDK
from ._tx_diagnostics import (
    ClientBackpressure,
    ClientNotConnected,
    FinalizationTimeout,
    InclusionTimeout,
    OutcomeCertainty,
    ReconciliationRequired,
    ReplacementUnsupported,
    SubmissionTimeout,
    TransactionDropped,
    TransactionError,
    TransactionInvalid,
    TransactionUsurped,
)
from .units import from_base_unit, from_quote_unit, to_base_unit, to_quote_unit
from .ws_client import WsClient, WsMessage, parse_ws_message

__all__ = [
    "__version__",
    "APIError",
    "ActiveOrderInfo",
    "ApiClient",
    "AsyncApiClient",
    "AsyncChainClient",
    "CancelOrderResult",
    "DelegateInfo",
    "ChainClient",
    "ChainError",
    "ClientBackpressure",
    "ClientNotConnected",
    "DeepXSDKError",
    "ExecutionState",
    "FinalizationTimeout",
    "InclusionTimeout",
    "LendingAssetPoolState",
    "LendingMarketState",
    "MarketSpec",
    "MarketNotFoundError",
    "ModifyOrderResult",
    "OraclePriceInfo",
    "OrderResult",
    "OutcomeCertainty",
    "PendingTransaction",
    "PerpLiquidationFeeRate",
    "PerpLiquidationSpec",
    "PerpMarketInfo",
    "PerpOrderInfo",
    "PerpOrderSpec",
    "PerpPositionInfo",
    "PerpPriceBounds",
    "PlaceOrderResult",
    "PositionUpdatedResult",
    "ReconciliationRequired",
    "RESTError",
    "ReplacementUnsupported",
    "RPCError",
    "SDK",
    "SettlePnlResult",
    "SpotCancelOrderResult",
    "SpotMarketSpec",
    "SpotOrderInfo",
    "SpotPlaceOrderResult",
    "SubaccountBorrowPosition",
    "SubaccountInfo",
    "SubaccountSpotPosition",
    "SubaccountSummary",
    "SubaccountUserStats",
    "SyncTransactionTicket",
    "SystemAccountInfo",
    "TotalCollateralAndMarginInfo",
    "SubmissionTimeout",
    "TransactionDropped",
    "TransactionError",
    "TransactionEvent",
    "TransactionInvalid",
    "TransactionManager",
    "TransactionSnapshot",
    "TransactionUsurped",
    "TxConfig",
    "TxError",
    "TxResult",
    "TxStatus",
    "TxTimings",
    "TxTimeouts",
    "TxUpdate",
    "ValidationError",
    "WsClient",
    "WsMessage",
    "from_base_unit",
    "from_quote_unit",
    "parse_ws_message",
    "to_base_unit",
    "to_quote_unit",
]
__all__ = list(dict.fromkeys(__all__ + list(_bridge_all)))
