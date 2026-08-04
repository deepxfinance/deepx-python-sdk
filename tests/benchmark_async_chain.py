"""Opt-in real-chain benchmark for the async direct-Substrate path."""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from deepx_sdk import AsyncChainClient, PendingTransaction


_DEFAULT_SLOT_DURATION_MS = 70
_MAX_ROLLING_WINDOW = 48
_SDK_OVERHEAD_P95_LIMIT_MS = 20.0
_IN_BLOCK_DISPATCH_P95_LIMIT_MS = 10.0


@dataclass(frozen=True)
class BenchmarkConfig:
    substrate_ws: str
    private_key: str
    subaccount: str
    market_id: int
    side: str
    size: int
    price: int
    order_type: str
    slippage: int | None
    cloid_start: int | None
    tx_count: int
    rolling_window: int
    slot_duration_ms: int
    slot_source: str

    @classmethod
    def from_environment(cls, *, require_credentials: bool) -> BenchmarkConfig:
        slot_from_env = "SLOT_DURATION_MS" in os.environ
        config = cls(
            substrate_ws=os.environ.get("SUBSTRATE_WS", ""),
            private_key=os.environ.get("PRIVATE_KEY", ""),
            subaccount=os.environ.get("SUBACCOUNT", ""),
            market_id=_integer_environment("MARKET_ID", 0),
            side=os.environ.get("ORDER_SIDE", "buy"),
            size=_integer_environment("ORDER_SIZE", 1),
            price=_integer_environment("ORDER_PRICE", 1),
            order_type=os.environ.get("ORDER_TYPE", "limit"),
            slippage=_optional_integer_environment("ORDER_SLIPPAGE"),
            cloid_start=_optional_integer_environment("ORDER_CLOID_START"),
            tx_count=_integer_environment("BENCH_TX_COUNT", 100),
            rolling_window=_integer_environment(
                "BENCH_ROLLING_WINDOW",
                _MAX_ROLLING_WINDOW,
            ),
            slot_duration_ms=_integer_environment(
                "SLOT_DURATION_MS",
                _DEFAULT_SLOT_DURATION_MS,
            ),
            slot_source=(
                "SLOT_DURATION_MS environment"
                if slot_from_env
                else "design default from chain source"
            ),
        )
        config.validate(require_credentials=require_credentials)
        return config

    def validate(self, *, require_credentials: bool) -> None:
        if self.tx_count <= 0:
            raise ValueError("BENCH_TX_COUNT must be positive")
        if not 1 <= self.rolling_window <= _MAX_ROLLING_WINDOW:
            raise ValueError("BENCH_ROLLING_WINDOW must be between 1 and 48")
        if self.slot_duration_ms <= 0:
            raise ValueError("SLOT_DURATION_MS must be positive")
        if self.size <= 0 or self.price < 0:
            raise ValueError("ORDER_SIZE must be positive and ORDER_PRICE non-negative")
        if require_credentials:
            missing = [
                name
                for name, value in (
                    ("SUBSTRATE_WS", self.substrate_ws),
                    ("PRIVATE_KEY", self.private_key),
                    ("SUBACCOUNT", self.subaccount),
                )
                if not value
            ]
            if "MARKET_ID" not in os.environ:
                missing.append("MARKET_ID")
            if missing:
                raise ValueError(
                    "real-chain benchmark requires: " + ", ".join(missing)
                )


@dataclass
class Samples:
    encode_ms: list[float]
    sign_ms: list[float]
    rpc_submit_ms: list[float]
    inclusion_ms: list[float]
    event_decode_ms: list[float]
    in_block_dispatch_ms: list[float]
    total_ms: list[float]

    @classmethod
    def empty(cls) -> Samples:
        return cls([], [], [], [], [], [], [])

    def append(self, pending: PendingTransaction[Any], total_ms: float) -> None:
        timings = pending.timings
        for field_name in (
            "encode_ms",
            "sign_ms",
            "rpc_submit_ms",
            "inclusion_ms",
            "event_decode_ms",
            "in_block_dispatch_ms",
        ):
            value = getattr(timings, field_name, None)
            if value is None:
                raise RuntimeError(
                    f"benchmark timing {field_name} was not recorded"
                )
            getattr(self, field_name).append(float(value))
        self.total_ms.append(total_ms)


def _integer_environment(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _optional_integer_environment(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw in {None, ""}:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _safe_endpoint(url: str) -> str:
    if not url:
        return "<not configured>"
    try:
        parsed = urlsplit(url)
        if not parsed.scheme or parsed.hostname is None:
            return "<configured endpoint>"
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit((parsed.scheme, host + port, parsed.path, "", ""))
    except (TypeError, ValueError):
        return "<configured endpoint>"


def _print_configuration(config: BenchmarkConfig, *, real_chain: bool) -> None:
    print(f"real_chain_executed: {'yes' if real_chain else 'no'}")
    print(f"endpoint: {_safe_endpoint(config.substrate_ws)}")
    print(f"tx_count: {config.tx_count}")
    print(f"rolling_window: {config.rolling_window}")
    print(f"configured_slot_duration_ms: {config.slot_duration_ms}")
    print(f"slot_configuration_source: {config.slot_source}")
    print("observed_finality: not measured")
    print("configured slot duration is not an observed finality measurement")


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _print_distribution(name: str, values: list[float]) -> None:
    print(
        f"{name}_ms: p50={_percentile(values, 0.50):.3f} "
        f"p95={_percentile(values, 0.95):.3f} max={max(values):.3f}"
    )


async def _run_real_chain(config: BenchmarkConfig) -> int:
    samples = Samples.empty()
    node_statuses: Counter[str] = Counter()
    client = AsyncChainClient(
        substrate_ws=config.substrate_ws,
        private_key=config.private_key,
        subaccount=config.subaccount,
        max_pool_transactions_per_account=_MAX_ROLLING_WINDOW,
    )
    try:
        # connect() warms both the persistent transport and cached encoder.
        # It does not compose, sign, or submit an order.
        await client.connect()
        _print_configuration(config, real_chain=True)

        active: dict[
            asyncio.Task[Any],
            tuple[PendingTransaction[Any], int],
        ] = {}
        next_index = 0
        while next_index < config.tx_count or active:
            while (
                next_index < config.tx_count
                and len(active) < config.rolling_window
            ):
                started_ns = time.perf_counter_ns()
                cloid = (
                    None
                    if config.cloid_start is None
                    else config.cloid_start + next_index
                )
                pending = await client.perp_market.place_order(
                    market_id=config.market_id,
                    side=config.side,
                    size=config.size,
                    price=config.price,
                    order_type=config.order_type,
                    slippage=config.slippage,
                    cloid=cloid,
                )
                pending.add_status_callback(
                    lambda update: node_statuses.update(
                        [update.node_status or update.status.value]
                    )
                )
                node_statuses.update(
                    [pending.node_status or pending.status.value]
                )
                wait_task = asyncio.create_task(pending.wait_in_block())
                active[wait_task] = (pending, started_ns)
                next_index += 1

            done, _waiting = await asyncio.wait(
                active,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                pending, started_ns = active.pop(task)
                task.result()
                total_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
                samples.append(pending, total_ms)

        for field_name in (
            "encode_ms",
            "sign_ms",
            "rpc_submit_ms",
            "inclusion_ms",
            "event_decode_ms",
            "in_block_dispatch_ms",
            "total_ms",
        ):
            _print_distribution(field_name, getattr(samples, field_name))

        sdk_overhead = [
            encode + sign
            for encode, sign in zip(
                samples.encode_ms,
                samples.sign_ms,
                strict=True,
            )
        ]
        _print_distribution("sdk_local_encode_sign_overhead", sdk_overhead)
        print("rpc_submit_ms includes node/network time; it is not labeled SDK overhead")

        components = client._components
        connection_count = (
            getattr(components.transport, "connection_count", "unavailable")
            if components is not None
            else "unavailable"
        )
        print(f"connection_count: {connection_count}")
        print(f"peak_tracked_count: {client.peak_tracked_transactions}")
        print(f"peak_pool_resident_count: {client.peak_pool_transactions}")
        statuses = ", ".join(
            f"{status}={count}" for status, count in sorted(node_statuses.items())
        )
        print(f"node_statuses: {statuses or '<none>'}")

        overhead_p95 = _percentile(sdk_overhead, 0.95)
        dispatch_p95 = _percentile(samples.in_block_dispatch_ms, 0.95)
        if overhead_p95 > _SDK_OVERHEAD_P95_LIMIT_MS:
            print(
                "threshold_failure: sdk local encode+sign overhead "
                f"p95 {overhead_p95:.3f}ms exceeds "
                f"{_SDK_OVERHEAD_P95_LIMIT_MS:.3f}ms",
                file=sys.stderr,
            )
            return 1
        if dispatch_p95 > _IN_BLOCK_DISPATCH_P95_LIMIT_MS:
            print(
                "threshold_failure: in-block dispatch "
                f"p95 {dispatch_p95:.3f}ms exceeds "
                f"{_IN_BLOCK_DISPATCH_P95_LIMIT_MS:.3f}ms",
                file=sys.stderr,
            )
            return 1
        return 0
    finally:
        await client.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark the real async Substrate transaction path. Without "
            "credentials, use --dry-run to validate configuration only."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate environment/defaults without connecting or submitting",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = BenchmarkConfig.from_environment(
            require_credentials=not args.dry_run
        )
    except ValueError as exc:
        print(f"configuration_error: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        _print_configuration(config, real_chain=False)
        return 0
    try:
        return asyncio.run(_run_real_chain(config))
    except BaseException as exc:
        print(f"benchmark_error: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
