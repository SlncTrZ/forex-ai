from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from .models import MacroSnapshot

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    value: T
    expires_at_utc: datetime


class MacroSnapshotCache:
    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry[MacroSnapshot]] = {}

    def get(self, key: str, now_utc: datetime) -> MacroSnapshot | None:
        entry = self._entries.get(key)
        if entry is None or now_utc >= entry.expires_at_utc:
            self._entries.pop(key, None)
            return None
        return entry.value

    def put(self, snapshot: MacroSnapshot, expires_at_utc: datetime) -> None:
        self._entries[snapshot.cache_key] = CacheEntry(snapshot, expires_at_utc)

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    def invalidate_all(self) -> None:
        self._entries.clear()

    def invalidate_for_event(self, affected_assets: tuple[str, ...]) -> int:
        # Source-neutral cache does not infer symbols from provider state; explicit
        # affected_assets metadata may be supplied in snapshot values.
        removed = 0
        for key, entry in list(self._entries.items()):
            assets = tuple(entry.value.values.get("affected_assets", ()))
            if not affected_assets or set(assets).intersection(affected_assets):
                self._entries.pop(key, None)
                removed += 1
        return removed
