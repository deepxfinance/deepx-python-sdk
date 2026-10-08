from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scalecodec.base import RuntimeConfigurationObject, ScaleBytes
from scalecodec.type_registry import load_type_registry_preset
from scalecodec.types import GenericExtrinsicV4
from substrateinterface import SubstrateInterface

from deepx_sdk import _native_py, _perp_market
from deepx_sdk._async_encoder import ExtrinsicEncoder, RuntimeSnapshot
from deepx_sdk._async_tracker import TransactionTracker
from deepx_sdk._errors import DeepXSDKError, RPCError
from deepx_sdk._runtime_compat import _ImmortalMetadata, create_signed_extrinsic


class Metadata:
    def __init__(self, era="CheckNonceEra", *, events_map=False, error="SpotADLNotReady"):
        self.era = era
        self.events_map = events_map
        self.error = error

    def __getitem__(self, key):
        return [None, {"extrinsic": {"signed_extensions": [], "version": 4}}]

    def get_signed_extensions(self):
        return {
            "CheckSpecVersion": {"extrinsic": "Null", "additional_signed": "u32"},
            "CheckTxVersion": {"extrinsic": "Null", "additional_signed": "u32"},
            "CheckGenesis": {"extrinsic": "Null", "additional_signed": "Hash"},
            self.era: {"extrinsic": "Era", "additional_signed": "Hash"},
            "CheckNonce": {"extrinsic": "Compact<u64>", "additional_signed": "Null"},
        }

    def get_metadata_pallet(self, name):
        assert name == "System"
        return SimpleNamespace(get_storage_function=lambda item: (
            object() if item == "Events" or self.events_map else None
        ))

    def get_pallet_by_index(self, index):
        assert index == 24
        # Explicit indexes, not list positions, identify variants.
        return SimpleNamespace(name="Lending", errors=[{
            "name": self.error, "index": 13,
        }])


class Signer:
    def __init__(self, metadata):
        self.metadata = metadata
        self.runtime_version = 372
        self.transaction_version = 1
        self.runtime_config = RuntimeConfigurationObject()
        self.runtime_config.update_type_registry(load_type_registry_preset("core"))
        self.calls = []

    def init_runtime(self):
        self.calls.append("init_runtime")

    def get_block_hash(self, number):
        assert number == 0
        return "0x" + "12" * 32

    def create_signed_extrinsic(self, *, call, keypair, nonce):
        self.init_runtime()
        payload = SubstrateInterface.generate_signature_payload(self, call=call, nonce=nonce)
        fields = GenericExtrinsicV4(metadata=self.metadata).type_mapping
        return payload, fields


def test_immortal_signature_payload_and_extra_match_legacy_without_mutation():
    # Several existing test modules install a process-wide substrate stub during
    # collection. Exercise the real dependency in a clean process in that case.
    if not hasattr(SubstrateInterface, "generate_signature_payload"):
        subprocess.run([
            sys.executable, "-c",
            f"import sys; sys.path.insert(0, {str(Path(__file__).parent)!r}); "
            "from test_mvcc_compat import test_immortal_signature_payload_and_extra_match_legacy_without_mutation as check; check()",
        ], check=True, timeout=30, capture_output=True, text=True)
        return
    call = SimpleNamespace(data=ScaleBytes("0x0102"))
    old = Signer(Metadata("CheckEra"))
    new = Signer(Metadata())
    original = new.metadata
    legacy_payload, legacy_fields = create_signed_extrinsic(old, call=call, keypair=None, nonce=123)
    payload, fields = create_signed_extrinsic(new, call=call, keypair=None, nonce=123)
    assert bytes(payload.data) == bytes(legacy_payload.data)
    assert fields == legacy_fields
    assert [name for name, _ in fields] == ["address", "signature", "era", "nonce", "call"]
    assert new.metadata is original
    assert "CheckNonceEra" in original.get_signed_extensions()
    assert old.calls == ["init_runtime"]
    assert new.calls == []  # frozen signing view cannot refresh away the alias
    assert bytes(payload.data).endswith(bytes.fromhex("12" * 64))


def test_unknown_era_combination_is_rejected():
    metadata = Metadata()
    extensions = metadata.get_signed_extensions()
    extensions["CheckMortality"] = extensions["CheckNonceEra"]
    metadata.get_signed_extensions = lambda: extensions
    with pytest.raises(ValueError, match="Conflicting"):
        create_signed_extrinsic(Signer(metadata), call=None, keypair=None, nonce=1)


def test_alias_preserves_extension_order_and_additional_signed_type():
    metadata = Metadata()
    adapted = _ImmortalMetadata(metadata).get_signed_extensions()
    assert list(adapted) == ["CheckSpecVersion", "CheckTxVersion", "CheckGenesis", "CheckEra", "CheckNonce"]
    assert adapted["CheckEra"]["additional_signed"] == "Hash"


@pytest.mark.parametrize("name", ["NoBorrow", "SpotADLNotReady"])
def test_module_error_uses_runtime_metadata_not_global_registry(name):
    error = _native_py._chain_error_from_failed_attrs(
        {"dispatch_error": {"Module": {"index": 24, "error": "0x0d000000"}}},
        "test", metadata=Metadata(error=name),
    )
    assert (error.code, error.name, error.pallet) == ("24_13", name, "Lending")


def test_unknown_runtime_error_does_not_fall_back_to_wrong_legacy_name():
    error = _native_py._chain_error_from_failed_attrs(
        {"dispatch_error": {"Module": {"index": 24, "error": "0x0f000000"}}},
        "test", metadata=Metadata(),
    )
    assert error.code == "24_15"
    assert error.name == ""


@pytest.mark.parametrize("terminal_state", ["filled", "cancelled"])
def test_pruned_order_is_not_retried_as_an_old_selector(terminal_state, monkeypatch):
    calls = []
    def missing(*args):
        calls.append(args)
        raise RuntimeError(f"Order Info not found: pruned {terminal_state} order")
    monkeypatch.setattr(_perp_market, "evm_call", missing)
    with pytest.raises(RuntimeError, match="Order Info not found"):
        _perp_market.order_info(
            evm_rpc_url="https://unused", precompile_address="0x" + "11" * 20,
            user="0x" + "22" * 20, order_id=12345,
        )
    assert len(calls) == 1


def test_sync_mvcc_events_skip_thread_storage():
    calls = []
    substrate = SimpleNamespace(metadata=Metadata(), init_runtime=lambda **kw: calls.append(kw))
    assert _native_py._events_from_system_events_map(substrate=substrate, block_hash="0xblock") == []
    assert calls == [{"block_hash": "0xblock"}]


@pytest.mark.parametrize("events_map", [False, True])
def test_event_storage_layout_and_runtime_are_selected_at_parent(events_map, monkeypatch):
    monkeypatch.setattr(_native_py, "_system_threads_storage_key_hex", lambda **_: "0xthreads")
    monkeypatch.setattr(_native_py, "_system_events_map_storage_key_hex", lambda **_: "0xmap")
    async def run():
        selected = SimpleNamespace(substrate=SimpleNamespace(metadata=Metadata(events_map=events_map)),
                                   system_events_storage_key="0xevents")
        class Encoder:
            snapshot = selected

            async def snapshot_for_events(self, block_hash, version):
                assert block_hash == "0xblock"
                assert version == {"specVersion": 372, "transactionVersion": 1}
                return selected

            async def decode_system_events(self, raw, *, snapshot):
                assert not events_map
                assert raw == "0x00" and snapshot is selected
                return []

            async def decode_system_events_map(self, raw, *, snapshot):
                assert events_map
                assert raw == ["0x00"] and snapshot is selected
                return []

        requests = []
        class Transport:
            async def request(self, method, params):
                requests.append((method, params))
                if method == "state_getRuntimeVersion":
                    assert params == ["0xparent"]
                    return {"specVersion": 372, "transactionVersion": 1}
                assert method == "state_getStorage" and params[1] == "0xblock"
                return "0x00"

        transport = Transport()
        tracker = TransactionTracker(transport, Encoder())
        result = await tracker._fetch_block_events("0xblock", 10, transport, parent_hash="0xparent")
        assert result.metadata is selected.substrate.metadata
        assert len(requests) == (3 if events_map else 2)
        if not events_map:
            assert requests[-1] == ("state_getStorage", ["0xevents", "0xblock"])
    asyncio.run(run())


def test_unavailable_events_raise_instead_of_becoming_empty_success():
    async def run():
        selected = SimpleNamespace(substrate=SimpleNamespace(metadata=Metadata()), system_events_storage_key="0xe")
        class Encoder:
            async def snapshot_for_events(self, *_): return selected
        class Transport:
            async def request(self, method, params):
                return {} if method == "state_getRuntimeVersion" else None
        tracker = TransactionTracker(Transport(), Encoder())
        with pytest.raises(RPCError, match="Event storage unavailable"):
            await tracker._fetch_block_events("0xb", 1, Transport(), parent_hash="0xp")
    asyncio.run(run())


def snapshot(version, genesis="genesis"):
    return RuntimeSnapshot(
        substrate=SimpleNamespace(get_block_hash=lambda _: genesis), keypair=None,
        system_events_storage_key="0xe", chain_time_ms=1, calibration_monotonic_ns=0,
        runtime_version=version, transaction_version=1,
    )


def test_historical_snapshot_cache_is_bounded_and_does_not_replace_signing_runtime():
    async def run():
        encoder = ExtrinsicEncoder("ws://unused", "unused")
        encoder._snapshot = snapshot(372)
        loads = []
        def load(*, block_hash):
            loads.append(block_hash)
            return snapshot(int(block_hash))
        encoder._load_default_snapshot = load
        current = await encoder.snapshot_for_events("unused", {"specVersion": 372, "transactionVersion": 1})
        assert current is encoder.snapshot and not loads
        for version in range(366, 372):
            for _ in range(2):
                selected = await encoder.snapshot_for_events(str(version), {"specVersion": version, "transactionVersion": 1})
                assert selected.runtime_version == version
        assert len(loads) == 6 and len(encoder._event_snapshots) == 4
        assert encoder.snapshot.runtime_version == 372
    asyncio.run(run())


@pytest.mark.parametrize("loaded", [snapshot(371, "other-chain"), snapshot(370)])
def test_historical_snapshot_rejects_inconsistent_source(loaded):
    async def run():
        encoder = ExtrinsicEncoder("ws://unused", "unused")
        encoder._snapshot = snapshot(372)
        encoder._load_default_snapshot = lambda **_: loaded
        with pytest.raises(DeepXSDKError):
            await encoder.snapshot_for_events("block", {"specVersion": 371, "transactionVersion": 1})
        assert not encoder._event_snapshots
    asyncio.run(run())


class RawBlockReceiptSubstrate:
    """MVCC events decode, but upstream full-block extrinsic decoding fails."""

    def __init__(self, events):
        self.raw_extrinsics = ["0x010203", "0x040506"]
        self.events = [
            SimpleNamespace(value=event, extrinsic_idx=event["extrinsic_idx"])
            for event in events
        ]
        self.requests = []

    def get_block(self, **kwargs):
        raise ValueError("Index '19' not present in Enum type mapping")

    def get_events(self, *, block_hash):
        assert block_hash == "0xblock"
        return self.events

    def rpc_request(self, method, params):
        self.requests.append((method, params))
        assert method == "chain_getBlock" and params == ["0xblock"]
        return {"result": {"block": {"extrinsics": self.raw_extrinsics}}}


class RawBlockReceipt:
    error_message = None

    def __init__(self, *, substrate, extrinsic_hash, block_hash, extrinsic_idx=None,
                 block_number=None, finalized=False):
        self.substrate = substrate
        self.extrinsic_hash = extrinsic_hash
        self.block_hash = block_hash
        self._index = extrinsic_idx
        self.block_number = block_number
        self.finalized = finalized

    @property
    def extrinsic_idx(self):
        if self._index is None:
            self.substrate.get_block(block_hash=self.block_hash)
        return self._index

    @property
    def triggered_events(self):
        index = self.extrinsic_idx
        return [event for event in self.substrate.events if event.extrinsic_idx == index]

    @property
    def is_success(self):
        # Model the upstream lazy lookup that fails before event processing.
        self.triggered_events
        return None


def raw_block_receipt(events):
    import hashlib

    substrate = RawBlockReceiptSubstrate(events)
    tx_hash = "0x" + hashlib.blake2b(bytes.fromhex("040506"), digest_size=32).hexdigest()
    return RawBlockReceipt(substrate=substrate, extrinsic_hash=tx_hash, block_hash="0xblock")


def system_event(name, *, index=1, attributes=None):
    return {"module_id": "System", "event_id": name, "extrinsic_idx": index,
            "attributes": attributes or {}}


def test_sync_receipt_index_uses_raw_hash_when_mvcc_decode_fails():
    receipt = raw_block_receipt([])
    assert _native_py._receipt_extrinsic_idx(receipt) == 1
    assert receipt.substrate.requests == [("chain_getBlock", ["0xblock"])]


def test_sync_inclusion_scan_uses_raw_extrinsics():
    original = raw_block_receipt([system_event("ExtrinsicSuccess")])
    receipt = _native_py._receipt_if_block_contains(
        substrate=original.substrate, receipt_cls=RawBlockReceipt,
        extrinsic_hash=original.extrinsic_hash, block_hash="0xblock", finalized=True,
    )
    assert receipt is not None
    assert receipt.extrinsic_idx == 1
    assert receipt.finalized is True


def test_sync_receipt_status_recovers_from_mvcc_decode_error():
    receipt = raw_block_receipt([system_event("ExtrinsicSuccess")])
    _native_py._ensure_receipt_success(receipt)


def test_sync_receipt_status_propagates_scoped_failure_after_decode_error():
    receipt = raw_block_receipt([
        system_event("ExtrinsicSuccess", index=0),
        system_event("ExtrinsicFailed", attributes={"dispatch_error": "BadOrigin"}),
    ])
    with pytest.raises(RuntimeError, match="submit extrinsic failed"):
        _native_py._ensure_receipt_success(receipt)


@pytest.mark.parametrize("events", [[], [system_event("ExtrinsicSuccess", index=0)],
    [{"module_id": "Subaccount", "event_id": "NewUserRecord", "extrinsic_idx": 1,
      "attributes": {}}]])
def test_sync_receipt_inclusion_without_execution_status_is_not_success(events):
    receipt = raw_block_receipt(events)
    with pytest.raises(RuntimeError, match="status unknown"):
        _native_py._ensure_receipt_success(receipt)


@pytest.mark.parametrize("path", ["pallet", "ethereum"])
def test_sync_wait_event_recovers_from_mvcc_receipt_decode_error(monkeypatch, path):
    receipt = raw_block_receipt([
        {"module_id": "Subaccount", "event_id": "NewUserRecord", "extrinsic_idx": 0,
         "attributes": {"user": "other"}},
        {"module_id": "Subaccount", "event_id": "NewUserRecord", "extrinsic_idx": 1,
         "attributes": {"user": "target"}},
        system_event("ExtrinsicSuccess"),
    ])
    if path == "pallet":
        monkeypatch.setattr(_native_py, "_submit_signed_pallet_call", lambda **kwargs: receipt)
        result = _native_py.submit_pallet_call_wait_event(
            substrate_ws="ws://unused", private_key="0x" + "11" * 32,
            call_module="Subaccount", call_function="initialize_subaccount", call_params={},
            pallet="Subaccount", event="NewUserRecord",
        )
    else:
        monkeypatch.setattr(_native_py, "_submit_ethereum_transact", lambda **kwargs: receipt)
        monkeypatch.setattr(_native_py, "_decode_signed_rlp_bytes_to_transaction_v2", lambda _: {})
        result = _native_py.submit_signed_tx_wait_event(
            substrate_ws="ws://unused", signed_tx_hex="0x010203", signer="0x" + "11" * 20,
            pallet="Subaccount", event="NewUserRecord",
        )
    assert result.extrinsic_hash == receipt.extrinsic_hash
    assert result.fields_json == '{"user": "target"}'


@pytest.mark.parametrize("allow_unknown", [False, True])
def test_sync_receipt_failure_is_not_hidden_by_expected_business_event(allow_unknown):
    receipt = raw_block_receipt([
        {"module_id": "Subaccount", "event_id": "NewUserRecord", "extrinsic_idx": 1,
         "attributes": {"user": "target"}},
        system_event("ExtrinsicFailed", attributes={"dispatch_error": "BadOrigin"}),
    ])
    with pytest.raises(RuntimeError, match="submit extrinsic failed"):
        _native_py._ensure_receipt_success(receipt, allow_unknown=allow_unknown)


@pytest.mark.parametrize("response", [{"result": None}, {"error": {"message": "unavailable"}},
    {"result": {"block": {"extrinsics": ["not hex"]}}}])
def test_sync_receipt_unavailable_raw_block_is_not_success(monkeypatch, response):
    receipt = raw_block_receipt([system_event("ExtrinsicSuccess")])
    monkeypatch.setattr(receipt.substrate, "rpc_request", lambda *_: response)
    assert _native_py._receipt_extrinsic_idx(receipt) is None
    with pytest.raises(RuntimeError, match="status unknown"):
        _native_py._ensure_receipt_success(receipt)


def test_sync_receipt_other_transactions_do_not_supply_an_index():
    receipt = raw_block_receipt([system_event("ExtrinsicSuccess")])
    receipt.extrinsic_hash = "0x" + "00" * 32
    assert _native_py._receipt_extrinsic_idx(receipt) is None
    with pytest.raises(RuntimeError, match="status unknown"):
        _native_py._ensure_receipt_success(receipt)
