"""Explicit, read-only runtime compatibility check. Never broadcasts a transaction.

Run with one or more --rpc HTTPS endpoints. Uses a throwaway key, not .env keys.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets

import requests
from scalecodec.base import ScaleBytes

from deepx_sdk import _native_py
from deepx_sdk._async_encoder import ExtrinsicEncoder, _encode_pallet_call_sync
from deepx_sdk._async_tracker import TransactionTracker
from deepx_sdk._perp_market import _perp_cancel_params, _perp_place_params
from deepx_sdk._runtime_compat import _ImmortalMetadata, uses_events_map
from deepx_sdk._spot_market import _spot_cancel_params, _spot_place_params


def check(url: str) -> dict:
    if not url.startswith(("https://", "http://")):
        raise ValueError("Use an HTTP(S) RPC endpoint for this read-only check")
    session = requests.Session()

    def rpc(method, params):
        assert method in {"chain_getBlockHash", "chain_getFinalizedHead", "chain_getHeader",
                          "eth_chainId", "state_getRuntimeVersion", "state_getStorage"}
        response = session.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                          "params": params}, timeout=20)
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            raise RuntimeError(body["error"])
        return body["result"]

    try:
        encoder = ExtrinsicEncoder(url, "0x" + secrets.token_hex(32), timeout_ms=20_000)
        snapshot = encoder._load_snapshot()
        encoder._snapshot = snapshot
        substrate = snapshot.substrate
        metadata = substrate.metadata
        extensions = metadata.get_signed_extensions()
        mvcc = "CheckNonceEra" in extensions
        codec_metadata = _ImmortalMetadata(metadata) if mvcc else metadata
        genesis = substrate.get_block_hash(0)
        assert genesis == rpc("chain_getBlockHash", [0])
        subaccount = "0x" + "11" * 20
        pair = "0x" + "22" * 32
        perp = _perp_place_params(
            subaccount=subaccount, market_id=3, is_long=True, size=10**16,
            price=2000_000000, order_type=0, slippage=None, take_profit=None,
            stop_loss=None, reduce_only=False, post_only=0, cloid=None,
        )
        spot = _spot_place_params(
            subaccount=subaccount, pair=pair, is_buy=True, quote_amount=10**6,
            base_amount=10**16, order_type={"Limit": "GTC"},
            post_only="None", reduce_only=False, cloid=None,
        )
        calls = [
            ("PerpMarket", "place_order", {"params": perp}),
            ("SpotMarket", "place_order", spot),
            ("PerpMarket", "cancel_order", _perp_cancel_params(
                subaccount=subaccount, market_id=3, order_id=snapshot.chain_time_ms,
                fast_cancel=False,
            )),
            ("SpotMarket", "cancel_order", _spot_cancel_params(
                subaccount=subaccount, pair=pair, is_buy=True,
                order_id=snapshot.chain_time_ms, fast_cancel=False,
            )),
        ]

        def encode_type(type_name, value):
            obj = substrate.runtime_config.create_scale_object(type_name, metadata=metadata)
            return bytes(obj.encode(value).data)

        for offset, (module, function, params) in enumerate(calls):
            nonce = snapshot.chain_time_ms + offset
            encoded = _encode_pallet_call_sync(snapshot, module, function, params, nonce)
            decoded = substrate.runtime_config.create_scale_object(
                "Extrinsic", data=ScaleBytes(encoded.data_hex), metadata=codec_metadata,
            ).decode(check_remaining=True)
            assert decoded["nonce"] == nonce and decoded["era"] == "00"
            assert decoded["call"]["call_module"] == module
            assert decoded["call"]["call_function"] == function
            assert encoded.tx_hash == "0x" + hashlib.blake2b(
                bytes.fromhex(encoded.data_hex[2:]), digest_size=32,
            ).hexdigest()

            # Independently rebuild the signed payload using ORIGINAL metadata
            # types and order, not the compatibility alias used by the signer.
            extra_values = {"CheckNonceEra": "Immortal", "CheckEra": "00",
                            "CheckMortality": "00", "CheckNonce": nonce,
                            "ChargeTransactionPayment": 0}
            additional_values = {"CheckSpecVersion": snapshot.runtime_version,
                                 "CheckTxVersion": snapshot.transaction_version,
                                 "CheckGenesis": genesis, "CheckNonceEra": genesis,
                                 "CheckEra": genesis, "CheckMortality": genesis}
            call = substrate.compose_call(call_module=module, call_function=function, call_params=params)
            payload = bytes(call.data.data)
            payload += b"".join(encode_type(definition["extrinsic"], extra_values.get(name, []))
                                for name, definition in extensions.items())
            payload += b"".join(encode_type(definition["additional_signed"], additional_values.get(name, []))
                                for name, definition in extensions.items())
            if len(payload) > 256:
                payload = hashlib.blake2b(payload, digest_size=32).digest()
            assert snapshot.keypair.verify(payload, bytes.fromhex(decoded["signature"][2:]))
            assert substrate.metadata is metadata

        block_hash = rpc("chain_getFinalizedHead", [])
        header = rpc("chain_getHeader", [block_hash])
        class Transport:
            async def request(self, method, params):
                return await asyncio.to_thread(rpc, method, params)
        transport = Transport()
        tracker = TransactionTracker(transport, encoder)
        events = asyncio.run(tracker._fetch_block_events(
            block_hash, int(header["number"], 16), transport, parent_hash=header["parentHash"],
        ))
        assert events.events, "Finalized block has no decoded events"
        error = _native_py._chain_error_from_failed_attrs(
            {"dispatch_error": {"Module": {"index": 24, "error": "0x0d000000"}}},
            "synthetic error using live metadata", metadata=metadata,
        )
        assert error.name == metadata.get_module_error(24, 13).value["name"]
        return {"rpc": url, "chain_id": int(rpc("eth_chainId", []), 16),
                "genesis": genesis, "runtime": snapshot.runtime_version,
                "era": "CheckNonceEra" if mvcc else "legacy",
                "events_layout": "EventsMap" if uses_events_map(substrate) else "Events",
                "signed_and_verified": len(calls), "decoded_events": len(events.events),
                "lending_error_13": error.name, "submitted": 0}
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", action="append", required=True)
    args = parser.parse_args()
    for endpoint in args.rpc:
        print(json.dumps(check(endpoint), ensure_ascii=False), flush=True)
