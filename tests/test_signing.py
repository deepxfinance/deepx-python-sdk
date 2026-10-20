from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

import deepx_sdk as dx
from deepx_sdk import signing
from deepx_sdk._async_encoder import RuntimeSnapshot


ACCOUNT = "0x" + "22" * 20
PAIR = "0x" + "33" * 32
NOW = 1781757000000
COMMON = dict(substrate_ws="wss://node", private_key="test-key", subaccount=ACCOUNT)


@pytest.fixture
def encoder(monkeypatch):
    state = SimpleNamespace(calls=[], version=371, failure=None, loads=0)
    monkeypatch.setattr(signing, "_nonce_high_water", {})
    monkeypatch.setattr(signing.time, "monotonic_ns", lambda: 0)

    class FrozenRuntime:
        block_hash = "0xblock"

        def get_block_hash(self, number):
            assert number == 0
            return "0xgenesis"

        def compose_call(self, **kwargs):
            assert kwargs["block_hash"] == "0xblock"
            if state.failure:
                raise state.failure
            state.calls.append(kwargs)
            return kwargs

        def create_signed_extrinsic(self, *, call, keypair, nonce):
            assert keypair.public_key == b"signer"
            raw = json.dumps({"call": call, "nonce": nonce}, sort_keys=True).encode()
            return SimpleNamespace(
                data=SimpleNamespace(to_hex=lambda: "0x" + raw.hex()),
                extrinsic_hash=hashlib.blake2b(raw, digest_size=32).digest(),
            )

        def rpc_request(self, *args, **kwargs):
            pytest.fail("signing must not submit, subscribe, or read from the frozen runtime")

    def load(self):
        state.loads += 1
        assert self._substrate_ws == "wss://node"
        assert self._private_key == "test-key"
        return RuntimeSnapshot(
            substrate=FrozenRuntime(), keypair=SimpleNamespace(public_key=b"signer"),
            system_events_storage_key="0xevents", chain_time_ms=NOW,
            calibration_monotonic_ns=0, runtime_version=state.version, transaction_version=1,
        )

    monkeypatch.setattr(signing.ExtrinsicEncoder, "_load_snapshot", load)
    return state


@pytest.mark.parametrize("kind,expected", [
    ("limit", {"Limit": "GTC"}), ("ioc", {"Limit": "IOC"}),
    ("market", {"Market": 25}), ("stop", "Stop"),
])
def test_perp_order_fields_and_result(encoder, kind, expected):
    signed = dx.build_signed_perp_order(
        **COMMON, market_id=3, is_long=False, size=10**18,
        price=None if kind == "market" else 2000_000000,
        order_type=kind, slippage=25, nonce_ms=NOW + 1,
        take_profit=2100_000000, stop_loss=1900_000000, reduce_only=True,
    )
    call = encoder.calls[-1]
    assert (call["call_module"], call["call_function"]) == ("PerpMarket", "place_order")
    assert call["call_params"] == {"params": {
        "subaccount": ACCOUNT, "market_id": 3, "is_long": False,
        "size": 10**18, "price": 0 if kind == "market" else 2000_000000,
        "order_type": expected, "take_profit": 2100_000000,
        "stop_loss": 1900_000000, "reduce_only": True, "post_only": "None",
    }}
    assert signed.nonce == NOW + 1
    assert signed.runtime_version == 371
    assert signed.tx_hash == "0x" + hashlib.blake2b(
        bytes.fromhex(signed.signed_extrinsic[2:]), digest_size=32,
    ).hexdigest()
    with pytest.raises(FrozenInstanceError):
        signed.nonce = 0
    assert "test-key" not in repr(signed)


@pytest.mark.parametrize("side,is_buy", [("buy", True), ("sell", False)])
@pytest.mark.parametrize("kind,expected", [
    ("limit", {"Limit": "GTC"}), ("ioc", {"Limit": "IOC"}), ("market", {"Market": 25}),
])
def test_spot_order_fields(encoder, side, is_buy, kind, expected):
    dx.build_signed_spot_order(
        **COMMON, pair=PAIR, side=side, quote_amount=100_000000,
        base_amount=10**18, order_type=kind, slippage=25, reduce_only=True,
    )
    call = encoder.calls[-1]
    assert (call["call_module"], call["call_function"]) == ("SpotMarket", "place_order")
    assert call["call_params"] == {"params": {
        "subaccount": ACCOUNT, "pair": PAIR, "is_buy": is_buy,
        "quote_amount": 100_000000, "base_amount": 10**18,
        "order_type": expected, "post_only": "None", "reduce_only": True,
    }}


@pytest.mark.parametrize("fast", [False, True])
def test_cancellation_preserves_large_order_id(encoder, fast):
    order_id = 2**64 - 1
    dx.build_signed_perp_cancel(**COMMON, market_id=3, order_id=order_id, fast_cancel=fast)
    assert encoder.calls[-1]["call_params"] == {"params": {
        "subaccount": ACCOUNT, "market_id": 3, "order_id": order_id,
        "cancel_reason": "UserCanceled", "fast_cancel": fast,
    }}
    for side in ("buy", "sell"):
        dx.build_signed_spot_cancel(**COMMON, pair=PAIR, side=side, order_id=order_id, fast_cancel=fast)
        assert encoder.calls[-1]["call_params"] == {"params": {
            "subaccount": ACCOUNT, "pair": PAIR, "is_buy": side == "buy",
            "order_id": order_id, "cancel_reason": "UserCanceled", "fast_cancel": fast,
        }}
    assert all(call["call_function"] == "cancel_order" for call in encoder.calls)


def test_concurrent_automatic_nonces_and_runtime_reload(encoder):
    def build(_):
        return dx.build_signed_perp_cancel(**COMMON, market_id=3, order_id=1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(build, range(32)))
    assert sorted(result.nonce for result in results) == list(range(NOW, NOW + 32))
    assert len({result.tx_hash for result in results}) == 32
    encoder.version = 372
    assert build(None).runtime_version == 372
    assert encoder.loads == 33


@pytest.mark.parametrize("nonce", [True, "123", 1.5, -1, NOW - 3600001, NOW + 3600001])
def test_invalid_nonce_is_not_signed(encoder, nonce):
    with pytest.raises(dx.ValidationError):
        dx.build_signed_perp_cancel(**COMMON, market_id=3, order_id=1, nonce_ms=nonce)
    assert encoder.calls == []


def test_encoding_failure_propagates(encoder):
    encoder.failure = ValueError("runtime requires a new field")
    with pytest.raises(ValueError, match="runtime requires a new field"):
        dx.build_signed_perp_cancel(**COMMON, market_id=3, order_id=1)


@pytest.mark.parametrize("kwargs", [
    {}, {"order_type": "market", "price": 1},
    {"order_type": "ioc", "price": 1, "post_only": 1}, {"order_type": "unknown"},
])
def test_invalid_perp_combination_fails_before_loading_metadata(encoder, kwargs):
    with pytest.raises(ValueError):
        dx.build_signed_perp_order(**COMMON, market_id=3, is_long=True, size=1, **kwargs)
    assert encoder.loads == 0


def test_spot_stop_is_not_supported(encoder):
    with pytest.raises(ValueError, match="invalid spot order_type"):
        dx.build_signed_spot_order(
            **COMMON, pair=PAIR, side="buy", quote_amount=1, base_amount=1, order_type="stop",
        )
    assert encoder.loads == 0
