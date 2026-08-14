from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Optional

from ._perp_market import (
    cancel_perp_order,
    close_position,
    close_position_limit,
    close_position_market,
    modify_perp_order,
    place_perp_order,
    place_perp_order_ioc,
    place_perp_order_limit,
    place_perp_order_market,
    set_global_leverage,
    set_per_market_leverage,
    set_profit_and_loss_point,
    settle_pnl,
)
from ._lending import (
    asset_pools,
    borrow,
    borrow_and_swap,
    borrow_and_swap_btc,
    borrow_and_swap_evm,
    bridge_invoke,
    buy_quota,
    deposit,
    deposit_from_subaccount,
    health_for,
    lending_markets,
    max_borrow_amount_for,
    max_withdraw_amount_for,
    repay,
    withdraw,
    withdraw_and_swap,
    withdraw_and_swap_btc,
    withdraw_and_swap_evm,
)
from ._perp_market import (
    active_pos_for_market,
    effective_leverage_for,
    free_deposit_for,
    get_liquidate_price,
    get_oracle_price_all,
    global_max_leverage_for,
    last_trade_price_for,
    mark_price_for,
    order_info,
    per_market_max_leverage_for,
    perp_markets,
    total_collateral_and_margin_required_for,
    user_active_orders,
    user_perp_positions,
)
from ._spot_market import (
    modify_spot_order,
    subaccount_cancel_order_buy_b,
    subaccount_cancel_order_sell_b,
    subaccount_place_market_order_buy_b_with_price,
    subaccount_place_market_order_buy_b_without_price,
    subaccount_place_market_order_sell_b_with_price,
    subaccount_place_market_order_sell_b_without_price,
    subaccount_place_order_buy_b,
    subaccount_place_order_buy_ioc_b,
    subaccount_place_order_sell_b,
    subaccount_place_order_sell_ioc_b,
    get_spot_market_spec,
    user_active_spot_orders,
)
from ._subaccount import (
    delegate_accounts_for,
    delegator_accounts_for,
    delete_subaccount,
    initialize_subaccount,
    liquidate_by_market,
    liquidate_perp_by_transfer,
    liquidate_spot_by_transfer,
    no_op,
    rename_subaccount,
    remove_delegate_account,
    set_delegate_account,
    set_spot_margin,
    subaccount_info,
    update_delegate_mode,
    user_stats,
)
from ._system import system_account
from ._substrate import get_perp_price_bounds
from ._market_resolver import MarketResolver
from ._network import (
    network_config,
    normalize_net,
    resolve_net,
    resolve_ordered_endpoints,
    resolve_substrate_ws_endpoints,
)
from ._rpc_transport import (
    DEFAULT_USER_AGENT,
    RpcEndpointPool,
    use_evm_rpc_config,
    use_substrate_ws_config,
)
from ._tx_config import merge_tx_config_kwargs
from ._types import (
    ActiveOrderInfo,
    CancelOrderResult,
    OraclePriceInfo,
    PerpMarketInfo,
    PerpOrderInfo,
    PerpPositionInfo,
    PerpPriceBounds,
    PlaceOrderResult,
    PositionUpdatedResult,
    SettlePnlResult,
    SpotCancelOrderResult,
    SpotMarketSpec,
    SpotOrderInfo,
    SpotPlaceOrderResult,
    SubaccountInfo,
    SubaccountSummary,
    SubaccountUserStats,
    SystemAccountInfo,
    LendingAssetPoolState,
    LendingMarketState,
    ModifyOrderResult,
    TotalCollateralAndMarginInfo,
    TxResult,
)

_DEFAULT_PERP_PRECOMPILE = "0x000000000000000000000000000000000000044E"
_DEFAULT_SPOT_PRECOMPILE = "0x000000000000000000000000000000000000044D"
_DEFAULT_LENDING_PRECOMPILE = "0x0000000000000000000000000000000000000450"
_DEFAULT_SUBACCOUNT_PRECOMPILE = "0x0000000000000000000000000000000000000451"
_DEFAULT_SYSTEM_PRECOMPILE = "0x0000000000000000000000000000000000000452"


def _normalize_optional_str(value: Optional[str]) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _resolve_precompile_address(
    override: Optional[str],
    module_value: str,
    default_value: str,
) -> str:
    resolved_override = _normalize_optional_str(override)
    if resolved_override:
        return resolved_override
    if module_value:
        return module_value
    return default_value


def _normalize_perp_side(side: str | bool) -> bool:
    if isinstance(side, bool):
        return side
    value = str(side).strip().lower()
    if value in {"buy", "long", "bid"}:
        return True
    if value in {"sell", "short", "ask"}:
        return False
    raise ValueError("side must be buy/long or sell/short")


def _normalize_spot_side(side: str | bool) -> str:
    if isinstance(side, bool):
        return "buy" if side else "sell"
    value = str(side).strip().lower()
    if value in {"buy", "bid"}:
        return "buy"
    if value in {"sell", "ask"}:
        return "sell"
    raise ValueError("side must be buy or sell")


def _normalize_order_type(order_type: str | int) -> str:
    if isinstance(order_type, int):
        mapping = {0: "limit", 1: "market", 2: "stop", 3: "ioc"}
        try:
            return mapping[int(order_type)]
        except KeyError as exc:
            raise ValueError(f"invalid order_type: {order_type}") from exc
    value = str(order_type).strip().lower().replace("_", "-")
    aliases = {
        "limit": "limit",
        "l": "limit",
        "market": "market",
        "m": "market",
        "stop": "stop",
        "ioc": "ioc",
        "i": "ioc",
    }
    try:
        return aliases[value]
    except KeyError as exc:
        raise ValueError("order_type must be limit, market, stop, or ioc") from exc


@dataclass
class ChainClient:
    substrate_ws: str = ""
    evm_rpc_url: str = ""
    private_key: str = ""
    perp_precompile_address: str = ""
    spot_precompile_address: str = ""
    lending_precompile_address: str = ""
    subaccount_precompile_address: str = ""
    system_precompile_address: str = ""
    subaccount: str = ""
    api_base_url: Optional[str] = None
    api_client: Any = None
    net: Optional[str] = None
    chain_id: Optional[int] = None
    gas_limit: Optional[int] = None
    max_fee_per_gas: Optional[int] = None
    max_priority_fee_per_gas: Optional[int] = None
    use_legacy: bool = False
    nonce_ms: Optional[int] = None
    evm_rpc_user_agent: str = DEFAULT_USER_AGENT
    evm_rpc_headers: Optional[dict[str, str]] = None
    evm_rpc_timeout: Optional[float] = None
    wait_for_finalized: bool = True
    print_state: bool = False
    max_completed_transactions: int = 10_000
    max_resolved_blocks: int = 256
    node_pool_limit_per_account: int = 50
    max_pool_transactions_per_account: int = 48
    priority_pool_reserve: int = 2
    substrate_ws_endpoints: Sequence[str] | None = None
    recovery_substrate_ws_endpoints: Sequence[str] | None = None
    evm_rpc_endpoints: Sequence[str] | None = None
    market: "MarketClient" = field(init=False, repr=False)
    perp_market: "PerpMarketClient" = field(init=False, repr=False)
    spot_market: "SpotMarketClient" = field(init=False, repr=False)
    subaccount_client: "SubaccountClient" = field(init=False, repr=False)
    system: "SystemClient" = field(init=False, repr=False)
    lending: "LendingClient" = field(init=False, repr=False)
    _market_resolver: MarketResolver = field(init=False, repr=False)
    _ticket_runtime: Any = field(init=False, default=None, repr=False)
    _ticket_runtime_lock: threading.Lock = field(
        init=False,
        default_factory=threading.Lock,
        repr=False,
    )
    _ticket_closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.max_completed_transactions = int(self.max_completed_transactions)
        self.max_resolved_blocks = int(self.max_resolved_blocks)
        self.node_pool_limit_per_account = int(self.node_pool_limit_per_account)
        self.max_pool_transactions_per_account = int(
            self.max_pool_transactions_per_account
        )
        self.priority_pool_reserve = int(self.priority_pool_reserve)
        if self.max_completed_transactions < 0:
            raise ValueError("max_completed_transactions must be non-negative")
        if self.max_resolved_blocks <= 0:
            raise ValueError("max_resolved_blocks must be positive")
        if self.node_pool_limit_per_account <= 0:
            raise ValueError("node_pool_limit_per_account must be positive")
        if self.max_pool_transactions_per_account <= 0:
            raise ValueError(
                "max_pool_transactions_per_account must be positive"
            )
        if self.priority_pool_reserve < 0:
            raise ValueError("priority_pool_reserve must be non-negative")
        if (
            self.max_pool_transactions_per_account + self.priority_pool_reserve
            > self.node_pool_limit_per_account
        ):
            raise ValueError(
                "max_pool_transactions_per_account "
                f"({self.max_pool_transactions_per_account}) + "
                f"priority_pool_reserve ({self.priority_pool_reserve}) exceeds "
                "node_pool_limit_per_account "
                f"({self.node_pool_limit_per_account})"
            )
        resolved_net = resolve_net(self.net)
        config = network_config(resolved_net)
        self.net = resolved_net

        self.evm_rpc_endpoints = resolve_ordered_endpoints(
            self.evm_rpc_url,
            self.evm_rpc_endpoints,
            default=config.evm_rpc_url,
            name="evm_rpc_endpoints",
        )
        self.evm_rpc_url = self.evm_rpc_endpoints[0]
        self._evm_rpc_pool = RpcEndpointPool(tuple(self.evm_rpc_endpoints))

        self.substrate_ws_endpoints = resolve_substrate_ws_endpoints(
            self.substrate_ws,
            self.substrate_ws_endpoints,
            default=config.substrate_ws,
        )
        self.substrate_ws = self.substrate_ws_endpoints[0]
        self.recovery_substrate_ws_endpoints = resolve_substrate_ws_endpoints(
            "",
            self.recovery_substrate_ws_endpoints,
            default=self.substrate_ws,
        )
        self._substrate_rpc_pool = RpcEndpointPool(
            tuple(self.substrate_ws_endpoints)
        )

        self.perp_precompile_address = _normalize_optional_str(self.perp_precompile_address)
        self.spot_precompile_address = _normalize_optional_str(self.spot_precompile_address)
        self.lending_precompile_address = _normalize_optional_str(self.lending_precompile_address)
        self.subaccount_precompile_address = _normalize_optional_str(
            self.subaccount_precompile_address
        )
        self.system_precompile_address = _normalize_optional_str(self.system_precompile_address)
        self.api_base_url = _normalize_optional_str(self.api_base_url)

        self._market_resolver = MarketResolver(
            net=self.net,
            api_base_url=self.api_base_url,
            api_client=self.api_client,
        )

        self.market = MarketClient(self)
        self.perp_market = PerpMarketClient(self)
        self.spot_market = SpotMarketClient(self)
        self.subaccount_client = SubaccountClient(self)
        self.system = SystemClient(self)
        self.lending = LendingClient(self)

    def __enter__(self) -> "ChainClient":
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._ticket_runtime_lock:
            if self._ticket_closed:
                return
            self._ticket_closed = True
            runtime = self._ticket_runtime
        if runtime is not None:
            runtime.close()

    @property
    def active_rpc_endpoint(self) -> str:
        with self._ticket_runtime_lock:
            runtime = self._ticket_runtime
        if runtime is None:
            return self._substrate_rpc_pool.active_display
        return runtime.active_rpc_endpoint

    @property
    def active_evm_rpc_endpoint(self) -> str:
        return self._evm_rpc_pool.active_display

    def _get_ticket_runtime(self) -> Any:
        with self._ticket_runtime_lock:
            if self._ticket_closed:
                raise RuntimeError(
                    "ChainClient is closed and cannot submit transactions."
                )
            if self._ticket_runtime is None:
                from ._sync_ticket import _SyncTicketRuntime

                self._ticket_runtime = _SyncTicketRuntime.from_chain_client(self)
            return self._ticket_runtime

    def preload_markets(self) -> None:
        self._market_resolver.preload()

    def refresh_markets(self) -> None:
        self._market_resolver.refresh()

    def _resolve_perp_precompile(self, override: Optional[str] = None) -> str:
        return _resolve_precompile_address(
            override,
            self.perp_precompile_address,
            _DEFAULT_PERP_PRECOMPILE,
        )

    def _resolve_spot_precompile(self, override: Optional[str] = None) -> str:
        return _resolve_precompile_address(
            override,
            self.spot_precompile_address,
            _DEFAULT_SPOT_PRECOMPILE,
        )

    def _resolve_lending_precompile(self, override: Optional[str] = None) -> str:
        return _resolve_precompile_address(
            override,
            self.lending_precompile_address,
            _DEFAULT_LENDING_PRECOMPILE,
        )

    def _resolve_subaccount_precompile(self, override: Optional[str] = None) -> str:
        return _resolve_precompile_address(
            override,
            self.subaccount_precompile_address,
            _DEFAULT_SUBACCOUNT_PRECOMPILE,
        )

    def _resolve_system_precompile(self, override: Optional[str] = None) -> str:
        return _resolve_precompile_address(
            override,
            self.system_precompile_address,
            _DEFAULT_SYSTEM_PRECOMPILE,
        )

    def _resolve_perp_market_id(
        self,
        *,
        market_id: Optional[int],
        symbol: Optional[str],
    ) -> int:
        if market_id is not None:
            return int(market_id)
        if symbol is None or str(symbol).strip() == "":
            raise ValueError("market_id or symbol is required")
        return self._market_resolver.resolve_perp_market_id(symbol)

    def _resolve_spot_pair(
        self,
        *,
        pair: Optional[str],
        symbol: Optional[str],
    ) -> str:
        if pair is not None and str(pair).strip() != "":
            return str(pair)
        if symbol is None or str(symbol).strip() == "":
            raise ValueError("pair or symbol is required")
        return self._market_resolver.resolve_spot_pair(symbol)

    def _resolve_lending_asset(
        self,
        *,
        asset: str | bytes | None,
        symbol: str | bytes | None,
    ) -> str | bytes:
        if asset is not None:
            return asset
        if symbol is None or (isinstance(symbol, str) and symbol.strip() == ""):
            raise ValueError("asset or symbol is required")
        return self._market_resolver.resolve_lending_asset(symbol)

    def _tx_kwargs(
        self,
        *,
        chain_id: Optional[int],
        gas_limit: Optional[int],
        max_fee_per_gas: Optional[int],
        max_priority_fee_per_gas: Optional[int],
        use_legacy: Optional[bool],
        wait_for_finalized: Optional[bool],
        timeout_ms: Optional[int],
        nonce_ms: Optional[int] | None = None,
        nonce: Optional[int] | None = None,
        use_nonce_ms: bool = False,
        use_nonce: bool = False,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "chain_id": chain_id if chain_id is not None else self.chain_id,
            "gas_limit": gas_limit if gas_limit is not None else self.gas_limit,
            "max_fee_per_gas": (
                max_fee_per_gas if max_fee_per_gas is not None else self.max_fee_per_gas
            ),
            "max_priority_fee_per_gas": (
                max_priority_fee_per_gas
                if max_priority_fee_per_gas is not None
                else self.max_priority_fee_per_gas
            ),
            "use_legacy": self.use_legacy if use_legacy is None else use_legacy,
            "wait_for_finalized": (
                self.wait_for_finalized if wait_for_finalized is None else wait_for_finalized
            ),
            "timeout_ms": timeout_ms,
        }
        if use_nonce_ms:
            kwargs["nonce_ms"] = nonce_ms
        if use_nonce:
            kwargs["nonce"] = nonce
        return kwargs


def _evm_transport_method(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        kwargs = merge_tx_config_kwargs(method, kwargs)
        client = self._client
        with use_substrate_ws_config(
            endpoint_pool=client._substrate_rpc_pool,
        ):
            with use_evm_rpc_config(
                user_agent=client.evm_rpc_user_agent,
                headers=client.evm_rpc_headers,
                timeout_s=client.evm_rpc_timeout,
                endpoint_pool=client._evm_rpc_pool,
            ):
                return method(self, *args, **kwargs)

    return wrapped


class _EvmTransportClientMixin:
    _client: ChainClient

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name, attr in list(cls.__dict__.items()):
            if name.startswith("_") or not callable(attr):
                continue
            setattr(cls, name, _evm_transport_method(attr))


@dataclass
class MarketClient:
    _client: ChainClient

    def get_perp_price_bounds(self, market_id: int) -> PerpPriceBounds:
        return get_perp_price_bounds(self._client.substrate_ws, market_id)


@dataclass
class PerpMarketClient(_EvmTransportClientMixin):
    _client: ChainClient

    def _precompile_address(self, override: Optional[str]) -> str:
        return self._client._resolve_perp_precompile(override)

    def submit_order(
        self,
        *,
        side: str | bool,
        size: int,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        order_type: str | int = "limit",
        price: Optional[int] = None,
        slippage: Optional[int] = None,
        take_profit: Optional[int] = None,
        stop_loss: Optional[int] = None,
        reduce_only: bool = False,
        post_only: int = 0,
        cloid: Optional[int] = None,
        nonce_ms: Optional[int] = None,
    ) -> "SyncTransactionTicket[PlaceOrderResult]":
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        runtime = self._client._get_ticket_runtime()
        return runtime.submit(
            lambda client: client.perp_market.place_order(
                side=side,
                size=size,
                market_id=resolved_market_id,
                order_type=order_type,
                price=price,
                slippage=slippage,
                take_profit=take_profit,
                stop_loss=stop_loss,
                reduce_only=reduce_only,
                post_only=post_only,
                cloid=cloid,
                nonce_ms=nonce_ms,
            )
        )

    def submit_cancel(
        self,
        *,
        order_id: int,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        fast_cancel: bool = False,
        nonce_ms: Optional[int] = None,
    ) -> "SyncTransactionTicket[CancelOrderResult]":
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        runtime = self._client._get_ticket_runtime()
        return runtime.submit(
            lambda client: client.perp_market.cancel_order(
                market_id=resolved_market_id,
                order_id=order_id,
                fast_cancel=fast_cancel,
                nonce_ms=nonce_ms,
            )
        )

    def place_order(
        self,
        *,
        side: str | bool,
        size: int,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        order_type: str | int = "limit",
        price: Optional[int] = None,
        slippage: Optional[int] = None,
        take_profit: Optional[int] = None,
        stop_loss: Optional[int] = None,
        reduce_only: bool = False,
        post_only: int = 0,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> PlaceOrderResult:
        is_long = _normalize_perp_side(side)
        normalized_order_type = _normalize_order_type(order_type)
        if normalized_order_type == "limit":
            if price is None:
                raise ValueError("price is required for limit orders")
            return self.place_perp_order_limit(
                market_id=market_id,
                symbol=symbol,
                is_long=is_long,
                size=size,
                price=price,
                take_profit=take_profit,
                stop_loss=stop_loss,
                reduce_only=reduce_only,
                post_only=post_only,
                cloid=cloid,
                precompile_address=precompile_address,
                subaccount=subaccount,
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            )
        if normalized_order_type == "market":
            if take_profit is not None or stop_loss is not None or post_only:
                return self.place_perp_order(
                    market_id=market_id,
                    symbol=symbol,
                    is_long=is_long,
                    size=size,
                    price=0 if price is None else price,
                    order_type=1,
                    slippage=slippage,
                    take_profit=take_profit,
                    stop_loss=stop_loss,
                    reduce_only=reduce_only,
                    post_only=post_only,
                    cloid=cloid,
                    precompile_address=precompile_address,
                    subaccount=subaccount,
                    chain_id=chain_id,
                    gas_limit=gas_limit,
                    max_fee_per_gas=max_fee_per_gas,
                    max_priority_fee_per_gas=max_priority_fee_per_gas,
                    use_legacy=use_legacy,
                    nonce_ms=nonce_ms,
                    wait_for_finalized=wait_for_finalized,
                    timeout_ms=timeout_ms,
                )
            return self.place_perp_order_market(
                market_id=market_id,
                symbol=symbol,
                is_long=is_long,
                size=size,
                slippage=slippage,
                reduce_only=reduce_only,
                cloid=cloid,
                precompile_address=precompile_address,
                subaccount=subaccount,
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            )
        if normalized_order_type == "ioc":
            if price is None:
                raise ValueError("price is required for ioc orders")
            return self.place_perp_order_ioc(
                market_id=market_id,
                symbol=symbol,
                is_long=is_long,
                size=size,
                price=price,
                reduce_only=reduce_only,
                cloid=cloid,
                precompile_address=precompile_address,
                subaccount=subaccount,
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            )
        if price is None:
            raise ValueError("price is required for stop orders")
        return self.place_perp_order(
            market_id=market_id,
            symbol=symbol,
            is_long=is_long,
            size=size,
            price=price,
            order_type=2,
            take_profit=take_profit,
            stop_loss=stop_loss,
            reduce_only=reduce_only,
            post_only=post_only,
            cloid=cloid,
            precompile_address=precompile_address,
            subaccount=subaccount,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce_ms=nonce_ms,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )

    def place_perp_order_limit(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        is_long: bool,
        size: int,
        price: int,
        take_profit: Optional[int] = None,
        stop_loss: Optional[int] = None,
        reduce_only: bool = False,
        post_only: int = 0,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> PlaceOrderResult:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return place_perp_order_limit(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            is_long=is_long,
            size=size,
            price=price,
            take_profit=take_profit,
            stop_loss=stop_loss,
            reduce_only=reduce_only,
            post_only=post_only,
            cloid=cloid,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def place_perp_order_ioc(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        is_long: bool,
        size: int,
        price: int,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> PlaceOrderResult:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return place_perp_order_ioc(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            is_long=is_long,
            size=size,
            price=price,
            reduce_only=reduce_only,
            cloid=cloid,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def place_perp_order_market(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        is_long: bool,
        size: int,
        slippage: Optional[int] = None,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> PlaceOrderResult:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return place_perp_order_market(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            is_long=is_long,
            size=size,
            slippage=slippage,
            reduce_only=reduce_only,
            cloid=cloid,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def place_perp_order(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        is_long: bool,
        size: int,
        price: int,
        order_type: int,
        slippage: Optional[int] = None,
        cloid: Optional[int] = None,
        take_profit: Optional[int] = None,
        stop_loss: Optional[int] = None,
        reduce_only: bool = False,
        post_only: int = 0,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> PlaceOrderResult:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return place_perp_order(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            is_long=is_long,
            size=size,
            price=price,
            order_type=order_type,
            slippage=slippage,
            cloid=cloid,
            take_profit=take_profit,
            stop_loss=stop_loss,
            reduce_only=reduce_only,
            post_only=post_only,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def cancel_order(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        order_id: int,
        fast_cancel: bool = False,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> CancelOrderResult:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return cancel_perp_order(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            order_id=order_id,
            fast_cancel=fast_cancel,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def settle_pnl(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SettlePnlResult | TxResult:
        """Settle unrealized PnL + pending funding into USDC deposit/borrow.

        Permissionless: any signer may settle any subaccount's position.
        Pass ``market_id``/``symbol`` for one market (returns ``SettlePnlResult``
        from the SettlePnl event); omit both to settle all markets (returns
        ``TxResult``, inclusion only).
        """
        resolved_market_id = (
            self._client._resolve_perp_market_id(market_id=market_id, symbol=symbol)
            if market_id is not None or (symbol is not None and str(symbol).strip() != "")
            else None
        )
        return settle_pnl(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def modify_order(
        self,
        *,
        order_id: int,
        is_long: bool,
        price: int,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        size: Optional[int] = None,
        new_total_quantity: Optional[int] = None,
        order_type: int = 0,
        slippage: Optional[int] = None,
        take_profit: Optional[int] = None,
        stop_loss: Optional[int] = None,
        reduce_only: bool = False,
        post_only: int = 0,
        cloid: Optional[int] = None,
        fast_cancel: bool = False,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> ModifyOrderResult:
        """Atomically cancel ``order_id`` and place a new order (single tx).

        The new order is a fresh order: all params are explicit. Pass either
        ``size`` (remaining size of the new order) or ``new_total_quantity``
        (total incl. the filled part; degrades to cancel-only when equal to
        the filled amount, rejected when smaller).
        """
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return modify_perp_order(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            order_id=order_id,
            is_long=is_long,
            price=price,
            size=size,
            new_total_quantity=new_total_quantity,
            order_type=order_type,
            slippage=slippage,
            take_profit=take_profit,
            stop_loss=stop_loss,
            reduce_only=reduce_only,
            post_only=post_only,
            cloid=cloid,
            fast_cancel=fast_cancel,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def close_position_limit(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        price: int,
        slippage: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> PlaceOrderResult:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return close_position_limit(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            price=price,
            slippage=slippage,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def close_position(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        price: Optional[int] = None,
        slippage: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> PlaceOrderResult:
        if price is None:
            return self.close_position_market(
                market_id=market_id,
                symbol=symbol,
                slippage=slippage,
                precompile_address=precompile_address,
                subaccount=subaccount,
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            )
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return close_position(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            price=price,
            slippage=slippage,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def close_position_market(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        slippage: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> PlaceOrderResult:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return close_position_market(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            slippage=slippage,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def set_profit_and_loss_point(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        take_profit_point: Optional[int] = None,
        stop_loss_point: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> PositionUpdatedResult:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return set_profit_and_loss_point(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            take_profit_point=take_profit_point,
            stop_loss_point=stop_loss_point,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def set_global_leverage(
        self,
        *,
        max_leverage: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return set_global_leverage(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            max_leverage=max_leverage,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def set_per_market_leverage(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        max_leverage: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return set_per_market_leverage(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
            max_leverage=max_leverage,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def perp_markets(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        precompile_address: Optional[str] = None,
    ) -> PerpMarketInfo:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return perp_markets(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            market_id=resolved_market_id,
        )

    def user_perp_positions(
        self,
        *,
        user: str,
        market_ids: Optional[list[int]] = None,
        symbols: Optional[list[str]] = None,
        precompile_address: Optional[str] = None,
    ) -> list[PerpPositionInfo]:
        if market_ids is None:
            if symbols is None:
                raise ValueError("market_ids or symbols is required")
            market_ids = [
                self._client._resolve_perp_market_id(market_id=None, symbol=symbol)
                for symbol in symbols
            ]
        return user_perp_positions(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            user=user,
            market_ids=market_ids,
        )

    def active_pos_for_market(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        precompile_address: Optional[str] = None,
    ) -> list[PerpPositionInfo]:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return active_pos_for_market(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            market_id=resolved_market_id,
        )

    def user_active_orders(
        self,
        *,
        user: str,
        precompile_address: Optional[str] = None,
    ) -> list[ActiveOrderInfo]:
        return user_active_orders(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            user=user,
        )

    def order_info(
        self,
        *,
        user: str,
        order_id: int,
        precompile_address: Optional[str] = None,
    ) -> PerpOrderInfo:
        return order_info(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            user=user,
            order_id=order_id,
        )

    def free_deposit_for(
        self,
        *,
        account: str,
        precompile_address: Optional[str] = None,
    ) -> int:
        return free_deposit_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            account=account,
        )

    def mark_price_for(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        precompile_address: Optional[str] = None,
    ) -> int:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return mark_price_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            market_id=resolved_market_id,
        )

    def global_max_leverage_for(
        self,
        *,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
    ) -> int:
        """Global leverage cap of the subaccount, scaled x1000 (10x = 10000)."""
        return global_max_leverage_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
        )

    def per_market_max_leverage_for(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
    ) -> int:
        """Per-market leverage override, scaled x1000; 0 means no override."""
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return per_market_max_leverage_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
        )

    def effective_leverage_for(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
    ) -> int:
        """min(global, per-market override or global), scaled x1000."""
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return effective_leverage_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            market_id=resolved_market_id,
        )

    def last_trade_price_for(
        self,
        *,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        precompile_address: Optional[str] = None,
    ) -> int:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return last_trade_price_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            market_id=resolved_market_id,
        )

    def total_collateral_and_margin_required_for(
        self,
        *,
        account: str,
        direction: int,
        precompile_address: Optional[str] = None,
    ) -> TotalCollateralAndMarginInfo:
        return total_collateral_and_margin_required_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            account=account,
            direction=direction,
        )

    def get_liquidate_price(
        self,
        *,
        account: str,
        market_id: Optional[int] = None,
        symbol: Optional[str] = None,
        precompile_address: Optional[str] = None,
    ) -> int | None:
        resolved_market_id = self._client._resolve_perp_market_id(
            market_id=market_id,
            symbol=symbol,
        )
        return get_liquidate_price(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            account=account,
            market_id=resolved_market_id,
        )

    def get_oracle_price_all(
        self,
        *,
        precompile_address: Optional[str] = None,
    ) -> list[OraclePriceInfo]:
        return get_oracle_price_all(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
        )


@dataclass
class SpotMarketClient(_EvmTransportClientMixin):
    _client: ChainClient

    def _precompile_address(self, override: Optional[str]) -> str:
        return self._client._resolve_spot_precompile(override)

    def submit_order(
        self,
        *,
        side: str | bool,
        quote_amount: int,
        base_amount: int,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        order_type: str | int = "limit",
        slippage: Optional[int] = None,
        post_only: int = 0,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        nonce_ms: Optional[int] = None,
    ) -> "SyncTransactionTicket[SpotPlaceOrderResult]":
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        runtime = self._client._get_ticket_runtime()
        return runtime.submit(
            lambda client: client.spot_market.place_order(
                side=side,
                pair=resolved_pair,
                quote_amount=quote_amount,
                base_amount=base_amount,
                order_type=order_type,
                slippage=slippage,
                post_only=post_only,
                reduce_only=reduce_only,
                cloid=cloid,
                nonce_ms=nonce_ms,
            )
        )

    def submit_cancel(
        self,
        *,
        side: str | bool,
        order_id: int,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        fast_cancel: bool = False,
        nonce_ms: Optional[int] = None,
    ) -> "SyncTransactionTicket[SpotCancelOrderResult]":
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        runtime = self._client._get_ticket_runtime()
        return runtime.submit(
            lambda client: client.spot_market.cancel_order(
                side=side,
                pair=resolved_pair,
                order_id=order_id,
                fast_cancel=fast_cancel,
                nonce_ms=nonce_ms,
            )
        )

    def place_order(
        self,
        *,
        side: str | bool,
        quote_amount: int,
        base_amount: int,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        order_type: str | int = "limit",
        slippage: Optional[int] = None,
        post_only: int = 0,
        reduce_only: bool = False,
        auto_cancel: bool = False,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotPlaceOrderResult:
        normalized_side = _normalize_spot_side(side)
        normalized_order_type = _normalize_order_type(order_type)
        if normalized_order_type == "stop":
            raise ValueError("spot order_type must be limit, market, or ioc")
        if normalized_order_type == "ioc":
            target = (
                self.subaccount_place_order_buy_ioc_b
                if normalized_side == "buy"
                else self.subaccount_place_order_sell_ioc_b
            )
            return target(
                pair=pair,
                symbol=symbol,
                quote_amount=quote_amount,
                base_amount=base_amount,
                reduce_only=reduce_only,
                cloid=cloid,
                precompile_address=precompile_address,
                subaccount=subaccount,
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            )
        if normalized_order_type == "limit":
            target = (
                self.subaccount_place_order_buy_b
                if normalized_side == "buy"
                else self.subaccount_place_order_sell_b
            )
            return target(
                pair=pair,
                symbol=symbol,
                quote_amount=quote_amount,
                base_amount=base_amount,
                post_only=post_only,
                reduce_only=reduce_only,
                slippage=slippage,
                auto_cancel=auto_cancel,
                cloid=cloid,
                precompile_address=precompile_address,
                subaccount=subaccount,
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            )
        if normalized_side == "buy":
            target = (
                self.subaccount_place_market_order_buy_b_without_price
                if slippage is None
                else self.subaccount_place_market_order_buy_b_with_price
            )
        else:
            target = (
                self.subaccount_place_market_order_sell_b_without_price
                if slippage is None
                else self.subaccount_place_market_order_sell_b_with_price
            )
        kwargs: dict[str, Any] = {
            "pair": pair,
            "symbol": symbol,
            "quote_amount": quote_amount,
            "base_amount": base_amount,
            "auto_cancel": auto_cancel,
            "reduce_only": reduce_only,
            "cloid": cloid,
            "precompile_address": precompile_address,
            "subaccount": subaccount,
            "chain_id": chain_id,
            "gas_limit": gas_limit,
            "max_fee_per_gas": max_fee_per_gas,
            "max_priority_fee_per_gas": max_priority_fee_per_gas,
            "use_legacy": use_legacy,
            "nonce_ms": nonce_ms,
            "wait_for_finalized": wait_for_finalized,
            "timeout_ms": timeout_ms,
        }
        if slippage is not None:
            kwargs["slippage"] = slippage
        return target(**kwargs)

    def cancel_order(
        self,
        *,
        side: str | bool,
        order_id: int,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        fast_cancel: bool = False,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotCancelOrderResult:
        target = (
            self.subaccount_cancel_order_buy_b
            if _normalize_spot_side(side) == "buy"
            else self.subaccount_cancel_order_sell_b
        )
        return target(
            pair=pair,
            symbol=symbol,
            order_id=order_id,
            fast_cancel=fast_cancel,
            precompile_address=precompile_address,
            subaccount=subaccount,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce_ms=nonce_ms,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )

    def modify_order(
        self,
        *,
        side: str | bool,
        order_id: int,
        quote_amount: int,
        base_amount: int,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        order_type: int = 0,
        slippage: Optional[int] = None,
        post_only: int = 0,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        fast_cancel: bool = False,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> ModifyOrderResult:
        """Atomically cancel ``order_id`` and place a new order (single tx).

        The new order is a fresh order: all params are explicit. ``side``
        ("buy"/"sell") must match the old order's side (it selects which book
        the cancel leg targets).
        """
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return modify_spot_order(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            pair=resolved_pair,
            order_id=order_id,
            is_buy=_normalize_spot_side(side) == "buy",
            quote_amount=quote_amount,
            base_amount=base_amount,
            order_type=order_type,
            slippage=slippage,
            post_only=post_only,
            reduce_only=reduce_only,
            cloid=cloid,
            fast_cancel=fast_cancel,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def subaccount_place_order_buy_b(
        self,
        *,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        quote_amount: int,
        base_amount: int,
        post_only: int = 0,
        reduce_only: bool = False,
        slippage: Optional[int] = None,
        auto_cancel: bool = False,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotPlaceOrderResult:
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return subaccount_place_order_buy_b(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            pair=resolved_pair,
            quote_amount=quote_amount,
            base_amount=base_amount,
            post_only=post_only,
            reduce_only=reduce_only,
            slippage=slippage,
            auto_cancel=auto_cancel,
            cloid=cloid,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def subaccount_place_order_sell_b(
        self,
        *,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        quote_amount: int,
        base_amount: int,
        post_only: int = 0,
        reduce_only: bool = False,
        slippage: Optional[int] = None,
        auto_cancel: bool = False,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotPlaceOrderResult:
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return subaccount_place_order_sell_b(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            pair=resolved_pair,
            quote_amount=quote_amount,
            base_amount=base_amount,
            post_only=post_only,
            reduce_only=reduce_only,
            slippage=slippage,
            auto_cancel=auto_cancel,
            cloid=cloid,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def subaccount_place_order_buy_ioc_b(
        self,
        *,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        quote_amount: int,
        base_amount: int,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotPlaceOrderResult:
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return subaccount_place_order_buy_ioc_b(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            pair=resolved_pair,
            quote_amount=quote_amount,
            base_amount=base_amount,
            reduce_only=reduce_only,
            cloid=cloid,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def subaccount_place_order_sell_ioc_b(
        self,
        *,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        quote_amount: int,
        base_amount: int,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotPlaceOrderResult:
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return subaccount_place_order_sell_ioc_b(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            pair=resolved_pair,
            quote_amount=quote_amount,
            base_amount=base_amount,
            reduce_only=reduce_only,
            cloid=cloid,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def subaccount_place_market_order_buy_b_without_price(
        self,
        *,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        quote_amount: int,
        base_amount: int,
        auto_cancel: bool = False,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotPlaceOrderResult:
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return subaccount_place_market_order_buy_b_without_price(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            pair=resolved_pair,
            quote_amount=quote_amount,
            base_amount=base_amount,
            auto_cancel=auto_cancel,
            reduce_only=reduce_only,
            cloid=cloid,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def subaccount_place_market_order_buy_b_with_price(
        self,
        *,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        quote_amount: int,
        base_amount: int,
        slippage: int,
        auto_cancel: bool = False,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotPlaceOrderResult:
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return subaccount_place_market_order_buy_b_with_price(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            pair=resolved_pair,
            quote_amount=quote_amount,
            base_amount=base_amount,
            slippage=slippage,
            auto_cancel=auto_cancel,
            reduce_only=reduce_only,
            cloid=cloid,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def subaccount_place_market_order_sell_b_without_price(
        self,
        *,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        quote_amount: int,
        base_amount: int,
        auto_cancel: bool = False,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotPlaceOrderResult:
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return subaccount_place_market_order_sell_b_without_price(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            pair=resolved_pair,
            quote_amount=quote_amount,
            base_amount=base_amount,
            auto_cancel=auto_cancel,
            reduce_only=reduce_only,
            cloid=cloid,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def subaccount_place_market_order_sell_b_with_price(
        self,
        *,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        quote_amount: int,
        base_amount: int,
        slippage: int,
        auto_cancel: bool = False,
        reduce_only: bool = False,
        cloid: Optional[int] = None,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotPlaceOrderResult:
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return subaccount_place_market_order_sell_b_with_price(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            pair=resolved_pair,
            quote_amount=quote_amount,
            base_amount=base_amount,
            slippage=slippage,
            auto_cancel=auto_cancel,
            reduce_only=reduce_only,
            cloid=cloid,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def subaccount_cancel_order_buy_b(
        self,
        *,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        order_id: int,
        fast_cancel: bool = False,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotCancelOrderResult:
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return subaccount_cancel_order_buy_b(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            pair=resolved_pair,
            order_id=order_id,
            fast_cancel=fast_cancel,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def subaccount_cancel_order_sell_b(
        self,
        *,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        order_id: int,
        fast_cancel: bool = False,
        precompile_address: Optional[str] = None,
        subaccount: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> SpotCancelOrderResult:
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return subaccount_cancel_order_sell_b(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount or self._client.subaccount,
            pair=resolved_pair,
            order_id=order_id,
            fast_cancel=fast_cancel,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce_ms=nonce_ms,
                use_nonce_ms=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def user_active_spot_orders(
        self,
        *,
        user: str,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        precompile_address: Optional[str] = None,
    ) -> list[SpotOrderInfo]:
        resolved_pair = (
            self._client._resolve_spot_pair(pair=pair, symbol=symbol)
            if pair is not None or symbol is not None
            else None
        )
        return user_active_spot_orders(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            user=user,
            pair=resolved_pair,
        )

    def get_spot_market_spec(
        self,
        *,
        pair: Optional[str] = None,
        symbol: Optional[str] = None,
        precompile_address: Optional[str] = None,
    ) -> SpotMarketSpec:
        resolved_pair = self._client._resolve_spot_pair(pair=pair, symbol=symbol)
        return get_spot_market_spec(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            pair=resolved_pair,
        )


@dataclass
class SubaccountClient(_EvmTransportClientMixin):
    _client: ChainClient

    def _precompile_address(self, override: Optional[str]) -> str:
        return self._client._resolve_subaccount_precompile(override)

    def initialize_subaccount(
        self,
        *,
        name: str | bytes,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return initialize_subaccount(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            name=name,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def delete_subaccount(
        self,
        *,
        subaccount: str,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return delete_subaccount(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def no_op(
        self,
        *,
        precompile_address: Optional[str] = None,
        nonce_ms: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        """Consume a timestamp nonce with no state change.

        Pass the same ``nonce_ms`` as a stuck pending transaction to replace
        it in the mempool (no_op has the highest pool priority); with
        ``nonce_ms=None`` a fresh millisecond timestamp is used.
        """
        return no_op(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            nonce_ms=nonce_ms,
            wait_for_finalized=(
                self._client.wait_for_finalized if wait_for_finalized is None else wait_for_finalized
            ),
            timeout_ms=timeout_ms,
        )

    def set_delegate_account(
        self,
        *,
        delegate: str,
        name: str | bytes,
        valid_until: int,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return set_delegate_account(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            delegate=delegate,
            name=name,
            valid_until=valid_until,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def update_delegate_mode(
        self,
        *,
        delegate: str,
        new_mode: int | str,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return update_delegate_mode(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            delegate=delegate,
            new_mode=new_mode,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def remove_delegate_account(
        self,
        *,
        delegate: str,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return remove_delegate_account(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            delegate=delegate,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def set_spot_margin(
        self,
        *,
        subaccount: str,
        enable_spot_margin: bool,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return set_spot_margin(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount,
            enable_spot_margin=enable_spot_margin,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def rename_subaccount(
        self,
        *,
        subaccount: str,
        new_name: str | bytes,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return rename_subaccount(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount,
            new_name=new_name,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def liquidate_perp_by_transfer(
        self,
        *,
        market_index: int,
        liquidator_max_base_amount: int,
        target_subaccount: str,
        liquidator: str,
        limit_price: Optional[int] = None,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return liquidate_perp_by_transfer(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            market_index=market_index,
            liquidator_max_base_amount=liquidator_max_base_amount,
            target_subaccount=target_subaccount,
            liquidator=liquidator,
            limit_price=limit_price,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def liquidate_spot_by_transfer(
        self,
        *,
        asset_symbol: str | bytes,
        liability_symbol: str | bytes,
        target_account_addr: str,
        liquidator: str,
        liquidator_max_liability_transfer: int,
        lending_market_id: int,
        limit_price: Optional[int] = None,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return liquidate_spot_by_transfer(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            asset_symbol=asset_symbol,
            liability_symbol=liability_symbol,
            target_account_addr=target_account_addr,
            liquidator=liquidator,
            liquidator_max_liability_transfer=liquidator_max_liability_transfer,
            lending_market_id=lending_market_id,
            limit_price=limit_price,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def liquidate_by_market(
        self,
        *,
        target_subaccount: str,
        liquidator: str,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return liquidate_by_market(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            target_subaccount=target_subaccount,
            liquidator=liquidator,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def user_stats(
        self,
        *,
        address: str,
        precompile_address: Optional[str] = None,
    ) -> SubaccountUserStats:
        return user_stats(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            address=address,
        )

    def subaccount_info(
        self,
        *,
        address: str,
        precompile_address: Optional[str] = None,
    ) -> SubaccountInfo:
        return subaccount_info(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            address=address,
        )

    def delegate_accounts_for(
        self,
        *,
        owner: str,
        precompile_address: Optional[str] = None,
    ) -> list[DelegateInfo]:
        return delegate_accounts_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            owner=owner,
        )

    def delegator_accounts_for(
        self,
        *,
        delegate: str,
        precompile_address: Optional[str] = None,
    ) -> list[str]:
        return delegator_accounts_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            delegate=delegate,
        )

@dataclass
class SystemClient(_EvmTransportClientMixin):
    _client: ChainClient

    def _precompile_address(self, override: Optional[str]) -> str:
        return self._client._resolve_system_precompile(override)

    def system_account(
        self,
        *,
        address: str,
        precompile_address: Optional[str] = None,
    ) -> SystemAccountInfo:
        return system_account(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            address=address,
        )


@dataclass
class LendingClient(_EvmTransportClientMixin):
    _client: ChainClient

    def _precompile_address(self, override: Optional[str]) -> str:
        return self._client._resolve_lending_precompile(override)

    def deposit(
        self,
        *,
        subaccount: str,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        amount: int,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        resolved_asset = self._client._resolve_lending_asset(asset=asset, symbol=symbol)
        return deposit(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount,
            asset=resolved_asset,
            amount=amount,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def deposit_from_subaccount(
        self,
        *,
        from_subaccount: str,
        subaccount: str,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        amount: int,
        auto_borrow: bool = False,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        resolved_asset = self._client._resolve_lending_asset(asset=asset, symbol=symbol)
        return deposit_from_subaccount(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            from_subaccount=from_subaccount,
            subaccount=subaccount,
            asset=resolved_asset,
            amount=amount,
            auto_borrow=auto_borrow,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def bridge_invoke(
        self,
        *,
        uid: str,
        amount: int,
        custom_data: str | bytes,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return bridge_invoke(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            uid=uid,
            amount=amount,
            custom_data=custom_data,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def withdraw(
        self,
        *,
        subaccount: str,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        amount: int,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        resolved_asset = self._client._resolve_lending_asset(asset=asset, symbol=symbol)
        return withdraw(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount,
            asset=resolved_asset,
            amount=amount,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def withdraw_and_swap_evm(
        self,
        *,
        subaccount: str,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        amount: int,
        dst_chain_id: int,
        token_id: int,
        dst_recipient: str,
        refund_address: str,
        salt: str,
        custom_data: str | bytes,
        signature: str | bytes,
        consumer_address: str,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        resolved_asset = self._client._resolve_lending_asset(asset=asset, symbol=symbol)
        return withdraw_and_swap_evm(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount,
            asset=resolved_asset,
            amount=amount,
            dst_chain_id=dst_chain_id,
            token_id=token_id,
            dst_recipient=dst_recipient,
            refund_address=refund_address,
            salt=salt,
            custom_data=custom_data,
            signature=signature,
            consumer_address=consumer_address,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def withdraw_and_swap(
        self,
        *,
        subaccount: str,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        amount: int,
        dst_chain_id: int,
        token_id: int,
        dst_recipient: str,
        refund_address: str,
        salt: str,
        custom_data: str | bytes,
        signature: str | bytes,
        consumer_address: str,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return withdraw_and_swap(
            subaccount=subaccount,
            asset=asset,
            symbol=symbol,
            amount=amount,
            dst_chain_id=dst_chain_id,
            token_id=token_id,
            dst_recipient=dst_recipient,
            refund_address=refund_address,
            salt=salt,
            custom_data=custom_data,
            signature=signature,
            consumer_address=consumer_address,
            precompile_address=precompile_address,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce=nonce,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )

    def withdraw_and_swap_btc(
        self,
        *,
        subaccount: str,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        amount: int,
        dst_recipient: str,
        refund_address: str,
        salt: str,
        signature: str | bytes,
        consumer_address: str,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        resolved_asset = self._client._resolve_lending_asset(asset=asset, symbol=symbol)
        return withdraw_and_swap_btc(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount,
            asset=resolved_asset,
            amount=amount,
            dst_recipient=dst_recipient,
            refund_address=refund_address,
            salt=salt,
            signature=signature,
            consumer_address=consumer_address,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def borrow(
        self,
        *,
        borrower: str,
        market_id: int,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        amount: int,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        resolved_asset = self._client._resolve_lending_asset(asset=asset, symbol=symbol)
        return borrow(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            borrower=borrower,
            market_id=market_id,
            asset=resolved_asset,
            amount=amount,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def borrow_and_swap_evm(
        self,
        *,
        borrower: str,
        market_id: int,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        amount: int,
        dst_chain_id: int,
        token_id: int,
        dst_recipient: str,
        refund_address: str,
        salt: str,
        custom_data: str | bytes,
        signature: str | bytes,
        consumer_address: str,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        resolved_asset = self._client._resolve_lending_asset(asset=asset, symbol=symbol)
        return borrow_and_swap_evm(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            borrower=borrower,
            market_id=market_id,
            asset=resolved_asset,
            amount=amount,
            dst_chain_id=dst_chain_id,
            token_id=token_id,
            dst_recipient=dst_recipient,
            refund_address=refund_address,
            salt=salt,
            custom_data=custom_data,
            signature=signature,
            consumer_address=consumer_address,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def borrow_and_swap(
        self,
        *,
        borrower: str,
        market_id: int,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        amount: int,
        dst_chain_id: int,
        token_id: int,
        dst_recipient: str,
        refund_address: str,
        salt: str,
        custom_data: str | bytes,
        signature: str | bytes,
        consumer_address: str,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return borrow_and_swap(
            borrower=borrower,
            market_id=market_id,
            asset=asset,
            symbol=symbol,
            amount=amount,
            dst_chain_id=dst_chain_id,
            token_id=token_id,
            dst_recipient=dst_recipient,
            refund_address=refund_address,
            salt=salt,
            custom_data=custom_data,
            signature=signature,
            consumer_address=consumer_address,
            precompile_address=precompile_address,
            chain_id=chain_id,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            use_legacy=use_legacy,
            nonce=nonce,
            wait_for_finalized=wait_for_finalized,
            timeout_ms=timeout_ms,
        )

    def borrow_and_swap_btc(
        self,
        *,
        borrower: str,
        market_id: int,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        amount: int,
        dst_recipient: str,
        refund_address: str,
        salt: str,
        signature: str | bytes,
        consumer_address: str,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        resolved_asset = self._client._resolve_lending_asset(asset=asset, symbol=symbol)
        return borrow_and_swap_btc(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            borrower=borrower,
            market_id=market_id,
            asset=resolved_asset,
            amount=amount,
            dst_recipient=dst_recipient,
            refund_address=refund_address,
            salt=salt,
            signature=signature,
            consumer_address=consumer_address,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def repay(
        self,
        *,
        who: str,
        market_id: int,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        amount: int,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        resolved_asset = self._client._resolve_lending_asset(asset=asset, symbol=symbol)
        return repay(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            who=who,
            market_id=market_id,
            asset=resolved_asset,
            amount=amount,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def buy_quota(
        self,
        *,
        account: str,
        quota: int,
        from_subaccount: Optional[str] = None,
        precompile_address: Optional[str] = None,
        chain_id: Optional[int] = None,
        gas_limit: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        use_legacy: Optional[bool] = None,
        nonce: Optional[int] = None,
        wait_for_finalized: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
    ) -> TxResult:
        return buy_quota(
            substrate_ws=self._client.substrate_ws,
            evm_rpc_url=self._client.evm_rpc_url,
            private_key=self._client.private_key,
            precompile_address=self._precompile_address(precompile_address),
            account=account,
            quota=quota,
            from_subaccount=from_subaccount,
            **self._client._tx_kwargs(
                chain_id=chain_id,
                gas_limit=gas_limit,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                use_legacy=use_legacy,
                nonce=nonce,
                use_nonce=True,
                wait_for_finalized=wait_for_finalized,
                timeout_ms=timeout_ms,
            ),
        )

    def lending_markets(
        self,
        *,
        market_id: int,
        precompile_address: Optional[str] = None,
    ) -> LendingMarketState:
        return lending_markets(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            market_id=market_id,
        )

    def asset_pools(
        self,
        *,
        market_id: int,
        precompile_address: Optional[str] = None,
    ) -> list[LendingAssetPoolState]:
        return asset_pools(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            market_id=market_id,
        )

    def health_for(
        self,
        *,
        subaccount: str,
        precompile_address: Optional[str] = None,
    ) -> int:
        return health_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            subaccount=subaccount,
        )

    def max_borrow_amount_for(
        self,
        *,
        account: str,
        lending_market: int,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        precompile_address: Optional[str] = None,
    ) -> int:
        resolved_asset = self._client._resolve_lending_asset(asset=asset, symbol=symbol)
        return max_borrow_amount_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            account=account,
            lending_market=lending_market,
            asset=resolved_asset,
        )

    def max_withdraw_amount_for(
        self,
        *,
        account: str,
        lending_market: int,
        asset: str | bytes | None = None,
        symbol: str | bytes | None = None,
        precompile_address: Optional[str] = None,
    ) -> int:
        resolved_asset = self._client._resolve_lending_asset(asset=asset, symbol=symbol)
        return max_withdraw_amount_for(
            evm_rpc_url=self._client.evm_rpc_url,
            precompile_address=self._precompile_address(precompile_address),
            account=account,
            lending_market=lending_market,
            asset=resolved_asset,
        )
