from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from forex_ai.research.replay import ReplayEvent
from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, TimeframeSnapshot

UTC = timezone.utc
_TIMEFRAME_SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400}


def _candle(row: Mapping[str, object]) -> Candle:
    return Candle(
        datetime.fromtimestamp(int(row["time"]), UTC),
        float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]),
        float(row.get("tick_volume") or row.get("real_volume") or 0.0),
    )


def _closed_rows(rows: Sequence[Mapping[str, object]], *, clock: datetime, timeframe: str, keep: int) -> tuple[Candle, ...]:
    seconds = _TIMEFRAME_SECONDS[timeframe]
    starts = [int(row["time"]) for row in rows]
    # A bar is closed only once its full timeframe lies at or before replay clock.
    cutoff_start = int(clock.timestamp()) - seconds
    end = bisect_right(starts, cutoff_start)
    begin = max(0, end - keep)
    return tuple(_candle(row) for row in rows[begin:end])


def build_replay_events_from_mt5_bars(
    *,
    symbol: str,
    point: float,
    m15_rows: Sequence[Mapping[str, object]],
    h1_rows: Sequence[Mapping[str, object]],
    h4_rows: Sequence[Mapping[str, object]],
    m5_rows: Sequence[Mapping[str, object]] | None = None,
    history_bars: int = 200,
    min_bars: int = 50,
) -> tuple[ReplayEvent, ...]:
    if point <= 0:
        raise ValueError("point must be positive")
    if history_bars < min_bars:
        raise ValueError("history_bars must be >= min_bars")

    m15_starts = [int(row["time"]) for row in m15_rows]
    h1_starts = [int(row["time"]) for row in h1_rows]
    h4_starts = [int(row["time"]) for row in h4_rows]

    m15_candles = tuple(_candle(row) for row in m15_rows)
    h1_candles = tuple(_candle(row) for row in h1_rows)
    h4_candles = tuple(_candle(row) for row in h4_rows)

    m5_starts = [int(row["time"]) for row in m5_rows] if m5_rows else []
    m5_candles = tuple(_candle(row) for row in m5_rows) if m5_rows else ()

    def _get_slice(candles: tuple[Candle, ...], starts: list[int], clock: datetime, timeframe: str) -> tuple[Candle, ...]:
        seconds = _TIMEFRAME_SECONDS[timeframe]
        cutoff_start = int(clock.timestamp()) - seconds
        end = bisect_right(starts, cutoff_start)
        begin = max(0, end - history_bars)
        return candles[begin:end]

    events: list[ReplayEvent] = []
    for row in m15_rows:
        start = datetime.fromtimestamp(int(row["time"]), UTC)
        clock = start.replace(microsecond=0) + timedelta(seconds=_TIMEFRAME_SECONDS["M15"])
        timeframes = {
            "M15": TimeframeSnapshot("M15", _get_slice(m15_candles, m15_starts, clock, "M15")),
            "H1": TimeframeSnapshot("H1", _get_slice(h1_candles, h1_starts, clock, "H1")),
            "H4": TimeframeSnapshot("H4", _get_slice(h4_candles, h4_starts, clock, "H4")),
        }
        if m5_rows:
            timeframes["M5"] = TimeframeSnapshot("M5", _get_slice(m5_candles, m5_starts, clock, "M5"))
        if min(len(tf.closed_bars) for tf in timeframes.values()) < min_bars:
            continue
        close = float(row["close"])
        spread_points = max(float(row.get("spread") or 0.0), 0.0)
        spread = spread_points * point
        bid = close
        ask = close + spread
        snapshot = MarketSnapshot(
            symbol=symbol,
            captured_at_utc=clock,
            market_time_msc=int(clock.timestamp() * 1000),
            bid=bid,
            ask=ask,
            timeframes=timeframes,
            spread_cost=spread,
            metadata={"source": "mt5_broker_history"},
        )
        events.append(ReplayEvent(clock_utc=clock, snapshot=snapshot))
    return tuple(events)
