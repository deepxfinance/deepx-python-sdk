"""Single source of truth for DeepX error codes.

Mirrors the upstream YAML registries:

- ``ErrorCodes.yaml`` — on-chain pallet errors (Substrate ``(pallet, error)`` tuples
  encoded as the string ``"<pallet>_<error>"``, e.g. ``"22_17"``).
- ``ApiErrorCodes.yaml`` — REST API errors (sequential integers starting at ``10001``).

Both registries and their layout are described in
``https://github.com/deepxfinance/notes-and-specs/blob/main/specs/error-codes.md``.

Adding new codes:

1. Add the entry to the upstream YAML registry.
2. Add the matching ``ChainErrorCode``/``APIErrorCode`` entry to this module.
3. Run the SDK test suite — ``tests/test_error_codes.py`` enforces that the
   codes registered here match the YAML contract (categories, pallet indices,
   template placeholders, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


# ---------------------------------------------------------------------------
# Category constants
# ---------------------------------------------------------------------------

ON_CHAIN: Final = "ON_CHAIN"
VALIDATION: Final = "VALIDATION"
AUTH: Final = "AUTH"
NOT_FOUND: Final = "NOT_FOUND"
RATE_LIMIT: Final = "RATE_LIMIT"
CONFLICT: Final = "CONFLICT"
INTERNAL: Final = "INTERNAL"

ALL_CATEGORIES: Final = (
    ON_CHAIN,
    VALIDATION,
    AUTH,
    NOT_FOUND,
    RATE_LIMIT,
    CONFLICT,
    INTERNAL,
)


# ---------------------------------------------------------------------------
# Pallet index → name (mirrors the runtime composition in
# runtime/dev/src/lib.rs and runtime/testnet/src/lib.rs)
# ---------------------------------------------------------------------------

PALLET_NAMES: Final = {
    19: "Subaccount",
    20: "SpotMarket",
    21: "Oracle",
    22: "PerpMarket",
    23: "InsuranceVault",
    24: "Lending",
    26: "PerpDeployer",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainErrorCode:
    """One entry from ``ErrorCodes.yaml``.

    ``code`` is the canonical identifier — the string ``"<pallet_index>_<error_index>"``
    (e.g. ``"22_17"``). It is the only field that downstream code is allowed to
    match against; ``name`` and ``msg`` may change without breaking consumers.
    """

    code: str
    name: str
    msg: str
    pallet: str
    category: str = ON_CHAIN

    @property
    def pallet_index(self) -> int:
        return int(self.code.split("_", 1)[0])

    @property
    def error_index(self) -> int:
        return int(self.code.split("_", 1)[1])


@dataclass(frozen=True)
class APIErrorCode:
    """One entry from ``ApiErrorCodes.yaml``."""

    code: int
    name: str
    msg: str
    category: str

    def format(self, **kwargs: object) -> str:
        """Render the message template with ``kwargs`` placeholders."""
        return format_msg(self.msg, **kwargs)


# ---------------------------------------------------------------------------
# Chain error registry (ErrorCodes.yaml)
# ---------------------------------------------------------------------------

_CHAIN_ENTRIES: tuple[dict[str, object], ...] = (
    # --- Subaccount (Pallet 19) ---
    {"code": "19_0", "name": "SubaccountNotInit", "pallet": "Subaccount",
     "msg": "Subaccount not initialized."},
    {"code": "19_1", "name": "InvalidDelegateAccount", "pallet": "Subaccount",
     "msg": "Delegate account is invalid or not authorized for this subaccount."},
    {"code": "19_2", "name": "ExceedMaxDelegateNum", "pallet": "Subaccount",
     "msg": "Subaccount has reached the maximum number of delegate accounts."},
    {"code": "19_3", "name": "OCTAccountNotInit", "pallet": "Subaccount",
     "msg": "OCT (One Click Trading) account not initialized."},
    {"code": "19_4", "name": "OCTAccountExist", "pallet": "Subaccount",
     "msg": "OCT account already exists."},
    {"code": "19_5", "name": "MaxOCTAccounts", "pallet": "Subaccount",
     "msg": "User has reached the maximum number of OCT accounts."},
    {"code": "19_6", "name": "InvalidSubaccountOwner", "pallet": "Subaccount",
     "msg": "Caller is not the owner or an authorized delegate of this subaccount."},
    {"code": "19_7", "name": "ChangeSpotMarginTradeFailedForOpenOrders", "pallet": "Subaccount",
     "msg": "Cannot switch spot margin trading mode while open spot orders exist."},
    {"code": "19_8", "name": "DeleteSubaccountCheckFailed", "pallet": "Subaccount",
     "msg": "Cannot delete subaccount: open positions, orders, or non-zero balances remain."},
    {"code": "19_9", "name": "DuplicateSubaccountName", "pallet": "Subaccount",
     "msg": "Subaccount name is already in use by this account."},
    {"code": "19_10", "name": "InvalidAccountId", "pallet": "Subaccount",
     "msg": "Invalid account ID."},
    {"code": "19_13", "name": "InvalidSubaccountStatus", "pallet": "Subaccount",
     "msg": "Subaccount status does not allow this operation (e.g. frozen, liquidating, or closed)."},
    {"code": "19_14", "name": "GetMarginCalculationFailed", "pallet": "Subaccount",
     "msg": "Failed to compute account margin."},
    {"code": "19_15", "name": "MeetMMReq", "pallet": "Subaccount",
     "msg": "Account still meets maintenance margin requirements and is not eligible for liquidation or bankruptcy."},
    {"code": "19_16", "name": "MarginShortageMathError", "pallet": "Subaccount",
     "msg": "Margin shortage computation failed."},
    {"code": "19_17", "name": "MarginFreedMathError", "pallet": "Subaccount",
     "msg": "Releasable margin computation failed."},
    {"code": "19_18", "name": "EmptyLiquidityBucket", "pallet": "Subaccount",
     "msg": "No liquidity bucket found during liquidation."},
    {"code": "19_19", "name": "InvalidEvmCallType", "pallet": "Subaccount",
     "msg": "Batch operation contains an unsupported EVM call type."},
    {"code": "19_20", "name": "TokenTransferFailed", "pallet": "Subaccount",
     "msg": "ERC20 token transfer within the subaccount failed."},
    {"code": "19_21", "name": "SwapFailed", "pallet": "Subaccount",
     "msg": "Token swap within the subaccount failed."},
    {"code": "19_22", "name": "ADLNotReady", "pallet": "Subaccount",
     "msg": "Auto-deleveraging (ADL) conditions are not yet met."},
    {"code": "19_25", "name": "BankruptNotReady", "pallet": "Subaccount",
     "msg": "Bankruptcy conditions are not yet met."},
    {"code": "19_26", "name": "InvalidIfManager", "pallet": "Subaccount",
     "msg": "Invalid Insurance Fund manager address."},
    {"code": "19_27", "name": "InvalidIfAddress", "pallet": "Subaccount",
     "msg": "Invalid Insurance Fund address."},
    {"code": "19_28", "name": "InvalidBankrupt", "pallet": "Subaccount",
     "msg": "Invalid bankruptcy request."},
    {"code": "19_29", "name": "OpenOrdersExist", "pallet": "Subaccount",
     "msg": "Subaccount has open orders."},
    {"code": "19_30", "name": "InvalidMaxSpotAssetTypes", "pallet": "Subaccount",
     "msg": "Configured maximum spot asset type limit is invalid."},
    {"code": "19_31", "name": "InvalidMaxPerpPositions", "pallet": "Subaccount",
     "msg": "Configured maximum perpetual positions limit is invalid."},
    {"code": "19_32", "name": "BatchOpsExceedLimit", "pallet": "Subaccount",
     "msg": "modify_orders requires exactly 2 operations (cancel + place)."},
    {"code": "19_33", "name": "InvalidModifyOps", "pallet": "Subaccount",
     "msg": "modify_orders expects [cancel, place] for the same subaccount."},
    {"code": "19_34", "name": "DelegateExpiry", "pallet": "Subaccount",
     "msg": "Delegate valid_until is in the past."},
    {"code": "19_35", "name": "ExceedMaxSubaccountNum", "pallet": "Subaccount",
     "msg": "Wallet already has the maximum number of subaccounts."},
    {"code": "19_36", "name": "ExceedMaxDelegatorNum", "pallet": "Subaccount",
     "msg": "Delegate account already bound to the maximum number of wallets."},
    {"code": "19_37", "name": "DelegateAccountNotInit", "pallet": "Subaccount",
     "msg": "Delegate account does not exist for this wallet."},
    {"code": "19_38", "name": "InvalidNextOrderId", "pallet": "Subaccount",
     "msg": "restore_subaccount_info requires next_order_id >= 1."},
    {"code": "19_39", "name": "SubaccountAlreadyInit", "pallet": "Subaccount",
     "msg": "restore_subaccount_info target subaccount already exists."},
    {"code": "19_40", "name": "InvalidDelegateMode", "pallet": "Subaccount",
     "msg": "Delegate mode is disabled on-chain (only PlaceOrCancelOrder/Disable are usable)."},
    # --- SpotMarket (Pallet 20) ---
    {"code": "20_0", "name": "SpotMarketNotInit", "pallet": "SpotMarket",
     "msg": "Spot market not initialized."},
    {"code": "20_1", "name": "SpotMarketAlreadyInit", "pallet": "SpotMarket",
     "msg": "Spot market for this trading pair already exists."},
    {"code": "20_2", "name": "InvalidAmount", "pallet": "SpotMarket",
     "msg": "Order size or transfer amount is invalid (zero or invalid precision)."},
    {"code": "20_4", "name": "IncorrectOrderMaker", "pallet": "SpotMarket",
     "msg": "Order maker address does not match on-chain records."},
    {"code": "20_5", "name": "OrderAlreadyInactive", "pallet": "SpotMarket",
     "msg": "Order is already inactive (filled, cancelled, or expired)."},
    {"code": "20_6", "name": "InvalidBuyOrderId", "pallet": "SpotMarket",
     "msg": "Buy order ID does not exist."},
    {"code": "20_7", "name": "InvalidSellOrderId", "pallet": "SpotMarket",
     "msg": "Sell order ID does not exist."},
    {"code": "20_8", "name": "PricesNotMatch", "pallet": "SpotMarket",
     "msg": "Buy and sell order prices do not match."},
    {"code": "20_9", "name": "InvalidSlippage", "pallet": "SpotMarket",
     "msg": "Slippage parameter is outside the allowed range."},
    {"code": "20_10", "name": "InvalidPrice", "pallet": "SpotMarket",
     "msg": "Order price is invalid (zero, invalid tick size, or outside price limits)."},
    {"code": "20_11", "name": "ExceedMaxPendingOrders", "pallet": "SpotMarket",
     "msg": "User has reached the maximum number of pending orders in this market."},
    {"code": "20_12", "name": "SubaccountNotInit", "pallet": "SpotMarket",
     "msg": "Subaccount not initialized."},
    {"code": "20_13", "name": "InvalidSubaccountOwner", "pallet": "SpotMarket",
     "msg": "Caller is not the subaccount owner or an authorized delegate."},
    {"code": "20_17", "name": "InsufficientBalance", "pallet": "SpotMarket",
     "msg": "Insufficient available balance to place order or transfer funds."},
    {"code": "20_20", "name": "ExceedMaxActiveSpotOrders", "pallet": "SpotMarket",
     "msg": "User has reached the maximum number of active spot orders."},
    {"code": "20_23", "name": "MinimumSpotOrderSize", "pallet": "SpotMarket",
     "msg": "Order size is below the market minimum order size."},
    {"code": "20_24", "name": "InvalidSpotTickSize", "pallet": "SpotMarket",
     "msg": "Price does not comply with the market tick size."},
    {"code": "20_25", "name": "InvalidSpotStepSize", "pallet": "SpotMarket",
     "msg": "Quantity does not comply with the market step size."},
    {"code": "20_26", "name": "SpotOrderMarginExceed", "pallet": "SpotMarket",
     "msg": "Required margin for the spot order exceeds available account margin."},
    {"code": "20_27", "name": "MarketQueueErrForPostOnlyOrder", "pallet": "SpotMarket",
     "msg": "Post-Only order would immediately match a market order and was rejected."},
    {"code": "20_28", "name": "LimitQueueErrForPostOnlyOrder", "pallet": "SpotMarket",
     "msg": "Post-Only order would immediately match a limit order and was rejected."},
    {"code": "20_29", "name": "ReduceOnlyCheckFailed", "pallet": "SpotMarket",
     "msg": "Reduce-Only order validation failed."},
    {"code": "20_30", "name": "GetBorrowLiabilityValueFailed", "pallet": "SpotMarket",
     "msg": "Failed to fetch borrow liability value."},
    {"code": "20_32", "name": "MathDivFailed", "pallet": "SpotMarket",
     "msg": "Division failed (division by zero or overflow)."},
    {"code": "20_33", "name": "InvalidAccountStatus", "pallet": "SpotMarket",
     "msg": "Account status does not allow spot trading (e.g. liquidating or frozen)."},
    {"code": "20_34", "name": "InvalidSpotNotional", "pallet": "SpotMarket",
     "msg": "Order notional value is below the market minimum notional."},
    {"code": "20_35", "name": "MinPriceLimited", "pallet": "SpotMarket",
     "msg": "Order price is below the minimum allowed price."},
    {"code": "20_36", "name": "MaxPriceLimited", "pallet": "SpotMarket",
     "msg": "Order price is above the maximum allowed price."},
    {"code": "20_37", "name": "InvalidLimitPriceSpec", "pallet": "SpotMarket",
     "msg": "Invalid limit order price (deviation from oracle or mark price is too large)."},
    {"code": "20_38", "name": "InvalidMaxDeviation", "pallet": "SpotMarket",
     "msg": "Configured maximum price deviation is invalid."},
    {"code": "20_39", "name": "ExceedMaxSpotAssetTypes", "pallet": "SpotMarket",
     "msg": "Subaccount has reached the maximum number of spot asset types."},
    {"code": "20_40", "name": "InvalidSpotOrderType", "pallet": "SpotMarket",
     "msg": "Unsupported spot order type."},
    {"code": "20_43", "name": "PlaceSpotExceedClientOrderId", "pallet": "SpotMarket",
     "msg": "Client order id (cloid) is outside the allowed range [2^31-1, 2^32-2]."},
    {"code": "20_44", "name": "PlaceSpotExceedSystemOrderId", "pallet": "SpotMarket",
     "msg": "System order id range is exhausted for this subaccount."},
    {"code": "20_45", "name": "SpotDuplicateClientOrderId", "pallet": "SpotMarket",
     "msg": "This client order id (cloid) was already used by the subaccount."},
    {"code": "20_46", "name": "InvalidSpotFeeRate", "pallet": "SpotMarket",
     "msg": "Spot fee rate is outside the allowed range."},
    # --- Oracle (Pallet 21) ---
    {"code": "21_4", "name": "NodeNotWhitelisted", "pallet": "Oracle",
     "msg": "Node submitting prices is not on the whitelist."},
    {"code": "21_5", "name": "TooManySymbols", "pallet": "Oracle",
     "msg": "Maximum number of supported symbols has been reached."},
    {"code": "21_6", "name": "SymbolTooLong", "pallet": "Oracle",
     "msg": "Symbol exceeds the maximum allowed length."},
    {"code": "21_7", "name": "SymbolAlreadyExists", "pallet": "Oracle",
     "msg": "Symbol already exists."},
    # --- PerpMarket (Pallet 22) ---
    {"code": "22_0", "name": "MarketNotFound", "pallet": "PerpMarket",
     "msg": "Perpetual market does not exist."},
    {"code": "22_1", "name": "InvalidMarketId", "pallet": "PerpMarket",
     "msg": "Market ID is invalid or not registered."},
    {"code": "22_3", "name": "NotAuthorized", "pallet": "PerpMarket",
     "msg": "Caller is not authorized (not a market admin or deployer)."},
    {"code": "22_4", "name": "TooManyActiveOrders", "pallet": "PerpMarket",
     "msg": "User has reached the maximum number of active orders in this market."},
    {"code": "22_5", "name": "ZeroOrderSize", "pallet": "PerpMarket",
     "msg": "Order size is zero."},
    {"code": "22_6", "name": "InvalidLeverage", "pallet": "PerpMarket",
     "msg": "Leverage is invalid or exceeds the market allowed range."},
    {"code": "22_8", "name": "UserAccountNotFound", "pallet": "PerpMarket",
     "msg": "Subaccount not found."},
    {"code": "22_9", "name": "MaxPriceLimited", "pallet": "PerpMarket",
     "msg": "Order price is above the maximum allowed (relative to mark or oracle price)."},
    {"code": "22_10", "name": "MinPriceLimited", "pallet": "PerpMarket",
     "msg": "Order price is below the minimum allowed (relative to mark or oracle price)."},
    {"code": "22_11", "name": "MaxSlippageLimited", "pallet": "PerpMarket",
     "msg": "Market order slippage exceeds the maximum allowed slippage."},
    {"code": "22_13", "name": "OrderNotFound", "pallet": "PerpMarket",
     "msg": "Perpetual order does not exist."},
    {"code": "22_14", "name": "PerpPositionNotFound", "pallet": "PerpMarket",
     "msg": "Perpetual position does not exist."},
    {"code": "22_25", "name": "InvalidOrderType", "pallet": "PerpMarket",
     "msg": "Unsupported perpetual order type."},
    {"code": "22_30", "name": "InvalidAccountStatus", "pallet": "PerpMarket",
     "msg": "Account status does not allow this operation (liquidating, frozen, or closed)."},
    {"code": "22_31", "name": "InvalidTakeProfit", "pallet": "PerpMarket",
     "msg": "Take-profit price is invalid (incorrect direction or price)."},
    {"code": "22_32", "name": "InvalidStopLoss", "pallet": "PerpMarket",
     "msg": "Stop-loss price is invalid (incorrect direction or price)."},
    {"code": "22_35", "name": "IsClosingNow", "pallet": "PerpMarket",
     "msg": "Market or account is in a closing flow; new operations are not accepted."},
    {"code": "22_36", "name": "PausedPlaceOrder", "pallet": "PerpMarket",
     "msg": "Order placement is paused for this market."},
    {"code": "22_39", "name": "MinimumPerpOrderSize", "pallet": "PerpMarket",
     "msg": "Order size is below the market minimum order size."},
    {"code": "22_40", "name": "InvalidPerpTickSize", "pallet": "PerpMarket",
     "msg": "Price does not comply with the market tick size."},
    {"code": "22_41", "name": "InvalidPerpStepSize", "pallet": "PerpMarket",
     "msg": "Quantity does not comply with the market step size."},
    {"code": "22_42", "name": "InvalidReduceOnlySize", "pallet": "PerpMarket",
     "msg": "Reduce-Only order size exceeds the closable position size."},
    {"code": "22_43", "name": "InvalidReduceOnlyDirection", "pallet": "PerpMarket",
     "msg": "Reduce-Only order direction matches the existing position."},
    {"code": "22_44", "name": "MarketQueueErrForPostOnlyOrder", "pallet": "PerpMarket",
     "msg": "Post-Only order would immediately match a market order and was rejected."},
    {"code": "22_45", "name": "LimitQueueErrForPostOnlyOrder", "pallet": "PerpMarket",
     "msg": "Post-Only order would immediately match a limit order and was rejected."},
    {"code": "22_46", "name": "SubaccountNotInit", "pallet": "PerpMarket",
     "msg": "Subaccount not initialized."},
    {"code": "22_48", "name": "PerpOrderMarginExceed", "pallet": "PerpMarket",
     "msg": "Required margin for the perpetual order exceeds available account margin."},
    {"code": "22_49", "name": "MeetMMReq", "pallet": "PerpMarket",
     "msg": "Account still meets maintenance margin requirements and is not eligible for liquidation."},
    {"code": "22_50", "name": "InvalidBaseAssetAmountForLiquidator", "pallet": "PerpMarket",
     "msg": "Invalid base asset amount specified by the liquidator."},
    {"code": "22_51", "name": "BaseAmountForMarginShortageErr", "pallet": "PerpMarket",
     "msg": "Failed to compute base asset amount from margin shortage."},
    {"code": "22_52", "name": "InvalidPctFreeable", "pallet": "PerpMarket",
     "msg": "Invalid releasable margin percentage."},
    {"code": "22_53", "name": "LiquidatorInsufficientCollateral", "pallet": "PerpMarket",
     "msg": "Liquidator collateral is insufficient to complete liquidation."},
    {"code": "22_54", "name": "PruneBaseAmountFailed", "pallet": "PerpMarket",
     "msg": "Failed to prune or adjust base asset amount."},
    {"code": "22_55", "name": "CheckLiquidationLimitPriceFailed", "pallet": "PerpMarket",
     "msg": "Liquidation limit price check failed."},
    {"code": "22_56", "name": "MarginFreedMathError", "pallet": "PerpMarket",
     "msg": "Releasable margin computation failed."},
    {"code": "22_57", "name": "MarginShortageMathError", "pallet": "PerpMarket",
     "msg": "Margin shortage computation failed."},
    {"code": "22_58", "name": "EmptyLiquidityBucket", "pallet": "PerpMarket",
     "msg": "No liquidity bucket found during liquidation."},
    {"code": "22_59", "name": "InvalidOrderStatus", "pallet": "PerpMarket",
     "msg": "Order status does not allow this operation (e.g. filled or cancelled)."},
    {"code": "22_60", "name": "InvalidDelegate", "pallet": "PerpMarket",
     "msg": "Delegate is not authorized to operate on behalf of this subaccount."},
    {"code": "22_61", "name": "InvalidPerpNotional", "pallet": "PerpMarket",
     "msg": "Order notional value is below the market minimum notional."},
    {"code": "22_62", "name": "InvalidClosingPrice", "pallet": "PerpMarket",
     "msg": "Closing price is invalid or outside the allowed range."},
    {"code": "22_63", "name": "AlreadyClosing", "pallet": "PerpMarket",
     "msg": "Position is already in a closing flow."},
    {"code": "22_65", "name": "InvalidBankruptPrice", "pallet": "PerpMarket",
     "msg": "Computed bankruptcy price is invalid."},
    {"code": "22_66", "name": "PruneBankruptFailed", "pallet": "PerpMarket",
     "msg": "Failed to prune asset amount during bankruptcy."},
    {"code": "22_67", "name": "EmptyAdlRanked", "pallet": "PerpMarket",
     "msg": "ADL ranking list is empty; no counterparty can be selected."},
    {"code": "22_68", "name": "InvalidLimitPriceSpec", "pallet": "PerpMarket",
     "msg": "Invalid limit order price (deviation from mark or oracle price is too large)."},
    {"code": "22_69", "name": "ExceedMaxPerpPositions", "pallet": "PerpMarket",
     "msg": "Subaccount has reached the maximum number of perpetual positions."},
    {"code": "22_70", "name": "LeverageCapExceeded", "pallet": "PerpMarket",
     "msg": "Leverage exceeds the allowed cap for this market or account."},
    {"code": "22_71", "name": "InvalidLiquidationFillAmount", "pallet": "PerpMarket",
     "msg": "Invalid liquidation fill amount."},
    {"code": "22_72", "name": "IncompatibleOrderFlags", "pallet": "PerpMarket",
     "msg": "Incompatible order flags (e.g. Post-Only with IOC)."},
    {"code": "22_73", "name": "UnsupportedTimeInForce", "pallet": "PerpMarket",
     "msg": "Unsupported time-in-force (TIF) type."},
    {"code": "22_74", "name": "PlacePerpExceedSystemOrderId", "pallet": "PerpMarket",
     "msg": "System order id range is exhausted for this subaccount."},
    {"code": "22_75", "name": "PlacePerpExceedClientOrderId", "pallet": "PerpMarket",
     "msg": "Client order id (cloid) is outside the allowed range [2^31-1, 2^32-2]."},
    {"code": "22_76", "name": "PerpDuplicateClientOrderId", "pallet": "PerpMarket",
     "msg": "This client order id (cloid) was already used by the subaccount."},
    # --- InsuranceVault (Pallet 23) ---
    {"code": "23_0", "name": "ZeroAmount", "pallet": "InsuranceVault",
     "msg": "Stake/unstake amount is zero."},
    {"code": "23_2", "name": "NoStake", "pallet": "InsuranceVault",
     "msg": "User has no stake record in this market."},
    {"code": "23_3", "name": "UnstakeExceedsStake", "pallet": "InsuranceVault",
     "msg": "Unstake amount exceeds the staked amount."},
    {"code": "23_4", "name": "CooldownNotPassed", "pallet": "InsuranceVault",
     "msg": "Stake cooldown period has not ended."},
    {"code": "23_5", "name": "NoReward", "pallet": "InsuranceVault",
     "msg": "No claimable rewards."},
    {"code": "23_6", "name": "NoPoolStake", "pallet": "InsuranceVault",
     "msg": "The reward pool has no staked funds."},
    {"code": "23_7", "name": "NoClearingAccount", "pallet": "InsuranceVault",
     "msg": "No clearing account has been configured."},
    {"code": "23_8", "name": "NotClearingAccount", "pallet": "InsuranceVault",
     "msg": "Caller is not an authorized clearing account."},
    {"code": "23_9", "name": "CheckedMulIntFailed", "pallet": "InsuranceVault",
     "msg": "Reward share computation overflow."},
    {"code": "23_10", "name": "InvalidMarket", "pallet": "InsuranceVault",
     "msg": "Market ID is not in the insurance vault."},
    {"code": "23_11", "name": "VaultSubaccountNotInit", "pallet": "InsuranceVault",
     "msg": "Insurance vault subaccount not initialized."},
    # --- Lending (Pallet 24) ---
    {"code": "24_0", "name": "MarketAlreadyExists", "pallet": "Lending",
     "msg": "Lending market already exists."},
    {"code": "24_1", "name": "AssetPoolAlreadyExists", "pallet": "Lending",
     "msg": "Asset pool already exists in this lending market."},
    {"code": "24_2", "name": "NoPosition", "pallet": "Lending",
     "msg": "User has no position in this lending market."},
    {"code": "24_3", "name": "InvalidAccountId", "pallet": "Lending",
     "msg": "Invalid account ID."},
    {"code": "24_4", "name": "InvalidAsset", "pallet": "Lending",
     "msg": "Asset is not supported by the lending market."},
    {"code": "24_5", "name": "InvalidAssetPool", "pallet": "Lending",
     "msg": "Asset pool does not exist."},
    {"code": "24_6", "name": "NoDeposit", "pallet": "Lending",
     "msg": "User has no deposit record."},
    {"code": "24_8", "name": "InsufficientBalance", "pallet": "Lending",
     "msg": "Account balance is insufficient to deposit, borrow, or repay."},
    {"code": "24_10", "name": "InterestError", "pallet": "Lending",
     "msg": "Interest calculation failed."},
    {"code": "24_11", "name": "InsufficientLiquidity", "pallet": "Lending",
     "msg": "Asset pool liquidity is insufficient for borrowing or withdrawal."},
    {"code": "24_12", "name": "HealthFactorTooLow", "pallet": "Lending",
     "msg": "Health factor would fall below the safe threshold after this operation."},
    {"code": "24_13", "name": "NoBorrow", "pallet": "Lending",
     "msg": "User has no borrow record."},
    {"code": "24_15", "name": "NothingToRepay", "pallet": "Lending",
     "msg": "No borrow amount to repay."},
    {"code": "24_16", "name": "WithdrawTooMuch", "pallet": "Lending",
     "msg": "Withdrawal amount exceeds the withdrawable deposit."},
    {"code": "24_18", "name": "DepositAlreadyExists", "pallet": "Lending",
     "msg": "Deposit record for this asset already exists."},
    {"code": "24_20", "name": "TooMuchBorrow", "pallet": "Lending",
     "msg": "Borrow amount exceeds pool limit or borrowable capacity."},
    {"code": "24_21", "name": "CorruptedSnapshot", "pallet": "Lending",
     "msg": "Deposit/borrow snapshot data is corrupted."},
    {"code": "24_22", "name": "BorrowAlreadyExists", "pallet": "Lending",
     "msg": "Borrow record for this asset already exists."},
    {"code": "24_23", "name": "InsufficientDeposit", "pallet": "Lending",
     "msg": "Deposit amount is insufficient."},
    {"code": "24_24", "name": "SubaccountNotInit", "pallet": "Lending",
     "msg": "Subaccount not initialized."},
    {"code": "24_25", "name": "FetchPriceFailed", "pallet": "Lending",
     "msg": "Failed to fetch asset oracle price."},
    {"code": "24_26", "name": "InsufficientHealth", "pallet": "Lending",
     "msg": "Account health is insufficient to complete this operation."},
    {"code": "24_28", "name": "InsufficientTokenToWithdraw", "pallet": "Lending",
     "msg": "Insufficient withdrawable token amount."},
    {"code": "24_30", "name": "InsufficientWithdraw", "pallet": "Lending",
     "msg": "Insufficient withdrawable balance."},
    {"code": "24_31", "name": "UnexpectErrorAtBorrow", "pallet": "Lending",
     "msg": "Unexpected internal error during borrow."},
    {"code": "24_32", "name": "UnexpectErrorAtWithdraw", "pallet": "Lending",
     "msg": "Unexpected internal error during withdrawal."},
    {"code": "24_33", "name": "InvalidLiabilityTransferAmountForLiquidator", "pallet": "Lending",
     "msg": "Invalid liability transfer amount for the liquidator."},
    {"code": "24_34", "name": "UserLiabilityAssetNotFound", "pallet": "Lending",
     "msg": "Liquidated user has no corresponding liability asset."},
    {"code": "24_35", "name": "UserDepositAssetNotFound", "pallet": "Lending",
     "msg": "Liquidated user has no corresponding deposit or collateral asset."},
    {"code": "24_36", "name": "LiquidatorDepositAssetNotFound", "pallet": "Lending",
     "msg": "Liquidator has no deposit record to receive transferred assets."},
    {"code": "24_37", "name": "GetMarginCalculationFailed", "pallet": "Lending",
     "msg": "Failed to compute account margin."},
    {"code": "24_38", "name": "MeetMMReq", "pallet": "Lending",
     "msg": "Account still meets maintenance margin requirements and is not eligible for liquidation."},
    {"code": "24_39", "name": "MarginShortageMathError", "pallet": "Lending",
     "msg": "Margin shortage computation failed."},
    {"code": "24_40", "name": "MarginFreedMathError", "pallet": "Lending",
     "msg": "Releasable margin computation failed."},
    {"code": "24_42", "name": "CalculateLiabilityTransferAmountCoverShortageFailed", "pallet": "Lending",
     "msg": "Failed to compute liability transfer amount to cover margin shortage."},
    {"code": "24_43", "name": "CalculateLiabilityTransferAmountBaseOnAssetAmountFailed", "pallet": "Lending",
     "msg": "Failed to compute liability transfer amount from asset amount."},
    {"code": "24_44", "name": "CalculateAssetTransferAmountBaseOnLiabilityTransferValueFailed", "pallet": "Lending",
     "msg": "Failed to compute asset transfer amount from liability transfer value."},
    {"code": "24_45", "name": "InvalidLiabilityTransferAmount", "pallet": "Lending",
     "msg": "Invalid liability transfer amount (zero or out of range)."},
    {"code": "24_46", "name": "InvalidAssetTransferAmount", "pallet": "Lending",
     "msg": "Invalid asset transfer amount (zero or out of range)."},
    {"code": "24_47", "name": "InsufficientAssetAmountForLiquidator", "pallet": "Lending",
     "msg": "Liquidator asset amount is insufficient to complete liquidation."},
    {"code": "24_48", "name": "LiquidatorInsufficientCollateral", "pallet": "Lending",
     "msg": "Liquidator collateral is insufficient."},
    {"code": "24_49", "name": "GetLiabilityAssetsReturnNone", "pallet": "Lending",
     "msg": "User liability asset list is empty."},
    {"code": "24_50", "name": "CheckLimitPriceFailed", "pallet": "Lending",
     "msg": "Liquidation limit price check failed."},
    {"code": "24_51", "name": "ExceedSupplyCap", "pallet": "Lending",
     "msg": "Deposit would exceed the asset pool supply cap."},
    {"code": "24_52", "name": "ExceedBorrowCap", "pallet": "Lending",
     "msg": "Borrow would exceed the asset pool borrow cap."},
    {"code": "24_53", "name": "InsufficientProtocolReserve", "pallet": "Lending",
     "msg": "Protocol reserve is insufficient to complete the operation."},
    {"code": "24_54", "name": "ExceedMaxSpotAssetTypes", "pallet": "Lending",
     "msg": "Subaccount has reached the maximum number of spot asset types."},
    {"code": "24_55", "name": "NoNonQuoteSpotDeposit", "pallet": "Lending",
     "msg": "User has no non-quote spot deposit to use as collateral."},
    {"code": "24_56", "name": "NoSpotMarketForAsset", "pallet": "Lending",
     "msg": "No spot market exists for this asset."},
    {"code": "24_57", "name": "InvalidQuotePaymentForIfTakeover", "pallet": "Lending",
     "msg": "Invalid quote asset payment amount for Insurance Fund takeover."},
    # --- PerpDeployer (Pallet 26) ---
    {"code": "26_0", "name": "AlreadyDeployer", "pallet": "PerpDeployer",
     "msg": "This account is already registered as a deployer."},
    {"code": "26_1", "name": "NotDeployer", "pallet": "PerpDeployer",
     "msg": "This account is not a registered deployer."},
    {"code": "26_2", "name": "InsufficientStake", "pallet": "PerpDeployer",
     "msg": "Stake amount is below the minimum required to become a deployer."},
    {"code": "26_3", "name": "HasActiveMarkets", "pallet": "PerpDeployer",
     "msg": "Deployer still has active markets attached; cannot request unstake."},
    {"code": "26_4", "name": "UnstakeNotMatured", "pallet": "PerpDeployer",
     "msg": "Unstake lock period has not ended; cannot finalize."},
    {"code": "26_5", "name": "TooManyMarkets", "pallet": "PerpDeployer",
     "msg": "Deployer has reached the maximum number of deployable markets."},
    {"code": "26_6", "name": "MarketAlreadyAttached", "pallet": "PerpDeployer",
     "msg": "Market is already attached to another deployer."},
    {"code": "26_7", "name": "InvalidAccountId", "pallet": "PerpDeployer",
     "msg": "Invalid account ID."},
    {"code": "26_8", "name": "StakeAssetInfoNotInit", "pallet": "PerpDeployer",
     "msg": "Stake asset configuration not initialized."},
)


def _build_chain_registry() -> dict[str, ChainErrorCode]:
    registry: dict[str, ChainErrorCode] = {}
    for entry in _CHAIN_ENTRIES:
        entry_dict = entry
        code = entry_dict["code"]
        registry[code] = ChainErrorCode(
            code=code,
            name=entry_dict["name"],
            msg=entry_dict["msg"],
            pallet=entry_dict["pallet"],
            category=ON_CHAIN,
        )
    return registry


CHAIN_ERROR_CODES: Final = _build_chain_registry()


# ---------------------------------------------------------------------------
# API error registry (ApiErrorCodes.yaml)
# ---------------------------------------------------------------------------

_API_ENTRIES: tuple[dict[str, object], ...] = (
    {"code": 10001, "name": "INVALID_PARAMETER", "category": VALIDATION,
     "msg": "Invalid parameter: {param}."},
    {"code": 10002, "name": "INVALID_JSON", "category": VALIDATION,
     "msg": "Request body is not valid JSON."},
    {"code": 10003, "name": "INVALID_ENUM_VALUE", "category": VALIDATION,
     "msg": "'{value}' is not a valid value for '{param}'. Allowed: {allowed}."},
    {"code": 10004, "name": "INVALID_SIGNATURE", "category": AUTH,
     "msg": "Invalid cryptographic signature."},
    {"code": 10005, "name": "SIGNATURE_EXPIRED", "category": AUTH,
     "msg": "Signature timestamp is outside the allowed window."},
    {"code": 10006, "name": "API_KEY_NOT_FOUND", "category": AUTH,
     "msg": "API key does not exist or has been revoked."},
    {"code": 10007, "name": "MARKET_NOT_FOUND", "category": NOT_FOUND,
     "msg": "Market '{symbol}' does not exist or is not active."},
    {"code": 10008, "name": "ORDER_NOT_FOUND", "category": NOT_FOUND,
     "msg": "Order '{orderId}' not found."},
    {"code": 10009, "name": "SUBACCOUNT_NOT_FOUND", "category": NOT_FOUND,
     "msg": "Subaccount not found."},
    {"code": 10010, "name": "RATE_LIMIT_EXCEEDED", "category": RATE_LIMIT,
     "msg": "Rate limit exceeded. Retry after {retryAfter} seconds."},
    {"code": 10011, "name": "INTERNAL_ERROR", "category": INTERNAL,
     "msg": "An unexpected internal error occurred."},
    {"code": 10012, "name": "SERVICE_UNAVAILABLE", "category": INTERNAL,
     "msg": "Service temporarily unavailable. Please try again later."},
    {"code": 10013, "name": "CONFLICT", "category": CONFLICT,
     "msg": "The requested operation conflicts with the current state of the resource."},
    {"code": 10014, "name": "INVALID_ADDRESS", "category": VALIDATION,
     "msg": "The provided subaccount or wallet address is not valid."},
    {"code": 10015, "name": "CHANNEL_NOT_FOUND", "category": NOT_FOUND,
     "msg": "Channel '{channel}' does not exist."},
)


def _build_api_registry() -> dict[int, APIErrorCode]:
    registry: dict[int, APIErrorCode] = {}
    for entry in _API_ENTRIES:
        entry_dict = entry
        registry[int(entry_dict["code"])] = APIErrorCode(
            code=int(entry_dict["code"]),
            name=str(entry_dict["name"]),
            msg=str(entry_dict["msg"]),
            category=str(entry_dict["category"]),
        )
    return registry


API_ERROR_CODES: Final = _build_api_registry()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format_msg(template: str, **kwargs: object) -> str:
    """Render a message template, substituting ``{key}`` placeholders.

    Raises ``KeyError`` if a placeholder is missing from ``kwargs`` — this is
    intentional so callers fix the upstream code rather than silently ship
    half-formatted strings.
    """
    return template.format(**kwargs)


def lookup_chain_error(code: str) -> ChainErrorCode | None:
    """Look up a chain error by its canonical ``"<pallet>_<error>"`` code."""
    return CHAIN_ERROR_CODES.get(code)


def lookup_api_error(code: int) -> APIErrorCode | None:
    """Look up an API error by its integer code."""
    return API_ERROR_CODES.get(code)
