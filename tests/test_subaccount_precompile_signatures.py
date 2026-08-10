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


def test_update_delegate_mode_uses_pallet_call(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_submit_subaccount_call(**kwargs: Any) -> Any:
        captured["submit_kwargs"] = kwargs
        return types.SimpleNamespace(tx_hash="0xabc", event=None)

    monkeypatch.setattr(_subaccount, "_submit_subaccount_call", fake_submit_subaccount_call)

    _subaccount.update_delegate_mode(
        substrate_ws="ws://127.0.0.1:9944",
        evm_rpc_url="http://127.0.0.1:8545",
        private_key="0x" + "11" * 32,
        precompile_address="0x0000000000000000000000000000000000000451",
        delegate="0x00000000000000000000000000000000000000aa",
        new_mode=3,
    )

    assert captured["submit_kwargs"]["call_function"] == "update_delegate_mode"
    assert captured["submit_kwargs"]["call_params"] == {
        "address": "0x00000000000000000000000000000000000000aa",
        "new_mode": "Disable",
    }


def test_update_delegate_mode_rejects_disabled_modes() -> None:
    import pytest

    for disabled in (1, 2, "DepositOrWithdraw", "UpdateSubaccount"):
        with pytest.raises(ValueError, match="disabled on-chain"):
            _subaccount.update_delegate_mode(
                substrate_ws="ws://127.0.0.1:9944",
                evm_rpc_url="http://127.0.0.1:8545",
                private_key="0x" + "11" * 32,
                precompile_address="0x0000000000000000000000000000000000000451",
                delegate="0x00000000000000000000000000000000000000aa",
                new_mode=disabled,
            )
