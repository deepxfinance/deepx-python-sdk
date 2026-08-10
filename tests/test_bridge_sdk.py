from __future__ import annotations

import json
import types
import urllib.error

import pytest
from eth_abi import encode

import deepx_sdk as dx
import deepx_sdk.api as api_mod
import deepx_sdk.bridge as bridge_mod
from deepx_sdk._network import DEFAULT_NET, network_config


class _DummyResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        pass


class _DummyPublicKey:
    def __init__(self, raw: bytes, text: str = "solana-owner") -> None:
        self._raw = raw
        self._text = text

    def toBuffer(self) -> bytes:
        return self._raw

    def toString(self) -> str:
        return self._text


def _sign_body(dst_chain_id: int | str = 4835) -> dict[str, object]:
    return {
        "domain": {
            "name": "Channel",
            "version": "1",
            "chain_id": 11155111,
            "verifying_contract": "0xconsumer",
        },
        "params": {
            "dst_chain_id": dst_chain_id,
            "token_id": "3",
            "amount": "1000000",
            "sender": "0xsender",
            "dst_recipient": "0xrecipient",
            "refund_address": "0xsender",
            "salt": "0xsalt",
            "custom_data_hex": "0x",
        },
    }


def test_bridge_signature_routing_and_request_body(monkeypatch) -> None:
    captured: list[tuple[str, dict[str, object]]] = []

    def fake_urlopen(req, *args, **kwargs):
        captured.append((req.full_url, json.loads(req.data.decode("utf-8"))))
        return _DummyResponse('{"signature":"0xsignature"}')

    monkeypatch.setattr(bridge_mod.urllib.request, "urlopen", fake_urlopen)

    body = _sign_body()
    assert bridge_mod.get_sign_bridge_out_url("https://api.example.com/", 4835) == (
        "https://api.example.com/internal/v1/sign-bridge/sign-bridge-out/bytes32"
    )
    assert bridge_mod.get_sign_bridge_out_url(
        "https://api.example.com/",
        bridge_mod.BITCOIN_TESTNET_CHAIN_ID,
    ) == "https://api.example.com/internal/v1/sign-bridge/sign-bridge-out/bytes"

    assert bridge_mod.fetch_sign_bridge_out_signature("https://api.example.com", body) == {
        "signature": "0xsignature"
    }
    assert captured[0] == (
        "https://api.example.com/internal/v1/sign-bridge/sign-bridge-out/bytes32",
        body,
    )

    btc_result = bridge_mod.fetch_sign_bridge_out_signature(
        "https://api.example.com/",
        body,
        bridge_mod.BITCOIN_TESTNET_CHAIN_ID,
    )
    expected_btc_body = bridge_mod.create_sign_bridge_out_request_body(
        body,
        bridge_mod.BITCOIN_TESTNET_CHAIN_ID,
    )
    assert btc_result["signature"] == "0xsignature"
    assert captured[1] == (
        "https://api.example.com/internal/v1/sign-bridge/sign-bridge-out/bytes",
        expected_btc_body,
    )
    assert expected_btc_body["params"] == {
        "amount": "1000000",
        "sender": "0xsender",
        "dst_recipient": "0xrecipient",
        "refund_address": "0xsender",
        "salt": "0xsalt",
    }


def test_bridge_signature_error_paths(monkeypatch) -> None:
    with pytest.raises(ValueError, match="Backend API base URL"):
        bridge_mod.get_sign_bridge_out_url("", 1)

    with pytest.raises(ValueError, match="Destination chain ID"):
        bridge_mod.fetch_sign_bridge_out_signature(
            "https://api.example.com",
            {
                "domain": _sign_body()["domain"],
                "params": {
                    "amount": "1",
                    "sender": "0xsender",
                    "dst_recipient": "0xrecipient",
                    "refund_address": "0xsender",
                    "salt": "0xsalt",
                },
            },
        )

    def http_error(req, *args, **kwargs):
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            {},
            _DummyResponse('{"msg":"invalid bridge payload"}'),
        )

    monkeypatch.setattr(bridge_mod.urllib.request, "urlopen", http_error)
    with pytest.raises(dx.RESTError) as exc_info:
        bridge_mod.fetch_sign_bridge_out_signature("https://api.example.com", _sign_body())
    assert exc_info.value.message == "invalid bridge payload"

    monkeypatch.setattr(
        bridge_mod.urllib.request,
        "urlopen",
        lambda req, *args, **kwargs: _DummyResponse("{}"),
    )
    with pytest.raises(ValueError, match="did not include a signature"):
        bridge_mod.fetch_sign_bridge_out_signature("https://api.example.com", _sign_body())


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("bc1p" + "a" * 30, "p2tr"),
        ("tb1q" + "a" * 30, "p2wpkh"),
        ("3" + "a" * 30, "p2sh"),
        ("m" + "a" * 30, "p2pkh"),
    ],
)
def test_bitcoin_address_type_branches(address: str, expected: str) -> None:
    assert bridge_mod.get_bitcoin_address_type(address) == expected


def test_bridge_amount_and_bitcoin_helpers() -> None:
    assert bridge_mod.normalize_bridge_amount(None) == "0"
    assert bridge_mod.normalize_bridge_amount(" 1,250.50 ") == "1250.50"
    assert bridge_mod.parse_bridge_amount("1,250.50", 6) == 1_250_500_000
    assert bridge_mod.parse_bridge_amount("0.01") == 10_000_000_000_000_000
    assert bridge_mod.add_bridge_fee(100, 25) == 125

    quote = bridge_mod.BridgeFeeQuote(fee=250_000, decimals=6, symbol="USDC")
    btc_quote = bridge_mod.BridgeFeeQuote(fee=5_000, decimals=8, symbol="BTC")
    assert bridge_mod.format_bridge_fee(None) == "0"
    assert bridge_mod.format_bridge_fee(quote) == "0.25 USDC"
    assert bridge_mod.format_bridge_fee(btc_quote) == "0.00005 BTC"
    assert bridge_mod.format_bridge_total_amount(None, None) == "N/A"
    assert bridge_mod.format_bridge_total_amount("10", quote) == "10.25 USDC"
    assert bridge_mod.get_bridge_total_amount("0.01", btc_quote) == "0.01005"
    assert bridge_mod.get_bridge_total_amount(" 1,000 ", None) == "1000"

    btc_address = "tb1qfm3x9sl8j6q8h5u4x26w9e0z2v6k2z9p0d3v8s"
    assert bridge_mod.is_valid_bitcoin_address(btc_address) is True
    assert bridge_mod.is_valid_bitcoin_address("not-a-btc-address") is False
    assert bridge_mod.get_bitcoin_address_network("bc1q" + "a" * 30) == "livenet"
    assert bridge_mod.get_bitcoin_address_network(btc_address) == "testnet"
    assert bridge_mod.format_bitcoin_address(btc_address).startswith("0x02")
    assert bridge_mod.to_satoshis("0.00000001") == 1
    assert bridge_mod.to_satoshis("1.25") == 125_000_000
    assert bridge_mod.get_bitcoin_balance_satoshis(True) == 1
    assert bridge_mod.get_bitcoin_balance_satoshis(100) == 100
    assert bridge_mod.get_bitcoin_balance_satoshis(1.0) == 1
    assert bridge_mod.get_bitcoin_balance_satoshis({"total": "125000000"}) == 125_000_000
    assert bridge_mod.from_satoshis(125_000_000) == "1.25"
    assert bridge_mod.from_satoshis(-1) == "-0.00000001"

    receiver = "0x" + "00" * 12 + "945f1caca227dc9954d68792f19a89841d05cf0a"
    assert bridge_mod.generate_bitcoin_deposit_data(
        chain_id=4845,
        receiver=receiver,
        consumer_data="0x1234",
    ) == (
        "000012ed"
        "000000000000000000000000945f1caca227dc9954d68792f19a89841d05cf0a"
        "0002"
        "1234"
    )
    with pytest.raises(ValueError, match="receiver must be exactly 32 bytes"):
        bridge_mod.generate_bitcoin_deposit_data(chain_id=1, receiver="0x1234")
    with pytest.raises(ValueError, match="even-length"):
        bridge_mod.generate_bitcoin_deposit_data(chain_id=1, receiver=receiver, consumer_data="0x123")
    with pytest.raises(ValueError, match="too large"):
        bridge_mod.generate_bitcoin_deposit_data(
            chain_id=1,
            receiver=receiver,
            consumer_data="0x" + "11" * 65536,
        )

    assert bridge_mod.normalize_bitcoin_deposit_utxos(
        payment_address="tb1qsender",
        payment_pubkey="02".ljust(66, "0"),
        utxos=[
            {
                "txid": "a",
                "vout": 0,
                "satoshis": 10_000,
                "scriptPk": "0014",
                "addressType": 1,
                "inscriptions": [],
            },
            {
                "txid": "b",
                "vout": 1,
                "satoshis": 20_000,
                "scriptPk": "0014",
                "addressType": 1,
                "runes": [{"id": "protected"}],
            },
        ],
    ) == [
        {
            "txId": "a",
            "outputIndex": 0,
            "satoshis": 10_000,
            "scriptPk": "0014",
            "addressType": 1,
            "address": "tb1qsender",
            "pubkey": "02".ljust(66, "0"),
            "ords": [],
        }
    ]


def test_withdraw_recipient_and_solana_fee_helpers() -> None:
    owner = _DummyPublicKey(b"\x11" * 32, "owner")
    token_account = _DummyPublicKey(b"\x22" * 32, "token")

    assert bridge_mod.get_solana_bridge_fee_payload_length() == 160
    assert bridge_mod.get_solana_bridge_fee_payload_length("0x1234") == 162
    assert bridge_mod.get_solana_bridge_fee_payload_length(b"\x00\x01") == 162
    with pytest.raises(ValueError, match="even length"):
        bridge_mod.get_solana_bridge_fee_payload_length("0x123")

    assert bridge_mod.get_withdraw_dst_recipient(
        target_network={"networkType": "solana", "name": "Solana"},
        token_symbol="SOL",
        solana_public_key=owner,
    ) == "0x" + "11" * 32
    assert bridge_mod.get_withdraw_dst_recipient(
        target_network={"networkType": "solana", "name": "Solana"},
        token_symbol="USDC",
        target_token_address="mint",
        solana_public_key=owner,
        get_solana_token_account_address=lambda **kwargs: token_account,
    ) == "0x" + "22" * 32

    btc_address = "tb1qfm3x9sl8j6q8h5u4x26w9e0z2v6k2z9p0d3v8s"
    assert bridge_mod.get_withdraw_dst_recipient(
        target_network={"networkType": "bitcoin"},
        bitcoin_address=btc_address,
    ) == bridge_mod.format_bitcoin_address(btc_address)
    assert bridge_mod.get_withdraw_dst_recipient(
        target_network={"networkType": "evm"},
        evm_address="0x" + "44" * 20,
    ) == "0x" + "00" * 12 + "44" * 20


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"target_network": {"networkType": "solana"}, "token_symbol": "SOL"},
            "Solana wallet not connected",
        ),
        (
            {
                "target_network": {"networkType": "solana", "name": "Solana"},
                "token_symbol": "USDC",
                "solana_public_key": _DummyPublicKey(b"\x11" * 32),
            },
            "Token USDC not found on Solana",
        ),
        (
            {
                "target_network": {"networkType": "solana", "name": "Solana"},
                "token_symbol": "USDC",
                "target_token_address": "mint",
                "solana_public_key": _DummyPublicKey(b"\x11" * 32),
            },
            "Solana token account resolver is required",
        ),
        (
            {"target_network": {"networkType": "bitcoin"}},
            "Bitcoin wallet not connected",
        ),
        (
            {"target_network": {"networkType": "evm"}},
            "Wallet is not connected",
        ),
    ],
)
def test_withdraw_recipient_errors(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        bridge_mod.get_withdraw_dst_recipient(**kwargs)


def test_bridge_history_helpers() -> None:
    app_chain_id = 9999
    subaccount = "0x" + "11" * 20
    tx = {
        "swapRecordSrcChainHash": "0xsrc",
        "swapRecordSrcChainId": app_chain_id,
        "swapRecordDstChainId": "1",
        "swapRecordSrcUserAddress": "0x" + "22" * 20,
        "swapRecordDstUserAddress": "0x" + "33" * 20,
        "swapRecordStatus": "Pending",
    }

    assert bridge_mod.get_bridge_status_check_hash(None, " ", " 0xabc ") == "0xabc"
    assert bridge_mod.get_bridge_status_check_src_hash({"srcHash": "0xsrc"}) == "0xsrc"
    assert bridge_mod.get_bridge_status_check_dst_hash({"dstChainHash": "0xdst"}) == "0xdst"
    assert bridge_mod.create_bridge_status_check_update(
        {"swapRecordDstChainHash": "0xdst"},
        "0xsrc",
    ) == {
        "swapRecordSrcChainHash": "0xsrc",
        "swapRecordDstChainHash": "0xdst",
        "swapRecordStatus": "Verifying",
    }

    assert bridge_mod.apply_bridge_history_subaccount_address(tx, app_chain_id, None) is tx
    assert bridge_mod.apply_bridge_history_subaccount_address(
        tx,
        app_chain_id,
        subaccount,
    )["swapRecordSrcUserAddress"] == subaccount
    assert bridge_mod.apply_bridge_history_subaccount_address(
        {"swapRecordSrcChainId": "1", "swapRecordDstChainId": app_chain_id},
        app_chain_id,
        subaccount,
    )["swapRecordDstUserAddress"] == subaccount

    assert bridge_mod.needs_bridge_history_supplement(tx, app_chain_id) is True
    assert bridge_mod.needs_bridge_history_supplement(
        {**tx, "swapRecordDstChainHash": "0xdst", "swapRecordDstUserAddress": "0xuser"},
        app_chain_id,
    ) is False
    assert bridge_mod.needs_bridge_history_supplement(
        {**tx, "swapRecordSrcChainId": "1"},
        app_chain_id,
    ) is False

    supplement = {
        "swapRecordDstChainHash": "0xdst",
        "swapRecordDstChainTime": "1710000010000",
        "swapRecordDstUserAddress": "0x" + "44" * 20,
        "swapRecordStatus": "Success",
    }
    merged = bridge_mod.merge_bridge_history_supplement(tx, supplement, app_chain_id, subaccount)
    assert merged["swapRecordSrcUserAddress"] == subaccount
    assert merged["swapRecordDstChainHash"] == "0xdst"
    assert merged["swapRecordStatus"] == "Pending"
    assert bridge_mod.merge_bridge_history_supplement(tx, None, app_chain_id) is tx

    assert bridge_mod.merge_bridge_history_update(
        tx,
        {"swapRecordDstChainHash": "0xdst", "swapRecordStatus": "Verifying"},
    ) == {**tx, "swapRecordDstChainHash": "0xdst", "swapRecordStatus": "Verifying"}


def test_bridge_service_client_routes_and_status(monkeypatch) -> None:
    requests: list[tuple[str, str, str | None]] = []
    responses = [
        _DummyResponse(
            '{"code":"OK","data":[{"swapRecordSrcChainHash":"0xabc",'
            '"swapRecordDstChainHash":"0xdst"}]}'
        ),
        _DummyResponse('{"code":"OK","data":[] }'),
    ]

    def fake_urlopen(req, timeout=0):
        data = req.data.decode("utf-8") if req.data else None
        requests.append((req.get_method(), req.full_url, data))
        return responses.pop(0)

    monkeypatch.setattr(api_mod.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="bridge_api_url is required"):
        bridge_mod.BridgeServiceClient(" ")

    service = bridge_mod.BridgeServiceClient("https://bridge.example.com/")
    status = service.check_transaction_status(chain_id=123, src_chain_hash="0xabc")
    assert status["stage"] == "received"
    assert status["record"]["swapRecordDstChainHash"] == "0xdst"
    assert requests[0] == (
        "POST",
        "https://bridge.example.com/swap/swap-record:check",
        json.dumps({"checkInfos": [{"chainID": 123, "srcChainHash": ["0xabc"]}]}),
    )

    assert service.list_records(
        page_no=2,
        page_size=50,
        user_address="0xabc",
        src_chain_hash="0xdef",
        bridge_no="BR-1",
    ) == {"code": "OK", "data": []}
    assert requests[1] == (
        "GET",
        "https://bridge.example.com/swap/swap-records?pageNo=2&pageSize=50"
        "&userAddress=0xabc&srcChainHash=0xdef&bridgeNo=BR-1",
        None,
    )


def test_bridge_api_fee_submission_and_status(monkeypatch) -> None:
    captured_fee_call: dict[str, object] = {}

    def fake_evm_call(rpc_url: str, contract_address: str, data: bytes) -> bytes:
        captured_fee_call.update(
            rpc_url=rpc_url,
            contract_address=contract_address,
            data=data,
        )
        return encode(["uint256"], [321])

    monkeypatch.setattr(bridge_mod, "evm_call", fake_evm_call)
    api = bridge_mod.BridgeApi(
        rpc_url="https://rpc.example.com",
        chain_id=11155111,
        contract_address="0x" + "12" * 20,
        private_key="0xpriv",
    )

    assert bridge_mod.get_bridge_token_id(None) == 3
    assert bridge_mod.get_bridge_token_id("btc") == 82
    with pytest.raises(ValueError, match="unknown bridge token symbol"):
        bridge_mod.get_bridge_token_id("NOPE")

    assert api.get_bridge_fee(
        dst_chain_id=4835,
        amount=100,
        dst_recipient="0x" + "11" * 32,
        symbol="USDC",
        custom_data="0x1234",
    ) == 321
    assert captured_fee_call["rpc_url"] == "https://rpc.example.com"
    assert captured_fee_call["contract_address"] == "0x" + "12" * 20

    with pytest.raises(ValueError, match="uint32"):
        api.get_bridge_fee(
            dst_chain_id=2**32,
            amount=100,
            dst_recipient="0x" + "11" * 32,
        )
    with pytest.raises(ValueError, match="at most 32 bytes"):
        api.get_bridge_fee(
            dst_chain_id=1,
            amount=100,
            dst_recipient="0x" + "11" * 33,
        )

    signed_calls: dict[str, object] = {}
    rpc_calls: list[tuple[str, str, list[object]]] = []

    def fake_build_signed_tx(**kwargs):
        signed_calls.update(kwargs)
        return types.SimpleNamespace(signed_tx="0xsigned")

    def fake_rpc_call(rpc_url: str, method: str, params: list[object]) -> object:
        rpc_calls.append((rpc_url, method, params))
        return "0xbridgehash"

    monkeypatch.setattr(bridge_mod, "build_signed_tx", fake_build_signed_tx)
    monkeypatch.setattr(bridge_mod, "_rpc_call", fake_rpc_call)

    tx = api.bridge_out(
        dst_chain_id=4835,
        amount=100,
        dst_recipient="0x" + "11" * 32,
        refund_address="0x" + "22" * 20,
        salt="0x" + "33" * 32,
        signature="0x4444",
        token_id=3,
        is_native=True,
    )
    assert tx.tx_hash == "0xbridgehash"
    # fee auto-queried (fake_evm_call above returns 321); native -> value = amount + fee
    assert signed_calls["value"] == 421
    assert signed_calls["chain_id"] == 11155111
    assert rpc_calls == [
        ("https://rpc.example.com", "eth_sendRawTransaction", ["0xsigned"])
    ]

    # explicit fee: non-native -> value = fee
    api.bridge_out(
        dst_chain_id=4835,
        amount=100,
        dst_recipient="0x" + "11" * 32,
        refund_address="0x" + "22" * 20,
        salt="0x" + "33" * 32,
        signature="0x4444",
        token_id=3,
        fee=7,
    )
    assert signed_calls["value"] == 7

    with pytest.raises(ValueError, match="private_key is required"):
        bridge_mod.BridgeApi(
            rpc_url="https://rpc.example.com",
            chain_id=1,
            contract_address="0x" + "12" * 20,
        ).bridge_out(
            dst_chain_id=1,
            amount=1,
            dst_recipient="0x" + "11" * 32,
            refund_address="0x" + "22" * 20,
            salt="0x" + "33" * 32,
            signature="0x",
            token_id=3,
        )

    monkeypatch.setattr(
        bridge_mod,
        "_rpc_call",
        lambda rpc_url, method, params: {"status": "0x1"},
    )
    assert api.check_transaction("0xbridgehash") is True
    monkeypatch.setattr(bridge_mod, "_rpc_call", lambda rpc_url, method, params: None)
    assert api.check_transaction("0xbridgehash") is False


def test_approve_erc20_targets_token_contract(monkeypatch) -> None:
    signed_calls: dict[str, object] = {}

    def fake_build_signed_tx(**kwargs):
        signed_calls.update(kwargs)
        return types.SimpleNamespace(signed_tx="0xsigned")

    monkeypatch.setattr(bridge_mod, "build_signed_tx", fake_build_signed_tx)
    monkeypatch.setattr(
        bridge_mod, "_rpc_call", lambda rpc_url, method, params: "0xapprovehash"
    )

    api = bridge_mod.BridgeApi(
        rpc_url="https://rpc.example.com",
        chain_id=11155111,
        contract_address="0x" + "12" * 20,
        private_key="0xpriv",
    )
    tx = api.approve_erc20(token_address="0x" + "aa" * 20, amount=5)
    assert tx.tx_hash == "0xapprovehash"
    # the tx goes to the token contract, not the bridge contract
    assert signed_calls["precompile_address"] == "0x" + "aa" * 20
    assert signed_calls["value"] == 0
    data = signed_calls["data"]
    assert data[:4].hex() == "095ea7b3"  # approve(address,uint256)
    # spender defaults to the bridge contract address
    assert data[4 + 12 : 4 + 32].hex() == "12" * 20


def test_btc_channel_api(monkeypatch) -> None:
    evm_calls: list[bytes] = []

    def fake_evm_call(rpc_url: str, contract_address: str, data: bytes) -> bytes:
        evm_calls.append(data)
        # 1st call is convertAmountToL1, 2nd is getTotalWithdrawFee
        if len(evm_calls) == 1:
            return encode(["uint256"], [50])
        return encode(["uint256", "uint256"], [11, 22])

    signed_calls: dict[str, object] = {}

    def fake_build_signed_tx(**kwargs):
        signed_calls.update(kwargs)
        return types.SimpleNamespace(signed_tx="0xsigned")

    monkeypatch.setattr(bridge_mod, "evm_call", fake_evm_call)
    monkeypatch.setattr(bridge_mod, "build_signed_tx", fake_build_signed_tx)
    monkeypatch.setattr(
        bridge_mod, "_rpc_call", lambda rpc_url, method, params: "0xbtchash"
    )

    api = bridge_mod.BtcChannelApi(
        rpc_url="https://rpc.example.com",
        chain_id=4845,
        contract_address="0x" + "12" * 20,
        private_key="0xpriv",
    )
    recipient = "0x" + "01" * 64

    tx = api.btc_withdraw(
        amount=1000,
        recipient=recipient,
        refund_address="0x" + "22" * 20,
        salt="0x" + "33" * 32,
        signature="0x4444",
    )
    assert tx.tx_hash == "0xbtchash"
    assert len(evm_calls) == 2  # convertAmountToL1 + getTotalWithdrawFee
    # msg.value must equal channelFee exactly
    assert signed_calls["value"] == 11
    assert signed_calls["precompile_address"] == "0x" + "12" * 20

    # explicit channel_fee skips the on-chain query
    evm_calls.clear()
    api.btc_withdraw(
        amount=1000,
        recipient=recipient,
        refund_address="0x" + "22" * 20,
        salt="0x" + "33" * 32,
        signature="0x4444",
        channel_fee=3,
    )
    assert evm_calls == []
    assert signed_calls["value"] == 3

    with pytest.raises(ValueError, match="exactly 64 bytes"):
        api.btc_withdraw(
            amount=1000,
            recipient="0x" + "01" * 32,
            refund_address="0x" + "22" * 20,
            salt="0x" + "33" * 32,
            signature="0x4444",
            channel_fee=3,
        )


def test_fetch_sign_bridge_out_signature_passes_timeout(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(req, *args, **kwargs):
        seen.update(kwargs)
        return _DummyResponse('{"signature":"0xsignature"}')

    monkeypatch.setattr(bridge_mod.urllib.request, "urlopen", fake_urlopen)
    bridge_mod.fetch_sign_bridge_out_signature(
        "https://api.example.com", _sign_body(), timeout=7
    )
    assert seen["timeout"] == 7

    seen.clear()
    bridge_mod.fetch_sign_bridge_out_signature("https://api.example.com", _sign_body())
    assert seen["timeout"] == 30


# === Edge coverage for previously uncovered defensive branches in bridge.py ===

def test_network_value_fallback_paths() -> None:
    """Lines 70, 77: _network_value handles None and plain objects via getattr."""
    # network=None → return None (line 70)
    assert bridge_mod._network_value(None, "chain_id") is None
    # object with attributes (not a Mapping) → getattr fallback (line 77)
    obj = types.SimpleNamespace(chain_id=42, name="ethereum")
    assert bridge_mod._network_value(obj, "chain_id", "chainId") == 42
    assert bridge_mod._network_value(obj, "missing_attr") is None


def test_normalize_hex_bytes_edge_inputs() -> None:
    """Lines 92, 100, 102: empty strip, odd non-hex, even non-hex."""
    # Line 92: empty after strip
    assert bridge_mod._normalize_hex_bytes("   ") == b""
    # Line 100: odd length but not all hex chars → utf-8 encode
    assert bridge_mod._normalize_hex_bytes("abx") == b"abx"
    # Line 102: even length but contains non-hex chars → utf-8 encode
    assert bridge_mod._normalize_hex_bytes("abxy") == b"abxy"


def test_zero_pad_value_raises_for_oversize() -> None:
    """Line 116: ValueError when raw bytes exceed requested size."""
    with pytest.raises(ValueError, match="at most 2 bytes"):
        bridge_mod._zero_pad_value(b"\x00\x00\x00", size=2)


def test_public_key_string_string_bytes_and_other() -> None:
    """Lines 122, 127-129: string passthrough, bytes hex, str() fallback."""
    # Line 122: plain string → returned as-is
    assert bridge_mod._public_key_string("plain-string") == "plain-string"
    # Lines 127-128: bytes/bytearray/memoryview → "0x" + hex
    assert bridge_mod._public_key_string(b"\x01\x02") == "0x0102"
    assert bridge_mod._public_key_string(bytearray(b"\xde\xad")) == "0xdead"
    assert bridge_mod._public_key_string(memoryview(b"\xab\xcd")) == "0xabcd"
    # Line 129: object without to_string/toString → str(value) fallback
    assert bridge_mod._public_key_string(42) == "42"


def test_public_key_bytes_string_and_object_branches() -> None:
    """Lines 139-148: string branches, __bytes__ method, TypeError."""

    class _HasBytes:
        def __bytes__(self) -> bytes:
            return b"\xab\xcd"

    # Lines 140-142: "0x"-prefixed string → _normalize_hex_bytes
    assert bridge_mod._public_key_bytes("0xdeadbeef") == b"\xde\xad\xbe\xef"
    # Line 144: even-length all-hex string without 0x prefix → bytes.fromhex
    assert bridge_mod._public_key_bytes("deadbeef") == b"\xde\xad\xbe\xef"
    # Line 145: non-hex string → utf-8 encode
    assert bridge_mod._public_key_bytes("hello") == b"hello"
    # Lines 146-147: object with __bytes__ method
    assert bridge_mod._public_key_bytes(_HasBytes()) == b"\xab\xcd"
    # Line 148: object without usable repr → TypeError
    with pytest.raises(TypeError, match="usable byte representation"):
        bridge_mod._public_key_bytes(object())


def test_response_message_returns_none_for_non_mapping() -> None:
    """Line 153: non-Mapping payload → None."""
    assert bridge_mod._response_message("not a mapping") is None
    assert bridge_mod._response_message(42) is None
    assert bridge_mod._response_message(None) is None
    assert bridge_mod._response_message([1, 2]) is None


def test_fetch_sign_bridge_out_signature_invalid_params_returns_body(monkeypatch) -> None:
    """Line 207: body.params isn't a Mapping → return unchanged dict(body)."""
    body = _sign_body()
    body["params"] = "not-a-mapping"  # Force the isinstance(params, Mapping) → False branch
    captured: list[str] = []

    def fake_urlopen(req, *args, **kwargs):
        captured.append(req.full_url)
        return _DummyResponse('{"signature":"0xabc"}')

    monkeypatch.setattr(bridge_mod.urllib.request, "urlopen", fake_urlopen)
    # Pass Bitcoin chain ID explicitly so the Bitcoin branch is taken,
    # then the non-Mapping params hits the early-return path.
    result = bridge_mod.fetch_sign_bridge_out_signature(
        "https://api.example.com",
        body,
        bridge_mod.BITCOIN_TESTNET_CHAIN_ID,
    )
    assert result == {"signature": "0xabc"}


def test_fetch_sign_bridge_out_signature_http_error_branches(monkeypatch) -> None:
    """Lines 245-246, 251-252: HTTPError with bad fp.read() and non-JSON body."""

    class _BadFp:
        def read(self) -> bytes:
            raise OSError("socket closed")

    def http_error_read_failure(req, *args, **kwargs):
        raise urllib.error.HTTPError(
            req.full_url, 500, "Server Error", {}, _BadFp()
        )

    monkeypatch.setattr(bridge_mod.urllib.request, "urlopen", http_error_read_failure)
    with pytest.raises(dx.RESTError) as exc_info:
        bridge_mod.fetch_sign_bridge_out_signature("https://api.example.com", _sign_body())
    assert exc_info.value.status_code == 500
    assert exc_info.value.message  # falls back to exc.reason

    def http_error_bad_json(req, *args, **kwargs):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            _DummyResponse("not-json-{{{"),
        )

    monkeypatch.setattr(bridge_mod.urllib.request, "urlopen", http_error_bad_json)
    with pytest.raises(dx.RESTError) as exc_info:
        bridge_mod.fetch_sign_bridge_out_signature("https://api.example.com", _sign_body())
    assert exc_info.value.status_code == 400


def test_fetch_sign_bridge_out_signature_url_error(monkeypatch) -> None:
    """Lines 260-261: URLError → RESTError wrapping the underlying error."""

    def url_error(req, *args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(bridge_mod.urllib.request, "urlopen", url_error)
    with pytest.raises(dx.RESTError, match="connection refused"):
        bridge_mod.fetch_sign_bridge_out_signature("https://api.example.com", _sign_body())


def test_fetch_sign_bridge_out_signature_invalid_success_json(monkeypatch) -> None:
    """Lines 267-268: HTTP success but body isn't valid JSON."""

    def bad_json(req, *args, **kwargs):
        return _DummyResponse("not valid json at all")

    monkeypatch.setattr(bridge_mod.urllib.request, "urlopen", bad_json)
    with pytest.raises(ValueError, match="did not include a signature"):
        bridge_mod.fetch_sign_bridge_out_signature("https://api.example.com", _sign_body())


def test_is_valid_bitcoin_address_empty_inputs() -> None:
    """Line 321: None and empty string → False."""
    assert bridge_mod.is_valid_bitcoin_address(None) is False
    assert bridge_mod.is_valid_bitcoin_address("") is False


def test_get_bitcoin_balance_satoshis_non_mapping_input() -> None:
    """Line 375: input is neither a parseable value nor a Mapping → 0."""
    assert bridge_mod.get_bitcoin_balance_satoshis("not-a-mapping-or-number") == 0
    assert bridge_mod.get_bitcoin_balance_satoshis(None) == 0
    assert bridge_mod.get_bitcoin_balance_satoshis([1, 2, 3]) == 0


def test_get_bridge_status_check_hash_returns_none_for_all_blank() -> None:
    """Line 498: every value is None or whitespace → return None."""
    assert bridge_mod.get_bridge_status_check_hash(None, "", "   ") is None
    assert bridge_mod.get_bridge_status_check_hash() is None


def test_check_transaction_status_no_records(monkeypatch) -> None:
    """Lines 620, 631: records not a list / no matching record → {"stage": "confirming"}."""
    client = bridge_mod.BridgeServiceClient(bridge_api_url="https://api.example.com")

    # Records not a list (line 620)
    monkeypatch.setattr(
        client._client, "request", lambda *a, **kw: {"data": "not-a-list"}
    )
    assert client.check_transaction_status(chain_id=1, src_chain_hash="0xabc") == {
        "stage": "confirming"
    }

    # Records is a list but no entry matches (line 631)
    monkeypatch.setattr(
        client._client,
        "request",
        lambda *a, **kw: {"data": [{"swapRecordSrcChainHash": "0xdifferent"}]},
    )
    assert client.check_transaction_status(chain_id=1, src_chain_hash="0xabc") == {
        "stage": "confirming"
    }


def test_check_transaction_receipt_non_mapping(monkeypatch) -> None:
    """Line 752: receipt isn't a Mapping → return False."""
    api = bridge_mod.BridgeApi(
        rpc_url="https://rpc.example.com",
        chain_id=11155111,
        contract_address="0x" + "12" * 20,
    )

    monkeypatch.setattr(bridge_mod, "_rpc_call", lambda *a, **kw: "not-a-mapping")
    assert api.check_transaction("0xbridgehash") is False

    monkeypatch.setattr(bridge_mod, "_rpc_call", lambda *a, **kw: [1, 2, 3])
    assert api.check_transaction("0xbridgehash") is False


# === bridge_out_with_sign (one-shot high-level method) ===

_ANVIL_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
_ANVIL_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
_TOKEN_ADDR = "0x" + "9e" * 20
_BRIDGE_ADDR = "0x" + "12" * 20


def _selector(sig: str) -> bytes:
    from eth_utils import keccak

    return keccak(text=sig)[:4]


def _setup_one_shot_mocks(monkeypatch, *, is_gas_token=False, allowance=0, fee=0):
    evm_calls: list[tuple[str, bytes]] = []

    def fake_evm_call(rpc_url: str, contract_address: str, data: bytes) -> bytes:
        evm_calls.append((contract_address, data))
        sel = data[:4]
        if sel == _selector("tokenIdToInfo(uint256)"):
            return encode(
                ["bool", "bool", "address", "uint8", "uint8", "bool"],
                [False, is_gas_token, _TOKEN_ADDR, 6, 6, False],
            )
        if sel == _selector("allowance(address,address)"):
            return encode(["uint256"], [allowance])
        if sel == _selector("getBridgeFee(uint256,uint32,uint256,bytes32,bytes)"):
            return encode(["uint256"], [fee])
        raise AssertionError(f"unexpected eth_call selector: {sel.hex()}")

    sign_bodies: list[dict[str, object]] = []

    def fake_sign(base_url, body, dst_chain_id=None, **kwargs):
        sign_bodies.append({"base_url": base_url, "body": body, "dst_chain_id": dst_chain_id})
        return {"signature": "0xsig", "digest": "0xdigest", "matches_authorizer": True}

    sent: list[dict[str, object]] = []

    def fake_build_signed_tx(**kwargs):
        sent.append(kwargs)
        return types.SimpleNamespace(signed_tx="0xsigned%d" % len(sent))

    def fake_rpc_call(rpc_url, method, params):
        return "0xtx%d" % len(sent)

    monkeypatch.setattr(bridge_mod, "evm_call", fake_evm_call)
    monkeypatch.setattr(bridge_mod, "fetch_sign_bridge_out_signature", fake_sign)
    monkeypatch.setattr(bridge_mod, "build_signed_tx", fake_build_signed_tx)
    monkeypatch.setattr(bridge_mod, "_rpc_call", fake_rpc_call)
    return evm_calls, sign_bodies, sent


def _one_shot_api() -> "bridge_mod.BridgeApi":
    return bridge_mod.BridgeApi(
        rpc_url="https://rpc.example.com",
        chain_id=4845,
        contract_address=_BRIDGE_ADDR,
        private_key=_ANVIL_KEY,
    )


def test_bridge_out_with_sign_happy_path_with_approve(monkeypatch) -> None:
    evm_calls, sign_bodies, sent = _setup_one_shot_mocks(monkeypatch, allowance=0, fee=7)
    api = _one_shot_api()

    result = api.bridge_out_with_sign(
        sign_api_base="https://sign.example.com",
        dst_chain_id=bridge_mod.SOLANA_DEVNET_CHAIN_ID,
        amount=1_000_000,
        dst_recipient="0x" + "ab" * 32,
        token_id=3,
        salt="0x" + "01" * 32,
    )

    # sender derived from the private key
    assert result["sender"] == _ANVIL_ADDR
    assert result["salt"] == "0x" + "01" * 32
    assert result["signature"] == "0xsig"
    # approve was sent, and before bridgeOut
    assert result["approve_tx_hash"] == "0xtx1"
    assert result["tx_hash"] == "0xtx2"
    assert sent[0]["precompile_address"] == _TOKEN_ADDR  # approve -> token contract
    assert sent[1]["precompile_address"] == _BRIDGE_ADDR  # bridgeOut -> bridge contract
    # non-gas token: value = fee
    assert sent[1]["value"] == 7

    body = sign_bodies[0]["body"]
    assert sign_bodies[0]["base_url"] == "https://sign.example.com"
    assert body["domain"] == {
        "name": "Channel",
        "version": "1",
        "chain_id": 4845,
        "verifying_contract": _BRIDGE_ADDR,
    }
    params = body["params"]
    assert params["sender"] == _ANVIL_ADDR
    assert params["refund_address"] == _ANVIL_ADDR
    assert params["dst_recipient"] == "0x" + "ab" * 32
    assert params["salt"] == "0x" + "01" * 32
    assert params["token_id"] == "3"
    assert params["amount"] == "1000000"


def test_bridge_out_with_sign_gas_token_skips_approve(monkeypatch) -> None:
    evm_calls, sign_bodies, sent = _setup_one_shot_mocks(monkeypatch, is_gas_token=True, fee=0)
    api = _one_shot_api()

    result = api.bridge_out_with_sign(
        sign_api_base="https://sign.example.com",
        dst_chain_id=11155111,
        amount=10,
        dst_recipient="0x" + "ab" * 32,
        token_id=1,
    )
    assert result["approve_tx_hash"] is None
    assert len(sent) == 1  # bridgeOut only
    # gas token: value = amount + fee
    assert sent[0]["value"] == 10
    # allowance was never queried
    assert all(c[1][:4] != _selector("allowance(address,address)") for c in evm_calls)


def test_bridge_out_with_sign_sufficient_allowance_skips_approve(monkeypatch) -> None:
    _, _, sent = _setup_one_shot_mocks(monkeypatch, allowance=10**9, fee=0)
    api = _one_shot_api()

    result = api.bridge_out_with_sign(
        sign_api_base="https://sign.example.com",
        dst_chain_id=11155111,
        amount=100,
        dst_recipient="0x" + "ab" * 32,
        token_id=3,
    )
    assert result["approve_tx_hash"] is None
    assert len(sent) == 1


def test_bridge_out_with_sign_random_salt_and_unregistered_token(monkeypatch) -> None:
    _, sign_bodies, _ = _setup_one_shot_mocks(monkeypatch, fee=0)
    api = _one_shot_api()

    kwargs = dict(
        sign_api_base="https://sign.example.com",
        dst_chain_id=11155111,
        amount=100,
        dst_recipient="0x" + "ab" * 32,
        token_id=3,
    )
    r1 = api.bridge_out_with_sign(**kwargs)
    r2 = api.bridge_out_with_sign(**kwargs)
    assert r1["salt"] != r2["salt"]
    assert sign_bodies[0]["body"]["params"]["salt"] == r1["salt"]

    # token not registered (token == 0x0)
    def fake_evm_call_unset(rpc_url, contract_address, data):
        return encode(
            ["bool", "bool", "address", "uint8", "uint8", "bool"],
            [False, False, "0x" + "00" * 20, 0, 0, False],
        )

    monkeypatch.setattr(bridge_mod, "evm_call", fake_evm_call_unset)
    with pytest.raises(ValueError, match="not registered"):
        api.bridge_out_with_sign(**kwargs)


def test_bridge_out_with_sign_wait(monkeypatch) -> None:
    _, _, _ = _setup_one_shot_mocks(monkeypatch, fee=0)
    api = _one_shot_api()

    kwargs = dict(
        sign_api_base="https://sign.example.com",
        dst_chain_id=11155111,
        amount=100,
        dst_recipient="0x" + "ab" * 32,
        token_id=3,
        wait=True,
    )
    with pytest.raises(ValueError, match="bridge_api_url"):
        api.bridge_out_with_sign(**kwargs)

    monkeypatch.setattr(
        bridge_mod.BridgeServiceClient,
        "check_transaction_status",
        lambda self, *, chain_id, src_chain_hash: {"stage": "received", "record": {"swapRecordDstChainHash": "0xdst"}},
    )
    result = api.bridge_out_with_sign(**kwargs, bridge_api_url="https://rbool.example.com")
    assert result["bridge_status"]["stage"] == "received"


def test_solana_address_to_bytes32() -> None:
    out = bridge_mod.solana_address_to_bytes32("ChU4NmZpvu3MEpRuBPpB3aWSJQbvEdcngttRjnSkbXss")
    assert out == "0xadce62235534179be049716a4ce39001febd474d01cbda3cc118755e52c2299e"

    with pytest.raises(ValueError, match="invalid base58"):
        bridge_mod.solana_address_to_bytes32("0OIl")  # not base58 characters
    with pytest.raises(ValueError, match="32 bytes"):
        bridge_mod.solana_address_to_bytes32("1111")  # too short


def test_bridge_in_logs_fetch_decode_and_wait(monkeypatch) -> None:
    recipient = "0x" + "ab" * 20
    data = encode(["bytes32", "uint256", "uint256"], [b"\x11" * 32, 3, 1_000_000])
    log = {
        "transactionHash": "0xbridgein",
        "blockNumber": hex(123),
        "data": "0x" + data.hex(),
    }
    rpc_calls: list[tuple[str, str, list[object]]] = []

    def fake_rpc_call(rpc_url: str, method: str, params: list[object]) -> object:
        rpc_calls.append((rpc_url, method, params))
        return [log]

    monkeypatch.setattr(bridge_mod, "_rpc_call", fake_rpc_call)
    api = bridge_mod.BridgeApi(
        rpc_url="https://rpc.example.com",
        chain_id=11155111,
        contract_address="0x" + "34" * 20,
    )

    logs = api.get_bridge_in_logs(recipient=recipient, from_block=100)
    assert logs == [
        {
            "tx_hash": "0xbridgein",
            "block_number": 123,
            "tx_unique_identification": "0x" + "11" * 32,
            "token_id": 3,
            "amount": 1_000_000,
        }
    ]
    params = rpc_calls[0][2][0]
    assert params["address"] == "0x" + "34" * 20
    assert params["topics"] == [
        bridge_mod.BRIDGE_IN_EVENT_TOPIC,
        "0x" + recipient[2:].rjust(64, "0"),
    ]
    assert params["fromBlock"] == hex(100)
    assert params["toBlock"] == "latest"

    # wait returns the first matching log
    assert api.wait_bridge_in(recipient=recipient, interval_s=0)["token_id"] == 3

    # timeout when nothing arrives
    monkeypatch.setattr(bridge_mod, "_rpc_call", lambda *args: [])
    with pytest.raises(TimeoutError, match="no BridgeIn event"):
        api.wait_bridge_in(timeout_s=0, interval_s=0)

    # transient RPC errors are retried, not raised
    calls = {"n": 0}

    def flaky_rpc_call(rpc_url: str, method: str, params: list[object]) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return [log]

    monkeypatch.setattr(bridge_mod, "_rpc_call", flaky_rpc_call)
    assert api.wait_bridge_in(interval_s=0)["amount"] == 1_000_000


def test_bridge_latest_block(monkeypatch) -> None:
    monkeypatch.setattr(bridge_mod, "_rpc_call", lambda *args: "0x7b")
    api = bridge_mod.BridgeApi(
        rpc_url="https://rpc.example.com",
        chain_id=11155111,
        contract_address="0x" + "34" * 20,
    )
    assert api.latest_block() == 123


def test_bridge_api_net_resolution() -> None:
    default = bridge_mod.BridgeApi()
    expected = network_config(DEFAULT_NET)
    assert default.net == DEFAULT_NET
    assert default.rpc_url == expected.evm_rpc_url
    assert default.chain_id == expected.chain_id
    assert default.contract_address == expected.bridge_contract

    devnet = bridge_mod.BridgeApi(net="devnet")
    assert devnet.chain_id == 4845
    assert devnet.contract_address == "0xa32408eD9f1dFa1e2dc30143F9133Af31E8514ed"

    custom = bridge_mod.BridgeApi(
        rpc_url="https://x", chain_id=1, contract_address="0x" + "11" * 20
    )
    assert custom.rpc_url == "https://x"
    assert custom.chain_id == 1
    assert custom.contract_address == "0x" + "11" * 20
