"""Compatibility with the hashless era used by the MVCC runtime.

Only SDK-generated immortal transactions are adapted. Mortal NonceEra uses an
absolute deadline, not the legacy Era period/phase encoding.
"""

from __future__ import annotations

import copy
from typing import Any


class _ImmortalMetadata:
    """Local view for upstream codecs which only recognize CheckEra by name."""

    def __init__(self, metadata: Any) -> None:
        self._metadata = metadata

    def __getitem__(self, key: Any) -> Any:
        return self._metadata[key]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._metadata, name)

    def get_signed_extensions(self) -> dict[str, Any]:
        return {
            ("CheckEra" if name == "CheckNonceEra" else name): (
                {**definition, "extrinsic": "Era"}
                if name == "CheckNonceEra" else definition
            )
            for name, definition in self._metadata.get_signed_extensions().items()
        }


def create_signed_extrinsic(
    substrate: Any, *, call: Any, keypair: Any, nonce: int,
) -> Any:
    """Sign with an immortal era on either runtime, without shared mutations."""
    metadata = getattr(substrate, "metadata", None)
    extensions = (
        metadata.get_signed_extensions()
        if callable(getattr(metadata, "get_signed_extensions", None)) else {}
    )
    if "CheckNonceEra" not in extensions:
        return substrate.create_signed_extrinsic(call=call, keypair=keypair, nonce=nonce)
    if "CheckEra" in extensions or "CheckMortality" in extensions:
        raise ValueError("Conflicting transaction era extensions in runtime metadata")

    # The MVCC runtime deliberately preserves Immortal=0x00 and signs the genesis
    # hash as additional data. Retain its metadata type for that hash. Do not
    # alias mortal transactions, or mutate metadata shared with another encoder.
    signing_view = copy.copy(substrate)
    signing_view.metadata = _ImmortalMetadata(metadata)
    # compose_call already initialized this runtime. Keep the same snapshot for
    # signing; upstream init_runtime() must not swap metadata to a newer head.
    signing_view.init_runtime = lambda *args, **kwargs: None
    return signing_view.create_signed_extrinsic(call=call, keypair=keypair, nonce=nonce)


def uses_events_map(substrate: Any) -> bool:
    """Use metadata when present; keep probing for legacy/mock interfaces."""
    metadata = getattr(substrate, "metadata", None)
    getter = getattr(metadata, "get_metadata_pallet", None)
    if not callable(getter):
        return True
    system = getter("System")
    if system is None:
        raise ValueError("Runtime metadata does not contain System")
    return system.get_storage_function("EventsMap") is not None
