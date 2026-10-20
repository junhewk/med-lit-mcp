"""Size of tool results as clients receive them.

Clients cap one tool result: pi cuts MCP text over 20 KiB (20,480 bytes) and saves the rest to a file,
Hermes moves a result over 50,000 characters to a file. What they measure is the text block FastMCP
sends, which is the returned dict serialized as indented JSON, in UTF-8. Batches are therefore sized
by that encoded text, the whole response included (criteria, instructions, metadata), not by the
characters of the abstracts alone.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

import pydantic_core

# Under pi's 20,480-byte limit with room for the client's own wrapping. MED_LIT_RESPONSE_BYTES
# changes it, for clients with a smaller or larger limit.
DEFAULT_RESPONSE_BYTES = 18 * 1024
MIN_RESPONSE_BYTES = 4 * 1024

T = TypeVar("T")


def response_bytes() -> int:
    value = os.environ.get("MED_LIT_RESPONSE_BYTES")
    if not value:
        return DEFAULT_RESPONSE_BYTES
    try:
        return max(MIN_RESPONSE_BYTES, int(value))
    except ValueError:
        return DEFAULT_RESPONSE_BYTES


def encoded_size(result: Any) -> int:
    """Bytes of the text FastMCP sends for `result` (mcp's _convert_to_content: indented JSON)."""
    if isinstance(result, str):
        return len(result.encode("utf-8"))
    return len(pydantic_core.to_json(result, fallback=str, indent=2))


def fill(
    response: dict[str, Any],
    key: str,
    candidates: Iterable[T],
    build: Callable[[T], dict[str, Any]],
    *,
    shrink: Callable[[dict[str, Any], int], dict[str, Any] | None] | None = None,
    budget: int | None = None,
) -> tuple[dict[str, Any], int]:
    """Add built items to response[key] while the encoded response stays within the budget.

    Returns the response and how many candidates were taken. When even the first item does not fit,
    `shrink(item, room)` may return a smaller version of it (for example a cut abstract); without
    one, or if it still does not fit, the first item is sent anyway so the caller never gets an
    empty batch while work remains. The response then exceeds the budget, and says so in
    `oversized`.
    """
    limit = budget or response_bytes()
    response = response | {key: []}
    taken = 0
    for candidate in candidates:
        item = build(candidate)
        trial = response | {key: [*response[key], item]}
        if encoded_size(trial) <= limit:
            response = trial
            taken += 1
            continue
        if taken == 0:
            smaller = shrink(item, limit) if shrink else None
            if smaller is not None and encoded_size(response | {key: [smaller]}) <= limit:
                response = response | {key: [smaller]}
            else:
                response = response | {key: [item], "oversized": True}
            taken = 1
        break
    return response, taken


def cut_to_fit(response: dict[str, Any], key: str, item: dict[str, Any], field: str, limit: int) -> dict[str, Any] | None:
    """The largest prefix of item[field] (whole words where possible) that keeps the response within limit."""
    text = str(item.get(field) or "")
    low, high, best = 0, len(text), None
    while low <= high:
        middle = (low + high) // 2
        cut = text[:middle]
        if middle < len(text) and " " in cut:
            cut = cut[: cut.rfind(" ")]
        candidate = item | {field: cut}
        if encoded_size(response | {key: [candidate]}) <= limit:
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return best
