from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from forex_ai.market.context_config import load_market_context_snapshot
from forex_ai.market.structure import build_higher_timeframe_structure
from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, TimeframeSnapshot

UTC = timezone.utc
NOW = datetime(2026, 9, 5, 4, 0, tzinfo=UTC)


def _bars(*, timeframe_minutes: int, count: int, base: float, step: float) -> tuple[Candle, ...]:
    rows = []
    price = base
    for index in range(count):
        direction = 1 if (index // 6) % 2 == 0 else -1
        close = price + direction * step
        high = max(price, close) + step * 1.5
        low = min(price, close) - step * 1.5
        rows.append(
            Candle(
                NOW - timedelta(minutes=timeframe_minutes * (count - index)),
                price,
                high,
                low,
                close,
                100 + index,
            )
        )
        price = close
    return tuple(rows)


def test_h4_d1_structure_returns_context_only_support_and_resistance():
    snapshot = load_market_context_snapshot(Path("config/market-context.yaml"), allow_last_good=False)
    h4 = TimeframeSnapshot("H4", _bars(timeframe_minutes=240, count=120, base=100.0, step=1.0))
    d1 = TimeframeSnapshot("D1", _bars(timeframe_minutes=1440, count=120, base=95.0, step=2.0))
    structure = build_higher_timeframe_structure(
        timeframes={"H4": h4, "D1": d1},
        reference_price=100.0,
        captured_at_utc=NOW,
        config=snapshot.higher_timeframe_structure,
        config_fingerprint=snapshot.fingerprint,
    )
    payload = structure.to_dict()
    assert payload["context_only"] is True
    assert payload["status"] == "READY"
    assert set(payload["source_timeframes"]) == {"H4", "D1"}
    assert payload["supports"]
    assert payload["resistances"]
    assert all(level["center"] <= 100.0 for level in payload["supports"])
    assert all(level["center"] > 100.0 for level in payload["resistances"])


def test_market_context_hot_reload_and_last_known_good(tmp_path, monkeypatch):
    active = tmp_path / "market-context.yaml"
    raw = yaml.safe_load(Path("config/market-context.yaml").read_text(encoding="utf-8"))
    active.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("FOREX_AI_MARKET_CONTEXT_CONFIG", str(active))

    before = load_market_context_snapshot()
    raw["higher_timeframe_structure"]["refresh_seconds"] = 900
    active.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    after = load_market_context_snapshot()
    assert after.fingerprint != before.fingerprint
    assert after.higher_timeframe_structure.refresh_seconds == 900

    raw["higher_timeframe_structure"]["timeframes"]["H4"]["pivot_left"] = 0
    active.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    fallback = load_market_context_snapshot()
    assert fallback.loaded_from_last_good
    assert fallback.fingerprint == after.fingerprint
    assert fallback.higher_timeframe_structure.refresh_seconds == 900


def test_context_does_not_change_strategy_decision_fingerprint():
    bars = _bars(timeframe_minutes=15, count=60, base=100.0, step=0.2)
    tf = TimeframeSnapshot("M15", bars)
    base_kwargs = dict(
        symbol="EURUSDc",
        captured_at_utc=NOW,
        market_time_msc=int(NOW.timestamp() * 1000),
        bid=1.1000,
        ask=1.1002,
        timeframes={"M15": tf},
    )
    a = MarketSnapshot(**base_kwargs, context={"higher_timeframe_structure": {"status": "READY", "supports": [1]}})
    b = MarketSnapshot(**base_kwargs, context={"higher_timeframe_structure": {"status": "READY", "supports": [2]}})
    assert a.decision_fingerprint == b.decision_fingerprint
    assert a.fingerprint != b.fingerprint
