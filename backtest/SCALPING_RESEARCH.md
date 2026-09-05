# Scalping / short-horizon research candidates

The current frozen research universe contains EURUSD and XAUUSD with M15/H1/H4 replay data. Therefore the first research phase is short-horizon intraday trading on M15 decisions (15–120 minute outcome windows), not true M1/M5 high-frequency scalping.

## Indicator set added to the canonical library

`src/forex_ai/market/indicators.py` now provides reusable implementations with explicit parameters:

- EMA / SMA
- ATR
- trend efficiency
- RSI (Wilder)
- Bollinger Bands, bandwidth and %B
- fast Stochastic %K/%D
- DMI (+DI/-DI) and ADX (Wilder)
- tick-volume z-score

Strategy code must pass periods/thresholds explicitly from YAML. Indicator implementations do not decide entry conditions by themselves.

## Candidate method 1 — Trend continuation scalp

Purpose: test the hypothesis already exposed by exploration research on XAUUSD: aligned HTF trend can continue without a textbook EMA pullback.

Potential evidence:

- H4/H1 directional alignment
- price relative to configurable fast/slow EMA
- EMA separation / ATR
- fast/slow EMA slope / ATR
- ADX trend strength and +DI/-DI direction
- RSI regime rather than simple 70/30 reversal logic
- reclaim / continuation candle strength
- distance from fast EMA / ATR

Important: do not require every item as a hard gate. Journal them as features first.

## Candidate method 2 — Bollinger mean reversion / range scalp

Purpose: trade short-term reversion only when the market behaves like a range rather than a strong trend.

Potential evidence:

- Bollinger %B near/beyond upper or lower band
- Bollinger bandwidth low or stable
- RSI extreme or returning from an extreme
- Stochastic %K/%D reversal near range extremes
- ADX low/falling to identify weak trend regime
- local support/resistance / prior range location

This method must explicitly reject or down-rank strong directional regimes because RSI/Stochastic can remain extreme during trends.

## Candidate method 3 — Volatility compression breakout

Purpose: separate genuine compression-to-expansion setups from the existing candle-expansion gate.

Potential evidence:

- Bollinger bandwidth percentile / compression
- 20-bar (configurable) range or Donchian-style boundary
- close outside the range
- ATR expansion
- ADX rising after the break
- tick-volume z-score as participation proxy
- breakout extension / ATR
- spread / ATR

This is a natural successor experiment for `volatility_breakout_v1`: current `min_expansion` and `min_efficiency` should be treated as features during exploration rather than assumed optimal gates.

## Candidate method 4 — Momentum reset in trend

Purpose: enter an established trend after short-term momentum resets, without demanding a specific EMA touch.

Potential evidence:

- H4/H1 trend alignment
- EMA slope/separation
- RSI returning toward a trend-support regime then re-accelerating
- Stochastic reset/crossover in trend direction
- ADX above a configurable trend-strength region
- continuation candle body / ATR

This differs from Trend Pullback because the reset is defined by momentum state, not by a mandatory price touch near EMA20.

## Indicators deliberately deferred

### VWAP

VWAP is highly useful in centralized intraday markets because it weights price by traded volume. Spot FX does not have one centralized exchange volume feed; the current MT5 historical bars provide broker tick volume. A `tick-VWAP` can be researched later but must be labeled as a broker tick-volume proxy, not true market VWAP.

### MACD

MACD is derived from moving averages and would initially add substantial feature redundancy to the existing EMA slope/separation measurements. It can be added later if tests show value beyond raw EMA relationships.

### Parabolic SAR

Parabolic SAR is useful for trailing/reversal logic, but it is more stateful and overlaps with trade-management research. It is deferred until entry cohorts are better established.

## Research discipline

For each method:

1. Define strategy identity (the minimum condition that makes it that method).
2. Convert confirmations into recorded continuous features where possible.
3. Run on the fixed 8-week dataset without tuning on every result.
4. Split results by the existing OOS and IS month partitions.
5. Record candidate count, setup clusters, mark-R horizons, MFE, MAE and first TP/SL touch.
6. Promote a rule only when it has a coherent market rationale and survives another unseen period.

No method in this document is automatically live-capable.
