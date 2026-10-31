# Forex-AI — Sideway Market Regime Routing & Range Liquidity Trap

**Status:** source implementation complete; exploration strategy registered.
**Scope:** deterministic regime detection, breakout veto in sideway, and false breakout counter-attacks.
No unapproved live orders; fails closed under UNCERTAIN or unconfirmed regime.

## 1. Regime detection and classification
- Multi-timeframe closed bars: strictly completed H1 bars only (zero lookahead).
- Indicators: ADX(14), +DI/-DI, EMA(50), EMA(200), ATR(14).
- Classes:
  - **UPTREND:** ADX >= 22, EMA50 > EMA200 with gap >= 0.3 ATR, positive slope, +DI > -DI.
  - **DOWNTREND:** ADX >= 22, EMA50 < EMA200 with gap <= -0.3 ATR, negative slope, -DI > +DI.
  - **SIDEWAY:** ADX < 20 and low slope (|slope| <= 0.22) with compressed bandwidth or tight EMA gap.
  - **UNCERTAIN:** Ambiguous structure, transitional momentum, or stale feeds.

## 2. Strategy eligibility policy
When `regime == "SIDEWAY"`:
- `supertrend_volume_m5`, `supertrend_stochastic_m5`, `keylevel_breakout_v2`, `smc_pro_fvg_v2`, `mtf_trend_continuation_v2` are automatically **VETOED** with `REGIME_SIDEWAY_VETO` to eliminate choppy-market friction drag.
- Only specialized sideway strategies are permitted:
  - `btc_squeeze_breakout_m15` with source-backed `BTC_SQUEEZE_BREAKOUT_CONFIRMED`.
  - `range_liquidity_trap_m5` with source-backed `RANGE_LIQUIDITY_TRAP_CONFIRMED`.

## 3. Range Liquidity Trap (M5)
- **Concept:** Identifies false breakout liquidity sweeps at 20-bar range extremes during London/NY active sessions.
- **Sell Trap:** Price spikes above Range High but closes back inside range on a bearish candle with upper wick >= 25% of range. Stop Loss placed above the sweep wick + buffer. Take profit targets opposite range boundary (RR >= 1:2.0).
- **Buy Trap:** Price spikes below Range Low but closes back inside range on a bullish candle with lower wick >= 25% of range. Stop Loss placed below the sweep wick - buffer. Take profit targets opposite range boundary (RR >= 1:2.0).
- **Risk Invariant:** Stop distance must satisfy asset-specific min_stop_distance and remain bounded within 0.2 to 4.0 ATR.
