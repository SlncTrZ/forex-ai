from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Callable, Mapping

from forex_ai.market.candles import candle_shape, is_inside_bar
from forex_ai.market.indicators import atr, ema, trend_state
from forex_ai.research.scalping_config import (
    BreakoutRetestParameters,
    EMACrossParameters,
    HarnessParameters,
    InsideBarParameters,
    PinbarParameters,
    ScalpingStrategySpec,
)
from forex_ai.research.scalping_regime import audit_signal_regime
from forex_ai.strategy.v1.contracts import MarketSnapshot, fingerprint


@dataclass(frozen=True)
class ScalpingSignal:
    signal_id: str
    setup_key: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    symbol: str
    side: str
    decision_timeframe: str
    generated_at_utc: datetime
    expires_at_utc: datetime
    entry: float
    stop_loss: float
    take_profit: float
    features: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("invalid signal side")
        if self.generated_at_utc.tzinfo is None or self.expires_at_utc.tzinfo is None:
            raise ValueError("timezone-aware signal timestamps required")
        if self.side == "BUY" and not (self.stop_loss < self.entry < self.take_profit):
            raise ValueError("invalid BUY geometry")
        if self.side == "SELL" and not (self.take_profit < self.entry < self.stop_loss):
            raise ValueError("invalid SELL geometry")
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop_loss)

    @property
    def target_r(self) -> float:
        return abs(self.take_profit - self.entry) / self.risk


SignalEvaluator = Callable[[MarketSnapshot, ScalpingStrategySpec, datetime], ScalpingSignal | None]


def _make_signal(
    *,
    snapshot: MarketSnapshot,
    spec: ScalpingStrategySpec,
    side: str,
    decision_timeframe: str,
    setup_key: str,
    now: datetime,
    entry: float,
    stop_loss: float,
    target_r: float,
    expiry_minutes: int,
    features: Mapping[str, Any],
) -> ScalpingSignal | None:
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None
    if side == "BUY":
        if stop_loss >= entry:
            return None
        take_profit = entry + risk * target_r
    else:
        if stop_loss <= entry:
            return None
        take_profit = entry - risk * target_r
    signal_id = fingerprint({
        "strategy_id": spec.strategy_id,
        "strategy_version": spec.version,
        "strategy_config_fingerprint": spec.fingerprint,
        "symbol": snapshot.symbol,
        "side": side,
        "setup_key": setup_key,
    })[:32]
    return ScalpingSignal(
        signal_id=signal_id,
        setup_key=setup_key,
        strategy_id=spec.strategy_id,
        strategy_version=spec.version,
        strategy_config_fingerprint=spec.fingerprint,
        symbol=snapshot.symbol,
        side=side,
        decision_timeframe=decision_timeframe,
        generated_at_utc=now,
        expires_at_utc=now + timedelta(minutes=expiry_minutes),
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        features=features,
    )


def _setup_key(spec: ScalpingStrategySpec, snapshot: MarketSnapshot, **identity: Any) -> str:
    return fingerprint({
        "strategy_id": spec.strategy_id,
        "strategy_version": spec.version,
        "symbol": snapshot.symbol,
        **identity,
    })


def evaluate_inside_bar(snapshot: MarketSnapshot, spec: ScalpingStrategySpec, now: datetime) -> ScalpingSignal | None:
    p = spec.parameters
    if not isinstance(p, InsideBarParameters):
        raise TypeError("inside-bar config mismatch")
    tf = snapshot.timeframes.get(p.decision_timeframe)
    if tf is None or len(tf.closed_bars) < max(p.atr_period + 1, 3):
        return None
    bars = tf.closed_bars
    mother, inside, trigger = bars[-3], bars[-2], bars[-1]
    if not is_inside_bar(mother, inside):
        return None
    atr_value = atr(bars, p.atr_period)
    if atr_value <= 0:
        return None
    mother_shape = candle_shape(mother)
    if mother_shape.range / atr_value < p.mother_min_range_atr:
        return None
    if mother_shape.body_ratio < p.mother_min_body_ratio:
        return None

    buy_break = trigger.close > mother.high + p.breakout_buffer_atr * atr_value
    sell_break = trigger.close < mother.low - p.breakout_buffer_atr * atr_value
    if p.require_mother_direction:
        buy_break = buy_break and mother_shape.direction == "BULL"
        sell_break = sell_break and mother_shape.direction == "BEAR"
    if buy_break == sell_break:
        return None
    side = "BUY" if buy_break else "SELL"
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    stop = (
        inside.low - p.stop_buffer_atr * atr_value
        if side == "BUY"
        else inside.high + p.stop_buffer_atr * atr_value
    )
    key = _setup_key(
        spec,
        snapshot,
        mother_time=mother.time_utc,
        inside_time=inside.time_utc,
        side=side,
    )
    return _make_signal(
        snapshot=snapshot,
        spec=spec,
        side=side,
        decision_timeframe=p.decision_timeframe,
        setup_key=key,
        now=now,
        entry=entry,
        stop_loss=stop,
        target_r=p.target_r,
        expiry_minutes=p.expiry_minutes,
        features={
            "mother_range_atr": mother_shape.range / atr_value,
            "mother_body_ratio": mother_shape.body_ratio,
            "mother_direction": mother_shape.direction,
            "inside_range_atr": (inside.high - inside.low) / atr_value,
            "breakout_distance_atr": (
                (trigger.close - mother.high) / atr_value
                if side == "BUY"
                else (mother.low - trigger.close) / atr_value
            ),
            "atr": atr_value,
        },
    )


def evaluate_ema_cross(snapshot: MarketSnapshot, spec: ScalpingStrategySpec, now: datetime) -> ScalpingSignal | None:
    p = spec.parameters
    if not isinstance(p, EMACrossParameters):
        raise TypeError("ema-cross config mismatch")
    tf = snapshot.timeframes.get(p.decision_timeframe)
    required = max(p.ema_trend + 2, p.atr_period + 1, p.stop_lookback_bars)
    if tf is None or len(tf.closed_bars) < required:
        return None
    bars = tf.closed_bars
    closes = [bar.close for bar in bars]
    atr_value = atr(bars, p.atr_period)
    if atr_value <= 0:
        return None
    prev_trigger = ema(closes[:-1], p.ema_trigger)
    prev_signal = ema(closes[:-1], p.ema_signal)
    trigger = ema(closes, p.ema_trigger)
    signal_line = ema(closes, p.ema_signal)
    trend_line = ema(closes, p.ema_trend)
    buy_cross = prev_trigger <= prev_signal and trigger > signal_line and signal_line > trend_line
    sell_cross = prev_trigger >= prev_signal and trigger < signal_line and signal_line < trend_line
    if buy_cross == sell_cross:
        return None
    side = "BUY" if buy_cross else "SELL"
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    stop_bars = bars[-p.stop_lookback_bars:]
    stop = (
        min(bar.low for bar in stop_bars) - p.stop_buffer_atr * atr_value
        if side == "BUY"
        else max(bar.high for bar in stop_bars) + p.stop_buffer_atr * atr_value
    )
    cross_bar = bars[-1]
    key = _setup_key(spec, snapshot, cross_time=cross_bar.time_utc, side=side)
    return _make_signal(
        snapshot=snapshot,
        spec=spec,
        side=side,
        decision_timeframe=p.decision_timeframe,
        setup_key=key,
        now=now,
        entry=entry,
        stop_loss=stop,
        target_r=p.target_r,
        expiry_minutes=p.expiry_minutes,
        features={
            "ema_trigger": trigger,
            "ema_signal": signal_line,
            "ema_trend": trend_line,
            "ema_trigger_signal_separation_atr": (trigger - signal_line) / atr_value,
            "ema_signal_trend_separation_atr": (signal_line - trend_line) / atr_value,
            "previous_trigger_signal_separation_atr": (prev_trigger - prev_signal) / atr_value,
            "triple_alignment": "BULL" if trigger > signal_line > trend_line else "BEAR" if trigger < signal_line < trend_line else "MIXED",
            "cross_bar_range_atr": (cross_bar.high - cross_bar.low) / atr_value,
            "atr": atr_value,
        },
    )


def evaluate_breakout_retest(snapshot: MarketSnapshot, spec: ScalpingStrategySpec, now: datetime) -> ScalpingSignal | None:
    p = spec.parameters
    if not isinstance(p, BreakoutRetestParameters):
        raise TypeError("breakout-retest config mismatch")
    tf = snapshot.timeframes.get(p.decision_timeframe)
    required = p.range_bars + p.breakout_search_bars + 2
    if tf is None or len(tf.closed_bars) < max(required, p.atr_period + 2):
        return None
    bars = tf.closed_bars
    latest_index = len(bars) - 1
    latest = bars[latest_index]
    current_atr = atr(bars, p.atr_period)
    if current_atr <= 0:
        return None

    first_breakout_index = max(p.range_bars, latest_index - p.breakout_search_bars)
    for breakout_index in range(latest_index - 2, first_breakout_index - 1, -1):
        prior = bars[breakout_index - p.range_bars:breakout_index]
        prior_atr = atr(bars[:breakout_index], p.atr_period)
        if prior_atr <= 0:
            continue
        upper = max(bar.high for bar in prior)
        lower = min(bar.low for bar in prior)
        breakout = bars[breakout_index]
        buy_break = breakout.close > upper + p.min_breakout_close_atr * prior_atr
        sell_break = breakout.close < lower - p.min_breakout_close_atr * prior_atr
        if buy_break == sell_break:
            continue
        retest_bars = bars[breakout_index + 1:latest_index]
        if not retest_bars:
            continue
        if buy_break:
            retest = [
                bar for bar in retest_bars
                if upper - p.retest_tolerance_atr * current_atr <= bar.low <= upper + p.retest_tolerance_atr * current_atr
            ]
            confirmed = latest.close > upper + p.confirmation_buffer_atr * current_atr
            if not retest or not confirmed:
                continue
            side = "BUY"
            entry = snapshot.ask
            stop = min(bar.low for bar in retest_bars) - p.stop_buffer_atr * current_atr
            retest_depth = (min(bar.low for bar in retest_bars) - upper) / current_atr
            breakout_strength = (breakout.close - upper) / prior_atr
            boundary = upper
        else:
            retest = [
                bar for bar in retest_bars
                if lower - p.retest_tolerance_atr * current_atr <= bar.high <= lower + p.retest_tolerance_atr * current_atr
            ]
            confirmed = latest.close < lower - p.confirmation_buffer_atr * current_atr
            if not retest or not confirmed:
                continue
            side = "SELL"
            entry = snapshot.bid
            stop = max(bar.high for bar in retest_bars) + p.stop_buffer_atr * current_atr
            retest_depth = (lower - max(bar.high for bar in retest_bars)) / current_atr
            breakout_strength = (lower - breakout.close) / prior_atr
            boundary = lower
        key = _setup_key(
            spec,
            snapshot,
            breakout_time=breakout.time_utc,
            side=side,
        )
        return _make_signal(
            snapshot=snapshot,
            spec=spec,
            side=side,
            decision_timeframe=p.decision_timeframe,
            setup_key=key,
            now=now,
            entry=entry,
            stop_loss=stop,
            target_r=p.target_r,
            expiry_minutes=p.expiry_minutes,
            features={
                "range_boundary": boundary,
                "breakout_strength_atr": breakout_strength,
                "bars_since_breakout": latest_index - breakout_index,
                "retest_depth_atr": retest_depth,
                "confirmation_distance_atr": (
                    (latest.close - boundary) / current_atr
                    if side == "BUY"
                    else (boundary - latest.close) / current_atr
                ),
                "atr": current_atr,
            },
        )
    return None


def _nearest_context_zone(snapshot: MarketSnapshot, role: str) -> Mapping[str, Any] | None:
    context = snapshot.context.get("higher_timeframe_structure")
    if not isinstance(context, Mapping):
        return None
    zones = context.get("supports" if role == "support" else "resistances")
    if not isinstance(zones, list) or not zones:
        return None
    return min(zones, key=lambda zone: abs(float(zone.get("distance_from_price", 0.0))))


def evaluate_pinbar(snapshot: MarketSnapshot, spec: ScalpingStrategySpec, now: datetime) -> ScalpingSignal | None:
    p = spec.parameters
    if not isinstance(p, PinbarParameters):
        raise TypeError("pinbar config mismatch")
    tf = snapshot.timeframes.get(p.decision_timeframe)
    if tf is None or len(tf.closed_bars) < p.atr_period + 1:
        return None
    bars = tf.closed_bars
    candle = bars[-1]
    shape = candle_shape(candle)
    atr_value = atr(bars, p.atr_period)
    if atr_value <= 0 or shape.range / atr_value < p.min_range_atr:
        return None
    if shape.body_ratio > p.max_body_ratio:
        return None

    bullish = (
        shape.lower_wick_ratio >= p.min_primary_wick_ratio
        and shape.upper_wick_ratio <= p.max_opposite_wick_ratio
        and shape.close_position >= p.min_close_extreme_ratio
    )
    bearish = (
        shape.upper_wick_ratio >= p.min_primary_wick_ratio
        and shape.lower_wick_ratio <= p.max_opposite_wick_ratio
        and shape.close_position <= 1.0 - p.min_close_extreme_ratio
    )
    if bullish == bearish:
        return None
    side = "BUY" if bullish else "SELL"
    zone = _nearest_context_zone(snapshot, "support" if side == "BUY" else "resistance")
    if zone is None:
        return None
    zone_center = float(zone["center"])
    reference = (snapshot.bid + snapshot.ask) / 2.0
    sr_distance_atr = abs(reference - zone_center) / atr_value
    if sr_distance_atr > p.max_sr_distance_atr:
        return None
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    stop = (
        candle.low - p.stop_buffer_atr * atr_value
        if side == "BUY"
        else candle.high + p.stop_buffer_atr * atr_value
    )
    key = _setup_key(spec, snapshot, pinbar_time=candle.time_utc, side=side)
    return _make_signal(
        snapshot=snapshot,
        spec=spec,
        side=side,
        decision_timeframe=p.decision_timeframe,
        setup_key=key,
        now=now,
        entry=entry,
        stop_loss=stop,
        target_r=p.target_r,
        expiry_minutes=p.expiry_minutes,
        features={
            "body_ratio": shape.body_ratio,
            "upper_wick_ratio": shape.upper_wick_ratio,
            "lower_wick_ratio": shape.lower_wick_ratio,
            "close_position": shape.close_position,
            "range_atr": shape.range / atr_value,
            "sr_distance_atr": sr_distance_atr,
            "sr_timeframe": zone.get("timeframe"),
            "sr_importance": zone.get("importance"),
            "sr_touches": zone.get("touches"),
            "atr": atr_value,
        },
    )


EVALUATORS: Mapping[str, SignalEvaluator] = MappingProxyType({
    "inside_bar_momentum_breakout_v1": evaluate_inside_bar,
    "ema_cross_scalp_v1": evaluate_ema_cross,
    "breakout_retest_v1": evaluate_breakout_retest,
    "pinbar_reversal_v1": evaluate_pinbar,
})


def add_common_features(signal: ScalpingSignal, snapshot: MarketSnapshot, harness: HarnessParameters) -> ScalpingSignal:
    decision_tf = snapshot.timeframes[signal.decision_timeframe]
    decision_atr = atr(decision_tf.closed_bars, harness.sr_feature_atr_period)
    h1 = snapshot.timeframes.get("H1")
    h1_state = (
        trend_state(h1.closed_bars, harness.h1_ema_fast, harness.h1_ema_slow)
        if h1 is not None and len(h1.closed_bars) >= harness.h1_ema_slow
        else "UNAVAILABLE"
    )
    support = _nearest_context_zone(snapshot, "support")
    resistance = _nearest_context_zone(snapshot, "resistance")
    reference = (snapshot.bid + snapshot.ask) / 2.0

    def normalized_distance(zone: Mapping[str, Any] | None) -> float | None:
        if zone is None or decision_atr <= 0:
            return None
        return abs(reference - float(zone["center"])) / decision_atr

    common = {
        "h1_trend": h1_state,
        "spread_atr": None if decision_atr <= 0 else (snapshot.ask - snapshot.bid) / decision_atr,
        "nearest_support_distance_atr": normalized_distance(support),
        "nearest_resistance_distance_atr": normalized_distance(resistance),
        "nearest_support_timeframe": None if support is None else support.get("timeframe"),
        "nearest_resistance_timeframe": None if resistance is None else resistance.get("timeframe"),
        "utc_hour": signal.generated_at_utc.hour,
        "weekday": signal.generated_at_utc.weekday(),
        **dict(audit_signal_regime(snapshot, signal.side, harness)),
    }
    return replace(signal, features={**dict(signal.features), **common})
