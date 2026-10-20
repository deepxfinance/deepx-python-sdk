from __future__ import annotations

from dataclasses import dataclass
from typing import Any


__all__ = [
    "ActiveOrderInfo",
    "CancelOrderResult",
    "DelegateInfo",
    "LendingAssetPoolState",
    "LendingMarketState",
    "MarketSpec",
    "ModifyOrderResult",
    "OraclePriceInfo",
    "OrderResult",
    "PerpLiquidationFeeRate",
    "PerpLiquidationSpec",
    "PerpMarketInfo",
    "PerpOrderInfo",
    "PerpOrderSpec",
    "PerpPositionInfo",
    "PerpPriceBounds",
    "PlaceOrderResult",
    "PositionUpdatedResult",
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
    "SystemAccountInfo",
    "TotalCollateralAndMarginInfo",
    "TxConfig",
    "TxResult",
]


@dataclass
class TxConfig:
    chain_id: int | None = None
    gas_limit: int | None = None
    max_fee_per_gas: int | None = None
    max_priority_fee_per_gas: int | None = None
    use_legacy: bool | None = None
    nonce_ms: int | None = None
    nonce: int | None = None
    wait_for_finalized: bool | None = None
    timeout_ms: int | None = None


@dataclass
class PerpPriceBounds:
    mark_price: int
    lower: int
    upper: int
    max_deviation_bps: int
    base_decimal: int
    tick_size: int
    step_size: int
    min_order_size: int
    min_notional: int | None = None

    @property
    def min_qty(self) -> int:
        return self.min_order_size


@dataclass
class OrderResult:
    order_id: int
    tx_hash: str
    extrinsic_hash: str


@dataclass
class PlaceOrderResult(OrderResult):
    pass


@dataclass
class CancelOrderResult(OrderResult):
    pass


@dataclass
class SpotPlaceOrderResult(OrderResult):
    pass


@dataclass
class SpotCancelOrderResult(OrderResult):
    pass


@dataclass
class ModifyOrderResult(OrderResult):
    # `order_id` is the NEW order id; `canceled_order_id` is the old one.
    # When a modify degrades to cancel-only (new_total_quantity == filled),
    # both carry the old order id and no new order exists.
    canceled_order_id: int = 0


@dataclass
class TxResult:
    tx_hash: str
    event: dict[str, Any] | None = None


@dataclass
class PositionUpdatedResult:
    tx_hash: str
    extrinsic_hash: str
    fields: dict


@dataclass
class MarketSpec:
    min_order_size: int
    tick_size: int
    step_size: int
    min_notional: int | None = None

    @property
    def min_qty(self) -> int:
        return self.min_order_size


@dataclass
class PerpOrderSpec(MarketSpec):
    pass


@dataclass
class PerpLiquidationFeeRate:
    liquidator_share_fee_rate: int
    insurance_fund_share_fee_rate: int


@dataclass
class PerpLiquidationSpec:
    liquidation_duration: int
    liquidity_bucket_slippage_step: int
    liquidity_bucket_slippage_limit: int
    liquidity_dust_value: int
    liquidation_fee_rate: PerpLiquidationFeeRate


@dataclass
class PerpMarketInfo:
    id: int
    name: str
    base_symbol: str
    base_decimal: int
    quote_market_id: int
    network: str
    height: int
    funding_rate: int
    last_cacl_funding_rate_time: int
    oracle_price: int
    mark_price: int
    max_deviation_bps: int
    maintenance_margin_ratio: int
    taker_fee_rate: int
    maker_fee_rate: int
    order_spec: PerpOrderSpec
    open_interest: int
    long_open_pos_num: int
    short_open_pos_num: int
    base_interest_rate: int
    impact_margin_value: int
    funding_rate_clamp_upper_bound: int
    funding_rate_clamp_lower_bound: int
    base_address: str | None = None
    quote_symbol: str | None = None
    quote_address: str | None = None
    quote_decimal: int | None = None
    initial_margin_ratio: int | None = None
    max_active_orders: int | None = None
    is_quote_market: bool | None = None
    liquidation_spec: PerpLiquidationSpec | None = None
    cumulative_funding_index: int | None = None  # only present on newer precompiles


@dataclass
class ActiveOrderInfo:
    owner: str
    market_id: int
    order_side: int
    order_type: int
    order_id: int
    price: int
    created_at: int


@dataclass
class PerpOrderInfo:
    order_id: int
    owner: str
    market_id: int
    is_long: bool
    size: int
    price: int
    order_type: int
    create_time: int
    leverage: int
    slippage: int
    status: int
    size_filled: int
    size_remain: int
    take_profit: int
    stop_loss: int


@dataclass
class TotalCollateralAndMarginInfo:
    collateral: int
    margin_required: int


@dataclass
class OraclePriceInfo:
    symbol: str
    price: int


@dataclass
class PerpPositionInfo:
    market_id: int
    is_long: bool
    base_asset_amount: int
    entry_price: int
    leverage: int
    last_funding_rate: int
    version: int
    realized_pnl: int
    funding_payment: int
    owner: str
    take_profit: int
    stop_loss: int
    liquidate_price: int
    last_settle_price: int | None = None  # only present once the precompile exposes it


@dataclass
class SettlePnlResult:
    tx_hash: str
    extrinsic_hash: str
    market_id: int
    unrealized: int
    funding: int
    total: int


@dataclass
class SpotOrderInfo:
    pair: str
    id: int
    maker: str
    price: int
    quote_amount: int
    base_amount: int
    create_time: int
    status: int
    is_buy: bool
    order_type: int
    slippage: int


@dataclass
class SpotMarketSpec(MarketSpec):
    pass


@dataclass
class SubaccountSpotPosition:
    symbol: str
    token_amount: int


@dataclass
class SubaccountBorrowPosition:
    lending_market_id: int
    asset: str
    amount: int
    interest: int


@dataclass
class DelegateInfo:
    delegate_address: str
    delegate_name: str
    valid_until: int
    # Chain runtime 190: delegates are wallet-level and carry a mode
    # (DelegateMode: 0=PlaceOrCancelOrder, 3=Disable; 1/2 are disabled
    # on-chain since runtime 194). Defaults keep pre-190 3-field decodes
    # working.
    mode: int = 0
    create_time: int = 0


@dataclass
class SubaccountInfo:
    authority: str
    delegate: str
    name: str
    spot_positions: list[SubaccountSpotPosition]
    borrow_positions: list[SubaccountBorrowPosition]
    next_order_id: int
    status: int
    is_margin_trading_enabled: bool
    address: str | None = None
    liquidation_start_at: int | None = None
    next_liquidation_id: int | None = None
    margin_strategy: int | None = None
    # populated by the delegates-vec precompile layout; `delegate` is the
    # legacy single-address field (empty on the new layout)
    delegates: list[DelegateInfo] | None = None


@dataclass
class SubaccountSummary:
    subaccount: str
    name: str


@dataclass
class SubaccountUserStats:
    subaccounts: list[SubaccountSummary]
    if_staked_quote_asset_amount: int
    number_of_sub_accounts: int
    number_of_sub_accounts_created: int


@dataclass
class SystemAccountInfo:
    nonce: int
    update: int
    time_nonce: list[int]
    quota: int
    is_exist: bool


@dataclass
class LendingMarketState:
    market_id: int
    market_name: str
    liquidation_bonus: int


@dataclass
class LendingAssetPoolState:
    market_id: int
    asset: str
    decimal: int
    total_deposits: int
    total_borrows: int
    cumulative_deposit_interest: int
    cumulative_borrow_interest: int
    last_updated_slot: int
    reserve_factor: int
    custom_liquidation_bonus: int
    initial_asset_weight: int
    maintenance_asset_weight: int
    initial_borrow_weight: int
    maintenance_borrow_weight: int
    apr_borrow: int
    apr_lend: int
    protocol_reserve: int
    supply_cap: int
    borrow_cap: int
