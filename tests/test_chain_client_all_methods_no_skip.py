from __future__ import annotations

import inspect

import deepx_sdk as dx
import deepx_sdk.api as api_mod
import pytest
from deepx_sdk import client as client_mod


SUBACCOUNT = "0x" + "22" * 20
OTHER = "0x" + "44" * 20
PAIR = "0x" + "33" * 32
ZERO32 = "0x" + "00" * 32


class _Result:
    order_id = 123
    tx_hash = "0xtx"


class _MarketsEndpoint:
    def __init__(self, owner: "_FakeApiClient", name: str, response) -> None:
        self._owner = owner
        self._name = name
        self._response = response

    def markets(self):
        self._owner.calls[self._name] += 1
        return self._response


class _FakeApiClient:
    def __init__(
        self,
        *,
        perp_markets=None,
        spot_markets=None,
        lending_markets=None,
    ) -> None:
        self.calls = {"perp": 0, "spot": 0, "lending": 0}
        v1 = type("_FakeV1", (), {})()
        v1.perp = _MarketsEndpoint(self, "perp", perp_markets or [])
        v1.spot = _MarketsEndpoint(self, "spot", spot_markets or [])
        v1.lending = _MarketsEndpoint(self, "lending", lending_markets or [])
        self.v1 = v1


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


def _make_client(**overrides) -> dx.ChainClient:
    kwargs = {
        "substrate_ws": "wss://node",
        "evm_rpc_url": "https://rpc",
        "private_key": "0xpk",
        "subaccount": SUBACCOUNT,
        "chain_id": 1,
        "gas_limit": 0,
        "max_fee_per_gas": 0,
        "max_priority_fee_per_gas": 0,
    }
    kwargs.update(overrides)
    return dx.ChainClient(**kwargs)


def test_chain_client_all_public_methods_are_explicitly_covered() -> None:
    expected = {
        "ChainClient": {"close", "preload_markets", "refresh_markets"},
        "MarketClient": {"get_perp_price_bounds"},
        "PerpMarketClient": {
            "submit_order",
            "submit_cancel",
            "place_order",
            "place_perp_order_limit",
            "place_perp_order_market",
            "place_perp_order_ioc",
            "place_perp_order",
            "cancel_order",
            "modify_order",
            "settle_pnl",
            "close_position_limit",
            "close_position",
            "close_position_market",
            "set_profit_and_loss_point",
            "set_global_leverage",
            "set_per_market_leverage",
            "perp_markets",
            "user_perp_positions",
            "active_pos_for_market",
            "user_active_orders",
            "order_info",
            "free_deposit_for",
            "mark_price_for",
            "last_trade_price_for",
            "total_collateral_and_margin_required_for",
            "get_liquidate_price",
            "get_oracle_price_all",
            "global_max_leverage_for",
            "per_market_max_leverage_for",
            "effective_leverage_for",
        },
        "SpotMarketClient": {
            "submit_order",
            "submit_cancel",
            "place_order",
            "cancel_order",
            "modify_order",
            "subaccount_place_order_buy_b",
            "subaccount_place_order_sell_b",
            "subaccount_place_order_buy_ioc_b",
            "subaccount_place_order_sell_ioc_b",
            "subaccount_place_market_order_buy_b_without_price",
            "subaccount_place_market_order_buy_b_with_price",
            "subaccount_place_market_order_sell_b_without_price",
            "subaccount_place_market_order_sell_b_with_price",
            "subaccount_cancel_order_buy_b",
            "subaccount_cancel_order_sell_b",
            "user_active_spot_orders",
            "get_spot_market_spec",
        },
        "SubaccountClient": {
            "initialize_subaccount",
            "delete_subaccount",
            "no_op",
            "set_delegate_account",
            "update_delegate_mode",
            "remove_delegate_account",
            "set_spot_margin",
            "rename_subaccount",
            "liquidate_perp_by_transfer",
            "liquidate_spot_by_transfer",
            "liquidate_by_market",
            "user_stats",
            "subaccount_info",
            "delegate_accounts_for",
            "delegator_accounts_for",
        },
        "SystemClient": {"system_account"},
        "LendingClient": {
            "deposit",
            "deposit_from_subaccount",
            "bridge_invoke",
            "withdraw",
            "withdraw_and_swap_evm",
            "withdraw_and_swap",
            "withdraw_and_swap_btc",
            "borrow_and_swap_evm",
            "borrow",
            "borrow_and_swap",
            "borrow_and_swap_btc",
            "repay",
            "buy_quota",
            "lending_markets",
            "asset_pools",
            "health_for",
            "max_borrow_amount_for",
            "max_withdraw_amount_for",
        },
    }
    actual = {
        "ChainClient": _public_methods(client_mod.ChainClient),
        "MarketClient": _public_methods(client_mod.MarketClient),
        "PerpMarketClient": _public_methods(client_mod.PerpMarketClient),
        "SpotMarketClient": _public_methods(client_mod.SpotMarketClient),
        "SubaccountClient": _public_methods(client_mod.SubaccountClient),
        "SystemClient": _public_methods(client_mod.SystemClient),
        "LendingClient": _public_methods(client_mod.LendingClient),
    }
    assert actual == expected


def test_chain_client_all_public_methods_dispatch_without_skip(monkeypatch) -> None:
    calls: list[str] = []

    def patch(name: str):
        def fake(*args, **kwargs):
            calls.append(name)
            return _Result()

        monkeypatch.setattr(client_mod, name, fake)

    for name in [
        "get_perp_price_bounds",
        "place_perp_order_limit",
        "place_perp_order_market",
        "place_perp_order_ioc",
        "place_perp_order",
        "cancel_perp_order",
        "modify_perp_order",
        "settle_pnl",
        "close_position_limit",
        "close_position",
        "close_position_market",
        "set_profit_and_loss_point",
        "perp_markets",
        "user_perp_positions",
        "active_pos_for_market",
        "user_active_orders",
        "order_info",
        "free_deposit_for",
        "mark_price_for",
        "last_trade_price_for",
        "total_collateral_and_margin_required_for",
        "get_liquidate_price",
        "get_oracle_price_all",
        "global_max_leverage_for",
        "per_market_max_leverage_for",
        "effective_leverage_for",
        "subaccount_place_order_buy_b",
        "subaccount_place_order_sell_b",
        "subaccount_place_order_buy_ioc_b",
        "subaccount_place_order_sell_ioc_b",
        "subaccount_place_market_order_buy_b_without_price",
        "subaccount_place_market_order_buy_b_with_price",
        "subaccount_place_market_order_sell_b_without_price",
        "subaccount_place_market_order_sell_b_with_price",
        "subaccount_cancel_order_buy_b",
        "subaccount_cancel_order_sell_b",
        "modify_spot_order",
        "user_active_spot_orders",
        "get_spot_market_spec",
        "initialize_subaccount",
        "delete_subaccount",
        "no_op",
        "set_delegate_account",
        "update_delegate_mode",
        "remove_delegate_account",
        "set_spot_margin",
        "rename_subaccount",
        "liquidate_perp_by_transfer",
        "liquidate_spot_by_transfer",
        "liquidate_by_market",
        "user_stats",
        "subaccount_info",
        "delegate_accounts_for",
        "delegator_accounts_for",
        "system_account",
        "deposit",
        "deposit_from_subaccount",
        "bridge_invoke",
        "withdraw",
        "withdraw_and_swap_evm",
        "withdraw_and_swap",
        "withdraw_and_swap_btc",
        "borrow",
        "borrow_and_swap_evm",
        "borrow_and_swap",
        "borrow_and_swap_btc",
        "repay",
        "buy_quota",
        "lending_markets",
        "asset_pools",
        "health_for",
        "max_borrow_amount_for",
        "max_withdraw_amount_for",
    ]:
        patch(name)

    client = _make_client()
    client.market.get_perp_price_bounds(3)
    client.perp_market.place_perp_order_limit(market_id=3, is_long=True, size=1, price=1)
    client.perp_market.place_perp_order_market(market_id=3, is_long=True, size=1)
    client.perp_market.place_perp_order_ioc(market_id=3, is_long=True, size=1, price=1)
    client.perp_market.place_perp_order(market_id=3, is_long=True, size=1, price=1, order_type=0)
    client.perp_market.cancel_order(market_id=3, order_id=1)
    client.perp_market.modify_order(order_id=1, market_id=3, is_long=True, price=1, size=1)
    client.perp_market.settle_pnl(market_id=3)
    client.perp_market.settle_pnl()
    client.perp_market.close_position_limit(market_id=3, price=1)
    client.perp_market.close_position(market_id=3, price=0, slippage=10)
    client.perp_market.close_position_market(market_id=3, slippage=10)
    client.perp_market.set_profit_and_loss_point(market_id=3, take_profit_point=1)
    client.perp_market.perp_markets(market_id=3)
    client.perp_market.user_perp_positions(user=SUBACCOUNT, market_ids=[3])
    client.perp_market.active_pos_for_market(market_id=3)
    client.perp_market.user_active_orders(user=SUBACCOUNT)
    client.perp_market.order_info(user=SUBACCOUNT, order_id=1)
    client.perp_market.free_deposit_for(account=SUBACCOUNT)
    client.perp_market.mark_price_for(market_id=3)
    client.perp_market.last_trade_price_for(market_id=3)
    client.perp_market.total_collateral_and_margin_required_for(account=SUBACCOUNT, direction=0)
    client.perp_market.get_liquidate_price(account=SUBACCOUNT, market_id=3)
    client.perp_market.get_oracle_price_all()
    client.perp_market.global_max_leverage_for()
    client.perp_market.per_market_max_leverage_for(market_id=3)
    client.perp_market.effective_leverage_for(market_id=3)
    client.spot_market.subaccount_place_order_buy_b(pair=PAIR, quote_amount=1, base_amount=1)
    client.spot_market.subaccount_place_order_sell_b(pair=PAIR, quote_amount=1, base_amount=1)
    client.spot_market.subaccount_place_order_buy_ioc_b(pair=PAIR, quote_amount=1, base_amount=1)
    client.spot_market.subaccount_place_order_sell_ioc_b(pair=PAIR, quote_amount=1, base_amount=1)
    client.spot_market.subaccount_place_market_order_buy_b_without_price(pair=PAIR, quote_amount=1, base_amount=1)
    client.spot_market.subaccount_place_market_order_buy_b_with_price(pair=PAIR, quote_amount=1, base_amount=1, slippage=1)
    client.spot_market.subaccount_place_market_order_sell_b_without_price(pair=PAIR, quote_amount=1, base_amount=1)
    client.spot_market.subaccount_place_market_order_sell_b_with_price(pair=PAIR, quote_amount=1, base_amount=1, slippage=1)
    client.spot_market.subaccount_cancel_order_buy_b(pair=PAIR, order_id=1)
    client.spot_market.subaccount_cancel_order_sell_b(pair=PAIR, order_id=1)
    client.spot_market.modify_order(side="buy", order_id=1, pair=PAIR, quote_amount=1, base_amount=1)
    client.spot_market.user_active_spot_orders(user=SUBACCOUNT, pair=PAIR)
    client.spot_market.get_spot_market_spec(pair=PAIR)
    client.subaccount_client.initialize_subaccount(name=b"test")
    client.subaccount_client.delete_subaccount(subaccount=SUBACCOUNT)
    client.subaccount_client.no_op()
    client.subaccount_client.set_delegate_account(delegate=OTHER, name=b"mm", valid_until=1)
    client.subaccount_client.update_delegate_mode(delegate=OTHER, new_mode=1)
    client.subaccount_client.remove_delegate_account(delegate=OTHER)
    client.subaccount_client.set_spot_margin(subaccount=SUBACCOUNT, enable_spot_margin=True)
    client.subaccount_client.rename_subaccount(subaccount=SUBACCOUNT, new_name=b"new")
    client.subaccount_client.liquidate_perp_by_transfer(
        market_index=3,
        liquidator_max_base_amount=1,
        target_subaccount=SUBACCOUNT,
        liquidator=OTHER,
    )
    client.subaccount_client.liquidate_spot_by_transfer(
        asset_symbol=b"ETH",
        liability_symbol=b"USDC",
        target_account_addr=SUBACCOUNT,
        liquidator=OTHER,
        liquidator_max_liability_transfer=1,
        lending_market_id=1,
    )
    client.subaccount_client.liquidate_by_market(target_subaccount=SUBACCOUNT, liquidator=OTHER)
    client.subaccount_client.user_stats(address=SUBACCOUNT)
    client.subaccount_client.subaccount_info(address=SUBACCOUNT)
    client.subaccount_client.delegate_accounts_for(owner=SUBACCOUNT)
    client.subaccount_client.delegator_accounts_for(delegate=SUBACCOUNT)
    client.system.system_account(address=SUBACCOUNT)
    client.lending.deposit(subaccount=SUBACCOUNT, asset=b"USDC", amount=1)
    client.lending.deposit_from_subaccount(from_subaccount=SUBACCOUNT, subaccount=OTHER, asset=b"USDC", amount=1)
    client.lending.bridge_invoke(uid=ZERO32, amount=1, custom_data=b"")
    client.lending.withdraw(subaccount=SUBACCOUNT, asset=b"USDC", amount=1)
    client.lending.withdraw_and_swap_evm(
        subaccount=SUBACCOUNT,
        asset=b"USDC",
        amount=1,
        dst_chain_id=1,
        token_id=1,
        dst_recipient=ZERO32,
        refund_address=OTHER,
        salt=ZERO32,
        custom_data=b"",
        signature=b"",
        consumer_address=OTHER,
    )
    client.lending.withdraw_and_swap(
        subaccount=SUBACCOUNT,
        asset=b"USDC",
        amount=1,
        dst_chain_id=1,
        token_id=1,
        dst_recipient=ZERO32,
        refund_address=OTHER,
        salt=ZERO32,
        custom_data=b"",
        signature=b"",
        consumer_address=OTHER,
    )
    client.lending.withdraw_and_swap_btc(
        subaccount=SUBACCOUNT,
        asset=b"USDC",
        amount=1,
        dst_recipient=ZERO32,
        refund_address=OTHER,
        salt=ZERO32,
        signature=b"",
        consumer_address=OTHER,
    )
    client.lending.borrow(borrower=SUBACCOUNT, market_id=1, asset=b"USDC", amount=1)
    client.lending.borrow_and_swap_evm(
        borrower=SUBACCOUNT,
        market_id=1,
        asset=b"USDC",
        amount=1,
        dst_chain_id=1,
        token_id=1,
        dst_recipient=ZERO32,
        refund_address=OTHER,
        salt=ZERO32,
        custom_data=b"",
        signature=b"",
        consumer_address=OTHER,
    )
    client.lending.borrow_and_swap(
        borrower=SUBACCOUNT,
        market_id=1,
        asset=b"USDC",
        amount=1,
        dst_chain_id=1,
        token_id=1,
        dst_recipient=ZERO32,
        refund_address=OTHER,
        salt=ZERO32,
        custom_data=b"",
        signature=b"",
        consumer_address=OTHER,
    )
    client.lending.borrow_and_swap_btc(
        borrower=SUBACCOUNT,
        market_id=1,
        asset=b"USDC",
        amount=1,
        dst_recipient=ZERO32,
        refund_address=OTHER,
        salt=ZERO32,
        signature=b"",
        consumer_address=OTHER,
    )
    client.lending.repay(who=SUBACCOUNT, market_id=1, asset=b"USDC", amount=1)
    client.lending.buy_quota(account=SUBACCOUNT, quota=1)
    client.lending.lending_markets(market_id=1)
    client.lending.asset_pools(market_id=1)
    client.lending.health_for(subaccount=SUBACCOUNT)
    client.lending.max_borrow_amount_for(account=SUBACCOUNT, lending_market=1, asset=b"USDC")
    client.lending.max_withdraw_amount_for(account=SUBACCOUNT, lending_market=1, asset=b"USDC")

    assert calls == [
        "get_perp_price_bounds",
        "place_perp_order_limit",
        "place_perp_order_market",
        "place_perp_order_ioc",
        "place_perp_order",
        "cancel_perp_order",
        "modify_perp_order",
        "settle_pnl",
        "settle_pnl",
        "close_position_limit",
        "close_position",
        "close_position_market",
        "set_profit_and_loss_point",
        "perp_markets",
        "user_perp_positions",
        "active_pos_for_market",
        "user_active_orders",
        "order_info",
        "free_deposit_for",
        "mark_price_for",
        "last_trade_price_for",
        "total_collateral_and_margin_required_for",
        "get_liquidate_price",
        "get_oracle_price_all",
        "global_max_leverage_for",
        "per_market_max_leverage_for",
        "effective_leverage_for",
        "subaccount_place_order_buy_b",
        "subaccount_place_order_sell_b",
        "subaccount_place_order_buy_ioc_b",
        "subaccount_place_order_sell_ioc_b",
        "subaccount_place_market_order_buy_b_without_price",
        "subaccount_place_market_order_buy_b_with_price",
        "subaccount_place_market_order_sell_b_without_price",
        "subaccount_place_market_order_sell_b_with_price",
        "subaccount_cancel_order_buy_b",
        "subaccount_cancel_order_sell_b",
        "modify_spot_order",
        "user_active_spot_orders",
        "get_spot_market_spec",
        "initialize_subaccount",
        "delete_subaccount",
        "no_op",
        "set_delegate_account",
        "update_delegate_mode",
        "remove_delegate_account",
        "set_spot_margin",
        "rename_subaccount",
        "liquidate_perp_by_transfer",
        "liquidate_spot_by_transfer",
        "liquidate_by_market",
        "user_stats",
        "subaccount_info",
        "delegate_accounts_for",
        "delegator_accounts_for",
        "system_account",
        "deposit",
        "deposit_from_subaccount",
        "bridge_invoke",
        "withdraw",
        "withdraw_and_swap_evm",
        "withdraw_and_swap",
        "withdraw_and_swap_btc",
        "borrow",
        "borrow_and_swap_evm",
        "borrow_and_swap",
        "borrow_and_swap_btc",
        "repay",
        "buy_quota",
        "lending_markets",
        "asset_pools",
        "health_for",
        "max_borrow_amount_for",
        "max_withdraw_amount_for",
    ]


def test_chain_client_tx_config_applies_to_existing_tx_kwargs(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr(client_mod, "place_perp_order_limit", fake)

    client = _make_client()
    tx_config = dx.TxConfig(
        chain_id=99,
        gas_limit=123,
        max_fee_per_gas=5,
        max_priority_fee_per_gas=6,
        use_legacy=True,
        nonce_ms=777,
        wait_for_finalized=False,
        timeout_ms=888,
    )

    client.perp_market.place_perp_order_limit(
        market_id=3,
        is_long=True,
        size=1,
        price=1,
        chain_id=None,
        gas_limit=456,
        nonce_ms=999,
        tx_config=tx_config,
    )

    assert captured["chain_id"] == 99
    assert captured["gas_limit"] == 456
    assert captured["max_fee_per_gas"] == 5
    assert captured["max_priority_fee_per_gas"] == 6
    assert captured["use_legacy"] is True
    assert captured["nonce_ms"] == 999
    assert captured["wait_for_finalized"] is False
    assert captured["timeout_ms"] == 888


def test_chain_client_tx_config_maps_nonce_aliases(monkeypatch) -> None:
    captured: dict[str, dict] = {}

    def capture(name: str):
        def fake(**kwargs):
            captured[name] = kwargs
            return _Result()

        return fake

    monkeypatch.setattr(client_mod, "subaccount_cancel_order_buy_b", capture("spot"))
    monkeypatch.setattr(client_mod, "close_position_market", capture("close_position"))
    monkeypatch.setattr(client_mod, "deposit", capture("deposit"))

    client = _make_client()
    client.spot_market.subaccount_cancel_order_buy_b(
        pair=PAIR,
        order_id=1,
        tx_config=dx.TxConfig(nonce=111),
    )
    client.perp_market.close_position_market(
        market_id=3,
        tx_config=dx.TxConfig(nonce_ms=222),
    )
    client.lending.deposit(
        subaccount=SUBACCOUNT,
        asset=b"USDC",
        amount=1,
        tx_config=dx.TxConfig(nonce=333, timeout_ms=444),
    )

    assert captured["spot"]["nonce_ms"] == 111
    assert captured["close_position"]["nonce"] == 222
    assert captured["deposit"]["nonce"] == 333
    assert captured["deposit"]["timeout_ms"] == 444


def test_chain_client_high_level_order_aliases_dispatch(monkeypatch) -> None:
    captured: dict[str, dict] = {}

    def capture(name: str):
        def fake(**kwargs):
            captured[name] = kwargs
            return _Result()

        return fake

    monkeypatch.setattr(client_mod, "place_perp_order_limit", capture("perp_limit"))
    monkeypatch.setattr(client_mod, "place_perp_order_market", capture("perp_market"))
    monkeypatch.setattr(client_mod, "place_perp_order_ioc", capture("perp_ioc"))
    monkeypatch.setattr(client_mod, "place_perp_order", capture("perp_generic"))
    monkeypatch.setattr(client_mod, "close_position_market", capture("close_market"))
    monkeypatch.setattr(client_mod, "subaccount_place_order_buy_b", capture("spot_limit_buy"))
    monkeypatch.setattr(client_mod, "subaccount_place_order_buy_ioc_b", capture("spot_ioc_buy"))
    monkeypatch.setattr(
        client_mod,
        "subaccount_place_market_order_sell_b_with_price",
        capture("spot_market_sell"),
    )
    monkeypatch.setattr(client_mod, "subaccount_cancel_order_sell_b", capture("spot_cancel_sell"))

    client = _make_client()
    client.perp_market.place_order(
        market_id=3,
        side="buy",
        size=1,
        price=2,
        tx_config=dx.TxConfig(chain_id=99),
    )
    client.perp_market.place_order(
        market_id=4,
        side="short",
        size=2,
        order_type="market",
    )
    client.perp_market.place_order(
        market_id=5,
        side=True,
        size=3,
        order_type="market",
        take_profit=9,
    )
    assert captured["perp_generic"]["order_type"] == 1
    assert captured["perp_generic"]["take_profit"] == 9

    client.perp_market.place_order(
        market_id=6,
        side="sell",
        size=4,
        price=7,
        order_type="stop",
    )
    client.perp_market.close_position(market_id=7, slippage=10)
    client.spot_market.place_order(
        pair=PAIR,
        side="buy",
        quote_amount=1,
        base_amount=2,
        post_only=1,
    )
    client.spot_market.place_order(
        pair=PAIR,
        side="sell",
        quote_amount=3,
        base_amount=4,
        order_type="market",
        slippage=5,
    )
    client.spot_market.place_order(
        pair=PAIR,
        side="buy",
        quote_amount=5,
        base_amount=6,
        order_type="ioc",
    )
    client.spot_market.cancel_order(pair=PAIR, side=False, order_id=8)

    assert captured["perp_limit"]["is_long"] is True
    assert captured["perp_limit"]["market_id"] == 3
    assert captured["perp_limit"]["chain_id"] == 99
    assert captured["perp_market"]["is_long"] is False
    assert captured["perp_market"]["market_id"] == 4
    assert captured["perp_generic"]["order_type"] == 2
    assert captured["perp_generic"]["price"] == 7
    assert captured["close_market"]["market_id"] == 7
    assert captured["close_market"]["slippage"] == 10
    assert captured["spot_limit_buy"]["post_only"] == 1
    assert captured["spot_market_sell"]["slippage"] == 5
    assert captured["spot_ioc_buy"]["quote_amount"] == 5
    assert captured["spot_cancel_sell"]["order_id"] == 8

    with pytest.raises(ValueError, match="price is required for limit orders"):
        client.perp_market.place_order(market_id=1, side="buy", size=1)
    with pytest.raises(ValueError, match="spot order_type must be limit, market, or ioc"):
        client.spot_market.place_order(
            pair=PAIR,
            side="buy",
            quote_amount=1,
            base_amount=1,
            order_type="stop",
        )
    with pytest.raises(ValueError, match="price is required for stop orders"):
        client.perp_market.place_order(
            market_id=1,
            side="buy",
            size=1,
            order_type="stop",
        )
    with pytest.raises(ValueError, match="price is required for ioc orders"):
        client.perp_market.place_order(
            market_id=1,
            side="buy",
            size=1,
            order_type="ioc",
        )
    with pytest.raises(ValueError, match="side must be buy/long"):
        client.perp_market.place_order(market_id=1, side="flat", size=1)
    with pytest.raises(ValueError, match="side must be buy or sell"):
        client.spot_market.place_order(
            pair=PAIR,
            side="flat",
            quote_amount=1,
            base_amount=1,
        )
    with pytest.raises(ValueError, match="invalid order_type"):
        client.perp_market.place_order(
            market_id=1,
            side="buy",
            size=1,
            order_type=9,
        )


def test_chain_client_high_level_aliases_accept_symbol_and_tx_config(monkeypatch) -> None:
    captured: dict[str, dict] = {}

    def capture(name: str):
        def fake(**kwargs):
            captured[name] = kwargs
            return _Result()

        return fake

    monkeypatch.setattr(client_mod, "place_perp_order_limit", capture("perp_order"))
    monkeypatch.setattr(client_mod, "close_position_market", capture("perp_close"))
    monkeypatch.setattr(client_mod, "subaccount_place_order_buy_b", capture("spot_order"))
    monkeypatch.setattr(client_mod, "subaccount_cancel_order_sell_b", capture("spot_cancel"))
    monkeypatch.setattr(client_mod, "max_withdraw_amount_for", capture("lending_view"))

    api = _FakeApiClient(
        perp_markets=[{"symbol": "ETH-USDC", "marketId": 3}],
        spot_markets=[{"symbol": "ETH-USDC", "marketId": PAIR}],
        lending_markets=[{"asset": "USDC"}],
    )
    client = _make_client(api_client=api)

    client.perp_market.place_order(
        symbol="eth-usdc",
        side="long",
        size=1,
        price=2,
        tx_config=dx.TxConfig(chain_id=99, timeout_ms=123, wait_for_finalized=False),
    )
    client.perp_market.close_position(
        symbol="eth-usdc",
        slippage=10,
        tx_config=dx.TxConfig(nonce=777, timeout_ms=456),
    )
    client.spot_market.place_order(
        symbol="eth-usdc",
        side=True,
        quote_amount=4,
        base_amount=5,
        tx_config=dx.TxConfig(gas_limit=88, nonce_ms=999),
    )
    client.spot_market.cancel_order(
        symbol="eth-usdc",
        side="sell",
        order_id=6,
        tx_config=dx.TxConfig(max_fee_per_gas=10, timeout_ms=789),
    )
    client.lending.max_withdraw_amount_for(
        account=SUBACCOUNT,
        lending_market=1,
        symbol="usdc",
    )

    assert captured["perp_order"]["market_id"] == 3
    assert captured["perp_order"]["chain_id"] == 99
    assert captured["perp_order"]["timeout_ms"] == 123
    assert captured["perp_order"]["wait_for_finalized"] is False
    assert captured["perp_close"]["market_id"] == 3
    assert captured["perp_close"]["nonce"] == 777
    assert captured["spot_order"]["pair"] == PAIR
    assert captured["spot_order"]["gas_limit"] == 88
    assert captured["spot_order"]["nonce_ms"] == 999
    assert captured["spot_cancel"]["pair"] == PAIR
    assert captured["spot_cancel"]["max_fee_per_gas"] == 10
    assert captured["spot_cancel"]["timeout_ms"] == 789
    assert captured["lending_view"]["asset"] == "USDC"
    assert api.calls == {"perp": 1, "spot": 1, "lending": 1}


def test_chain_client_tx_config_rejects_non_tx_methods() -> None:
    client = _make_client()

    with pytest.raises(TypeError, match="does not accept tx_config"):
        client.system.system_account(
            address=SUBACCOUNT,
            tx_config=dx.TxConfig(chain_id=99),
        )


def test_chain_client_identifier_resolution_requires_identifier() -> None:
    client = _make_client()

    with pytest.raises(ValueError, match="market_id or symbol is required"):
        client._resolve_perp_market_id(market_id=None, symbol="")
    with pytest.raises(ValueError, match="pair or symbol is required"):
        client._resolve_spot_pair(pair=None, symbol="")
    with pytest.raises(ValueError, match="asset or symbol is required"):
        client._resolve_lending_asset(asset=None, symbol="")
    with pytest.raises(ValueError, match="market_ids or symbols is required"):
        client.perp_market.user_perp_positions(user=SUBACCOUNT)


def test_chain_client_symbol_resolution_for_perp_spot_and_lending(monkeypatch) -> None:
    captured: dict[str, dict] = {}

    def capture(name: str):
        def fake(**kwargs):
            captured[name] = kwargs
            return _Result()

        return fake

    monkeypatch.setattr(client_mod, "place_perp_order_limit", capture("perp"))
    monkeypatch.setattr(client_mod, "subaccount_cancel_order_buy_b", capture("spot"))
    monkeypatch.setattr(client_mod, "deposit", capture("lending"))

    api = _FakeApiClient(
        perp_markets={"data": [{"symbol": "ETH-USDC", "marketId": "3"}]},
        spot_markets={"items": [{"symbol": "ETH-USDC", "marketId": PAIR}]},
        lending_markets=[{"asset": "USDC"}],
    )
    client = _make_client(api_client=api)

    client.perp_market.place_perp_order_limit(
        symbol="eth-usdc",
        is_long=True,
        size=1,
        price=1,
    )
    client.spot_market.subaccount_cancel_order_buy_b(symbol="eth-usdc", order_id=1)
    client.lending.deposit(subaccount=SUBACCOUNT, symbol="usdc", amount=1)

    assert captured["perp"]["market_id"] == 3
    assert captured["spot"]["pair"] == PAIR
    assert captured["lending"]["asset"] == "USDC"
    assert api.calls == {"perp": 1, "spot": 1, "lending": 1}


def test_chain_client_original_identifiers_take_precedence_over_symbol(monkeypatch) -> None:
    captured: dict[str, dict] = {}

    def capture(name: str):
        def fake(**kwargs):
            captured[name] = kwargs
            return _Result()

        return fake

    monkeypatch.setattr(client_mod, "mark_price_for", capture("perp"))
    monkeypatch.setattr(client_mod, "get_spot_market_spec", capture("spot"))
    monkeypatch.setattr(client_mod, "deposit", capture("lending"))

    api = _FakeApiClient()
    client = _make_client(api_client=api)

    client.perp_market.mark_price_for(market_id=7, symbol="ETH-USDC")
    client.spot_market.get_spot_market_spec(pair=PAIR, symbol="ETH-USDC")
    client.lending.deposit(subaccount=SUBACCOUNT, asset=b"USDC", symbol="ETH", amount=1)

    assert captured["perp"]["market_id"] == 7
    assert captured["spot"]["pair"] == PAIR
    assert captured["lending"]["asset"] == b"USDC"
    assert api.calls == {"perp": 0, "spot": 0, "lending": 0}


def test_chain_client_lending_symbol_rejects_pair_like_unknown_symbol(monkeypatch) -> None:
    called = False

    def fake_deposit(**kwargs):
        nonlocal called
        called = True
        return _Result()

    monkeypatch.setattr(client_mod, "deposit", fake_deposit)

    api = _FakeApiClient(lending_markets=[{"asset": "USDC"}])
    client = _make_client(api_client=api)

    with pytest.raises(ValueError, match="unknown lending symbol"):
        client.lending.deposit(subaccount=SUBACCOUNT, symbol="ETH-USDC", amount=1)

    assert called is False
    assert api.calls == {"perp": 0, "spot": 0, "lending": 1}


def test_chain_client_preload_and_refresh_markets() -> None:
    api = _FakeApiClient(
        perp_markets=[{"symbol": "ETH-USDC", "marketId": 3}],
        spot_markets=[{"symbol": "ETH-USDC", "marketId": PAIR}],
        lending_markets=[{"asset": "USDC"}],
    )
    client = _make_client(api_client=api)

    client.preload_markets()
    client.preload_markets()

    assert api.calls == {"perp": 1, "spot": 1, "lending": 1}
    assert client._resolve_perp_market_id(market_id=None, symbol="ETH-USDC") == 3
    assert client._resolve_spot_pair(pair=None, symbol="ETH-USDC") == PAIR
    assert client._resolve_lending_asset(asset=None, symbol="USDC") == "USDC"
    assert api.calls == {"perp": 1, "spot": 1, "lending": 1}

    client.refresh_markets()

    assert api.calls == {"perp": 2, "spot": 2, "lending": 2}


def test_chain_client_api_base_url_is_used_for_lazy_symbol_resolution(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class LazyApiClient(_FakeApiClient):
        def __init__(self, *, base_url: str, net: str) -> None:
            captured["base_url"] = base_url
            captured["net"] = net
            super().__init__(perp_markets=[{"symbol": "ETH-USDC", "marketId": 3}])

    def fake_mark_price_for(**kwargs):
        captured["market_id"] = kwargs["market_id"]
        return 1

    monkeypatch.setattr(api_mod, "ApiClient", LazyApiClient)
    monkeypatch.setattr(client_mod, "mark_price_for", fake_mark_price_for)

    client = _make_client(
        net="testnet",
        api_base_url=" https://rest.example.test ",
    )

    assert client.perp_market.mark_price_for(symbol="ETH-USDC") == 1
    assert client.api_base_url == "https://rest.example.test"
    assert captured == {
        "base_url": "https://rest.example.test",
        "net": "testnet",
        "market_id": 3,
    }
