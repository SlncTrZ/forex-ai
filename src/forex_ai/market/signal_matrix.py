"""Read-only, closed-candle indicator signal registry for the operator matrix.

Diagnostic telemetry only: never consumed by candidate admission, risk, or order routing.
Column IDs and strategy bindings are extensible; all calculations reuse market.indicators.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from forex_ai.integration.adapters import timeframe_snapshot
from forex_ai.journal.db import log_audit_event, session
from forex_ai.market.indicators import (
    atr, bollinger_squeeze, dmi_adx, ema, rsi, supertrend,
)
from forex_ai.strategy.config import StrategyConfigSnapshot
from forex_ai.strategy.v1.contracts import fingerprint

UTC = timezone.utc
POLICY = "signal-matrix-v1"
EVENT = "SIGNAL_MATRIX_OBSERVATION"
COLUMNS = (
    ("RSI", "RSI(14) / 50"),
    ("EMA", "EMA20 / EMA50"),
    ("ADX", "ADX(14) + DI / 20"),
    ("ST", "Supertrend(10,3)"),
    ("VOL", "Tick volume / SMA20 ≥ 1.15"),
    ("SQZ", "BB/KC squeeze release"),
    ("STRUCT", "20-bar high/low breakout"),
    ("TRG", "Candle body / ATR14 ≥ 0.5"),
)
# CORE is a diagnostic mapping, NOT an exact representation of each strategy's
# admission rules. WATCH remains visible without claiming it gates execution.
ROWS = (
    ("btc_squeeze_breakout_m15", "BTC Squeeze", "M15", ("EMA", "VOL", "SQZ", "TRG")),
    ("supertrend_stochastic_m5", "ST + Stoch", "M5", ("EMA", "ST", "TRG")),
    ("supertrend_volume_m5", "ST + Volume", "M5", ("EMA", "ST", "VOL", "TRG")),
    ("smc_pro_fvg_v2", "SMC Pro FVG", "M5", ("STRUCT", "TRG")),
    ("keylevel_breakout_v2", "Keylevel", "M5", ("EMA", "STRUCT", "TRG")),
    ("mtf_trend_continuation_v2", "MTF Trend", "M5", ("EMA", "ADX", "VOL", "TRG")),
    ("pro_volatility_expansion_v2", "Vol Expansion", "M5", ("VOL", "SQZ", "TRG")),
    ("ichimoku_mtf_v2", "Ichimoku MTF", "M15", ("EMA", "TRG")),
)
DURATION = {"M5": timedelta(minutes=5), "M15": timedelta(minutes=15)}
MAX_BARS = 310
FRESH_WINDOW = {"M5": timedelta(minutes=12), "M15": timedelta(minutes=30)}


def _closed(rows: list[dict], tf: str, now: datetime):
    ordered = sorted(rows, key=lambda row: int(row["time"]))
    result = timeframe_snapshot(tf, ordered)
    bars = result.closed_bars
    if result.current_bar and result.current_bar.time_utc + DURATION[tf] <= now:
        bars = (*bars, result.current_bar)
    return bars


def _cell(state: str = "UNKNOWN", *, value: float | None = None,
          rule: str = "", closed_utc: str | None = None, core: bool = False) -> dict:
    assert state in ("BULL", "BEAR", "NEUTRAL", "UNKNOWN", "STALE", "NA")
    return {"state": state, "met": state in ("BULL", "BEAR"),
            "value": round(value, 5) if value is not None and math.isfinite(value) else None,
            "rule": rule, "closed_utc": closed_utc, "core": core}


def _classify(value: float | None, *, positive: bool, negative: bool,
              rule: str, closed_utc: str) -> dict:
    if value is None or not math.isfinite(value):
        return _cell(rule=rule, closed_utc=closed_utc)
    return _cell("BULL" if positive else "BEAR" if negative else "NEUTRAL",
                 value=value, rule=rule, closed_utc=closed_utc)


def _metrics(bars: tuple, tf: str, now: datetime) -> dict[str, dict]:
    missing = {k: _cell(rule=label) for k, label in COLUMNS}
    if len(bars) < 60:
        return missing
    last = bars[-1]
    closed_utc = (last.time_utc + DURATION[tf]).isoformat()
    if last.time_utc + DURATION[tf] > now or now - last.time_utc - DURATION[tf] > FRESH_WINDOW[tf]:
        return {k: _cell("STALE", rule=label, closed_utc=closed_utc) for k, label in COLUMNS}

    closes = [x.close for x in bars]
    out = dict(missing)
    r = rsi(closes, 14)
    out["RSI"] = _classify(r, positive=r is not None and r > 50,
                            negative=r is not None and r < 50,
                            rule="RSI(14) > 50 bullish; < 50 bearish", closed_utc=closed_utc)
    fast, slow = ema(closes, 20), ema(closes, 50)
    out["EMA"] = _classify(fast - slow, positive=fast > slow, negative=fast < slow,
                            rule="EMA20 > EMA50 bullish; < bearish", closed_utc=closed_utc)
    adx = dmi_adx(bars, 14)
    out["ADX"] = _classify(adx.adx if adx else None,
                            positive=bool(adx and adx.adx >= 20 and adx.plus_di > adx.minus_di),
                            negative=bool(adx and adx.adx >= 20 and adx.minus_di > adx.plus_di),
                            rule="ADX ≥ 20, +DI versus -DI", closed_utc=closed_utc)
    st = supertrend(bars, 10, 3.0)
    out["ST"] = _classify(st.value if st else None,
                           positive=bool(st and st.trend == "UP"),
                           negative=bool(st and st.trend == "DOWN"),
                           rule="Supertrend(10,3) direction", closed_utc=closed_utc)
    baseline = sum(x.volume for x in bars[-21:-1]) / 20
    ratio = last.volume / baseline if baseline > 0 else None
    out["VOL"] = _classify(ratio,
                            positive=ratio is not None and ratio >= 1.15 and last.close > last.open,
                            negative=ratio is not None and ratio >= 1.15 and last.close < last.open,
                            rule="Tick volume ≥ 1.15 × prev20 SMA + candle direction",
                            closed_utc=closed_utc)
    sqz = bollinger_squeeze(bars, 20, 2.0, 1.5)
    # A squeeze ON is compression, not a release/trade trigger.
    previous_sqz = bollinger_squeeze(bars[:-1], 20, 2.0, 1.5)
    release = bool(sqz and previous_sqz and not sqz.squeeze_on and previous_sqz.squeeze_on)
    out["SQZ"] = _classify(sqz.bandwidth if sqz else None,
                            positive=bool(release and sqz.momentum_direction == "UP"),
                            negative=bool(release and sqz.momentum_direction == "DOWN"),
                            rule="Squeeze ON→OFF and 5-bar momentum",
                            closed_utc=closed_utc)
    ceiling = max(x.high for x in bars[-21:-1])
    floor = min(x.low for x in bars[-21:-1])
    out["STRUCT"] = _classify(last.close,
                              positive=last.close > ceiling,
                              negative=last.close < floor,
                              rule="Close beyond previous 20-bar high/low", closed_utc=closed_utc)
    a = atr(bars, 14)
    body_ratio = abs(last.close - last.open) / a if a > 0 else None
    out["TRG"] = _classify(body_ratio,
                           positive=body_ratio is not None and body_ratio >= .5 and last.close > last.open,
                           negative=body_ratio is not None and body_ratio >= .5 and last.close < last.open,
                           rule="Body ≥ 0.5 × ATR14 + candle direction", closed_utc=closed_utc)
    return out


def build_matrix(symbol: str, *, bars_by_tf: Mapping[str, list[dict]],
                 snapshot: StrategyConfigSnapshot, market_state: str,
                 now_utc: datetime) -> dict[str, Any]:
    """Each symbol has 8 strategy rows x 8 diagnostic indicator columns."""
    now = now_utc.astimezone(UTC)
    metrics = {}
    for tf in ("M5", "M15"):
        try:
            metrics[tf] = _metrics(_closed(bars_by_tf.get(tf, []), tf, now), tf, now)
        except (KeyError, TypeError, ValueError, OverflowError):
            metrics[tf] = {k: _cell(rule=label) for k, label in COLUMNS}
    active = market_state == "ACTIVE"
    rows = []
    for strategy_id, label, tf, core in ROWS:
        eligible = (symbol.upper().startswith("BTC") if strategy_id == "btc_squeeze_breakout_m15"
                    else not symbol.upper().startswith("BTC"))
        spec = snapshot.strategies.get(strategy_id)
        enabled = bool(spec and spec.enabled)
        cells = []
        for key, rule in COLUMNS:
            if not eligible:
                cell = _cell("NA", rule=rule)
            elif not active:
                cell = _cell("STALE", rule=rule)
            else:
                cell = dict(metrics[tf][key])
            cell.update({"indicator": key, "core": key in core})
            cells.append(cell)
        rows.append({"strategy_id": strategy_id, "label": label,
                     "timeframe": tf, "enabled": enabled, "eligible": eligible,
                     "cells": cells})
    decision = {"policy_version": POLICY, "symbol": symbol,
                "market_state": market_state, "observed_at": now.isoformat(),
                "columns": [{"id": k, "label": k, "description": v} for k, v in COLUMNS],
                "rows": rows, "strategy_fingerprint": snapshot.fingerprint,
                "diagnostic_only": True}
    decision["fingerprint"] = fingerprint(decision)
    return decision


def observe_matrix(db_path, decision: dict) -> dict:
    """One bounded journal entry per new fingerprint (never one entry per tick)."""
    symbol = decision["symbol"]
    key = hashlib.sha256(f"{POLICY}:{symbol}:{decision['fingerprint']}".encode()).hexdigest()
    with session(db_path) as con:
        exists = con.execute(
            "SELECT 1 FROM audit_events WHERE event_type=? AND entity_id=? LIMIT 1",
            (EVENT, key)).fetchone()
    if not exists:
        log_audit_event(db_path, event_type=EVENT, source="signal_matrix_observer",
                        symbol=symbol, entity_id=key, payload=decision)
    return decision
