"""SDK-owned secp256k1 keys; never use the process-wide default backend."""

from __future__ import annotations

from typing import Any

from eth_account import Account
from eth_keys.backends import CoinCurveECCBackend
from eth_keys.datatypes import PrivateKey
from eth_utils import combomethod
from hexbytes import HexBytes
from scalecodec.base import ScaleBytes


def _native_private_key(key: Any) -> PrivateKey:
    try:
        backend = CoinCurveECCBackend()
    except (ImportError, OSError) as exc:
        raise ImportError(
            "Native secp256k1 signing requires coincurve. "
            "Reinstall deepx-python-sdk with its dependencies."
        ) from exc
    raw = key.to_bytes() if isinstance(key, PrivateKey) else HexBytes(key)
    return PrivateKey(raw, backend=backend)


class NativeAccount(Account):
    @combomethod
    def _parse_private_key(self, key: Any) -> PrivateKey:
        # LocalAccount methods pass raw bytes back through this parser when signing.
        return _native_private_key(key)

    # eth-account 0.11 uses the previous name for the same parser.
    _parsePrivateKey = _parse_private_key


class NativeKeypair:
    """The ECDSA keypair interface consumed by Substrate extrinsic encoding."""

    crypto_type = 2  # Substrate KeypairType.ECDSA / MultiSignature::Ecdsa.

    def __init__(self, private_key: str) -> None:
        self._signing_key = _native_private_key(private_key)
        public_key = self._signing_key.public_key
        self.public_key = public_key.to_canonical_address()
        self.ss58_address = public_key.to_checksum_address()

    def sign(self, data: ScaleBytes | bytes | str) -> bytes:
        if isinstance(data, ScaleBytes):
            data = bytes(data.data)
        elif isinstance(data, str):
            data = bytes.fromhex(data[2:]) if data.startswith("0x") else data.encode()
        return self._signing_key.sign_msg(data).to_bytes()
