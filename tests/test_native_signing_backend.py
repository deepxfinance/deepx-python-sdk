from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor

import ecdsa
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_keys.backends import NativeECCBackend
from eth_keys.datatypes import Signature
from scalecodec.base import ScaleBytes

from deepx_sdk import ApiClient
from deepx_sdk import _native_py


# Public test scalar, with signatures captured before changing the backend.
KEY = "0x" + "01".zfill(64)
ADDRESS = "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"
PAYLOAD = b"deepx native signing regression"
SUBSTRATE_SIGNATURE = (
    "1b663be0d5f6fb3fa733d6aa4db1dce5fcdff9d21f2492a2f20ffb38dcf22d882d"
    "5ee144691c965f6b3f0a0a1b8b24e7dba91fd76719a564219d6691c568b43801"
)
QUOTA_SIGNATURE = (
    "2b860d523e7ead223ff1ce94af28d3cdfee6f62f5664c4e973a56375510499d07b"
    "0165b78c40648501fbcf4bc44c9ba5fb58508fb24a9c8bf1b20428a5ca03641b"
)


@pytest.fixture
def reject_python_signing(monkeypatch):
    monkeypatch.setenv("ECC_BACKEND_CLASS", "eth_keys.backends.NativeECCBackend")

    def rejected(*args, **kwargs):
        pytest.fail("SDK used pure-Python private-key operations")

    monkeypatch.setattr(NativeECCBackend, "private_key_to_public_key", rejected)
    monkeypatch.setattr(NativeECCBackend, "ecdsa_sign", rejected)
    monkeypatch.setattr(ecdsa.SigningKey, "sign_digest", rejected)


@pytest.mark.parametrize("legacy,expected_tx,expected_hash", [
    (False,
     "0x02f8698212f88301e240808082520894000000000000000000000000000000000000044e"
     "80821234c080a0e6145df936b950eecfae445e1aa67fd08ce025a8242926679bd0d094605e77c6"
     "a05d92088f6bf785684f5a745a386c364c44d2cdbccdd8846abb01f82bf042fff8",
     "0x1ca3f4f503b68c21719aae2fdab971193c1bd8d008c8c180e6ef859d44c79b5b"),
    (True,
     "0xf8668301e2408082520894000000000000000000000000000000000000044e80821234822613"
     "a0457d617826e08997765f8734d7de55f46eedacb9243d0319656238b698423141"
     "a04a38e3e3d709a8168896d295139543e2b34d48cab17135a1bd05b5a2612827eb",
     "0x91c4797cbb819e7504c833bb430d11c8c4ee0f9b4b3756e2bdb647496a15b690"),
])
def test_evm_signing_preserves_wire_bytes(
    reject_python_signing, legacy, expected_tx, expected_hash,
):
    signed = _native_py.build_signed_tx(
        evm_rpc_url="https://unused.invalid", private_key=KEY,
        precompile_address="0x000000000000000000000000000000000000044E",
        data_hex="0x1234", chain_id=4856, gas_limit=21000, nonce_ms=123456,
        use_legacy=legacy,
    )
    assert signed.signed_tx == expected_tx
    assert signed.tx_hash == expected_hash
    assert Account.recover_transaction(signed.signed_tx) == ADDRESS


@pytest.mark.parametrize("payload", [
    PAYLOAD, PAYLOAD.decode(), "0x" + PAYLOAD.hex(), ScaleBytes(PAYLOAD),
])
def test_substrate_signing_preserves_wire_bytes(reject_python_signing, payload):
    keypair = _native_py._create_ecdsa_keypair(KEY)
    assert keypair.ss58_address == ADDRESS
    assert keypair.public_key == bytes.fromhex(ADDRESS[2:])
    assert keypair.crypto_type == 2
    signature = keypair.sign(payload)
    assert signature.hex() == SUBSTRATE_SIGNATURE
    assert Signature(signature).recover_public_key_from_msg(PAYLOAD).to_checksum_address() == ADDRESS


def test_quota_claim_uses_native_signing(reject_python_signing, monkeypatch):
    api = ApiClient(base_url="https://unused.invalid", private_key=KEY)
    captured = {}

    def request(method, path, **kwargs):
        captured.update(kwargs["json_body"])
        return captured

    monkeypatch.setattr(api, "request", request)
    api.v1.account.claim_quota(idempotency_key="audit-test")
    assert captured["signature"] == "0x" + QUOTA_SIGNATURE
    assert Account.recover_message(
        encode_defunct(text=captured["message"]), signature=captured["signature"],
    ) == ADDRESS


def test_concurrent_substrate_signing(reject_python_signing):
    keypair = _native_py._create_ecdsa_keypair(KEY)
    with ThreadPoolExecutor(max_workers=4) as executor:
        signatures = list(executor.map(keypair.sign, [PAYLOAD] * 16))
    assert all(s.hex() == SUBSTRATE_SIGNATURE for s in signatures)


def test_address_derivation_uses_native_backend(reject_python_signing):
    from deepx_sdk._lending import _signer_address
    from deepx_sdk.bridge import _evm_address_from_key

    assert _signer_address(KEY).lower() == ADDRESS.lower()
    assert _evm_address_from_key(KEY) == ADDRESS


def test_sdk_does_not_change_host_backend_configuration(reject_python_signing):
    original_keys = Account._keys
    _native_py._create_ecdsa_keypair(KEY).sign(PAYLOAD)
    account, *_ = _native_py._get_signing_libs()
    account.from_key(KEY).sign_message(encode_defunct(text="host configuration"))
    assert Account._keys is original_keys
    assert os.environ["ECC_BACKEND_CLASS"] == "eth_keys.backends.NativeECCBackend"


@pytest.mark.parametrize("path", ["evm", "substrate", "quota"])
def test_native_library_missing_stops_signing(monkeypatch, path):
    monkeypatch.setitem(sys.modules, "coincurve", None)
    with pytest.raises(ImportError, match="coincurve"):
        if path == "evm":
            _native_py.build_signed_tx(
                evm_rpc_url="https://unused.invalid", private_key=KEY,
                precompile_address="0x000000000000000000000000000000000000044E",
                data_hex="0x1234", chain_id=4856, gas_limit=21000, nonce_ms=123456,
            )
        elif path == "substrate":
            _native_py._create_ecdsa_keypair(KEY)
        else:
            api = ApiClient(base_url="https://unused.invalid", private_key=KEY)
            api.v1.account.claim_quota(idempotency_key="audit-test")
