import sys
import types

from eth_abi import encode

if "substrateinterface" not in sys.modules:
    substrate_stub = types.ModuleType("substrateinterface")

    class _SubstrateInterfacePlaceholder:
        pass

    substrate_stub.SubstrateInterface = _SubstrateInterfacePlaceholder
    sys.modules["substrateinterface"] = substrate_stub

from deepx_sdk._perp_market import (
    _PERP_MARKET_TUPLE,
    _decode_perp_market,
    _decode_perp_market_tuple,
)
from deepx_sdk._subaccount import (
    _ACCOUNT_INFO_DELEGATES_TUPLE,
    _ACCOUNT_INFO_TUPLE,
    _USER_TUPLE_V1,
    _decode_subaccount_info,
    _decode_subaccount_info_tuple,
)


def test_decode_perp_market_latest() -> None:
    value = (
        3,
        b"ETH-USDC",
        b"ETH",
        18,
        1,
        b"ethereum",
        123,
        11,
        1000,
        2000,
        2010,
        500,
        10000,
        5000,
        -50,
        (1, 1, 1),
        888,
        9,
        10,
        77,
        12345,
        2,
        -2,
    )
    raw = encode([_PERP_MARKET_TUPLE], [value])
    decoded_tuple = _decode_perp_market_tuple(raw)
    market = _decode_perp_market(decoded_tuple)

    assert market.id == 3
    assert market.base_symbol == "ETH"
    assert market.base_address is None
    assert market.quote_symbol is None
    assert market.quote_address is None
    assert market.initial_margin_ratio is None
    assert market.max_active_orders is None
    assert market.is_quote_market is None
    assert market.liquidation_spec is None


def test_decode_subaccount_info_latest() -> None:
    value = (
        "0x0000000000000000000000000000000000000011",
        "0x00000000000000000000000000000000000000aa",
        "0x0000000000000000000000000000000000000022",
        b"demo",
        4,
        [(b"USDC", 1000)],
        12,
        True,
        (True, 777),
        88,
        1,
    )
    raw = encode([_ACCOUNT_INFO_TUPLE], [value])
    layout, decoded = _decode_subaccount_info_tuple(raw)
    info = _decode_subaccount_info(decoded, layout)

    assert layout == "latest"
    assert info.status == 4
    assert info.address.lower() == "0x00000000000000000000000000000000000000aa"
    assert info.liquidation_start_at == 777
    assert info.next_liquidation_id == 88
    assert info.margin_strategy == 1
    assert info.borrow_positions == []
    assert info.spot_positions[0].symbol == "USDC"


def test_decode_subaccount_info_legacy_user() -> None:
    value = (
        "0x0000000000000000000000000000000000000011",
        "0x0000000000000000000000000000000000000022",
        b"demo",
        [(b"USDC", 1000)],
        [(1, b"USDC", 100, 1)],
        10,
        1,
        True,
    )
    raw = encode([_USER_TUPLE_V1], [value])
    layout, decoded = _decode_subaccount_info_tuple(raw)
    info = _decode_subaccount_info(decoded, layout)

    assert layout == "legacy_user"
    assert info.authority.lower() == "0x0000000000000000000000000000000000000011"
    assert info.address is None
    assert info.liquidation_start_at is None
    assert info.next_liquidation_id is None
    assert info.margin_strategy is None
    assert len(info.borrow_positions) == 1


def test_decode_subaccount_info_delegates_vec() -> None:
    value = (
        "0x0000000000000000000000000000000000000011",
        [("0x00000000000000000000000000000000000000dd", b"mm-bot", 1781999999000)],
        b"demo",
        [("0x" + b"".hex(), 0)] if False else [(b"USDC", 1000)],
        [(1, b"USDC", 100, 1)],
        10,
        1,
        True,
    )
    raw = encode([_ACCOUNT_INFO_DELEGATES_TUPLE], [value])
    layout, decoded = _decode_subaccount_info_tuple(raw)
    info = _decode_subaccount_info(decoded, layout)

    assert layout == "delegates_vec"
    assert info.authority.lower() == "0x0000000000000000000000000000000000000011"
    assert info.delegate == ""
    assert len(info.delegates) == 1
    assert info.delegates[0].delegate_address.lower() == "0x00000000000000000000000000000000000000dd"
    assert info.delegates[0].delegate_name == "mm-bot"
    assert info.delegates[0].valid_until == 1781999999000
    assert info.spot_positions[0].symbol == "USDC"
    assert info.next_order_id == 10
