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


def resolve_symbol_strict(base: str, available: list[dict[str, Any]]) -> str | None:
    """Fail closed when broker aliases are ambiguous.

    Exact match always wins. Otherwise exactly one suffix/prefix/substring candidate
    must exist; guessing between multiple live broker symbols is unsafe.
    """
    names = [str(item.get("name", "")) for item in available if item.get("name")]
    if base in names:
        return base
    base_upper = base.upper()
    prefix = sorted({name for name in names if name.upper().startswith(base_upper)})
    candidates = prefix or sorted({name for name in names if base_upper in name.upper()})
    if len(candidates) != 1:
        return None
    return candidates[0]
