from __future__ import annotations

from typing import Any


def resolve_symbol(base: str, available: list[dict[str, Any]]) -> str | None:
    names = [str(item.get("name", "")) for item in available]
    if base in names:
        return base

    base_upper = base.upper()
    candidates = [name for name in names if name.upper().startswith(base_upper)]
    if not candidates:
        candidates = [name for name in names if base_upper in name.upper()]
    if not candidates:
        return None

    # Prefer shortest broker suffix/prefix variant, then lexical stability.
    candidates.sort(key=lambda name: (abs(len(name) - len(base)), len(name), name))
    return candidates[0]
