from __future__ import annotations

from types import SimpleNamespace

import pytest
from eth_abi import decode, encode
from eth_utils import keccak

from deepx_sdk import ChainClient, _lending, _perp_market


ACCOUNT = "0x" + "22" * 20
PRECOMPILE = "0x" + "45" * 20


@pytest.mark.parametrize("amount", [0, 2**128 - 1])
@pytest.mark.parametrize("auto_borrow", [False, True])
@pytest.mark.parametrize("asset", ["usdc", b"usdc", "0x75736463"])
def test_transfer_limit_abi_and_exact_integer(monkeypatch, amount, auto_borrow, asset):
    def call(url, to, data):
        assert (url, to) == ("https://rpc", PRECOMPILE)
        assert data[:4] == keccak(text="maxTransferAmountFor(address,uint8,bytes,bool)")[:4]
        assert decode(["address", "uint8", "bytes", "bool"], data[4:]) == (
            ACCOUNT, 1, b"usdc", auto_borrow,
        )
        return encode(["uint128"], [amount])

    monkeypatch.setattr(_lending, "evm_call", call)
    client = ChainClient(evm_rpc_url="https://rpc", lending_precompile_address=PRECOMPILE)
    assert client.lending.max_transfer_amount_for(
        account=ACCOUNT, lending_market=1, asset=asset, auto_borrow=auto_borrow,
    ) == amount


def test_transfer_limit_symbol_resolution_and_default_no_borrow(monkeypatch):
    api = SimpleNamespace(v1=SimpleNamespace(lending=SimpleNamespace(
        markets=lambda: [{"asset": "usdc"}],
    )))
    client = ChainClient(evm_rpc_url="https://rpc", api_client=api)

    def call(url, to, data):
        assert to == PRECOMPILE
        assert decode(["address", "uint8", "bytes", "bool"], data[4:]) == (
            ACCOUNT, 1, b"usdc", False,
        )
        return encode(["uint128"], [1234567])

    monkeypatch.setattr(_lending, "evm_call", call)
    assert client.lending.max_transfer_amount_for(
        account=ACCOUNT, lending_market=1, symbol="USDC", precompile_address=PRECOMPILE,
    ) == 1234567


@pytest.mark.parametrize("auto_borrow", ["false", "true", 0, 1, None])
def test_transfer_limit_rejects_non_boolean_before_rpc(monkeypatch, auto_borrow):
    def unexpected(*args):
        pytest.fail("invalid auto_borrow must not reach the RPC")

    monkeypatch.setattr(_lending, "evm_call", unexpected)
    with pytest.raises(ValueError, match="auto_borrow must be a bool"):
        ChainClient().lending.max_transfer_amount_for(
            account=ACCOUNT, lending_market=1, asset=b"usdc", auto_borrow=auto_borrow,
        )


def test_transfer_limit_propagates_rpc_failure(monkeypatch):
    error = RuntimeError("revert get max transfer amount failed")

    def fail(*args):
        raise error

    monkeypatch.setattr(_lending, "evm_call", fail)
    with pytest.raises(RuntimeError) as caught:
        ChainClient().lending.max_transfer_amount_for(
            account=ACCOUNT, lending_market=1, asset="usdc",
        )
    assert caught.value is error


@pytest.mark.parametrize("max_leverage", [12500, None])
def test_delegate_leverage_preserves_signer_and_target(monkeypatch, max_leverage):
    captured = {}

    def submit(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(tx_hash="0xtx", fields_json="")

    monkeypatch.setattr(_perp_market, "submit_pallet_call_wait_event", submit)
    client = ChainClient(private_key="delegate-key", subaccount="0x" + "33" * 20)
    result = client.perp_market.set_per_market_leverage(
        subaccount=ACCOUNT, market_id=3, max_leverage=max_leverage,
    )
    assert result.tx_hash == "0xtx"
    assert captured["private_key"] == "delegate-key"
    assert captured["call_module"] == "PerpMarket"
    assert captured["call_function"] == "set_per_market_leverage"
    assert captured["call_params"] == {
        "subaccount": ACCOUNT, "market_id": 3, "max_leverage": max_leverage,
    }
    assert captured["event"] == "PerMarketLeverageSet"
    assert captured["use_timestamp_nonce"] is False
