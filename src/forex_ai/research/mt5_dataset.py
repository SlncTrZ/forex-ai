from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from forex_ai.research.replay import ReplayEvent
from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, TimeframeSnapshot

UTC = timezone.utc
_TIMEFRAME_SECONDS = {"M15": 900, "H1": 3600, "H4": 14400}


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
    history_bars: int = 200,
    min_bars: int = 50,
) -> tuple[ReplayEvent, ...]:
    if point <= 0:
        raise ValueError("point must be positive")
    if history_bars < min_bars:
        raise ValueError("history_bars must be >= min_bars")
    events: list[ReplayEvent] = []
    for row in m15_rows:
        start = datetime.fromtimestamp(int(row["time"]), UTC)
        clock = start.replace(microsecond=0) + timedelta(seconds=_TIMEFRAME_SECONDS["M15"])
        timeframes = {
            "M15": TimeframeSnapshot("M15", _closed_rows(m15_rows, clock=clock, timeframe="M15", keep=history_bars)),
            "H1": TimeframeSnapshot("H1", _closed_rows(h1_rows, clock=clock, timeframe="H1", keep=history_bars)),
            "H4": TimeframeSnapshot("H4", _closed_rows(h4_rows, clock=clock, timeframe="H4", keep=history_bars)),
        }
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
            commission_cost=0.0,
            metadata={"source": "mt5_broker_history", "historical_spread_points": spread_points},
        )
        events.append(ReplayEvent(clock, snapshot))
    return tuple(events)
