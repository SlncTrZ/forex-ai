# D3 Market Regime — Active Candidate Router + Command Center Telemetry

## Implementation status
Implemented on development branch `feat/d3-regime-command-center`.
Not deployed to the running immutable release on .227 unless explicitly stated
in a deployment manifest. **This is a real candidate veto when the branch is
deployed, not a passive shadow/scoring observer.** It does not submit orders.

## Canonical flow
```text
MT5 broker bars (M5/M15/H1/H4)
  -> only CLOSED H1 bars, >=220 observations, latest <= 3h old
  -> local EMA50/200, ADX14/DMI, EMA slope/ATR, BB width/ATR
  -> Quant provisional regime (UPTREND, DOWNTREND, SIDEWAY, UNCERTAIN)
  -> D3 structured 3-choice regime classification from numeric facts ONLY
  -> Quant/D3 agreement and confidence >= 0.75
  -> regime decision persisted to audit once per unique H1 feature fingerprint
  -> shared Strategy Router vetoes incompatible candidates BEFORE broker risk
  -> unchanged BrokerAwareRiskEngine + pending execution service
  -> existing independent D3 pre-execution + live broker validation
  -> MT5 order_check / order_send (guarded, once)
```

## Effective eligibility rules (version `regime-h1-v1`)
- **UPTREND** permits BUY candidates only.
- **DOWNTREND** permits SELL candidates only.
- **SIDEWAY** vetoes trend/breakout entries; the sole interim exception is
  `btc_squeeze_breakout_m15` *with source-backed evidence code*
  `BTC_SQUEEZE_BREAKOUT_CONFIRMED`, which the strategy only produces
  after its M15 squeeze-release, volume and momentum checks.
- **UNCERTAIN** vetoes all new candidates: quant/model disagreement, weak D3
  evidence, incomplete H1 warm-up, stale data and D3 transport errors.
- Strategy vetoes happen **after** creating/persisting candidate envelopes but
  **before** broker risk evaluation. They do not remove candidates from the
  journal; they provide a reproducible rejection reason.

## Quantization and data freshness
`src/forex_ai/advisory/regime_router.py` reuses production indicator functions
rather than a new analysis library. MT5 candle timestamps represent the
**bar's opening time**: an H1 bar is not eligible until opening time + 3600s
is <= captured market timestamp. The scanner now requests at least 261 raw
bars per timeframe for H1 EMA200 warmup, but does not use the forming bar.

An H1 bar >3h old cannot authorize new entries via regime routing.
This may block weekend trading until fresh H1 candles are available.
Some gold/BTC configurations run different trading hours; staleness must
always be examined with the broker's actual calendar and risk controls.

D3 receives numeric and categorical *measured features*, but never the
Quant classifier's proposed regime; this prevents answer leakage.
Confidence is the model's structured score, **not P(win)**.

## Cache, audit and scalability
The model is called only once per symbol/closed-H1 feature fingerprint/model
fingerprint/policy version. A SQLite audit event
`D3_REGIME_DECISION` stores the decision and is used as persistent cache,
including conservative blocked outcomes. A compound event/entity index
bounds query latency as the audit journal grows.

Telemetry fields: symbol, quant/model/effective regime, confidence, H1
closed-time, feature/model fingerprints, reason_code, policy_version,
inference latency. No credentials, full API responses, account login IDs
or arbitrary model prompts are stored.

## Command Center
`GET /api/regimes` is **read-only** and returns the latest observed audit
decision per symbol with an age calculation. React Command Center renders
real values only, flags stale H1 decisions and displays an explicit empty
state before a first audit event. No second browser-to-broker order path.

Existing HTTP dashboard is owner-only over SSH/Unix socket; this change
does **not** add remote public login or expose a new TCP port.

## Acceptance/test
- `python -m pytest tests/test_d3_regime_router.py tests/test_dashboard_regimes.py`
- `python -m pytest tests -q -p no:cacheprovider`
- `cd dashboard/ui && npm ci --offline --ignore-scripts && npm run build && npm test`
- D3 synthetic response probe proves transport/schema without any MT5 send.
- An owner-reviewed immutable release, risk snapshot, service preflight and
  rollback test are required before active deployment on .227.

**Not yet implemented in this phase:** D3 candidate-level Confluence Score
(30/25/25/20), calibrated score thresholds, position-size reduction, a
reproducible .152 walk-forward A/B evaluation, or a public Command Center.
No score-based permission to increase the existing risk limit is granted.

## Known evidence and next milestone
The 2026 research reports under `_private/research/` contain conflicting
claims and likely H1-to-M5 look-ahead in a separate script. The production
router's timestamp handling is stricter, but does not itself validate
historical alpha. Next, reuse this module for a point-in-time chronological
backtest with normalized broker execution costs and compare:

A. Baseline strategy signals without a regime router
B. Quant-only regime/eligibility
C. Quant + D3 regime/eligibility
D. C + source-backed Confluence Scoring/risk reduction

Track PnL, PF, MaxDD, missed winning trades, false veto rates, model
availability, latency and separate results for regime transitions.
