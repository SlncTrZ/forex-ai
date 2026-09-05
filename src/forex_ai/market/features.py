from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .indicators import atr, dmi_adx, ema, rsi


@dataclass(frozen=True)
class _FeatureBar:
    high: float
    low: float
    close: float
    volume: float = 0.0


def bars_frame(bars: list[dict[str, Any]]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars).copy()
    required = ["open", "high", "low", "close"]
    missing = [name for name in required if name not in df.columns]
    if missing:
        raise ValueError(f"Missing OHLC fields: {missing}")
    for name in required:
        df[name] = pd.to_numeric(df[name], errors="coerce")
    if "time" in df.columns:
        df["time"] = pd.to_numeric(df["time"], errors="coerce")
    if "tick_volume" in df.columns:
        df["tick_volume"] = pd.to_numeric(df["tick_volume"], errors="coerce").fillna(0)
    elif "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    return df.dropna(subset=required).reset_index(drop=True)


def summarize_features(
    bars: list[dict[str, Any]],
    *,
    ema_fast_period: int = 20,
    ema_slow_period: int = 50,
    rsi_period: int = 14,
    atr_period: int = 14,
    adx_period: int = 14,
    breakout_period: int = 20,
) -> dict[str, Any]:
    df = bars_frame(bars)
    minimum = max(ema_slow_period, rsi_period + 1, atr_period + 1, adx_period * 2, breakout_period + 1)
    if len(df) < minimum:
        return {"ready": False, "bars": len(df)}

    volumes = (
        df["tick_volume"].tolist()
        if "tick_volume" in df.columns
        else df["volume"].tolist()
        if "volume" in df.columns
        else [0.0] * len(df)
    )
    feature_bars = tuple(
        _FeatureBar(float(row.high), float(row.low), float(row.close), float(volume))
        for row, volume in zip(df.itertuples(index=False), volumes)
    )
    closes = [bar.close for bar in feature_bars]
    close = closes[-1]
    ema_fast = ema(closes, ema_fast_period)
    ema_slow = ema(closes, ema_slow_period)
    atr_value = atr(feature_bars, atr_period)
    rsi_value = rsi(closes, rsi_period)
    dmi = dmi_adx(feature_bars, adx_period)
    adx_value = None if dmi is None else dmi.adx

    if close > ema_fast > ema_slow:
        trend = "up"
    elif close < ema_fast < ema_slow:
        trend = "down"
    else:
        trend = "mixed"

    prior = feature_bars[-(breakout_period + 1):-1]
    high = max(bar.high for bar in prior)
    low = min(bar.low for bar in prior)
    breakout = "up" if close > high else "down" if close < low else "none"

    recent = []
    for _, candle in df.tail(breakout_period).iterrows():
        recent.append(
            {
                "time": int(candle["time"]) if "time" in candle and pd.notna(candle["time"]) else None,
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
            }
        )

    return {
        "ready": True,
        "bars": len(df),
        "close": close,
        "ema20": ema_fast if ema_fast_period == 20 else None,
        "ema50": ema_slow if ema_slow_period == 50 else None,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_fast_period": ema_fast_period,
        "ema_slow_period": ema_slow_period,
        "rsi14": rsi_value if rsi_period == 14 else None,
        "rsi": rsi_value,
        "rsi_period": rsi_period,
        "atr14": atr_value if atr_period == 14 else None,
        "atr": atr_value,
        "atr_period": atr_period,
        "adx14": adx_value if adx_period == 14 else None,
        "adx": adx_value,
        "adx_period": adx_period,
        "trend": trend,
        "breakout_20": breakout if breakout_period == 20 else None,
        "breakout": breakout,
        "breakout_period": breakout_period,
        "prior_high20": high if breakout_period == 20 else None,
        "prior_low20": low if breakout_period == 20 else None,
        "prior_high": high,
        "prior_low": low,
        "distance_ema20_atr": None if not atr_value or ema_fast_period != 20 else (close - ema_fast) / atr_value,
        "distance_ema_fast_atr": None if not atr_value else (close - ema_fast) / atr_value,
        "recent_candles": recent,
    }
