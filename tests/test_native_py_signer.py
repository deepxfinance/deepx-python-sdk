from __future__ import annotations

import io
import importlib.util
import json
from pathlib import Path
import sys
import urllib.error

from eth_account import Account
import pytest

from deepx_sdk import ChainError


def _load_native_py():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "python"
        / "deepx_sdk"
        / "_native_py.py"
    )
    spec = importlib.util.spec_from_file_location("deepx_sdk._native_py_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load _native_py module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_signed_tx_python_fallback(monkeypatch) -> None:
    _native_py = _load_native_py()

    monkeypatch.setattr(_native_py, "_rpc_get_chain_id", lambda _url: 31337)
    monkeypatch.setattr(_native_py, "_rpc_get_transaction_count", lambda _url, _addr: 7)
    monkeypatch.setattr(_native_py, "_rpc_estimate_gas", lambda _url, _tx: 21000)

    private_key = "0x59c6995e998f97a5a0044966f0945384e95f1f0b1f3491e0f2d4f5b8f96f1e7b"
    precompile = "0x000000000000000000000000000000000000044E"

    signed = _native_py.build_signed_tx(
        evm_rpc_url="http://127.0.0.1:8545",
        private_key=private_key,
        precompile_address=precompile,
        data_hex="0x12345678",
        chain_id=None,
        gas_limit=None,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce_ms=None,
        use_timestamp_nonce=False,
    )

    assert signed.signed_tx.startswith("0x")
    assert signed.signer.startswith("0x")
    assert len(signed.signer) == 42
    assert signed.tx_hash.startswith("0x")
    assert len(signed.tx_hash) == 66

    recovered = Account.recover_transaction(signed.signed_tx).lower()
    assert recovered == signed.signer.lower()


def test_build_signed_tx_uses_default_gas_when_estimate_fails(monkeypatch) -> None:
    _native_py = _load_native_py()

    monkeypatch.setattr(_native_py, "_rpc_get_chain_id", lambda _url: 31337)

    def fail_estimate(_url, _tx):
        raise RuntimeError("eth_estimateGas error: revert")

    monkeypatch.setattr(_native_py, "_rpc_estimate_gas", fail_estimate)

    signed = _native_py.build_signed_tx(
        evm_rpc_url="http://127.0.0.1:8545",
        private_key="0x59c6995e998f97a5a0044966f0945384e95f1f0b1f3491e0f2d4f5b8f96f1e7b",
        precompile_address="0x000000000000000000000000000000000000044E",
        data_hex="0x18ae37ea",
        chain_id=None,
        gas_limit=None,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce_ms=1781757000123,
        use_timestamp_nonce=True,
    )

    tx = _native_py._decode_signed_rlp_bytes_to_transaction_v2(
        bytes.fromhex(signed.signed_tx[2:])
    )
    assert tx["EIP1559"]["gas_limit"] == _native_py.DEFAULT_PRECOMPILE_GAS_LIMIT
    assert signed.nonce == 1781757000123
    assert signed.gas_limit == 500_000


def test_build_signed_tx_uses_default_gas_when_estimate_returns_zero(monkeypatch) -> None:
    _native_py = _load_native_py()

    monkeypatch.setattr(_native_py, "_rpc_get_chain_id", lambda _url: 31337)
    monkeypatch.setattr(_native_py, "_rpc_estimate_gas", lambda _url, _tx: 0)

    signed = _native_py.build_signed_tx(
        evm_rpc_url="http://127.0.0.1:8545",
        private_key="0x59c6995e998f97a5a0044966f0945384e95f1f0b1f3491e0f2d4f5b8f96f1e7b",
        precompile_address="0x000000000000000000000000000000000000044E",
        data_hex="0x18ae37ea",
        chain_id=None,
        gas_limit=None,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce_ms=1781757000123,
        use_timestamp_nonce=True,
    )

    assert signed.gas_limit == _native_py.DEFAULT_PRECOMPILE_GAS_LIMIT


def test_decode_signed_rlp_to_transaction_v2_variants(monkeypatch) -> None:
    _native_py = _load_native_py()

    monkeypatch.setattr(_native_py, "_rpc_get_chain_id", lambda _url: 31337)
    monkeypatch.setattr(_native_py, "_rpc_estimate_gas", lambda _url, _tx: 21000)

    private_key = "0x59c6995e998f97a5a0044966f0945384e95f1f0b1f3491e0f2d4f5b8f96f1e7b"
    precompile = "0x000000000000000000000000000000000000044E"

    dynamic_fee = _native_py.build_signed_tx(
        evm_rpc_url="http://127.0.0.1:8545",
        private_key=private_key,
        precompile_address=precompile,
        data_hex="0x12345678",
        chain_id=31337,
        gas_limit=21000,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce_ms=1,
        use_timestamp_nonce=False,
    )
    tx_v2 = _native_py._decode_signed_rlp_bytes_to_transaction_v2(
        bytes.fromhex(dynamic_fee.signed_tx[2:])
    )
    assert "EIP1559" in tx_v2
    assert tx_v2["EIP1559"]["nonce"] == 1
    assert tx_v2["EIP1559"]["action"]["Call"] == precompile.lower()

    legacy = _native_py.build_signed_tx(
        evm_rpc_url="http://127.0.0.1:8545",
        private_key=private_key,
        precompile_address=precompile,
        data_hex="0x12345678",
        chain_id=31337,
        gas_limit=21000,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=True,
        nonce_ms=2,
        use_timestamp_nonce=False,
    )
    tx_legacy = _native_py._decode_signed_rlp_bytes_to_transaction_v2(
        bytes.fromhex(legacy.signed_tx[2:])
    )
    assert "Legacy" in tx_legacy
    assert tx_legacy["Legacy"]["nonce"] == 2
    assert tx_legacy["Legacy"]["action"]["Call"] == precompile.lower()
    assert tx_legacy["Legacy"]["signature"]["v"] in {0, 1}


def test_submit_signed_tx_wait_event_python_fallback(monkeypatch) -> None:
    _native_py = _load_native_py()

    class _DummyEvent:
        value = {
            "module_id": "SpotMarket",
            "event_id": "OrderCancelled",
            "attributes": {"order_id": 12345},
        }

    class _DummyReceipt:
        extrinsic_hash = "0xabc"
        block_hash = "0xdef"
        is_success = None
        error_message = None
        triggered_events = [_DummyEvent()]

        @property
        def substrate(self):
            class _S:
                @staticmethod
                def get_events(block_hash: str):
                    _ = block_hash
                    return [
                        _DummyEvent(),
                    ]

            return _S()

    class _DummySubstrate:
        def __init__(self, *, url: str) -> None:
            self.url = url

        def compose_call(self, *, call_module: str, call_function: str, call_params: dict) -> str:
            assert call_function == "transact"
            assert call_module in {"Ethereum", "ethereum"}
            assert "transaction" in call_params
            assert "source" in call_params or "signer" in call_params
            return "call"

        def create_unsigned_extrinsic(self, *, call: str) -> str:
            assert call == "call"
            return "xt"

        def submit_extrinsic(
            self,
            extrinsic: str,
            *,
            wait_for_inclusion: bool = False,
            wait_for_finalization: bool = False,
        ) -> _DummyReceipt:
            assert extrinsic == "xt"
            _ = wait_for_inclusion, wait_for_finalization
            return _DummyReceipt()

    monkeypatch.setattr(_native_py, "_get_substrate_interface_cls", lambda: _DummySubstrate)

    signed = _native_py.build_signed_tx(
        evm_rpc_url="http://127.0.0.1:8545",
        private_key="0x59c6995e998f97a5a0044966f0945384e95f1f0b1f3491e0f2d4f5b8f96f1e7b",
        precompile_address="0x000000000000000000000000000000000000044D",
        data_hex="0x1234",
        chain_id=31337,
        gas_limit=21000,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce_ms=3,
        use_timestamp_nonce=False,
    )

    result = _native_py.submit_signed_tx_wait_event(
        substrate_ws="ws://127.0.0.1:9944",
        signed_tx_hex=signed.signed_tx,
        signer=signed.signer,
        pallet="SpotMarket",
        event="OrderCancelled",
        wait_for_finalized=True,
        timeout_ms=1000,
    )

    assert result.tx_hash.startswith("0x")
    assert result.extrinsic_hash == "0xabc"
    assert result.pallet == "SpotMarket"
    assert result.event == "OrderCancelled"
    assert '"order_id": 12345' in result.fields_json


def test_submit_signed_tx_wait_event_fallback_to_block_events(monkeypatch) -> None:
    _native_py = _load_native_py()

    class _DummyEventObj:
        def __init__(self, value: dict) -> None:
            self.value = value

    class _DummyReceipt:
        extrinsic_hash = "0xabc2"
        block_hash = "0xdef2"
        extrinsic_idx = 1
        is_success = None
        error_message = None

        @property
        def triggered_events(self):  # type: ignore[override]
            raise RuntimeError("Extrinsic not found in supplied block")

        @property
        def substrate(self):
            class _S:
                @staticmethod
                def get_events(block_hash: str):
                    _ = block_hash
                    return [
                        _DummyEventObj(
                            {
                                "module_id": "System",
                                "event_id": "ExtrinsicSuccess",
                                "attributes": {},
                                "phase": {"ApplyExtrinsic": 1},
                            }
                        ),
                        _DummyEventObj(
                            {
                                "module_id": "SpotMarket",
                                "event_id": "OrderCancelled",
                                "attributes": {"order_id": 987},
                                "phase": {"ApplyExtrinsic": 1},
                            }
                        ),
                    ]

            return _S()

    class _DummySubstrate:
        def __init__(self, *, url: str) -> None:
            self.url = url

        def compose_call(self, *, call_module: str, call_function: str, call_params: dict) -> str:
            _ = call_module, call_function, call_params
            return "call"

        def create_unsigned_extrinsic(self, *, call: str) -> str:
            _ = call
            return "xt"

        def submit_extrinsic(
            self,
            extrinsic: str,
            *,
            wait_for_inclusion: bool = False,
            wait_for_finalization: bool = False,
        ) -> _DummyReceipt:
            _ = extrinsic, wait_for_inclusion, wait_for_finalization
            return _DummyReceipt()

    monkeypatch.setattr(_native_py, "_get_substrate_interface_cls", lambda: _DummySubstrate)

    signed = _native_py.build_signed_tx(
        evm_rpc_url="http://127.0.0.1:8545",
        private_key="0x59c6995e998f97a5a0044966f0945384e95f1f0b1f3491e0f2d4f5b8f96f1e7b",
        precompile_address="0x000000000000000000000000000000000000044D",
        data_hex="0x1234",
        chain_id=31337,
        gas_limit=21000,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce_ms=4,
        use_timestamp_nonce=False,
    )

    result = _native_py.submit_signed_tx_wait_event(
        substrate_ws="ws://127.0.0.1:9944",
        signed_tx_hex=signed.signed_tx,
        signer=signed.signer,
        pallet="SpotMarket",
        event="OrderCancelled",
        wait_for_finalized=True,
        timeout_ms=1000,
    )

    assert result.extrinsic_hash == "0xabc2"
    assert '"order_id": 987' in result.fields_json


def test_submit_signed_tx_wait_event_does_not_match_unscoped_block_events(
    monkeypatch,
) -> None:
    _native_py = _load_native_py()

    class _DummyEventObj:
        def __init__(self, value: dict) -> None:
            self.value = value

    class _DummyReceipt:
        extrinsic_hash = "0xabc3"
        block_hash = "0xdef3"
        is_success = None
        error_message = None

        @property
        def triggered_events(self):  # type: ignore[override]
            raise RuntimeError("Extrinsic not found in supplied block")

        @property
        def substrate(self):
            class _S:
                @staticmethod
                def get_events(block_hash: str):
                    _ = block_hash
                    return [
                        _DummyEventObj(
                            {
                                "module_id": "SpotMarket",
                                "event_id": "OrderCancelled",
                                "attributes": {"order_id": 987},
                            }
                        ),
                    ]

            return _S()

    class _DummySubstrate:
        def __init__(self, *, url: str) -> None:
            self.url = url

        def compose_call(self, *, call_module: str, call_function: str, call_params: dict) -> str:
            _ = call_module, call_function, call_params
            return "call"

        def create_unsigned_extrinsic(self, *, call: str) -> str:
            _ = call
            return "xt"

        def submit_extrinsic(
            self,
            extrinsic: str,
            *,
            wait_for_inclusion: bool = False,
            wait_for_finalization: bool = False,
        ) -> _DummyReceipt:
            _ = extrinsic, wait_for_inclusion, wait_for_finalization
            return _DummyReceipt()

    monkeypatch.setattr(_native_py, "_get_substrate_interface_cls", lambda: _DummySubstrate)

    signed = _native_py.build_signed_tx(
        evm_rpc_url="http://127.0.0.1:8545",
        private_key="0x59c6995e998f97a5a0044966f0945384e95f1f0b1f3491e0f2d4f5b8f96f1e7b",
        precompile_address="0x000000000000000000000000000000000000044D",
        data_hex="0x1234",
        chain_id=31337,
        gas_limit=21000,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        use_legacy=False,
        nonce_ms=4,
        use_timestamp_nonce=False,
    )

    try:
        _native_py.submit_signed_tx_wait_event(
            substrate_ws="ws://127.0.0.1:9944",
            signed_tx_hex=signed.signed_tx,
            signer=signed.signer,
            pallet="SpotMarket",
            event="OrderCancelled",
            wait_for_finalized=True,
            timeout_ms=1000,
        )
    except RuntimeError as exc:
        assert "event not found: SpotMarket::OrderCancelled" in str(exc)
    else:
        raise AssertionError("unscoped block event should not be treated as this tx result")


def test_safe_block_events_uses_rpc_decode_fallback(monkeypatch) -> None:
    _native_py = _load_native_py()

    class _QueryObj:
        value = None
        elements = []

    class _Substrate:
        def get_events(self, *, block_hash: str):
            _ = block_hash
            return []

        def query(self, *, module: str, storage_function: str, block_hash: str):
            _ = module, storage_function, block_hash
            return _QueryObj()

    class _Receipt:
        substrate = _Substrate()
        block_hash = "0xblock"

    monkeypatch.setattr(
        _native_py,
        "_events_from_system_events_rpc",
        lambda *, substrate, block_hash: [
            {
                "module_id": "SpotMarket",
                "event_id": "OrderCancelled",
                "attributes": {"order_id": 77},
                "extrinsic_idx": 1,
            }
        ],
    )

    events, err = _native_py._safe_block_events(_Receipt())
    assert err is None
    assert isinstance(events, list)
    assert len(events) == 1
    assert events[0]["module_id"] == "SpotMarket"
    assert events[0]["event_id"] == "OrderCancelled"


def test_submit_pallet_call_wait_event(monkeypatch) -> None:
    _native_py = _load_native_py()
    monkeypatch.setattr(_native_py.time, "time", lambda: 1.234)

    class _DummyKeypair:
        @staticmethod
        def create_from_private_key(**kwargs):
            assert "private_key" in kwargs
            return "keypair"

    class _DummyKeypairType:
        ECDSA = 2

    class _DummyEventObj:
        value = {
            "module_id": "Lending",
            "event_id": "Deposit",
            "attributes": {"amount": 123},
        }

    class _DummyReceipt:
        extrinsic_hash = "0xpal"
        block_hash = "0xblock"
        is_success = True
        error_message = None
        triggered_events = [_DummyEventObj()]

    class _DummySubstrate:
        def __init__(self, *, url: str) -> None:
            self.url = url

        def compose_call(self, *, call_module: str, call_function: str, call_params: dict) -> str:
            assert call_module == "Lending"
            assert call_function == "deposit"
            assert call_params["amount"] == 123
            return "call"

        def create_signed_extrinsic(self, *, call: str, keypair: object, nonce: int) -> str:
            assert call == "call"
            assert keypair == "keypair"
            assert nonce == 1234
            return "xt"

        def submit_extrinsic(
            self,
            extrinsic: str,
            *,
            wait_for_inclusion: bool = False,
            wait_for_finalization: bool = False,
        ) -> _DummyReceipt:
            assert extrinsic == "xt"
            # submit_pallet_call_wait_event waits for inclusion via
            # author_submitAndWatchExtrinsic (subscription), not fire-and-forget.
            assert wait_for_inclusion is True
            assert wait_for_finalization is False
            return _DummyReceipt()

    monkeypatch.setattr(_native_py, "_get_substrate_interface_cls", lambda: _DummySubstrate)
    monkeypatch.setattr(
        _native_py,
        "_get_substrate_keypair_libs",
        lambda: (_DummyKeypair, _DummyKeypairType),
    )

    result = _native_py.submit_pallet_call_wait_event(
        substrate_ws="ws://127.0.0.1:9944",
        private_key="0x" + "11" * 32,
        call_module="Lending",
        call_function="deposit",
        call_params={"amount": 123},
        pallet="Lending",
        event="Deposit",
        wait_for_finalized=True,
        timeout_ms=1000,
    )

    assert result.tx_hash == "0xpal"
    assert result.extrinsic_hash == "0xpal"
    assert result.pallet == "Lending"
    assert result.event == "Deposit"
    assert '"amount": 123' in result.fields_json


def test_submit_pallet_call_wait_event_raises_failed_block_event(monkeypatch) -> None:
    _native_py = _load_native_py()

    class _DummyReceipt:
        extrinsic_hash = "0xfailed"
        block_hash = "0xblock"
        extrinsic_idx = 2
        is_success = None
        error_message = None
        triggered_events = []

    monkeypatch.setattr(
        _native_py,
        "_submit_signed_pallet_call",
        lambda **_kwargs: _DummyReceipt(),
    )
    monkeypatch.setattr(
        _native_py,
        "_safe_block_events",
        lambda _receipt: (
            [
                {
                    "module_id": "System",
                    "event_id": "ExtrinsicFailed",
                    "attributes": {"dispatch_error": "BadOrigin"},
                    "phase": {"ApplyExtrinsic": 2},
                }
            ],
            None,
        ),
    )

    with pytest.raises(RuntimeError, match="submit extrinsic failed \\(block events\\)"):
        _native_py.submit_pallet_call_wait_event(
            substrate_ws="ws://127.0.0.1:9944",
            private_key="0x" + "11" * 32,
            call_module="Lending",
            call_function="deposit",
            call_params={"amount": 123},
            pallet="Lending",
            event="Deposit",
            wait_for_finalized=True,
            timeout_ms=1000,
        )


def test_submit_pallet_call_wait_event_module_dispatch_raises_chain_error(monkeypatch) -> None:
    """A Module dispatch_error (pallet_index, error_index) surfaces as a typed
    ChainError with code/name/pallet from the registry, not a raw RuntimeError."""
    _native_py = _load_native_py()

    class _DummyReceipt:
        extrinsic_hash = "0xfailed"
        block_hash = "0xblock"
        extrinsic_idx = 2
        is_success = None
        error_message = None
        triggered_events = []

    monkeypatch.setattr(
        _native_py,
        "_submit_signed_pallet_call",
        lambda **_kwargs: _DummyReceipt(),
    )
    # 19_9 = Subaccount::DuplicateSubaccountName; error 0x09000000 -> index 9 (LE).
    monkeypatch.setattr(
        _native_py,
        "_safe_block_events",
        lambda _receipt: (
            [
                {
                    "module_id": "System",
                    "event_id": "ExtrinsicFailed",
                    "attributes": {
                        "dispatch_error": {"Module": {"index": 19, "error": "0x09000000"}},
                    },
                    "phase": {"ApplyExtrinsic": 2},
                }
            ],
            None,
        ),
    )

    with pytest.raises(ChainError) as exc_info:
        _native_py.submit_pallet_call_wait_event(
            substrate_ws="ws://127.0.0.1:9944",
            private_key="0x" + "11" * 32,
            call_module="Subaccount",
            call_function="initialize_subaccount",
            call_params={"name": b"x"},
            pallet="Subaccount",
            event="NewUserRecord",
            wait_for_finalized=True,
            timeout_ms=1000,
        )

    err = exc_info.value
    assert err.code == "19_9"
    assert err.name == "DuplicateSubaccountName"
    assert err.pallet == "Subaccount"
    assert err.pallet_index == 19
    assert err.error_index == 9


def test_rpc_call_http_and_json_rpc_errors(monkeypatch) -> None:
    _native_py = _load_native_py()

    def http_error(req, timeout=None):
        _ = timeout
        raise urllib.error.HTTPError(
            req.full_url,
            503,
            "Unavailable",
            {},
            io.BytesIO(b"node down\nretry later"),
        )

    monkeypatch.setattr(_native_py.urllib.request, "urlopen", http_error)

    with pytest.raises(RuntimeError, match="eth_chainId request failed: HTTP 503"):
        _native_py._rpc_call("http://127.0.0.1:8545", "eth_chainId", [])

    class JsonResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}}
            ).encode("utf-8")

    monkeypatch.setattr(
        _native_py.urllib.request,
        "urlopen",
        lambda req, timeout=None: JsonResponse(),
    )

    with pytest.raises(RuntimeError, match="eth_chainId error"):
        _native_py._rpc_call("http://127.0.0.1:8545", "eth_chainId", [])


def test_build_signed_pallet_call_extrinsic_uses_nonce_ms(monkeypatch) -> None:
    _native_py = _load_native_py()

    class _DummyKeypair:
        @staticmethod
        def create_from_private_key(**kwargs):
            assert "private_key" in kwargs
            return "keypair"

    class _DummyKeypairType:
        ECDSA = 2

    class _DummyExtrinsicData:
        def to_hex(self) -> str:
            return "0xxt"

    class _DummyExtrinsic:
        data = _DummyExtrinsicData()

    class _DummySubstrate:
        def __init__(self, *, url: str) -> None:
            self.url = url

        def compose_call(self, *, call_module: str, call_function: str, call_params: dict) -> str:
            assert call_module == "PerpMarket"
            assert call_function == "place_order"
            assert call_params["market_id"] == 3
            return "call"

        def create_signed_extrinsic(
            self, *, call: str, keypair: object, nonce: int
        ) -> _DummyExtrinsic:
            assert call == "call"
            assert keypair == "keypair"
            assert nonce == 1781757000123
            return _DummyExtrinsic()

    monkeypatch.setattr(_native_py, "_get_substrate_interface_cls", lambda: _DummySubstrate)
    monkeypatch.setattr(
        _native_py,
        "_get_substrate_keypair_libs",
        lambda: (_DummyKeypair, _DummyKeypairType),
    )

    signed = _native_py.build_signed_pallet_call_extrinsic(
        substrate_ws="ws://127.0.0.1:9944",
        private_key="0x" + "11" * 32,
        call_module="PerpMarket",
        call_function="place_order",
        call_params={"market_id": 3},
        nonce_ms=1781757000123,
    )

    assert signed == "0xxt"
