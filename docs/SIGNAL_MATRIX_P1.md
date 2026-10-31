# Forex-AI P1 — Command Center Signal Matrix (source-only)

**Implementation status:** developed and tested in source. Not a tagged production release.
**Scope:** read-only 8x8 diagnostic indicator matrix and command center layout.
No trading controls, order paths, scoring thresholds, D3 admission, risk rules or broker contracts changed.

## Operator layout
- Top: Balance · MT5 | Realized PnL | Floating PnL | Market Stream BTC/XAU ticker.
- 01 Visual Stage: deliberately empty, reserved for the presentation visualization.
- 02 Quant + D3 Dynamics: concise, no small explanatory captions, two-column gauges.
- 03 Indicator Signal Matrix: below 02; rows are eight strategies and columns are eight indicators.
- Infrastructure Health / Owner Controls become 04 / 05 and retain existing controls.
- Matrix cells show BULL (green), BEAR (red), NEUTRAL (amber), UNKNOWN/STALE/NA (muted), with a bright core-condition outline.
- Closed bars and diagnostic metrics are **not** independently eligible order signals. The matrix does not affect trade admission.

## Architecture
- `src/forex_ai/market/signal_matrix.py`: versioned `COLUMNS`, `ROWS`, instrument-specific availability,
  closed-candle calculations reusing `market.indicators`, bounded fingerprinted `SIGNAL_MATRIX_OBSERVATION` audit.
- `scripts/observe_market_intelligence.py`: existing independent five-minute observer collects M5/M15 (270 bars each)
  alongside H1; market-intelligence errors and missing timeframes are isolated per symbol. No trading methods called.
- `src/forex_ai/dashboard/server.py`: bounded, sanitized `GET /api/signal-matrix`; read-only database connection,
  whitelisted column/state schema, stale suppression, no raw audit JSON.
- `dashboard/ui/src/SignalMatrix.tsx`: audit-backed matrix, real condition inspection; no fabricated bright cells.
  Column layout uses a dynamic count rather than hard-coded eight columns.
- `tests/test_signal_matrix.py`: closed-bar exclusion, stale/off-session/unknown handling, security whitelist,
  instrument eligibility and audit idempotency.

## Indicator registry (initial eight)
RSI(14), EMA20/EMA50, ADX14/+DI/-DI, Supertrend(10,3), tick-volume ratio,
Bollinger/Keltner squeeze release, 20-closed-bar high/low breakout and body/ATR14 trigger.
Thresholds are explicit **diagnostic reference conditions**, not guaranteed to exactly reproduce the
full indicator logic or final admission rules of each V2 strategy. Core/Watch bindings are informational,
and disabled strategies remain visible but labeled OFF.

Data quality: closed M5 bars stale after 12m; closed M15 bars stale after 30m; dashboard audit stale after 16m.
Market closed, missing history and unsupported symbols fail gray rather than lighting an indicator.
No tick-level indicator synthesis, performance claims or live scoring.

## Next phase — not implemented by P1
Weighted Score Engine, Telegram news adapters, historical retrieval, trade outcome tracking,
D3-generated recommendations and automatic policy changes all require separate work and authorization.
