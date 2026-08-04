from __future__ import annotations

import builtins
import os
from typing import Any


def _env_true(key: str) -> bool:
    return os.environ.get(key, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _brief_value(value: Any) -> str:
    if value is None:
        return "null"

    if isinstance(value, dict):
        parts: list[str] = []
        for key in (
            "code",
            "msg",
            "fail",
            "order_id",
            "tx_hash",
            "extrinsic_hash",
            "market_id",
            "totalCount",
            "pageNo",
            "pageSize",
            "hasNext",
        ):
            if key in value:
                parts.append(f"{key}={value.get(key)}")
        items = value.get("items")
        if isinstance(items, list):
            parts.append(f"items={len(items)}")
        if parts:
            return "{" + ", ".join(parts) + "}"
        return f"dict(len={len(value)})"

    if isinstance(value, list):
        return f"list(len={len(value)})"

    if isinstance(value, tuple):
        return f"tuple(len={len(value)})"

    if hasattr(value, "__dict__"):
        attrs = getattr(value, "__dict__", {})
        if isinstance(attrs, dict) and attrs:
            keys = sorted(attrs.keys())
            show = ", ".join(keys[:6])
            suffix = "..." if len(keys) > 6 else ""
            return f"{type(value).__name__}({show}{suffix})"

    text = str(value).replace("\n", " ")
    if len(text) > 180:
        text = text[:177] + "..."
    return text


def make_print():
    """Return a print-like function with env-controlled verbosity.

    - SDK_TEST_VERBOSE=1: print full values
    - SDK_TEST_FAIL_ONLY=1: suppress normal logs from script print calls
    - default: concise values
    """

    verbose = _env_true("SDK_TEST_VERBOSE")
    fail_only = _env_true("SDK_TEST_FAIL_ONLY")

    def _print(*args: Any, **kwargs: Any) -> None:
        if fail_only:
            return
        if verbose:
            builtins.print(*args, **kwargs)
            return

        if not args:
            builtins.print(*args, **kwargs)
            return

        compact = [arg if isinstance(arg, str) else _brief_value(arg) for arg in args]
        builtins.print(*compact, **kwargs)

    return _print
