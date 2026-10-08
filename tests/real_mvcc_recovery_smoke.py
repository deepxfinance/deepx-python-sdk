"""Read-only live recovery checks; no key file is loaded and no tx is submitted.

Exercises the SDK's real head subscriptions and reconnect loop, archived event
metadata, and replay of an already-finalized extrinsic. This is NOT a funded-order
or 24-hour trading-bot acceptance test. Reports retain unavailable history as an
unverified check rather than pretending it was an empty/successful block.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any

from deepx_sdk import AsyncChainClient
from deepx_sdk._async_encoder import EncodedExtrinsic
from deepx_sdk._async_tracker import ExpectedEvent, _TrackedTransaction
from deepx_sdk._pending_tx import PendingTransaction, TxStatus, TxTimeouts


async def wait_until(predicate, timeout: float = 40) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.2)


async def read_block(components, block_hash: str) -> tuple[dict, Any]:
    transport = components.recovery_scan_transport
    block = await transport.request("chain_getBlock", [block_hash])
    header = block["block"]["header"]
    events = await components.tracker._fetch_block_events(
        block_hash, int(header["number"], 16), transport,
        parent_hash=header["parentHash"],
    )
    return block["block"], events


async def replay_finalized(components, block_hash: str) -> dict:
    """Attach an observed tx locally, never author_submit/author_watch it."""
    block, resolved = await read_block(components, block_hash)
    successful = [
        e for e in resolved.events
        if e.get("module_id") == "System" and e.get("event_id") == "ExtrinsicSuccess"
    ]
    if not successful:
        raise RuntimeError("finalized block contains no successful extrinsic")
    event = successful[0]
    index = int(event["extrinsic_idx"])
    encoded_hex = block["extrinsics"][index]
    tx_hash = "0x" + hashlib.blake2b(bytes.fromhex(encoded_hex[2:]), digest_size=32).hexdigest()
    pending = PendingTransaction(tx_hash=tx_hash, nonce=0, cloid=None, timeouts=TxTimeouts())
    tracker = components.tracker
    tracker._transactions[tx_hash] = _TrackedTransaction(
        encoded=EncodedExtrinsic(encoded_hex, tx_hash, 0, 0, 0.0, 0.0),
        pending=pending, expected_event=ExpectedEvent("System", "ExtrinsicSuccess"),
        result_decoder=lambda fields, _: dict(fields), submit_started_ns=time.perf_counter_ns(),
    )
    tracker._active_transactions[tx_hash] = pending
    pending.mark_submitting()
    pending.mark_submitted()
    tracker.prepare_recovery_block(block_hash)
    try:
        await tracker.resolve_block(block_hash, transport=components.recovery_scan_transport)
        await pending.wait_in_block(timeout=20)
        await tracker.finalize_ancestor(pending, block_hash)
        await pending.wait_finalized(timeout=20)
        if pending.status is not TxStatus.FINALIZED:
            raise AssertionError(f"unexpected replay status: {pending.status}")
        return {"block": block_hash, "tx_hash": tx_hash, "tx_status": pending.status.value,
                "scope": "observed finalized extrinsic, not a newly submitted order"}
    finally:
        tracker._active_transactions.pop(tx_hash, None)
        tracker._transactions.pop(tx_hash, None)


async def check(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"endpoint": args.ws, "submitted": 0, "checks": {}, "samples": []}
    loop = asyncio.get_running_loop()
    exceptions: list[str] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: exceptions.append(str(context.get("exception") or context.get("message"))))
    client = AsyncChainClient(substrate_ws=args.ws, private_key="0x" + secrets.token_hex(32),
                              subaccount="0x" + "11" * 20, rpc_request_timeout_s=15)
    try:
        async with asyncio.timeout(90):
            await client.connect()
        components = client._components
        transports = [components.transport, components.recovery_transport, components.recovery_scan_transport]
        genesis = [await transport.request("chain_getBlockHash", [0]) for transport in transports]
        if any(value != args.genesis for value in genesis):
            raise AssertionError("configured/observed chain identities differ")
        report["genesis"] = genesis[0]
        report["runtime"] = components.encoder.snapshot.runtime_version
        report["checks"]["identity"] = "PASS"
        finalized = await components.recovery_scan_transport.request("chain_getFinalizedHead", [])
        block, events = await read_block(components, finalized)
        report["checks"]["finalized_events"] = {"status": "PASS", "events": len(events.events),
                                                      "height": int(block["header"]["number"], 16)}
        try:
            report["checks"]["finalized_replay"] = {"status": "PASS", **await replay_finalized(components, finalized)}
        except Exception as exc:
            report["checks"]["finalized_replay"] = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
        for height in args.historical_block:
            try:
                block_hash = await components.recovery_scan_transport.request("chain_getBlockHash", [height])
                if not block_hash:
                    raise RuntimeError("historical block hash unavailable")
                _, events = await read_block(components, block_hash)
                report["checks"][f"history_{height}"] = {"status": "PASS", "events": len(events.events)}
            except Exception as exc:
                report["checks"][f"history_{height}"] = {"status": "UNVERIFIED", "error": f"{type(exc).__name__}: {exc}"}
        report["checks"]["reconnects"] = []
        for number in range(args.reconnects):
            old_head = client.health_snapshot()["recovery"]["finalized_head"]
            counts = [transport.connection_count for transport in transports]
            before = time.monotonic()
            for transport in transports:
                await transport.force_reconnect("read-only acceptance fault injection")
            await wait_until(lambda: all(t.connection_count > count for t, count in zip(transports, counts)))
            await wait_until(lambda: (client.health_snapshot()["recovery"]["finalized_head"] or 0) > old_head)
            await wait_until(lambda: client.health_snapshot()["recovery"]["consecutive_failures"] == 0)
            observed = [await transport.request("chain_getBlockHash", [0]) for transport in transports]
            if any(value != args.genesis for value in observed):
                raise AssertionError("chain identity changed after reconnect")
            report["checks"]["reconnects"].append({"status": "PASS", "round": number + 1,
                "seconds": round(time.monotonic() - before, 3), "health": client.health_snapshot()})
        monitor_start = time.monotonic()
        first_head = client.health_snapshot()["recovery"]["finalized_head"]
        while time.monotonic() - monitor_start < args.seconds:
            report["samples"].append(client.health_snapshot())
            args.report.write_text(json.dumps(report, indent=2, default=str))
            await asyncio.sleep(min(5, args.seconds - (time.monotonic() - monitor_start)))
        final = client.health_snapshot()
        report["samples"].append(final)
        if final["recovery"]["finalized_head"] <= first_head:
            raise AssertionError("finalized head did not advance during observation")
        if final["recovery"]["consecutive_failures"] or final["recovery"]["last_error"]:
            raise AssertionError(f"recovery did not heal: {final['recovery']['last_error']}")
        if final["recovery"]["finalized_scan_head"] < final["recovery"]["finalized_head"] - 5:
            raise AssertionError("recovery scan has fallen behind finalized head")
        report["checks"]["observation"] = {"status": "PASS", "seconds": round(time.monotonic() - monitor_start, 2),
                                           "transient_errors": sum(bool(s["recovery"]["last_error"]) for s in report["samples"])}
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        await client.close()
        loop.set_exception_handler(previous)
        report["unhandled_exceptions"] = exceptions
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["status"] = "PASS" if not report.get("error") and not exceptions and all(
            not isinstance(v, dict) or v.get("status") == "PASS" for v in report["checks"].values()
        ) else "INCOMPLETE"
        args.report.write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ws", required=True)
    parser.add_argument("--genesis", required=True)
    parser.add_argument("--historical-block", type=int, action="append", default=[])
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--reconnects", type=int, default=2)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.seconds <= 0 or args.reconnects < 1:
        parser.error("seconds must be positive and reconnects at least one")
    report = asyncio.run(check(args))
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, indent=2, default=str))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
