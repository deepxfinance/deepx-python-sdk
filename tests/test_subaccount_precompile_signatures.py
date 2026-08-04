from __future__ import annotations

import sys
import types
from typing import Any

# Avoid hard dependency on substrate-interface when importing deepx_sdk package.
if "substrateinterface" not in sys.modules:
    substrate_stub = types.ModuleType("substrateinterface")

    class _SubstrateInterfacePlaceholder:
        pass

    substrate_stub.SubstrateInterface = _SubstrateInterfacePlaceholder
    sys.modules["substrateinterface"] = substrate_stub

from deepx_sdk import _subaccount


def test_create_oct_uses_pallet_call_with_single_address(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_submit_subaccount_call(**kwargs: Any) -> Any:
        captured["submit_kwargs"] = kwargs
        return types.SimpleNamespace(tx_hash="0xabc", event=None)

    monkeypatch.setattr(_subaccount, "_submit_subaccount_call", fake_submit_subaccount_call)

    _subaccount.create_one_click_trading_account(
        substrate_ws="ws://127.0.0.1:9944",
        evm_rpc_url="http://127.0.0.1:8545",
        private_key="0x" + "11" * 32,
        precompile_address="0x0000000000000000000000000000000000000451",
        new_account="0x00000000000000000000000000000000000000aa",
        quota=123,  # deprecated argument kept for compatibility.
    )

    assert captured["submit_kwargs"]["call_function"] == "create_one_click_trading_account"
    assert captured["submit_kwargs"]["call_params"] == {
        "new": "0x00000000000000000000000000000000000000aa"
    }
