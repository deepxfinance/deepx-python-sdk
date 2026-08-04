from __future__ import annotations

import json
import sys
import types

import pytest
from eth_abi import encode
from eth_utils import keccak

if "substrateinterface" not in sys.modules:
    substrate_stub = types.ModuleType("substrateinterface")

    class _SubstrateInterfacePlaceholder:
        pass

    substrate_stub.SubstrateInterface = _SubstrateInterfacePlaceholder
    sys.modules["substrateinterface"] = substrate_stub

from deepx_sdk import _abi, _lending, _perp_market, _spot_market, _subaccount, _system


ADDR = "0x" + "11" * 20
PAIR = "0x" + "22" * 32


def _selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def test_abi_encoders_and_normalizers() -> None:
    assert _abi.normalize_address("11" * 20) == ADDR
    assert _abi.normalize_address(ADDR) == ADDR
    assert _abi.normalize_bytes32(PAIR) == bytes.fromhex("22" * 32)
    assert _abi.normalize_bytes32("22" * 32) == bytes.fromhex("22" * 32)
    assert _abi.decode_abi(["uint8"], encode(["uint8"], [7])) == (7,)
    assert _abi.decode_abi(["uint8"], None) == tuple()

    with pytest.raises(ValueError, match="32-byte"):
        _abi.normalize_bytes32("0x1234")

    assert _abi.encode_perp_place_order(
        subaccount=ADDR,
        market_id=3,
        is_long=True,
        size=1,
        price=2,
        order_type=0,
        leverage=10,
        take_profit=None,
        stop_loss=9,
        reduce_only=False,
        post_only=1,
    ).startswith(
        _selector(
            "placePerpOrder(address,uint16,bool,uint128,uint128,uint8,uint8,uint128,uint128,bool,uint8)"
        )
    )
    assert _abi.encode_perp_cancel_order(
        subaccount=ADDR, market_id=3, order_id=4
    ).startswith(_selector("cancelOrder(address,uint16,uint32)"))
    assert _abi.encode_perp_close_position(
        subaccount=ADDR, market_id=3, price=1, slippage=None
    ).startswith(_selector("closePosition(address,uint16,uint128,uint64)"))
    assert _abi.encode_perp_set_profit_and_loss_point(
        subaccount=ADDR,
        market_id=3,
        take_profit_point=1,
        stop_loss_point=None,
    ).startswith(_selector("setProfitAndLossPoint(address,uint16,uint128,uint128)"))

    spot_cases = [
        (
            _abi.encode_spot_place_order(
                subaccount=ADDR,
                pair=PAIR,
                is_buy=True,
                quote_amount=1,
                base_amount=2,
                order_type=0,
                post_only=1,
                reduce_only=False,
                slippage=None,
                auto_cancel=False,
            ),
            "subaccountPlaceOrderBuyB(address,bytes32,uint256,uint256,uint8,bool)",
        ),
        (
            _abi.encode_spot_place_order(
                subaccount=ADDR,
                pair=PAIR,
                is_buy=False,
                quote_amount=1,
                base_amount=2,
                order_type=0,
                post_only=1,
                reduce_only=False,
                slippage=None,
                auto_cancel=False,
            ),
            "subaccountPlaceOrderSellB(address,bytes32,uint256,uint256,uint8,bool)",
        ),
        (
            _abi.encode_spot_place_order(
                subaccount=ADDR,
                pair=PAIR,
                is_buy=True,
                quote_amount=1,
                base_amount=2,
                order_type=1,
                post_only=0,
                reduce_only=True,
                slippage=None,
                auto_cancel=True,
            ),
            "subaccountPlaceMarketOrderBuyBWithoutPrice(address,bytes32,uint256,uint256,bool,bool)",
        ),
        (
            _abi.encode_spot_place_order(
                subaccount=ADDR,
                pair=PAIR,
                is_buy=False,
                quote_amount=1,
                base_amount=2,
                order_type=1,
                post_only=0,
                reduce_only=True,
                slippage=None,
                auto_cancel=True,
            ),
            "subaccountPlaceMarketOrderSellBWithoutPrice(address,bytes32,uint256,uint256,bool,bool)",
        ),
        (
            _abi.encode_spot_place_order(
                subaccount=ADDR,
                pair=PAIR,
                is_buy=True,
                quote_amount=1,
                base_amount=2,
                order_type=1,
                post_only=0,
                reduce_only=True,
                slippage=5,
                auto_cancel=True,
            ),
            "subaccountPlaceMarketOrderBuyBWithPrice(address,bytes32,uint256,uint256,uint8,bool,bool)",
        ),
        (
            _abi.encode_spot_place_order(
                subaccount=ADDR,
                pair=PAIR,
                is_buy=False,
                quote_amount=1,
                base_amount=2,
                order_type=1,
                post_only=0,
                reduce_only=True,
                slippage=5,
                auto_cancel=True,
            ),
            "subaccountPlaceMarketOrderSellBWithPrice(address,bytes32,uint256,uint256,uint8,bool,bool)",
        ),
        (
            _abi.encode_spot_cancel_order(
                subaccount=ADDR,
                pair=PAIR,
                order_id=8,
                is_buy=True,
            ),
            "subaccountCancelOrderBuyB(address,bytes32,uint256)",
        ),
        (
            _abi.encode_spot_cancel_order(
                subaccount=ADDR,
                pair=PAIR,
                order_id=8,
                is_buy=False,
            ),
            "subaccountCancelOrderSellB(address,bytes32,uint256)",
        ),
        (
            _abi.encode_spot_place_order_sell_b(
                subaccount=ADDR,
                pair=PAIR,
                quote_amount=1,
                base_amount=2,
                post_only=0,
                reduce_only=False,
            ),
            "subaccountPlaceOrderSellB(address,bytes32,uint256,uint256,uint8,bool)",
        ),
        (
            _abi.encode_spot_place_order_buy_b(
                subaccount=ADDR,
                pair=PAIR,
                quote_amount=1,
                base_amount=2,
                post_only=0,
                reduce_only=False,
            ),
            "subaccountPlaceOrderBuyB(address,bytes32,uint256,uint256,uint8,bool)",
        ),
        (
            _abi.encode_spot_place_market_order_sell_b_without_price(
                subaccount=ADDR,
                pair=PAIR,
                quote_amount=1,
                base_amount=2,
                auto_cancel=False,
                reduce_only=False,
            ),
            "subaccountPlaceMarketOrderSellBWithoutPrice(address,bytes32,uint256,uint256,bool,bool)",
        ),
        (
            _abi.encode_spot_place_market_order_sell_b_with_price(
                subaccount=ADDR,
                pair=PAIR,
                quote_amount=1,
                base_amount=2,
                slippage=3,
                auto_cancel=False,
                reduce_only=False,
            ),
            "subaccountPlaceMarketOrderSellBWithPrice(address,bytes32,uint256,uint256,uint8,bool,bool)",
        ),
        (
            _abi.encode_spot_place_market_order_buy_b_without_price(
                subaccount=ADDR,
                pair=PAIR,
                quote_amount=1,
                base_amount=2,
                auto_cancel=False,
                reduce_only=False,
            ),
            "subaccountPlaceMarketOrderBuyBWithoutPrice(address,bytes32,uint256,uint256,bool,bool)",
        ),
        (
            _abi.encode_spot_place_market_order_buy_b_with_price(
                subaccount=ADDR,
                pair=PAIR,
                quote_amount=1,
                base_amount=2,
                slippage=3,
                auto_cancel=False,
                reduce_only=False,
            ),
            "subaccountPlaceMarketOrderBuyBWithPrice(address,bytes32,uint256,uint256,uint8,bool,bool)",
        ),
        (
            _abi.encode_spot_cancel_order_sell_b(subaccount=ADDR, pair=PAIR, order_id=9),
            "subaccountCancelOrderSellB(address,bytes32,uint256)",
        ),
        (
            _abi.encode_spot_cancel_order_buy_b(subaccount=ADDR, pair=PAIR, order_id=9),
            "subaccountCancelOrderBuyB(address,bytes32,uint256)",
        ),
    ]
    for payload, signature in spot_cases:
        assert payload.startswith(_selector(signature))

    with pytest.raises(ValueError, match="invalid spot order_type"):
        _abi.encode_spot_place_order(
            subaccount=ADDR,
            pair=PAIR,
            is_buy=True,
            quote_amount=1,
            base_amount=2,
            order_type=9,
            post_only=0,
            reduce_only=False,
            slippage=None,
            auto_cancel=False,
        )


def test_perp_view_decoders(monkeypatch) -> None:
    owner = "0x" + "33" * 20
    pos = (3, True, 1, 2, 10, -1, 7, -2, -3, owner, 100, 90, 80)
    active_order = (owner, 3, 1, 0, 44, 1000, 777)
    order = (44, owner, 3, True, 1, 1000, 0, 777, 10, 0, 1, 1, 0, 100, 90)

    responses = [
        encode([f"{_perp_market._PERP_POSITION_TUPLE}[]"], [[pos]]),
        encode([f"{_perp_market._PERP_POSITION_TUPLE}[]"], [[pos]]),
        encode([f"{_perp_market._ACTIVE_ORDER_TUPLE}[]"], [[active_order]]),
        encode([_perp_market._PERP_ORDER_TUPLE], [order]),
        encode(["uint128"], [123]),
        encode(["uint128"], [456]),
        encode(["uint128"], [789]),
        encode([_perp_market._TOTAL_COLLATERAL_TUPLE], [(10, 20)]),
        encode(["uint128"], [999]),
        encode([f"{_perp_market._ORACLE_PRICE_TUPLE}[]"], [[(b"ETH", 1234)]]),
    ]

    def fake_evm_call(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr(_perp_market, "evm_call", fake_evm_call)

    assert _perp_market.user_perp_positions(
        evm_rpc_url="rpc", precompile_address=ADDR, user=owner, market_ids=[3]
    )[0].liquidate_price == 80
    assert _perp_market.active_pos_for_market(
        evm_rpc_url="rpc", precompile_address=ADDR, market_id=3
    )[0].owner == owner
    assert _perp_market.user_active_orders(
        evm_rpc_url="rpc", precompile_address=ADDR, user=owner
    )[0].order_id == 44
    assert _perp_market.order_info(
        evm_rpc_url="rpc", precompile_address=ADDR, user=owner, order_id=44
    ).take_profit == 100
    assert _perp_market.free_deposit_for(
        evm_rpc_url="rpc", precompile_address=ADDR, account=owner
    ) == 123
    assert _perp_market.mark_price_for(
        evm_rpc_url="rpc", precompile_address=ADDR, market_id=3
    ) == 456
    assert _perp_market.last_trade_price_for(
        evm_rpc_url="rpc", precompile_address=ADDR, market_id=3
    ) == 789
    assert _perp_market.total_collateral_and_margin_required_for(
        evm_rpc_url="rpc", precompile_address=ADDR, account=owner, direction=1
    ).margin_required == 20
    assert _perp_market.get_liquidate_price(
        evm_rpc_url="rpc", precompile_address=ADDR, account=owner, market_id=3
    ) == 999
    assert _perp_market.get_oracle_price_all(
        evm_rpc_url="rpc", precompile_address=ADDR
    )[0].symbol == "ETH"

    monkeypatch.setattr(
        _perp_market,
        "evm_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("return none")),
    )
    assert (
        _perp_market.get_liquidate_price(
            evm_rpc_url="rpc", precompile_address=ADDR, account=owner, market_id=3
        )
        is None
    )

    with pytest.raises(RuntimeError, match="unexpected perpMarkets layout"):
        _perp_market._decode_perp_market((1, 2))
    with pytest.raises(RuntimeError, match="unable to decode perpMarkets"):
        _perp_market._decode_perp_market_tuple(b"bad")


def test_perp_runtime_value_parsers() -> None:
    assert _perp_market._parse_int_value(True) == 1
    assert _perp_market._parse_int_value("0x10") == 16
    assert _perp_market._parse_int_value({"value": "5"}) == 5
    assert _perp_market._parse_int_value({"values": [1, 0]}) == 1
    assert _perp_market._parse_int_value([1, 2]) == (2 << 64) | 1
    assert _perp_market._parse_address_value({"id": ADDR}) == ADDR
    assert _perp_market._parse_address_value([1] * 20) == "0x" + "01" * 20
    assert _perp_market._parse_optional_u128_value({"None": None}) is None
    assert _perp_market._parse_optional_u128_value({"Some": "7"}) == 7
    assert _perp_market._parse_optional_u128_value("none") is None

    pos_dict = {
        "market_id": 3,
        "is_long": True,
        "base_asset_amount": 1,
        "entry_price": 2,
        "leverage": 10,
        "last_funding_rate": -1,
        "version": 7,
        "realized_pnl": -2,
        "funding_payment": -3,
        "owner": ADDR,
        "take_profit": {"Some": 100},
        "stop_loss": {"None": None},
    }
    assert _perp_market._parse_perp_position_value(pos_dict)["take_profit"] == 100
    assert _perp_market._parse_position_updated_fields(
        {"owner": ADDR, "market_id": 3, "pos": pos_dict, "pnl": -1}
    )["pnl"] == -1
    assert _perp_market._parse_position_updated_fields([ADDR, 3, list(pos_dict.values()), -1])[
        "market_id"
    ] == 3
    assert _perp_market._parse_position_updated_fields("raw") == {"raw": "raw"}
    assert _perp_market._parse_int_field({"x": "0x10"}, "x") == 16
    assert _perp_market._optional_u128(None) is None
    assert _perp_market._optional_u128(5) == 5
    assert _perp_market._optional_u64(None) is None
    assert _perp_market._optional_u64(6) == 6
    assert _perp_market._perp_order_type_u8(2) == 2
    assert _perp_market._post_only_u8(2) == 2

    with pytest.raises(ValueError, match="empty list"):
        _perp_market._parse_int_value([])
    with pytest.raises(ValueError, match="unsupported address"):
        _perp_market._parse_address_value(object())
    with pytest.raises(RuntimeError, match="event field 'x' not found"):
        _perp_market._parse_int_field({}, "x")
    with pytest.raises(RuntimeError, match="not an int-like"):
        _perp_market._parse_int_field({"x": object()}, "x")
    with pytest.raises(ValueError, match="invalid perp order_type"):
        _perp_market._perp_order_type_u8(9)
    with pytest.raises(ValueError, match="invalid post_only"):
        _perp_market._post_only_u8(9)


def test_spot_lending_subaccount_system_views(monkeypatch) -> None:
    owner = "0x" + "33" * 20
    spot_order = (bytes.fromhex("22" * 32), 1, owner, 100, 200, 3, 777, 1, True, 0, 5)
    lending_pool_v3 = (1, b"USDC", 6, 10, 20, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
    summary = (owner, b"main")

    spot_responses = [
        encode([f"{_spot_market._SPOT_ORDER_TUPLE}[]"], [[spot_order]]),
        encode([_spot_market._SPOT_MARKET_SPEC_TUPLE], [(11, 22, 33)]),
    ]
    monkeypatch.setattr(_spot_market, "evm_call", lambda *_args, **_kwargs: spot_responses.pop(0))
    assert _spot_market.user_active_spot_orders(
        evm_rpc_url="rpc", precompile_address=ADDR, user=owner
    )[0].pair == PAIR
    assert _spot_market.get_spot_market_spec(
        evm_rpc_url="rpc", precompile_address=ADDR, pair=PAIR
    ).tick_size == 22

    lending_responses = [
        encode([_lending._LENDING_MARKET_TUPLE], [(1, b"main", 25)]),
        encode([f"{_lending._ASSET_POOL_TUPLE_V3}[]"], [[lending_pool_v3]]),
        encode(["uint128"], [101]),
        encode(["uint128"], [202]),
        encode(["uint128"], [303]),
    ]
    monkeypatch.setattr(_lending, "evm_call", lambda *_args, **_kwargs: lending_responses.pop(0))
    assert _lending.lending_markets(
        evm_rpc_url="rpc", precompile_address=ADDR, market_id=1
    ).market_name == "main"
    assert _lending.asset_pools(evm_rpc_url="rpc", precompile_address=ADDR, market_id=1)[
        0
    ].borrow_cap == 0
    assert _lending.health_for(evm_rpc_url="rpc", precompile_address=ADDR, subaccount=owner) == 101
    assert (
        _lending.max_borrow_amount_for(
            evm_rpc_url="rpc",
            precompile_address=ADDR,
            account=owner,
            lending_market=1,
            asset=b"USDC",
        )
        == 202
    )
    assert (
        _lending.max_withdraw_amount_for(
            evm_rpc_url="rpc",
            precompile_address=ADDR,
            account=owner,
            lending_market=1,
            asset="USDC",
        )
        == 303
    )

    subaccount_responses = [
        encode([_subaccount._USER_STATS_TUPLE], [([summary], 10, 2, 3)]),
        encode([f"{_subaccount._ONE_CLICK_TRADING_TUPLE}[]"], [[(owner, 1, 777)]]),
        encode([f"{_subaccount._SUMMARY_TUPLE}[]"], [[summary]]),
    ]
    monkeypatch.setattr(
        _subaccount,
        "evm_call",
        lambda *_args, **_kwargs: subaccount_responses.pop(0),
    )
    assert _subaccount.user_stats(
        evm_rpc_url="rpc", precompile_address=ADDR, address=owner
    ).number_of_sub_accounts == 2
    assert _subaccount.one_click_trading_accounts_for(
        evm_rpc_url="rpc", precompile_address=ADDR, owner=owner
    )[0].mode == 1
    assert _subaccount.delegate_accounts(
        evm_rpc_url="rpc", precompile_address=ADDR, user=owner
    )[0].name == "main"

    system_responses = [
        encode([_system._SYSTEM_ACCOUNT_TUPLE_V2], [(1, 2, [3], 4, True)]),
        RuntimeError("v2 unavailable"),
        encode([_system._SYSTEM_ACCOUNT_TUPLE], [(0, 0, [], 0)]),
    ]

    def fake_system_evm_call(*_args, **_kwargs):
        value = system_responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(_system, "evm_call", fake_system_evm_call)
    assert _system.system_account(
        evm_rpc_url="rpc", precompile_address=ADDR, address=owner
    ).is_exist is True
    assert _system.system_account(
        evm_rpc_url="rpc", precompile_address=ADDR, address=owner
    ).is_exist is False

    monkeypatch.setattr(
        _system,
        "evm_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    with pytest.raises(RuntimeError, match="systemAccount decode failed"):
        _system.system_account(evm_rpc_url="rpc", precompile_address=ADDR, address=owner)


def test_spot_cancel_and_event_parsing_paths(monkeypatch) -> None:
    event = types.SimpleNamespace(tx_hash="0xtx", extrinsic_hash="0xext", fields_json='{"id": 77}')
    monkeypatch.setattr(
        _spot_market,
        "submit_pallet_call_wait_event",
        lambda **_kwargs: event,
    )
    assert _spot_market.subaccount_cancel_order_sell_b(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="pk",
        precompile_address=ADDR,
        subaccount=ADDR,
        pair=PAIR,
        order_id=77,
    ).order_id == 77

    _spot_market.subaccount_place_market_order_sell_b_without_price(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="pk",
        precompile_address=ADDR,
        subaccount=ADDR,
        pair=PAIR,
        quote_amount=1,
        base_amount=2,
    )

    failing_event = types.SimpleNamespace(
        tx_hash="0xtx",
        extrinsic_hash="0xext",
        fields_json='{"order": {"id": "bad"}}',
    )
    monkeypatch.setattr(
        _spot_market,
        "submit_pallet_call_wait_event",
        lambda **_kwargs: failing_event,
    )
    with pytest.raises(RuntimeError, match="order.id"):
        _spot_market.subaccount_cancel_order_buy_b(
            substrate_ws="ws",
            evm_rpc_url="rpc",
            private_key="pk",
            precompile_address=ADDR,
            subaccount=ADDR,
            pair=PAIR,
            order_id=1,
        )

    monkeypatch.setattr(
        _spot_market,
        "submit_pallet_call_wait_event",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("event missing")),
    )
    with pytest.raises(RuntimeError, match="spot cancel submitted"):
        _spot_market.subaccount_cancel_order_buy_b(
            substrate_ws="ws",
            evm_rpc_url="rpc",
            private_key="pk",
            precompile_address=ADDR,
            subaccount=ADDR,
            pair=PAIR,
            order_id=1,
        )

    assert _spot_market._post_only_u8(2) == 2
    with pytest.raises(ValueError, match="invalid post_only"):
        _spot_market._post_only_u8(9)
    assert _spot_market._parse_int_field({"order": {"id": "0x10"}}, "order_id") == 16
    assert _spot_market._parse_int_field({"id": [1, 0]}, "order_id") == 1
    with pytest.raises(RuntimeError, match="event field 'x' not found"):
        _spot_market._parse_int_field({}, "x")
    with pytest.raises(ValueError, match="unsupported int value"):
        _spot_market._parse_int_value(object())


def test_submit_helpers_event_payloads(monkeypatch) -> None:
    signed = types.SimpleNamespace(signed_tx="0xsigned", signer=ADDR, tx_hash="0xhash")
    event = types.SimpleNamespace(
        tx_hash="0xtx",
        pallet="Lending",
        event="Deposit",
        fields_json=json.dumps({"amount": 1}),
    )
    monkeypatch.setattr(_lending, "build_signed_tx", lambda **_kwargs: signed)
    monkeypatch.setattr(_lending, "submit_signed_tx_wait_event", lambda **_kwargs: event)
    assert _lending._submit_lending_tx(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="pk",
        precompile_address=ADDR,
        data=b"data",
        chain_id=None,
        gas_limit=None,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce=None,
        wait_for_finalized=True,
        timeout_ms=None,
        event_name="Deposit",
    ).event["amount"] == 1

    monkeypatch.setattr(
        _lending,
        "submit_signed_tx",
        lambda **_kwargs: types.SimpleNamespace(tx_hash="0xsubmit"),
    )
    assert _lending._submit_lending_tx(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="pk",
        precompile_address=ADDR,
        data=b"data",
        chain_id=None,
        gas_limit=None,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce=None,
        wait_for_finalized=True,
        timeout_ms=None,
    ).tx_hash == "0xsubmit"

    monkeypatch.setattr(
        _lending,
        "submit_pallet_call_wait_event",
        lambda **_kwargs: types.SimpleNamespace(
            tx_hash="0xtx",
            pallet="Lending",
            event="Deposit",
            fields_json="not-json",
        ),
    )
    assert _lending._submit_lending_call(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="pk",
        call_function="deposit",
        call_params={},
        wait_for_finalized=True,
        timeout_ms=None,
        nonce=None,
        event_name="Deposit",
    ).event == {"pallet": "Lending", "name": "Deposit"}

    monkeypatch.setattr(
        _lending,
        "submit_pallet_call",
        lambda **_kwargs: types.SimpleNamespace(tx_hash="0xpal"),
    )
    assert _lending._submit_lending_call(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="pk",
        call_function="buy_quota",
        call_params={},
        wait_for_finalized=True,
        timeout_ms=None,
        nonce=None,
    ).tx_hash == "0xpal"

    monkeypatch.setattr(_subaccount, "build_signed_tx", lambda **_kwargs: signed)
    monkeypatch.setattr(
        _subaccount,
        "submit_signed_tx_wait_event",
        lambda **_kwargs: types.SimpleNamespace(
            tx_hash="0xtx",
            pallet="Subaccount",
            event="Initialized",
            fields_json=json.dumps({"name": "main"}),
        ),
    )
    assert _subaccount._submit_subaccount_tx(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="pk",
        precompile_address=ADDR,
        data=b"data",
        chain_id=None,
        gas_limit=None,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce=None,
        wait_for_finalized=True,
        timeout_ms=None,
        event_name="Initialized",
    ).event["name"] == "main"

    monkeypatch.setattr(
        _subaccount,
        "submit_signed_tx_wait_event",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("event not found: x")),
    )
    assert _subaccount._submit_subaccount_tx(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="pk",
        precompile_address=ADDR,
        data=b"data",
        chain_id=None,
        gas_limit=None,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce=None,
        wait_for_finalized=True,
        timeout_ms=None,
        event_name="Optional",
        event_required=False,
    ).event is None

    monkeypatch.setattr(
        _subaccount,
        "submit_signed_tx",
        lambda **_kwargs: types.SimpleNamespace(tx_hash="0xsubmittx"),
    )
    assert _subaccount._submit_subaccount_tx(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="pk",
        precompile_address=ADDR,
        data=b"data",
        chain_id=None,
        gas_limit=None,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce=None,
        wait_for_finalized=True,
        timeout_ms=None,
    ).tx_hash == "0xsubmittx"

    monkeypatch.setattr(
        _subaccount,
        "submit_pallet_call",
        lambda **_kwargs: types.SimpleNamespace(tx_hash="0xcall"),
    )
    assert _subaccount._submit_subaccount_call(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="pk",
        call_function="set_spot_margin",
        call_params={},
        wait_for_finalized=True,
        timeout_ms=None,
        nonce=None,
        event_name="Optional",
        event_required=False,
    ).tx_hash == "0xcall"

    monkeypatch.setattr(
        _subaccount,
        "submit_pallet_call_wait_event",
        lambda **_kwargs: types.SimpleNamespace(
            tx_hash="0xevent",
            pallet="Subaccount",
            event="Done",
            fields_json="not-json",
        ),
    )
    assert _subaccount._submit_subaccount_call(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="pk",
        call_function="initialize",
        call_params={},
        wait_for_finalized=True,
        timeout_ms=None,
        nonce=None,
        event_name="Done",
    ).event == {"pallet": "Subaccount", "name": "Done"}

    assert _subaccount._tx_result_from_event(
        types.SimpleNamespace(tx_hash="0xempty", pallet="Subaccount", event="Done", fields_json="")
    ).event == {"pallet": "Subaccount", "name": "Done"}


def test_leverage_views_decode(monkeypatch) -> None:
    from eth_abi import encode

    responses = [
        encode(["uint64"], [10000]),
        encode(["uint64"], [0]),
        encode(["uint64"], [3000]),
    ]

    def fake_evm_call(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr(_perp_market, "evm_call", fake_evm_call)

    owner = "0x" + "22" * 20
    assert _perp_market.global_max_leverage_for(
        evm_rpc_url="rpc", precompile_address=ADDR, subaccount=owner
    ) == 10000
    assert _perp_market.per_market_max_leverage_for(
        evm_rpc_url="rpc", precompile_address=ADDR, subaccount=owner, market_id=3
    ) == 0
    assert _perp_market.effective_leverage_for(
        evm_rpc_url="rpc", precompile_address=ADDR, subaccount=owner, market_id=3
    ) == 3000
