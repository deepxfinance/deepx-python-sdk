import asyncio
from types import SimpleNamespace

import pytest

from deepx_sdk import ChainClient, TpSlAction, TpSlConfig
from deepx_sdk._position_tp_sl import tp_sl_params
from deepx_sdk.async_client import AsyncPerpMarketClient

OWNER = "0x" + "12" * 20


def test_settings_encode_current_runtime_variants_and_defaults():
    config = TpSlConfig("TakeProfit", 120_000_000)
    params = tp_sl_params(
        OWNER,
        3,
        [
            TpSlAction("Create", config),
            TpSlAction(
                "Update", TpSlConfig("StopLoss", 90_000_000, "Index", 5, 89_000_000), 2
            ),
            TpSlAction("Cancel", setting_id=3),
        ],
    )
    assert params["actions"] == [
        {
            "Create": {
                "condition": "TakeProfit",
                "source": "Mark",
                "trigger_price": 120_000_000,
                "quantity": "Entire",
                "execution": {"Market": None},
            }
        },
        {
            "Update": {
                "id": 2,
                "config": {
                    "condition": "StopLoss",
                    "source": "Index",
                    "trigger_price": 90_000_000,
                    "quantity": {"Partial": 5},
                    "execution": {"Limit": 89_000_000},
                },
            }
        },
        {"Cancel": {"id": 3}},
    ]


@pytest.mark.parametrize(
    "config",
    [
        TpSlConfig("TakeProfit", 1, "Last"),
        TpSlConfig("Other", 1),
        TpSlConfig("StopLoss", 0),
        TpSlConfig("StopLoss", True),
        TpSlConfig("StopLoss", 2**128),
        TpSlConfig("StopLoss", 1, quantity=0),
        TpSlConfig("StopLoss", 1, slippage=-1),
        TpSlConfig("StopLoss", 1, slippage=2**64),
        TpSlConfig("StopLoss", 1, limit_price=1, slippage=0),
    ],
)
def test_invalid_configs_fail_before_signing(config):
    with pytest.raises(ValueError):
        tp_sl_params(OWNER, 3, [TpSlAction("Create", config)])


@pytest.mark.parametrize(
    "actions",
    [
        [],
        [TpSlAction("Cancel", setting_id=1)] * 129,
        [TpSlAction("Cancel", setting_id=0)],
        [TpSlAction("Create")],
        [TpSlAction("Update", setting_id=1)],
        [TpSlAction("Cancel", setting_id=2**64)],
    ],
)
def test_invalid_batches_fail_before_signing(actions):
    with pytest.raises(ValueError):
        tp_sl_params(OWNER, 3, actions)


def test_sync_submission_uses_timestamp_nonce_and_existing_native_transport(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        "deepx_sdk.client.submit_pallet_call",
        lambda **kw: calls.append(kw) or SimpleNamespace(tx_hash="0xabc"),
    )
    client = ChainClient(net="testnet", subaccount=OWNER)
    result = client.perp_market.update_tp_sl(
        market_id=3, actions=[TpSlAction("Cancel", setting_id=7)], nonce_ms=123
    )
    assert result.tx_hash == "0xabc"
    assert calls[0]["call_function"] == "update_tp_sl"
    assert calls[0]["nonce_ms"] == 123
    assert calls[0]["use_timestamp_nonce"] is True
    assert calls[0]["call_params"]["actions"] == [{"Cancel": {"id": 7}}]


def test_async_submission_waits_for_extrinsic_success():
    calls = []

    async def submit(**kw):
        calls.append(kw)
        return "ticket"

    client = AsyncPerpMarketClient(SimpleNamespace(subaccount=OWNER, _submit=submit))
    result = asyncio.run(
        client.update_tp_sl(
            market_id=3,
            actions=[TpSlAction("Create", TpSlConfig("StopLoss", 90_000_000))],
        )
    )
    assert result == "ticket"
    assert calls[0]["expected_event"].pallet == "System"
    assert (
        calls[0]["result_decoder"]({}, SimpleNamespace(tx_hash="0xabc")).tx_hash
        == "0xabc"
    )


def test_public_queries_keep_uint64_ids_exact_and_pass_opaque_cursors():
    from deepx_sdk.api_v1 import AccountV1Client

    calls = []
    api = AccountV1Client(
        SimpleNamespace(request=lambda *a, **kw: calls.append((a, kw)))
    )
    api.subaccount_position_tp_sl(
        address=OWNER,
        symbol="btc-usdc",
        position_id=2**64 - 1,
        cursor="opaque.scope.value",
        limit=100,
    )
    assert calls[-1][0] == (
        "GET",
        f"/v1/account/subaccounts/{OWNER}/perp/positions/18446744073709551615/tp-sl",
    )
    assert calls[-1][1]["params"] == {
        "symbol": "BTC-USDC",
        "cursor": "opaque.scope.value",
        "limit": 100,
    }
    api.subaccount_position_tp_sl_history(
        address=OWNER, symbol="BTC-USDC", position_id="21", setting_id="2"
    )
    assert calls[-1][0][1].endswith("/21/tp-sl/2/history")
    for value in [0, True, 1.2, "../21", 2**64]:
        with pytest.raises(ValueError):
            api.subaccount_position_tp_sl(
                address=OWNER, symbol="BTC-USDC", position_id=value
            )
