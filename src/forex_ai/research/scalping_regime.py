from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from forex_ai.market.indicators import atr, dmi_adx, ema
from forex_ai.research.scalping_config import HarnessParameters
from forex_ai.strategy.v1.contracts import MarketSnapshot, TimeframeSnapshot


@dataclass(frozen=True)
class RegimeSnapshot:
    timeframe: str
    regime: str
    adx: float | None
    plus_di: float | None
    minus_di: float | None
    ema_fast: float | None
    ema_slow: float | None
    ema_separation_atr: float | None
    ema_slow_slope_atr: float | None

    def to_features(self, prefix: str = "regime") -> dict[str, object]:
        return {
            f"{prefix}_timeframe": self.timeframe,
            f"{prefix}": self.regime,
            f"{prefix}_adx": self.adx,
            f"{prefix}_plus_di": self.plus_di,
            f"{prefix}_minus_di": self.minus_di,
            f"{prefix}_ema_fast": self.ema_fast,
            f"{prefix}_ema_slow": self.ema_slow,
            f"{prefix}_ema_separation_atr": self.ema_separation_atr,
            f"{prefix}_ema_slow_slope_atr": self.ema_slow_slope_atr,
        }


def classify_regime(tf: TimeframeSnapshot, harness: HarnessParameters, *, timeframe_name: str | None = None) -> RegimeSnapshot:
    bars = tf.closed_bars
    name = timeframe_name or tf.timeframe
    required = max(
        harness.regime_ema_slow + harness.regime_slope_lookback_bars + 1,
        harness.regime_atr_period + 1,
        harness.regime_adx_period * 2,
    )
    if len(bars) < required:
        return RegimeSnapshot(name, "UNAVAILABLE", None, None, None, None, None, None, None)

    closes = [bar.close for bar in bars]
    atr_value = atr(bars, harness.regime_atr_period)
    dmi = dmi_adx(bars, harness.regime_adx_period)
    if atr_value <= 0 or dmi is None:
        return RegimeSnapshot(name, "UNAVAILABLE", None, None, None, None, None, None, None)

    fast = ema(closes, harness.regime_ema_fast)
    slow = ema(closes, harness.regime_ema_slow)
    lookback = harness.regime_slope_lookback_bars
    prior_slow = ema(closes[:-lookback], harness.regime_ema_slow)
    separation = (fast - slow) / atr_value
    slow_slope = (slow - prior_slow) / atr_value

    enough_strength = dmi.adx >= harness.regime_min_adx
    bullish = (
        enough_strength
        and separation >= harness.regime_min_separation_atr
        and slow_slope >= harness.regime_min_slow_slope_atr
        and dmi.plus_di > dmi.minus_di
    )
    bearish = (
        enough_strength
        and separation <= -harness.regime_min_separation_atr
        and slow_slope <= -harness.regime_min_slow_slope_atr
        and dmi.minus_di > dmi.plus_di
    )
    if bullish:
        regime = "UP"
    elif bearish:
        regime = "DOWN"
    else:
        regime = "SIDEWAYS"
    return RegimeSnapshot(
        timeframe=name,
        regime=regime,
        adx=dmi.adx,
        plus_di=dmi.plus_di,
        minus_di=dmi.minus_di,
        ema_fast=fast,
        ema_slow=slow,
        ema_separation_atr=separation,
        ema_slow_slope_atr=slow_slope,
    )


def signal_alignment(side: str, regime: str) -> str:
    if regime == "UP":
        return "WITH_TREND" if side == "BUY" else "COUNTER_TREND"
    if regime == "DOWN":
        return "WITH_TREND" if side == "SELL" else "COUNTER_TREND"
    if regime == "SIDEWAYS":
        return "NO_TREND"
    return "UNAVAILABLE"


def audit_signal_regime(snapshot: MarketSnapshot, side: str, harness: HarnessParameters) -> Mapping[str, object]:
    tf = snapshot.timeframes.get(harness.regime_timeframe)
    if tf is None:
        regime = RegimeSnapshot(harness.regime_timeframe, "UNAVAILABLE", None, None, None, None, None, None, None)
    else:
        regime = classify_regime(tf, harness, timeframe_name=harness.regime_timeframe)
    features = dict(regime.to_features())
    features["regime_alignment"] = signal_alignment(side, regime.regime)
    return features
