from __future__ import annotations

import sys
import types

import pytest
from eth_abi import encode
from eth_utils import keccak

# Avoid hard dependency during package import in test environments.
if "substrateinterface" not in sys.modules:
    substrate_stub = types.ModuleType("substrateinterface")

    class _SubstrateInterfacePlaceholder:
        pass

    substrate_stub.SubstrateInterface = _SubstrateInterfacePlaceholder
    sys.modules["substrateinterface"] = substrate_stub

from deepx_sdk import ChainClient, _lending, _perp_market, _spot_market, _subaccount, _substrate
from deepx_sdk import client as _client_mod
from deepx_sdk._perp_market import _PERP_MARKET_TUPLE, _decode_perp_market, _decode_perp_market_tuple


class _DummyQueryResult:
    def __init__(self, value):
        self.value = value


class _DummySubstrateNewSpec:
    def __init__(self, url: str):
        self.url = url

    def query(self, module: str, storage_name: str, params: list[int]):
        assert storage_name == "PerpMarkets"
        assert params == [3]
        return _DummyQueryResult(
            {
                "mark_price": 1_000_000_000_000_000_000,
                "max_deviation_bps": 500,
                "base_decimal": 18,
                "order_spec": {
                    "min_qty": 10_000_000_000_000_000,
                    "tick_size": 1_000_000_000_000_000,
                    "step_size": 5_000_000_000_000_000,
                    "min_notional": 10_000_000,
                },
            }
        )


class _DummySigned:
    signed_tx = "0xsigned"
    signer = "0x" + "11" * 20
    tx_hash = "0xtx"


class _DummyEvent:
    tx_hash = "0xtx"
    extrinsic_hash = "0xext"
    pallet = "Test"
    event = "Done"
    fields_json = '{"order_id": 123}'


def test_get_perp_price_bounds_reads_min_qty(monkeypatch) -> None:
    from deepx_sdk import _native_py

    # get_perp_price_bounds uses the shared connection factory (proxy/timeout); mock the factory to return the dummy
    monkeypatch.setattr(
        _native_py,
        "_create_substrate",
        lambda cls, ws, timeout_ms=None: _DummySubstrateNewSpec(ws),
    )

    bounds = _substrate.get_perp_price_bounds("ws://127.0.0.1:9944", 3)

    assert bounds.mark_price == 1_000_000_000_000_000_000
    assert bounds.max_deviation_bps == 500
    assert bounds.min_order_size == 10_000_000_000_000_000
    assert bounds.min_qty == bounds.min_order_size
    assert bounds.min_notional == 10_000_000


def test_decode_perp_market_compact_layout() -> None:
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
        -10,
        (1, 2, 3),
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
    assert market.order_spec.min_order_size == 1
    assert market.order_spec.tick_size == 2
    assert market.order_spec.step_size == 3
    assert market.order_spec.min_notional is None


def test_get_spot_market_spec_new_tuple(monkeypatch) -> None:
    raw = encode(["(uint128,uint128,uint128)"], [(11, 22, 33)])
    monkeypatch.setattr(_spot_market, "evm_call", lambda *_args, **_kwargs: raw)

    spec = _spot_market.get_spot_market_spec(
        evm_rpc_url="http://127.0.0.1:8545",
        precompile_address="0x" + "00" * 20,
        pair="0x" + "11" * 32,
    )
    assert spec.min_order_size == 11
    assert spec.tick_size == 22
    assert spec.step_size == 33
    assert spec.min_notional is None


def test_perp_order_actions_use_pallet_calls(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    result = _perp_market.place_perp_order(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "44" * 20,
        subaccount="0x" + "22" * 20,
        market_id=3,
        is_long=True,
        size=100,
        price=1000,
        order_type=0,
        post_only=1,
        nonce_ms=1781757000123,
    )

    assert result.order_id == 123
    assert captured["submit"]["call_module"] == "PerpMarket"
    assert captured["submit"]["call_function"] == "place_order"
    assert captured["submit"]["call_params"] == {
        "params": {
            "subaccount": "0x" + "22" * 20,
            "market_id": 3,
            "is_long": True,
            "size": 100,
            "price": 1000,
            "order_type": {"Limit": "GTC"},
            "take_profit": None,
            "stop_loss": None,
            "reduce_only": False,
            "post_only": "MustPostOnly",
            "cloid": None,
        }
    }
    assert captured["submit"]["pallet"] == "PerpMarket"
    assert captured["submit"]["event"] == "OrderPlaced"


def test_perp_order_actions_ioc_uses_pallet_calls(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    result = _perp_market.place_perp_order_ioc(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "44" * 20,
        subaccount="0x" + "22" * 20,
        market_id=3,
        is_long=True,
        size=100,
        price=1000,
        reduce_only=True,
        nonce_ms=1781757000123,
    )

    assert result.order_id == 123
    assert captured["submit"]["call_module"] == "PerpMarket"
    assert captured["submit"]["call_function"] == "place_order"
    assert captured["submit"]["call_params"] == {
        "params": {
            "subaccount": "0x" + "22" * 20,
            "market_id": 3,
            "is_long": True,
            "size": 100,
            "price": 1000,
            "order_type": {"Limit": "IOC"},
            "take_profit": None,
            "stop_loss": None,
            "reduce_only": True,
            "post_only": "None",
            "cloid": None,
        }
    }
    assert captured["submit"]["pallet"] == "PerpMarket"
    assert captured["submit"]["event"] == "OrderPlaced"


def test_perp_order_wrappers_forward_cloid(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    base = {
        "substrate_ws": "wss://node",
        "evm_rpc_url": "https://rpc",
        "private_key": "0xpk",
        "precompile_address": "0x" + "44" * 20,
        "subaccount": "0x" + "22" * 20,
        "market_id": 3,
        "is_long": True,
        "size": 100,
    }
    cloid = 2**31 - 1

    _perp_market.place_perp_order_limit(**base, price=1000, cloid=cloid)
    assert captured["submit"]["call_params"]["params"]["cloid"] == cloid
    assert captured["submit"]["call_params"]["params"]["order_type"] == {"Limit": "GTC"}

    _perp_market.place_perp_order_ioc(**base, price=1000, cloid=cloid + 1)
    assert captured["submit"]["call_params"]["params"]["cloid"] == cloid + 1

    _perp_market.place_perp_order_market(**base, slippage=50, cloid=cloid + 2)
    assert captured["submit"]["call_params"]["params"]["cloid"] == cloid + 2
    assert captured["submit"]["call_params"]["params"]["order_type"] == {"Market": 50}


def test_perp_order_dispatcher_routes_ioc(monkeypatch) -> None:
    """PerpMarketClient.place_order(order_type='ioc') → place_perp_order_ioc with order_type=3."""
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    import deepx_sdk.client as client_mod  # local import to avoid module-level cycle

    client = client_mod.ChainClient(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0x" + "11" * 32,
        subaccount="0x" + "22" * 20,
    )

    client.perp_market.place_order(
        market_id=3,
        side="buy",
        size=100,
        price=1000,
        order_type="ioc",
    )

    assert captured["submit"]["call_params"]["params"]["order_type"] == {"Limit": "IOC"}
    assert captured["submit"]["call_params"]["params"]["price"] == 1000


def test_perp_order_action_variants_and_validation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    base = {
        "substrate_ws": "wss://node",
        "evm_rpc_url": "https://rpc",
        "private_key": "0xpk",
        "precompile_address": "0x" + "44" * 20,
        "subaccount": "0x" + "22" * 20,
        "market_id": 3,
        "size": 100,
    }

    _perp_market.place_perp_order_market(**base, is_long=False, reduce_only=True)
    assert captured["submit"]["call_params"] == {
        "params": {
            "subaccount": "0x" + "22" * 20,
            "market_id": 3,
            "is_long": False,
            "size": 100,
            "price": 0,
            "order_type": {"Market": None},
            "take_profit": None,
            "stop_loss": None,
            "reduce_only": True,
            "post_only": "None",
            "cloid": None,
        }
    }

    _perp_market.place_perp_order(
        **base,
        is_long=True,
        price=1200,
        order_type=2,
        take_profit=1500,
        stop_loss=900,
        post_only=2,
    )
    assert captured["submit"]["call_params"]["params"]["order_type"] == "Stop"
    assert captured["submit"]["call_params"]["params"]["take_profit"] == 1500
    assert captured["submit"]["call_params"]["params"]["stop_loss"] == 900
    assert captured["submit"]["call_params"]["params"]["post_only"] == "Adaptive"

    with pytest.raises(ValueError, match="invalid perp order_type"):
        _perp_market.place_perp_order(**base, is_long=True, price=1, order_type=9)

    with pytest.raises(ValueError, match="invalid post_only"):
        _perp_market.place_perp_order(**base, is_long=True, price=1, order_type=0, post_only=9)


def test_perp_cancel_uses_pallet_call(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    result = _perp_market.cancel_perp_order(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "44" * 20,
        subaccount="0x" + "22" * 20,
        market_id=3,
        order_id=99,
    )

    assert result.order_id == 123
    assert captured["submit"]["call_module"] == "PerpMarket"
    assert captured["submit"]["call_function"] == "cancel_order"
    assert captured["submit"]["call_params"] == {
        "params": {
            "subaccount": "0x" + "22" * 20,
            "order_id": 99,
            "market_id": 3,
            "cancel_reason": "UserCanceled",
            "fast_cancel": False,
        }
    }
    assert captured["submit"]["event"] == "OrderCancelled"


def test_perp_cancel_fast_cancel_waits_for_inclusion_only(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _DummyTx:
        tx_hash = "0xtx"
        extrinsic_hash = "0xex"

    def fake_submit_pallet_call(**kwargs):
        captured["submit"] = kwargs
        return _DummyTx()

    def fail_wait_event(**_kwargs):
        raise AssertionError("fast_cancel must not wait for the pallet event")

    monkeypatch.setattr(_perp_market, "submit_pallet_call", fake_submit_pallet_call)
    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", fail_wait_event)

    result = _perp_market.cancel_perp_order(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "44" * 20,
        subaccount="0x" + "22" * 20,
        market_id=3,
        order_id=99,
        fast_cancel=True,
    )

    assert result.order_id == 99
    assert captured["submit"]["call_function"] == "cancel_order"
    assert captured["submit"]["call_params"] == {
        "params": {
            "subaccount": "0x" + "22" * 20,
            "order_id": 99,
            "market_id": 3,
            "cancel_reason": "UserCanceled",
            "fast_cancel": True,
        }
    }
    assert captured["submit"]["wait_for_finalized"] is False


def test_subaccount_no_op_uses_timestamp_nonce(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call(**kwargs):
        captured["submit"] = kwargs
        return types.SimpleNamespace(tx_hash="0xtx", extrinsic_hash="0xext")

    monkeypatch.setattr(_subaccount, "submit_pallet_call", fake_submit_pallet_call)

    result = _subaccount.no_op(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "51" * 20,
        nonce_ms=1781757000999,
    )

    assert result.tx_hash == "0xtx"
    assert result.event is None
    assert captured["submit"]["call_module"] == "Subaccount"
    assert captured["submit"]["call_function"] == "no_op"
    assert captured["submit"]["call_params"] == {}
    assert captured["submit"]["use_timestamp_nonce"] is True
    assert captured["submit"]["nonce_ms"] == 1781757000999


def test_perp_settle_pnl_single_market_waits_for_event(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return types.SimpleNamespace(
            tx_hash="0xtx",
            extrinsic_hash="0xext",
            fields_json='{"owner": "0x2222222222222222222222222222222222222222", '
            '"market_id": 3, "unrealized": -500, "funding": -2, "total": -502}',
        )

    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    result = _perp_market.settle_pnl(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "44" * 20,
        subaccount="0x" + "22" * 20,
        market_id=3,
        nonce=7,
    )

    assert captured["submit"]["call_module"] == "PerpMarket"
    assert captured["submit"]["call_function"] == "settle_pnl"
    assert captured["submit"]["call_params"] == {
        "subaccount": "0x" + "22" * 20,
        "market_id": 3,
    }
    assert captured["submit"]["use_timestamp_nonce"] is False
    assert captured["submit"]["nonce_ms"] == 7
    assert captured["submit"]["event"] == "SettlePnl"
    assert result.market_id == 3
    assert result.unrealized == -500
    assert result.funding == -2
    assert result.total == -502


def test_perp_modify_order_builds_cancel_place_ops(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_modify_orders(**kwargs):
        captured["submit"] = kwargs
        return types.SimpleNamespace(
            tx_hash="0xtx",
            extrinsic_hash="0xext",
            fields_json='{"order_id": 777}',
        )

    monkeypatch.setattr(_perp_market, "_submit_modify_orders", fake_submit_modify_orders)

    result = _perp_market.modify_perp_order(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "44" * 20,
        subaccount="0x" + "22" * 20,
        market_id=3,
        order_id=99,
        is_long=True,
        price=1_800_000_000,
        size=10**15,
        cloid=2**31 - 1,
        nonce_ms=1781757000123,
    )

    assert result.order_id == 777
    assert result.canceled_order_id == 99
    assert captured["submit"]["pallet"] == "PerpMarket"
    assert captured["submit"]["event"] == "OrderPlaced"
    assert captured["submit"]["nonce_ms"] == 1781757000123
    assert captured["submit"]["ops"] == [
        {
            "Cancel": {
                "Perp": {
                    "subaccount": "0x" + "22" * 20,
                    "order_id": 99,
                    "market_id": 3,
                    "cancel_reason": "UserCanceled",
                    "fast_cancel": False,
                }
            }
        },
        {
            "Place": {
                "Perp": {
                    "subaccount": "0x" + "22" * 20,
                    "market_id": 3,
                    "is_long": True,
                    "size": 10**15,
                    "price": 1_800_000_000,
                    "order_type": {"Limit": "GTC"},
                    "take_profit": None,
                    "stop_loss": None,
                    "reduce_only": False,
                    "post_only": "None",
                    "cloid": 2**31 - 1,
                }
            }
        },
    ]


def test_perp_modify_order_new_total_quantity(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_modify_orders(**kwargs):
        captured["submit"] = kwargs
        return types.SimpleNamespace(tx_hash="0xtx", extrinsic_hash="0xext", fields_json='{"order_id": 777}')

    def fake_order_info(**_kwargs):
        return types.SimpleNamespace(size_filled=4 * 10**14)  # 0.0004 ETH filled

    monkeypatch.setattr(_perp_market, "_submit_modify_orders", fake_submit_modify_orders)
    monkeypatch.setattr(_perp_market, "order_info", fake_order_info)

    base = {
        "substrate_ws": "wss://node",
        "evm_rpc_url": "https://rpc",
        "private_key": "0xpk",
        "precompile_address": "0x" + "44" * 20,
        "subaccount": "0x" + "22" * 20,
        "market_id": 3,
        "order_id": 99,
        "is_long": True,
        "price": 1_800_000_000,
    }

    # new_total > filled -> new size = new_total - filled
    result = _perp_market.modify_perp_order(**base, new_total_quantity=10**15)
    assert result.order_id == 777
    place = captured["submit"]["ops"][1]["Place"]["Perp"]
    assert place["size"] == 10**15 - 4 * 10**14

    # new_total == filled -> degrades to plain cancel
    cancelled: dict[str, object] = {}

    def fake_cancel_perp_order(**kwargs):
        cancelled["call"] = kwargs
        return types.SimpleNamespace(order_id=99, tx_hash="0xtx2", extrinsic_hash="0xext2")

    monkeypatch.setattr(_perp_market, "cancel_perp_order", fake_cancel_perp_order)
    result = _perp_market.modify_perp_order(**base, new_total_quantity=4 * 10**14)
    assert result.order_id == 99
    assert result.canceled_order_id == 99
    assert cancelled["call"]["order_id"] == 99

    # new_total < filled -> rejected locally
    with pytest.raises(ValueError, match="new_total_quantity"):
        _perp_market.modify_perp_order(**base, new_total_quantity=1)

    # size / new_total_quantity are mutually exclusive and one is required
    with pytest.raises(ValueError, match="exactly one"):
        _perp_market.modify_perp_order(**base, size=1, new_total_quantity=1)
    with pytest.raises(ValueError, match="exactly one"):
        _perp_market.modify_perp_order(**base)


def test_spot_modify_order_builds_cancel_place_ops(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_modify_orders(**kwargs):
        captured["submit"] = kwargs
        return types.SimpleNamespace(
            tx_hash="0xtx",
            extrinsic_hash="0xext",
            fields_json='{"order_id": 888}',
        )

    monkeypatch.setattr(_spot_market, "_submit_modify_orders", fake_submit_modify_orders)

    pair = "0x" + "33" * 32
    result = _spot_market.modify_spot_order(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "55" * 20,
        subaccount="0x" + "22" * 20,
        pair=pair,
        order_id=99,
        is_buy=True,
        quote_amount=1_500_000,
        base_amount=10**15,
        cloid=2**31 - 1,
    )

    assert result.order_id == 888
    assert result.canceled_order_id == 99
    assert captured["submit"]["pallet"] == "SpotMarket"
    assert captured["submit"]["event"] == "StateOrderBuy"
    assert captured["submit"]["ops"] == [
        {
            "Cancel": {
                "Spot": {
                    "subaccount": "0x" + "22" * 20,
                    "pair": pair,
                    "order_id": 99,
                    "is_buy": True,
                    "cancel_reason": "UserCanceled",
                    "fast_cancel": False,
                }
            }
        },
        {
            "Place": {
                "Spot": {
                    "subaccount": "0x" + "22" * 20,
                    "pair": pair,
                    "is_buy": True,
                    "quote_amount": 1_500_000,
                    "base_amount": 10**15,
                    "order_type": {"Limit": "GTC"},
                    "post_only": "None",
                    "reduce_only": False,
                    "cloid": 2**31 - 1,
                }
            }
        },
    ]


def test_perp_settle_pnl_all_markets_inclusion_only(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call(**kwargs):
        captured["submit"] = kwargs
        return types.SimpleNamespace(tx_hash="0xtx", extrinsic_hash="0xext")

    def fail_wait_event(**_kwargs):
        raise AssertionError("settle-all must not wait for a SettlePnl event")

    monkeypatch.setattr(_perp_market, "submit_pallet_call", fake_submit_pallet_call)
    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", fail_wait_event)

    result = _perp_market.settle_pnl(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "44" * 20,
        subaccount="0x" + "22" * 20,
    )

    assert result.tx_hash == "0xtx"
    assert result.event is None
    assert captured["submit"]["call_function"] == "settle_pnl"
    assert captured["submit"]["call_params"] == {
        "subaccount": "0x" + "22" * 20,
        "market_id": None,
    }
    assert captured["submit"]["use_timestamp_nonce"] is False


def test_perp_position_decode_supports_legacy_and_settle_layouts() -> None:
    base = [
        3, True, 10**18, 1_800_000_000, 10, -5, 1, 0, 0,
        bytes.fromhex("22" * 20), 0, 0, 0,
    ]
    raw_v1 = encode(
        [f"{_perp_market._PERP_POSITION_TUPLE}[]"],
        [[tuple(base)]],
    )
    (pos_v1,) = [_perp_market._decode_perp_position(p) for p in _perp_market._decode_perp_positions(raw_v1)]
    assert pos_v1.last_settle_price is None
    assert pos_v1.liquidate_price == 0

    raw_v2 = encode(
        [f"{_perp_market._PERP_POSITION_TUPLE_V2}[]"],
        [[tuple(base + [1_800_000_000])]],
    )
    (pos_v2,) = [_perp_market._decode_perp_position(p) for p in _perp_market._decode_perp_positions(raw_v2)]
    assert pos_v2.last_settle_price == 1_800_000_000


def test_perp_position_and_order_decode_scaled_leverage() -> None:
    # Regression: on-chain leverage is u64 (scaled by LEVERAGE_PRECISION=1000,
    # 10x=10000); the SDK used to decode it as uint8 -> NonEmptyPaddingBytes (seen live on devnet)
    pos = (
        3, True, 10**18, 1_800_000_000, 10000, -5, 1, 0, 0,
        bytes.fromhex("22" * 20), 0, 0, 0, 1_800_000_000,
    )
    raw = encode(
        [f"{_perp_market._PERP_POSITION_TUPLE_V2}[]"],
        [[pos]],
    )
    (decoded,) = [_perp_market._decode_perp_position(p) for p in _perp_market._decode_perp_positions(raw)]
    assert decoded.leverage == 10000

    order = (
        1, bytes.fromhex("22" * 20), 3, True, 10**18, 1_800_000_000,
        0, 1_700_000_000_000, 10000, 0, 0, 0, 10**18, 0, 0,
    )
    raw_order = encode([_perp_market._PERP_ORDER_TUPLE], [order])
    (decoded_order,) = _perp_market.decode_abi([_perp_market._PERP_ORDER_TUPLE], raw_order)
    assert decoded_order[8] == 10000  # leverage field


def test_perp_market_decode_supports_legacy_and_funding_index_layouts() -> None:
    market = (
        3, b"ETH-USDC", b"eth", 18, 1, b"", 100, 125, 999, 1_800_000_000, 1_800_000_000,
        500, 200, 100, -100, (10**15, 10_000, 10**15), 10**20, 272, 251, 10**17, 10**8, 2, 2,
    )
    info_v1 = _perp_market._decode_perp_market(market)
    assert info_v1.cumulative_funding_index is None
    info_v2 = _perp_market._decode_perp_market(market + (-777,))
    assert info_v2.cumulative_funding_index == -777
    with pytest.raises(RuntimeError, match="unexpected perpMarkets layout"):
        _perp_market._decode_perp_market(market + (-777, 1))


def test_perp_close_actions_use_pallet_calls(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    result = _perp_market.close_position_limit(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "44" * 20,
        subaccount="0x" + "22" * 20,
        market_id=3,
        price=1000,
        slippage=9,
        nonce=1781757000123,
    )

    assert result.order_id == 123
    assert captured["submit"]["call_module"] == "PerpMarket"
    assert captured["submit"]["call_function"] == "close_position"
    assert captured["submit"]["call_params"] == {
        "subaccount": "0x" + "22" * 20,
        "market_id": 3,
        "price": 1000,
        "slippage": 9,
    }
    assert captured["submit"]["event"] == "OrderPlaced"


def test_perp_close_market_uses_zero_price(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    _perp_market.close_position_market(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "44" * 20,
        subaccount="0x" + "22" * 20,
        market_id=3,
        slippage=None,
    )

    assert captured["submit"]["call_function"] == "close_position"
    assert captured["submit"]["call_params"] == {
        "subaccount": "0x" + "22" * 20,
        "market_id": 3,
        "price": 0,
        "slippage": None,
    }


def test_spot_order_actions_use_pallet_calls(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_spot_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    pair = "0x" + "33" * 32
    result = _spot_market.subaccount_place_order_buy_b(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "55" * 20,
        subaccount="0x" + "22" * 20,
        pair=pair,
        quote_amount=1000,
        base_amount=10,
        post_only=1,
        nonce_ms=1781757000456,
    )

    assert result.order_id == 123
    assert captured["submit"]["call_module"] == "SpotMarket"
    assert captured["submit"]["call_function"] == "place_order"
    assert captured["submit"]["call_params"] == {
        "params": {
            "subaccount": "0x" + "22" * 20,
            "pair": pair,
            "is_buy": True,
            "quote_amount": 1000,
            "base_amount": 10,
            "order_type": {"Limit": "GTC"},
            "post_only": "MustPostOnly",
            "reduce_only": False,
            "cloid": None,
        }
    }
    assert captured["submit"]["event"] == "StateOrderBuy"


def test_spot_order_action_variants_and_validation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_spot_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    pair = "0x" + "33" * 32
    base = {
        "substrate_ws": "wss://node",
        "evm_rpc_url": "https://rpc",
        "private_key": "0xpk",
        "precompile_address": "0x" + "55" * 20,
        "subaccount": "0x" + "22" * 20,
        "pair": pair,
        "quote_amount": 1000,
        "base_amount": 10,
    }

    _spot_market.subaccount_place_order_sell_b(**base, post_only=2, reduce_only=True)
    assert captured["submit"]["call_function"] == "place_order"
    assert captured["submit"]["event"] == "StateOrderSell"
    assert captured["submit"]["call_params"] == {
        "params": {
            "subaccount": "0x" + "22" * 20,
            "pair": pair,
            "is_buy": False,
            "quote_amount": 1000,
            "base_amount": 10,
            "order_type": {"Limit": "GTC"},
            "post_only": "Adaptive",
            "reduce_only": True,
            "cloid": None,
        }
    }

    _spot_market.subaccount_place_market_order_buy_b_without_price(
        **base,
        auto_cancel=True,
        reduce_only=True,
    )
    assert captured["submit"]["call_function"] == "place_order"
    assert captured["submit"]["call_params"]["params"]["order_type"] == {"Market": None}
    assert captured["submit"]["call_params"]["params"]["is_buy"] is True
    assert captured["submit"]["call_params"]["params"]["post_only"] == "None"
    assert captured["submit"]["call_params"]["params"]["reduce_only"] is True

    _spot_market.subaccount_place_market_order_sell_b_with_price(
        **base,
        slippage=9,
        auto_cancel=True,
    )
    assert captured["submit"]["call_function"] == "place_order"
    assert captured["submit"]["call_params"]["params"]["order_type"] == {"Market": 9}
    assert captured["submit"]["call_params"]["params"]["is_buy"] is False

    with pytest.raises(ValueError, match="spot market slippage"):
        _spot_market.subaccount_place_market_order_buy_b_with_price(**base, slippage=-1)

    # runtime 187+: 100 is valid now (on-chain bound is the pair's
    # max_deviation_bps, default 500); only the hard bound 10000 is local.
    with pytest.raises(ValueError, match="spot market slippage"):
        _spot_market.subaccount_place_market_order_buy_b_with_price(**base, slippage=10001)

    with pytest.raises(ValueError, match="invalid post_only"):
        _spot_market.subaccount_place_order_buy_b(**base, post_only=9)


def test_spot_order_actions_ioc_uses_pallet_calls(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_spot_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    pair = "0x" + "33" * 32

    result_buy = _spot_market.subaccount_place_order_buy_ioc_b(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "55" * 20,
        subaccount="0x" + "22" * 20,
        pair=pair,
        quote_amount=1000,
        base_amount=10,
        reduce_only=True,
        nonce_ms=1781757000789,
    )
    assert result_buy.order_id == 123
    assert captured["submit"]["call_module"] == "SpotMarket"
    assert captured["submit"]["call_function"] == "place_order"
    assert captured["submit"]["call_params"] == {
        "params": {
            "subaccount": "0x" + "22" * 20,
            "pair": pair,
            "is_buy": True,
            "quote_amount": 1000,
            "base_amount": 10,
            "order_type": {"Limit": "IOC"},
            "post_only": "None",
            "reduce_only": True,
            "cloid": None,
        }
    }
    assert captured["submit"]["event"] == "StateOrderBuy"

    result_sell = _spot_market.subaccount_place_order_sell_ioc_b(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "55" * 20,
        subaccount="0x" + "22" * 20,
        pair=pair,
        quote_amount=2000,
        base_amount=20,
        cloid=2**31 - 1,
    )
    assert result_sell.order_id == 123
    assert captured["submit"]["call_function"] == "place_order"
    assert captured["submit"]["call_params"]["params"]["order_type"] == {"Limit": "IOC"}
    assert captured["submit"]["call_params"]["params"]["is_buy"] is False
    assert captured["submit"]["call_params"]["params"]["post_only"] == "None"
    assert captured["submit"]["call_params"]["params"]["reduce_only"] is False
    assert captured["submit"]["call_params"]["params"]["cloid"] == 2**31 - 1
    assert captured["submit"]["event"] == "StateOrderSell"


def test_spot_cancel_uses_pallet_call(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call_wait_event(**kwargs):
        captured["submit"] = kwargs
        return _DummyEvent()

    monkeypatch.setattr(_spot_market, "submit_pallet_call_wait_event", fake_submit_pallet_call_wait_event)

    pair = "0x" + "33" * 32
    result = _spot_market.subaccount_cancel_order_buy_b(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "55" * 20,
        subaccount="0x" + "22" * 20,
        pair=pair,
        order_id=99,
    )

    assert result.order_id == 123
    assert captured["submit"]["call_module"] == "SpotMarket"
    assert captured["submit"]["call_function"] == "cancel_order"
    assert captured["submit"]["call_params"] == {
        "params": {
            "subaccount": "0x" + "22" * 20,
            "pair": pair,
            "order_id": 99,
            "is_buy": True,
            "cancel_reason": "UserCanceled",
            "fast_cancel": False,
        }
    }
    assert captured["submit"]["event"] == "OrderCancelled"


def test_spot_fast_cancel_waits_for_inclusion_only(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_submit_pallet_call(**kwargs):
        captured["submit"] = kwargs
        return types.SimpleNamespace(tx_hash="0xtx", extrinsic_hash="0xext")

    def fail_wait_event(**_kwargs):
        raise AssertionError("fast_cancel must not wait for the pallet event")

    monkeypatch.setattr(_spot_market, "submit_pallet_call", fake_submit_pallet_call)
    monkeypatch.setattr(_spot_market, "submit_pallet_call_wait_event", fail_wait_event)

    pair = "0x" + "33" * 32
    result = _spot_market.subaccount_cancel_order_buy_b(
        substrate_ws="wss://node",
        evm_rpc_url="https://rpc",
        private_key="0xpk",
        precompile_address="0x" + "55" * 20,
        subaccount="0x" + "22" * 20,
        pair=pair,
        order_id=99,
        fast_cancel=True,
    )

    assert result.order_id == 99
    assert captured["submit"]["call_function"] == "cancel_order"
    assert captured["submit"]["call_params"]["params"]["fast_cancel"] is True
    assert captured["submit"]["wait_for_finalized"] is False


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


def _patch_evm_tx_module(monkeypatch, module) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_build_signed_tx(**kwargs):
        kwargs.setdefault("nonce_ms", None)
        kwargs.setdefault("use_timestamp_nonce", True)
        captured["build"] = kwargs
        return _DummySigned()

    monkeypatch.setattr(module, "build_signed_tx", fake_build_signed_tx)
    if hasattr(module, "submit_signed_tx_wait_event"):
        monkeypatch.setattr(module, "submit_signed_tx_wait_event", lambda **_kwargs: _DummyEvent())
    if hasattr(module, "submit_signed_tx"):
        monkeypatch.setattr(module, "submit_signed_tx", lambda **_kwargs: _DummyEvent())
    return captured


def _assert_evm_tx_case(
    monkeypatch,
    *,
    module,
    call,
    expected_selector: str,
    use_timestamp_nonce: bool,
    expected_nonce_ms: int | None,
) -> None:
    captured = _patch_evm_tx_module(monkeypatch, module)
    call()
    build = captured["build"]
    assert build["data"][:4].hex() == expected_selector[2:]
    assert build["use_timestamp_nonce"] is use_timestamp_nonce
    assert build["nonce_ms"] == expected_nonce_ms


def test_low_level_non_order_sdk_uses_pallet_calls_where_available(monkeypatch) -> None:
    subaccount = "0x" + "22" * 20
    other = "0x" + "44" * 20
    zero32 = "0x" + "00" * 32
    base = {
        "substrate_ws": "wss://node",
        "evm_rpc_url": "https://rpc",
        "private_key": "0xpk",
        "wait_for_finalized": False,
    }
    perp = {**base, "precompile_address": "0x000000000000000000000000000000000000044E"}
    lending = {**base, "precompile_address": "0x0000000000000000000000000000000000000450"}
    subacct = {**base, "precompile_address": "0x0000000000000000000000000000000000000451"}

    captured: dict[str, dict] = {}

    def capture(name):
        def inner(**kwargs):
            captured[name] = kwargs
            return _DummyEvent()

        return inner

    def capture_submit(name):
        def inner(**kwargs):
            captured[name] = kwargs
            return types.SimpleNamespace(tx_hash="0xtx", extrinsic_hash="0xext")

        return inner

    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", capture("set_pnl"))
    monkeypatch.setattr(_lending, "submit_pallet_call_wait_event", capture("lending_event"))
    monkeypatch.setattr(_lending, "submit_pallet_call", capture_submit("lending_submit"))
    monkeypatch.setattr(_lending, "_signer_address", lambda _private_key: other)
    monkeypatch.setattr(_subaccount, "submit_pallet_call_wait_event", capture("subaccount_event"))
    monkeypatch.setattr(_subaccount, "submit_pallet_call", capture_submit("subaccount_submit"))

    _perp_market.set_profit_and_loss_point(
        **perp,
        subaccount=subaccount,
        market_id=3,
        take_profit_point=2000,
    )
    assert captured["set_pnl"]["call_module"] == "PerpMarket"
    assert captured["set_pnl"]["call_function"] == "set_profit_and_loss_point"
    assert captured["set_pnl"]["call_params"] == {
        "subaccount": subaccount,
        "market_id": 3,
        "take_profit_point": 2000,
        "stop_loss_point": 0,
    }

    cases = [
        (
            lambda: _lending.deposit(**lending, subaccount=subaccount, asset=b"USDC", amount=1),
            "deposit",
            {
                "from_subaccount": None,
                "subaccount": subaccount,
                "market_id": 1,
                "asset": b"USDC",
                "amount": 1,
            },
            True,
        ),
        (
            lambda: _lending.deposit_from_subaccount(
                **lending,
                from_subaccount=other,
                subaccount=subaccount,
                asset=b"USDC",
                amount=1,
            ),
            "deposit",
            {
                "from_subaccount": (other, False),
                "subaccount": subaccount,
                "market_id": 1,
                "asset": b"USDC",
                "amount": 1,
            },
            True,
        ),
        (
            lambda: _lending.withdraw(**lending, subaccount=subaccount, asset=b"USDC", amount=1),
            "withdraw",
            {
                "subaccount": subaccount,
                "to": other,
                "market_id": 1,
                "asset": b"USDC",
                "amount": 1,
                "mode": "OnlyWithdraw",
            },
            True,
        ),
        (
            lambda: _lending.withdraw_and_swap_evm(
                **lending,
                subaccount=subaccount,
                asset=b"USDC",
                amount=1,
                dst_chain_id=1,
                token_id=1,
                dst_recipient=zero32,
                refund_address=other,
                salt=zero32,
                custom_data=b"",
                signature=b"",
                consumer_address=other,
            ),
            "withdraw",
            {
                "subaccount": subaccount,
                "to": other,
                "market_id": 1,
                "asset": b"USDC",
                "amount": 1,
                "mode": {
                    "WithdrawAndSwap": {
                        "consumer_address": other,
                        "dst_chain_id": 1,
                        "token_id": 1,
                        "dst_recipient": zero32,
                        "refund_address": other,
                        "salt": zero32,
                        "custom_data": b"",
                        "signature": b"",
                    }
                },
            },
            False,
        ),
        (
            lambda: _lending.withdraw_and_swap(
                **lending,
                subaccount=subaccount,
                asset=b"USDC",
                amount=1,
                dst_chain_id=1,
                token_id=1,
                dst_recipient=zero32,
                refund_address=other,
                salt=zero32,
                custom_data=b"",
                signature=b"",
                consumer_address=other,
            ),
            "withdraw",
            {
                "subaccount": subaccount,
                "to": other,
                "market_id": 1,
                "asset": b"USDC",
                "amount": 1,
                "mode": {
                    "WithdrawAndSwap": {
                        "consumer_address": other,
                        "dst_chain_id": 1,
                        "token_id": 1,
                        "dst_recipient": zero32,
                        "refund_address": other,
                        "salt": zero32,
                        "custom_data": b"",
                        "signature": b"",
                    }
                },
            },
            False,
        ),
        (
            lambda: _lending.withdraw_and_swap_btc(
                **lending,
                subaccount=subaccount,
                asset=b"USDC",
                amount=1,
                dst_recipient=zero32,
                refund_address=other,
                salt=zero32,
                signature=b"",
                consumer_address=other,
            ),
            "withdraw",
            {
                "subaccount": subaccount,
                "to": other,
                "market_id": 1,
                "asset": b"USDC",
                "amount": 1,
                "mode": {
                    "WithdrawAndSwapBtc": {
                        "consumer_address": other,
                        "dst_recipient": zero32,
                        "refund_address": other,
                        "salt": zero32,
                        "signature": b"",
                    }
                },
            },
            False,
        ),
        (
            lambda: _lending.borrow(
                **lending,
                borrower=subaccount,
                market_id=1,
                asset=b"USDC",
                amount=1,
            ),
            "borrow",
            {
                "borrower": subaccount,
                "market_id": 1,
                "asset": b"USDC",
                "amount": 1,
                "mode": "OnlyWithdraw",
            },
            True,
        ),
        (
            lambda: _lending.borrow_and_swap_evm(
                **lending,
                borrower=subaccount,
                market_id=1,
                asset=b"USDC",
                amount=1,
                dst_chain_id=1,
                token_id=1,
                dst_recipient=zero32,
                refund_address=other,
                salt=zero32,
                custom_data=b"",
                signature=b"",
                consumer_address=other,
            ),
            "borrow",
            {
                "borrower": subaccount,
                "market_id": 1,
                "asset": b"USDC",
                "amount": 1,
                "mode": {
                    "WithdrawAndSwap": {
                        "consumer_address": other,
                        "dst_chain_id": 1,
                        "token_id": 1,
                        "dst_recipient": zero32,
                        "refund_address": other,
                        "salt": zero32,
                        "custom_data": b"",
                        "signature": b"",
                    }
                },
            },
            False,
        ),
        (
            lambda: _lending.borrow_and_swap(
                **lending,
                borrower=subaccount,
                market_id=1,
                asset=b"USDC",
                amount=1,
                dst_chain_id=1,
                token_id=1,
                dst_recipient=zero32,
                refund_address=other,
                salt=zero32,
                custom_data=b"",
                signature=b"",
                consumer_address=other,
            ),
            "borrow",
            {
                "borrower": subaccount,
                "market_id": 1,
                "asset": b"USDC",
                "amount": 1,
                "mode": {
                    "WithdrawAndSwap": {
                        "consumer_address": other,
                        "dst_chain_id": 1,
                        "token_id": 1,
                        "dst_recipient": zero32,
                        "refund_address": other,
                        "salt": zero32,
                        "custom_data": b"",
                        "signature": b"",
                    }
                },
            },
            False,
        ),
        (
            lambda: _lending.borrow_and_swap_btc(
                **lending,
                borrower=subaccount,
                market_id=1,
                asset=b"USDC",
                amount=1,
                dst_recipient=zero32,
                refund_address=other,
                salt=zero32,
                signature=b"",
                consumer_address=other,
            ),
            "borrow",
            {
                "borrower": subaccount,
                "market_id": 1,
                "asset": b"USDC",
                "amount": 1,
                "mode": {
                    "BorrowAndSwapBtc": {
                        "consumer_address": other,
                        "dst_recipient": zero32,
                        "refund_address": other,
                        "salt": zero32,
                        "signature": b"",
                    }
                },
            },
            False,
        ),
        (
            lambda: _lending.repay(**lending, who=subaccount, market_id=1, asset=b"USDC", amount=1),
            "repay",
            {"who": subaccount, "market_id": 1, "asset": b"USDC", "amount": 1},
            True,
        ),
        (
            lambda: _lending.buy_quota(**lending, account=subaccount, quota=1),
            "buy_quota",
            {"address": subaccount, "quota": 1, "from_subaccount": None},
            False,
        ),
        (
            lambda: _lending.buy_quota(**lending, account=subaccount, quota=2, from_subaccount=subaccount),
            "buy_quota",
            {"address": subaccount, "quota": 2, "from_subaccount": subaccount},
            False,
        ),
    ]

    for call, call_function, call_params, has_event in cases:
        captured.clear()
        call()
        key = "lending_event" if has_event else "lending_submit"
        assert captured[key]["call_module"] == "Lending"
        assert captured[key]["call_function"] == call_function
        assert captured[key]["call_params"] == call_params

    cases = [
        (
            lambda: _subaccount.initialize_subaccount(**subacct, name=b"test"),
            "initialize_subaccount",
            {"name": b"test"},
            True,
        ),
        (
            lambda: _subaccount.delete_subaccount(**subacct, subaccount=subaccount),
            "delete_subaccount",
            {"subaccount": subaccount},
            True,
        ),
        (
            lambda: _subaccount.set_delegate_account(
                **subacct,
                delegate=other,
                name=b"mm-bot",
                valid_until=1781757000999,
            ),
            "set_delegate_account",
            {
                "delegate": other,
                "name": b"mm-bot",
                "valid_until": 1781757000999,
            },
            True,
        ),
        (
            lambda: _subaccount.update_delegate_mode(
                **subacct,
                delegate=other,
                new_mode=1,
            ),
            "update_delegate_mode",
            {"address": other, "new_mode": "DepositOrWithdraw"},
            True,
        ),
        (
            lambda: _subaccount.remove_delegate_account(
                **subacct,
                delegate=other,
            ),
            "remove_delegate_account",
            {"delegate": other},
            True,
        ),
        (
            lambda: _subaccount.set_spot_margin(
                **subacct,
                subaccount=subaccount,
                enable_spot_margin=True,
            ),
            "set_spot_margin",
            {"address": subaccount, "enable_spot_margin": True},
            False,
        ),
        (
            lambda: _subaccount.rename_subaccount(**subacct, subaccount=subaccount, new_name=b"new"),
            "rename_subaccount",
            {"subaccount": subaccount, "new_name": b"new"},
            False,
        ),
        (
            lambda: _subaccount.liquidate_perp_by_transfer(
                **subacct,
                market_index=1,
                liquidator_max_base_amount=1,
                target_subaccount=subaccount,
                liquidator=other,
            ),
            "liquidate_perp_by_transfer",
            {
                "market_index": 1,
                "liquidator_max_base_amount": 1,
                "limit_price": None,
                "target_subaccount": subaccount,
                "liquidator": other,
            },
            False,
        ),
        (
            lambda: _subaccount.liquidate_spot_by_transfer(
                **subacct,
                asset_symbol=b"ETH",
                liability_symbol=b"USDC",
                target_account_addr=subaccount,
                liquidator=other,
                liquidator_max_liability_transfer=1,
                lending_market_id=1,
            ),
            "liquidate_spot_by_transfer",
            {
                "asset_symbol": b"ETH",
                "liability_symbol": b"USDC",
                "target_account_addr": subaccount,
                "liquidator": other,
                "limit_price": None,
                "liquidator_max_liability_transfer": 1,
                "lending_market_id": 1,
            },
            False,
        ),
        (
            lambda: _subaccount.liquidate_by_market(
                **subacct,
                target_subaccount=subaccount,
                liquidator=other,
            ),
            "liquidate_by_market",
            {"target_subaccount": subaccount, "liquidator": other},
            False,
        ),
    ]

    for call, call_function, call_params, has_event in cases:
        captured.clear()
        call()
        key = "subaccount_event" if has_event else "subaccount_submit"
        assert captured[key]["call_module"] == "Subaccount"
        assert captured[key]["call_function"] == call_function
        assert captured[key]["call_params"] == call_params


def test_low_level_remaining_evm_tx_sdk_nonce_modes_match_runtime_selectors(monkeypatch) -> None:
    other = "0x" + "44" * 20
    zero32 = "0x" + "00" * 32
    base = {
        "substrate_ws": "wss://node",
        "evm_rpc_url": "https://rpc",
        "private_key": "0xpk",
        "wait_for_finalized": False,
    }
    lending = {**base, "precompile_address": "0x0000000000000000000000000000000000000450"}

    cases = [
        (
            _lending,
            lambda: _lending.bridge_invoke(**lending, uid=zero32, amount=1, custom_data=b""),
            _selector("bridgeInvoke(bytes32,uint256,bytes)"),
            False,
            None,
        ),
    ]

    for module, call, expected_selector, use_timestamp_nonce, expected_nonce_ms in cases:
        _assert_evm_tx_case(
            monkeypatch,
            module=module,
            call=call,
            expected_selector=expected_selector,
            use_timestamp_nonce=use_timestamp_nonce,
            expected_nonce_ms=expected_nonce_ms,
        )

def test_chain_client_global_nonce_ms_does_not_default_native_nonce_paths(monkeypatch) -> None:
    client = ChainClient(private_key="0xpk", subaccount="0x" + "22" * 20, nonce_ms=1234567890123)
    captured: dict[str, dict] = {}

    def capture(name):
        def inner(**kwargs):
            captured[name] = kwargs
            return _DummyEvent()

        return inner

    monkeypatch.setattr(_client_mod, "subaccount_place_order_buy_b", capture("spot_order"))
    monkeypatch.setattr(_client_mod, "close_position", capture("close_position"))
    monkeypatch.setattr(_client_mod, "set_profit_and_loss_point", capture("set_pnl"))
    monkeypatch.setattr(_client_mod, "deposit", capture("deposit"))
    monkeypatch.setattr(_client_mod, "initialize_subaccount", capture("initialize_subaccount"))

    client.spot_market.subaccount_place_order_buy_b(
        pair="0x" + "33" * 32,
        quote_amount=1000,
        base_amount=10,
    )
    client.perp_market.close_position(market_id=3, price=1000)
    client.perp_market.set_profit_and_loss_point(market_id=3, take_profit_point=2000)
    client.lending.deposit(subaccount="0x" + "22" * 20, asset=b"USDC", amount=1)
    client.subaccount_client.initialize_subaccount(name=b"test")

    assert captured["spot_order"]["nonce_ms"] is None
    assert captured["close_position"]["nonce"] is None
    assert captured["set_pnl"]["nonce"] is None
    assert captured["deposit"]["nonce"] is None
    assert captured["initialize_subaccount"]["nonce"] is None
