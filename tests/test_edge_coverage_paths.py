from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest
from eth_abi import encode

if "substrateinterface" not in sys.modules:
    substrate_stub = types.ModuleType("substrateinterface")

    class _SubstrateInterfacePlaceholder:
        pass

    substrate_stub.SubstrateInterface = _SubstrateInterfacePlaceholder
    sys.modules["substrateinterface"] = substrate_stub

import deepx_sdk as dx
from deepx_sdk import (
    _lending,
    _market_resolver,
    _perp_market,
    _rpc_transport,
    _spot_market,
    _subaccount,
    _substrate,
    _tx_config,
    _types,
)
from deepx_sdk import api_v1 as api_v1_mod
from deepx_sdk import client as client_mod
from deepx_sdk import ws_client as ws_mod


ADDR = "0x" + "11" * 20
OTHER = "0x" + "22" * 20
PAIR = "0x" + "33" * 32


def test_transport_and_substrate_edge_helpers() -> None:
    with _rpc_transport.use_evm_rpc_config(
        user_agent=" ",
        headers={" ": "ignored", " X-Test ": 7},
        timeout_s=1.5,
    ):
        headers, timeout = _rpc_transport.rpc_request_options()
        assert headers["User-Agent"] == _rpc_transport.DEFAULT_USER_AGENT
        assert headers["X-Test"] == "7"
        assert timeout == 1.5

    calls: list[str] = []

    class _FallbackSubstrate:
        def query(self, module: str, _storage: str, _params: list[int]):
            calls.append(module)
            if len(calls) == 1:
                raise RuntimeError("first module failed")
            return types.SimpleNamespace(value={"id": 1})

    assert _substrate._query_perp_market(_FallbackSubstrate(), 1) == {"id": 1}
    assert calls[:2] == ["PerpMarket", "perp_market"]

    class _MissingSubstrate:
        def query(self, *_args):
            return types.SimpleNamespace(value=None)

    with pytest.raises(RuntimeError, match="failed to query PerpMarkets"):
        _substrate._query_perp_market(_MissingSubstrate(), 404)

    with pytest.raises(RuntimeError, match="unexpected storage value type"):
        _substrate._get_field([], "x")
    with pytest.raises(RuntimeError, match="missing field"):
        _substrate._get_field({}, "x")
    with pytest.raises(RuntimeError, match="unexpected storage value type"):
        _substrate._get_optional_int([], "x")
    assert _substrate._get_optional_int({"x": None}, "x") is None
    assert _market_resolver._first_present({"a": "", "b": None, "c": 3}, ("a", "b", "c")) == 3


def test_api_v1_private_validation_and_chain_tx_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    assert api_v1_mod._clean_params(
        {"blank": " ", "payload": {"x": 1}, "flag": False}
    ) == {"payload": '{"x": 1}', "flag": "false"}
    api_v1_mod._validate_merge_level(None)

    with pytest.raises(ValueError, match="invalid perp order_type"):
        api_v1_mod._perp_order_type(99)
    with pytest.raises(ValueError, match="invalid post_only"):
        api_v1_mod._post_only_param(99)
    with pytest.raises(ValueError, match="sort must be"):
        api_v1_mod._validate_sort("sideways")
    with pytest.raises(ValueError, match="start must be <= end"):
        api_v1_mod._validate_start_end(2, 1)
    with pytest.raises(ValueError, match="order_side must be"):
        api_v1_mod._normalize_order_side("hold")
    with pytest.raises(ValueError, match="missing is required"):
        api_v1_mod._require_value("missing", "")

    api = dx.ApiClient(
        base_url="http://api.test",
        substrate_ws="ws://node",
        evm_rpc_url="http://rpc",
        private_key="0xclient",
        chain_id=9,
        gas_limit=100,
        max_fee_per_gas=2,
        max_priority_fee_per_gas=1,
    )
    chain_tx = api.v1.chain_tx
    assert (
        chain_tx._resolve_required_str(
            name="precompile_address",
            override="",
            fallback="",
            default=" 0xabc ",
        )
        == "0xabc"
    )

    captured: dict[str, object] = {}

    def fake_build_signed_tx(**kwargs):
        captured["build_signed_tx"] = kwargs
        return types.SimpleNamespace(signed_tx="0xsigned", signer=ADDR)

    def fake_build_signed_extrinsic(**kwargs):
        captured["build_signed_extrinsic"] = kwargs
        return "0xextrinsic"

    def fake_request(method: str, path: str, **kwargs):
        captured["request"] = {"method": method, "path": path, **kwargs}
        return {"ok": True}

    monkeypatch.setattr(chain_tx, "_build_signed_tx", fake_build_signed_tx)
    monkeypatch.setattr(chain_tx, "_build_signed_extrinsic", fake_build_signed_extrinsic)
    monkeypatch.setattr(api, "request", fake_request)

    assert chain_tx._sign_and_submit(
        path="/v1/chain/tx/test",
        data=b"payload",
        evm_rpc_url="",
        private_key="",
        precompile_address="",
        fallback_precompile="",
        default_precompile=ADDR,
        chain_id=None,
        gas_limit=None,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=None,
        nonce_ms=None,
        use_timestamp_nonce=True,
    ) == {"ok": True}
    assert captured["build_signed_tx"]["evm_rpc_url"] == "http://rpc"
    assert captured["build_signed_tx"]["precompile_address"] == ADDR
    assert captured["build_signed_tx"]["chain_id"] == 9
    assert captured["build_signed_extrinsic"]["substrate_ws"] == "ws://node"
    assert captured["request"]["json_body"] == {"signedExtrinsic": "0xextrinsic"}

    native_chain_tx = dx.ApiClient(base_url="http://api.test").v1.chain_tx
    monkeypatch.setitem(sys.modules, "deepx_sdk._native_py", None)
    with pytest.raises(RuntimeError, match="Python signing backend unavailable"):
        native_chain_tx._build_signed_tx(
            evm_rpc_url="http://rpc",
            private_key="0xpk",
            precompile_address=ADDR,
            data=b"",
            chain_id=None,
            gas_limit=None,
            max_fee_per_gas=None,
            max_priority_fee_per_gas=None,
            use_legacy=False,
            nonce_ms=None,
            use_timestamp_nonce=True,
        )
    with pytest.raises(RuntimeError, match="Python substrate extrinsic builder unavailable"):
        native_chain_tx._build_signed_extrinsic(
            signed_tx="0xsigned",
            signer=ADDR,
            substrate_ws="ws",
        )
    with pytest.raises(RuntimeError, match="Python substrate extrinsic builder unavailable"):
        native_chain_tx._build_signed_pallet_call_extrinsic(
            private_key="0xpk",
            call_module="Module",
            call_function="call",
            call_params={},
        )


def test_api_v1_chain_tx_native_success_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_native = types.ModuleType("deepx_sdk._native_py")
    captured: dict[str, object] = {}

    def fake_build_signed_tx(**kwargs):
        captured["build_signed_tx"] = kwargs
        return types.SimpleNamespace(signed_tx="0xsigned", signer=ADDR)

    def fake_build_ethereum_transact_extrinsic(**kwargs):
        captured["build_extrinsic"] = kwargs
        return "0xextrinsic"

    def fake_build_signed_pallet_call_extrinsic(**kwargs):
        captured["build_pallet"] = kwargs
        return "0xpallet"

    fake_native.build_signed_tx = fake_build_signed_tx
    fake_native.build_ethereum_transact_extrinsic = fake_build_ethereum_transact_extrinsic
    fake_native.build_signed_pallet_call_extrinsic = fake_build_signed_pallet_call_extrinsic
    monkeypatch.setitem(sys.modules, "deepx_sdk._native_py", fake_native)

    api = dx.ApiClient(
        base_url="http://api.test",
        substrate_ws="ws://node",
        evm_rpc_user_agent="DeepXTest/1.0",
        evm_rpc_headers={"X-Test": "yes"},
        evm_rpc_timeout=2.5,
    )
    chain_tx = api.v1.chain_tx

    signed = chain_tx._build_signed_tx(
        evm_rpc_url="http://rpc",
        private_key="0xpk",
        precompile_address=ADDR,
        data=b"abc",
        chain_id=1,
        gas_limit=2,
        max_fee_per_gas=3,
        max_priority_fee_per_gas=4,
        use_legacy=False,
        nonce_ms=5,
        use_timestamp_nonce=True,
    )
    assert signed.signed_tx == "0xsigned"
    assert captured["build_signed_tx"]["data_hex"] == "0x616263"

    assert (
        chain_tx._build_signed_extrinsic(
            signed_tx="0xsigned",
            signer=ADDR,
            substrate_ws="ws://node",
        )
        == "0xextrinsic"
    )
    assert captured["build_extrinsic"]["signed_tx_hex"] == "0xsigned"

    assert (
        chain_tx._build_signed_pallet_call_extrinsic(
            private_key="0xpk",
            call_module="Module",
            call_function="call",
            call_params={"x": 1},
            nonce_ms=9,
        )
        == "0xpallet"
    )
    assert captured["build_pallet"]["substrate_ws"] == "ws://node"


def test_ws_edge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BytesWs:
        def __init__(self) -> None:
            self.sent: list[str | bytes] = []

        async def close(self) -> None:
            pass

        async def send(self, payload: str | bytes) -> None:
            self.sent.append(payload)

        async def recv(self) -> bytes:
            return b'{"method": "pong"}'

    async def fake_connect_additional(url: str, *, additional_headers=None, **kwargs):
        assert url == "ws://example.test/v1/ws"
        assert additional_headers == {"Authorization": "Bearer key"}
        assert kwargs["open_timeout"] == 1
        return _BytesWs()

    monkeypatch.setattr(
        ws_mod,
        "websockets",
        types.SimpleNamespace(connect=fake_connect_additional),
    )

    async def run_additional_headers() -> None:
        session = await ws_mod.WsClient(
            base_url="http://example.test",
            headers={"Authorization": "Bearer key"},
            open_timeout=1,
        ).connect()
        assert await session.recv_json() == {"method": "pong"}

    asyncio.run(run_additional_headers())

    captured: dict[str, object] = {}

    async def fake_connect_extra(url: str, **kwargs):
        captured["kwargs"] = kwargs
        return _BytesWs()

    def fail_signature(_obj):
        raise ValueError("no signature")

    monkeypatch.setattr(ws_mod.inspect, "signature", fail_signature)
    monkeypatch.setattr(ws_mod, "websockets", types.SimpleNamespace(connect=fake_connect_extra))

    async def run_extra_headers_fallback() -> None:
        await ws_mod.WsClient(
            base_url="ws://example.test",
            headers={"X-Test": "yes"},
        ).connect()

    asyncio.run(run_extra_headers_fallback())
    assert captured["kwargs"]["extra_headers"] == {"X-Test": "yes"}


def test_client_alias_and_identifier_edge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    assert client_mod._resolve_precompile_address(" 0xoverride ", "module", "default") == "0xoverride"

    with pytest.raises(ValueError, match="order_type must be limit, market, stop, or ioc"):
        client_mod._normalize_order_type("fill-or-kill")

    client = dx.ChainClient(
        substrate_ws="ws://node",
        evm_rpc_url="http://rpc",
        private_key="0xpk",
        subaccount=ADDR,
    )

    monkeypatch.setattr(
        client,
        "_resolve_perp_market_id",
        lambda *, market_id, symbol: {"ETH-USDC": 7}[symbol],
    )
    captured: dict[str, object] = {}

    def fake_user_perp_positions(**kwargs):
        captured["user_perp_positions"] = kwargs
        return []

    def fake_spot_market_buy(**kwargs):
        captured["spot_market_buy"] = kwargs
        return types.SimpleNamespace(order_id=1)

    monkeypatch.setattr(client_mod, "user_perp_positions", fake_user_perp_positions)
    monkeypatch.setattr(
        client_mod,
        "subaccount_place_market_order_buy_b_with_price",
        fake_spot_market_buy,
    )

    assert client.perp_market.user_perp_positions(user=ADDR, symbols=["ETH-USDC"]) == []
    assert captured["user_perp_positions"]["market_ids"] == [7]

    client.spot_market.place_order(
        pair=PAIR,
        side="buy",
        quote_amount=1,
        base_amount=2,
        order_type="market",
        slippage=3,
    )
    assert captured["spot_market_buy"]["slippage"] == 3


def test_perp_spot_lending_and_subaccount_edge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _market_resolver._as_list("not-a-list") == []
    assert _types.MarketSpec(min_order_size=5, tick_size=1, step_size=1).min_qty == 5

    def accepts_tx_config(*, nonce_ms=None):
        return nonce_ms

    with pytest.raises(TypeError, match="tx_config must be a TxConfig instance"):
        _tx_config.merge_tx_config_kwargs(accepts_tx_config, {"tx_config": object()})

    captured: dict[str, object] = {}

    def fake_place_perp_order(**kwargs):
        captured["place_perp_order"] = kwargs
        return types.SimpleNamespace(order_id=1)

    def fake_close_position_inner(**kwargs):
        captured["close_position"] = kwargs
        return types.SimpleNamespace(order_id=2)

    monkeypatch.setattr(_perp_market, "place_perp_order", fake_place_perp_order)
    monkeypatch.setattr(_perp_market, "_close_position_inner", fake_close_position_inner)
    _perp_market.place_perp_order_limit(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="0xpk",
        precompile_address=ADDR,
        subaccount=ADDR,
        market_id=1,
        is_long=True,
        size=1,
        price=2,
    )
    _perp_market.close_position(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="0xpk",
        precompile_address=ADDR,
        subaccount=ADDR,
        market_id=1,
        price=2,
    )
    assert captured["place_perp_order"]["order_type"] == 0
    assert captured["close_position"]["price"] == 2

    assert _perp_market._parse_int_value([1] * 16) == int.from_bytes(bytes([1] * 16), "little")
    with pytest.raises(ValueError, match="unsupported list value"):
        _perp_market._parse_int_value([object()])
    assert _perp_market._parse_address_value("abc") == "0xabc"
    assert _perp_market._parse_address_value({"value": ADDR}) == ADDR
    assert _perp_market._parse_address_value(bytes.fromhex("11" * 20)) == ADDR
    assert _perp_market._parse_optional_u128_value(None) is None
    assert _perp_market._parse_optional_u128_value({"value": {"Some": "8"}}) == 8
    assert _perp_market._parse_optional_u128_value("9") == 9
    assert _perp_market._parse_perp_position_value({"x": 1}) == {"raw": {"x": 1}}
    assert _perp_market._decode_bytes("ETH") == "ETH"
    assert _perp_market._decode_bytes(b"\xff") == "0xff"

    market_tuple = (
        3,
        b"ETH-USDC",
        b"ETH",
        -18,
        1,
        b"testnet",
        10,
        -1,
        20,
        100,
        101,
        500,
        1000,
        1,
        2,
        (1, 2, 3),
        4,
        5,
        6,
        -7,
        8,
        -9,
        -10,
    )
    monkeypatch.setattr(
        _perp_market,
        "evm_call",
        lambda *_args, **_kwargs: encode([_perp_market._PERP_MARKET_TUPLE], [market_tuple]),
    )
    assert _perp_market.perp_markets(
        evm_rpc_url="rpc",
        precompile_address=ADDR,
        market_id=3,
    ).name == "ETH-USDC"

    monkeypatch.setattr(
        _perp_market,
        "evm_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rpc down")),
    )
    with pytest.raises(RuntimeError, match="rpc down"):
        _perp_market.get_liquidate_price(
            evm_rpc_url="rpc",
            precompile_address=ADDR,
            account=ADDR,
            market_id=3,
        )

    assert _spot_market._parse_int_value(True) == 1
    assert _spot_market._parse_int_value({"value": "5"}) == 5
    assert _spot_market._parse_int_value({"values": [1] * 16}) == int.from_bytes(
        bytes([1] * 16),
        "little",
    )
    assert _spot_market._decode_address(bytes.fromhex("11" * 20)) == ADDR
    with pytest.raises(ValueError, match="empty list"):
        _spot_market._parse_int_value([])
    with pytest.raises(ValueError, match="unsupported list value"):
        _spot_market._parse_int_value([object()])
    with pytest.raises(RuntimeError, match="not an int-like"):
        _spot_market._parse_int_field({"x": object()}, "x")
    with pytest.raises(RuntimeError, match="event field 'id'"):
        _spot_market._parse_int_field({"id": object()}, "order_id")
    assert _spot_market._decode_address("0xabc") == "0xabc"
    assert _spot_market._decode_bytes32(PAIR) == PAIR

    monkeypatch.setattr(_lending, "evm_call", lambda *_args, **_kwargs: b"bad")
    with pytest.raises(RuntimeError, match="failed to decode assetPools response"):
        _lending.asset_pools(evm_rpc_url="rpc", precompile_address=ADDR, market_id=1)
    assert _lending._signer_address("0x" + "11" * 32).startswith("0x")
    assert _lending._normalize_bytes("0x55534443") == b"USDC"
    assert _lending._decode_bytes("USDC") == "USDC"
    assert _lending._decode_bytes(b"\xff") == "0xff"

    signed = types.SimpleNamespace(signed_tx="0xsigned", signer=ADDR, tx_hash="0xhash")
    monkeypatch.setattr(_lending, "build_signed_tx", lambda **_kwargs: signed)
    monkeypatch.setattr(
        _lending,
        "submit_signed_tx_wait_event",
        lambda **_kwargs: types.SimpleNamespace(
            tx_hash="0xtx",
            pallet="Lending",
            event="Deposit",
            fields_json="not-json",
        ),
    )
    assert _lending._submit_lending_tx(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="0xpk",
        precompile_address=ADDR,
        data=b"",
        chain_id=None,
        gas_limit=None,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce=None,
        wait_for_finalized=True,
        timeout_ms=None,
        event_name="Deposit",
    ).event == {"pallet": "Lending", "name": "Deposit"}

    latest_info = (
        ADDR,
        OTHER,
        ADDR,
        b"main",
        1,
        [(b"ETH", 10)],
        7,
        True,
        (False, 0),
        8,
        2,
    )
    monkeypatch.setattr(
        _subaccount,
        "evm_call",
        lambda *_args, **_kwargs: encode([_subaccount._ACCOUNT_INFO_TUPLE], [latest_info]),
    )
    assert _subaccount.subaccount_info(
        evm_rpc_url="rpc",
        precompile_address=ADDR,
        address=ADDR,
    ).name == "main"

    monkeypatch.setattr(_subaccount, "build_signed_tx", lambda **_kwargs: signed)
    monkeypatch.setattr(
        _subaccount,
        "submit_signed_tx_wait_event",
        lambda **_kwargs: types.SimpleNamespace(
            tx_hash="0xtx",
            pallet="Subaccount",
            event="Initialized",
            fields_json="not-json",
        ),
    )
    assert _subaccount._submit_subaccount_tx(
        substrate_ws="ws",
        evm_rpc_url="rpc",
        private_key="0xpk",
        precompile_address=ADDR,
        data=b"",
        chain_id=None,
        gas_limit=None,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce=None,
        wait_for_finalized=True,
        timeout_ms=None,
        event_name="Initialized",
    ).event == {"pallet": "Subaccount", "name": "Initialized"}

    monkeypatch.setattr(
        _subaccount,
        "submit_signed_tx_wait_event",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("different failure")),
    )
    with pytest.raises(RuntimeError, match="different failure"):
        _subaccount._submit_subaccount_tx(
            substrate_ws="ws",
            evm_rpc_url="rpc",
            private_key="0xpk",
            precompile_address=ADDR,
            data=b"",
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
        )

    monkeypatch.setattr(
        _subaccount,
        "submit_pallet_call_wait_event",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("call failed")),
    )
    with pytest.raises(RuntimeError, match="call failed"):
        _subaccount._submit_subaccount_call(
            substrate_ws="ws",
            evm_rpc_url="rpc",
            private_key="0xpk",
            call_function="initialize",
            call_params={},
            wait_for_finalized=True,
            timeout_ms=None,
            nonce=None,
            event_name="Initialized",
        )

    with pytest.raises(RuntimeError, match="unable to decode subaccountInfo"):
        _subaccount._decode_subaccount_info_tuple(b"bad")
    assert _subaccount._decode_optional_u64(None) is None
    assert _subaccount._decode_optional_u64(5) == 5
    assert _subaccount._normalize_bytes("0x55534443") == b"USDC"
    assert _subaccount._normalize_bytes("USDC") == b"USDC"
    assert _subaccount._decode_address(bytes.fromhex("11" * 20)) == ADDR
    assert _subaccount._decode_address("0xabc") == "0xabc"
    assert _subaccount._decode_bytes("main") == "main"
    assert _subaccount._decode_bytes(b"\xff") == "0xff"
