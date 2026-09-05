from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backtest.fetch_scalping_dataset import TIMEFRAME_SECONDS, build_scalping_replay_events
from forex_ai.research.dataset import freeze_replay_dataset, load_frozen_replay_dataset
from forex_ai.research.scalping_dataset import Partition

UTC = timezone.utc
RANGE_START = datetime(2026, 7, 13, tzinfo=UTC)
RANGE_END = RANGE_START + timedelta(days=1)


def _rows(start: datetime, step_seconds: int, count: int, *, base: float, spread: int = 2):
    rows = []
    price = base
    for index in range(count):
        direction = 1 if (index // 7) % 2 == 0 else -1
        delta = (0.0001 if base < 10 else 1.0) * direction
        close = price + delta
        padding = abs(delta) * 1.5
        rows.append({
            "time": int((start + timedelta(seconds=step_seconds * index)).timestamp()),
            "open": price,
            "high": max(price, close) + padding,
            "low": min(price, close) - padding,
            "close": close,
            "tick_volume": 100 + index,
            "spread": spread,
        })
        price = close
    return rows


def _dataset_rows():
    return {
        "M5": _rows(RANGE_START - timedelta(seconds=300 * 200), 300, 500, base=1.10),
        "M15": _rows(RANGE_START - timedelta(seconds=900 * 200), 900, 320, base=1.10),
        "H1": _rows(RANGE_START - timedelta(hours=200), 3600, 260, base=1.10),
        "H4": _rows(RANGE_START - timedelta(hours=4 * 140), 14400, 170, base=1.10),
        "D1": _rows(RANGE_START - timedelta(days=140), 86400, 150, base=1.10),
    }


def test_scalping_replay_uses_m5_m15_h1_only_and_h4_d1_as_context(monkeypatch, tmp_path):
    context_path = tmp_path / "market-context.yaml"
    context_path.write_text(open("config/market-context.yaml", encoding="utf-8").read(), encoding="utf-8")
    monkeypatch.setenv("FOREX_AI_MARKET_CONTEXT_CONFIG", str(context_path))

    events = build_scalping_replay_events(
        symbol="EURUSDc",
        point=0.00001,
        rows_by_tf=_dataset_rows(),
        history_bars=120,
        range_start=RANGE_START,
        range_end=RANGE_END,
    )
    assert events
    event = events[0]
    assert set(event.snapshot.timeframes) == {"M5", "M15", "H1"}
    for name, tf in event.snapshot.timeframes.items():
        assert tf.closed_bars
        assert tf.closed_bars[-1].time_utc + timedelta(seconds=TIMEFRAME_SECONDS[name]) <= event.clock_utc

    context = event.snapshot.context["higher_timeframe_structure"]
    assert context["context_only"] is True
    assert context["status"] == "READY"
    assert set(context["source_timeframes"]) == {"H4", "D1"}
    for level in [*context["supports"], *context["resistances"]]:
        assert datetime.fromisoformat(level["last_pivot_utc"]) <= event.clock_utc


def test_partition_strict_start_prevents_prepartition_bar_leakage():
    partition = Partition("IS", RANGE_START, RANGE_END)
    assert not partition.contains(RANGE_START)
    assert partition.contains(RANGE_START + timedelta(minutes=5))
    assert not partition.contains(RANGE_END)


def test_scalping_context_survives_frozen_dataset_round_trip(monkeypatch, tmp_path):
    context_path = tmp_path / "market-context.yaml"
    context_path.write_text(open("config/market-context.yaml", encoding="utf-8").read(), encoding="utf-8")
    monkeypatch.setenv("FOREX_AI_MARKET_CONTEXT_CONFIG", str(context_path))
    events = build_scalping_replay_events(
        symbol="EURUSDc",
        point=0.00001,
        rows_by_tf=_dataset_rows(),
        history_bars=120,
        range_start=RANGE_START,
        range_end=RANGE_START + timedelta(hours=2),
    )
    path = tmp_path / "replay.jsonl"
    freeze_replay_dataset(events, data_path=path, source_id="scalping-test", created_at_utc=RANGE_END)
    loaded = load_frozen_replay_dataset(path)
    assert loaded.manifest.record_count == len(events)
    assert loaded.events[0].snapshot.context == events[0].snapshot.context
    assert loaded.events[0].snapshot.decision_fingerprint == events[0].snapshot.decision_fingerprint
