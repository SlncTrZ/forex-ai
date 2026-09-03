from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr = _atr(df, period).replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


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
    return df.dropna(subset=required).reset_index(drop=True)


def summarize_features(bars: list[dict[str, Any]]) -> dict[str, Any]:
    df = bars_frame(bars)
    if len(df) < 60:
        return {"ready": False, "bars": len(df)}

    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["rsi14"] = _rsi(df["close"], 14)
    df["atr14"] = _atr(df, 14)
    df["adx14"] = _adx(df, 14)
    df["high20"] = df["high"].rolling(20).max().shift(1)
    df["low20"] = df["low"].rolling(20).min().shift(1)

    row = df.iloc[-1]
    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    atr = float(row["atr14"]) if pd.notna(row["atr14"]) else None
    rsi = float(row["rsi14"]) if pd.notna(row["rsi14"]) else None
    adx = float(row["adx14"]) if pd.notna(row["adx14"]) else None

    if close > ema20 > ema50:
        trend = "up"
    elif close < ema20 < ema50:
        trend = "down"
    else:
        trend = "mixed"

    high20 = float(row["high20"]) if pd.notna(row["high20"]) else None
    low20 = float(row["low20"]) if pd.notna(row["low20"]) else None
    breakout = "none"
    if high20 is not None and close > high20:
        breakout = "up"
    elif low20 is not None and close < low20:
        breakout = "down"

    recent = []
    for _, candle in df.tail(20).iterrows():
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
        "ema20": ema20,
        "ema50": ema50,
        "rsi14": rsi,
        "atr14": atr,
        "adx14": adx,
        "trend": trend,
        "breakout_20": breakout,
        "prior_high20": high20,
        "prior_low20": low20,
        "distance_ema20_atr": None if not atr else (close - ema20) / atr,
        "recent_candles": recent,
    }
