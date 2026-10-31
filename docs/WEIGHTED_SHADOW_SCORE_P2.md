# Forex-AI P2 — Weighted Signal Score Engine (SHADOW ONLY)

Status: **development source**, unreleased; Production remains at the last approved tag.
P2 is a deterministic research score over the real closed-candle P1 matrix and the same
observation cycle's H1 Quant/D3 audit. It is NOT a trading candidate, order approval,
broker entry command, profit probability or strategy-rule rewrite.

## Score contract (policy `shadow-weighted-v1`)

| Component | Max | Required evidence |
| --- | ---: | --- |
| Setup | 28 | Directional match for this strategy's diagnostic ST/SQZ/STRUCT/TRG core trigger columns |
| Trend | 20 | EMA20/EMA50 direction matching BUY/SELL |
| Momentum | 16 | RSI14 and ADX14/+DI/-DI directional indicators (8 each) |
| Volume | 14 | Tick-volume ratio + latest closed candle direction |
| D3 | 12 | Fresh H1 Quant/D3 class agreement matching direction, valid response; used only with technical trigger evidence |
| News | 5 | **UNAVAILABLE; 0 points** (future authorized-feed adapter only) |
| History | 5 | **UNAVAILABLE; 0 points** (future settled-trade similarity tracker only) |
| Total | **100** | Present effective maximum **90**, not normalized to 100 |

The provisional research threshold is **75 points**. The score evaluates BUY and
SELL independently; neither a Bull cell nor a green numeric score grants entry.
`SHADOW_THRESHOLD_MET` means ONLY a simulated research threshold was met
**and all core diagnostic trigger columns aligned**. It does NOT indicate that
strategy-specific native entry checks, Quant production regime router, confluence
or broker/execution safety would approve a trade.

`INSUFFICIENT_EVIDENCE`, `STALE`, `DISABLED`, `NOT_APPLICABLE` and
`NO_COMPLETE_SETUP` are explicit non-actionable verdicts.
Unknown/stale cells earn zero; there is no pro-rata redistribution to known
features or fabricated D3/news/history points. Confidence and choice probability
are logged by the original Regime observer; these are **not** calibrated P(win).
A low-confidence but valid, Quant/D3-agreeing H1 class can still contribute
categorical alignment in Shadow analysis; the known DEMO policy is unchanged.
Invalid/unavailable/misaligned/stale D3 never earns 12 points.

## Data flow and scalability

`scripts/observe_market_intelligence.py`:
one BTC/XAU scheduled read-only MT5 H1/M5/M15 pass
→ P1 `SIGNAL_MATRIX_OBSERVATION`
→ P2 `score_matrix(matrix, same_cycle_observed_regime)`
→ P2 `SHADOW_SIGNAL_SCORE` versioned/fingerprinted journal event
→ `GET /api/shadow-scores` bounded, read-only, sanitized audit
→ `dashboard/ui/src/SignalMatrix.tsx` row scores and selected 7-component explanation.

The scoring engine does NOT call Ollama, MT5, broker execution, Risk Engine,
account-control persistence, candidate scanner or order sender. It reuses an
already available H1 D3 observation; hence no additional inference Latency or
per-token cost on the critical execution path. Journal writes are idempotent
per scored feature/regime fingerprint. API explicitly hides stale scores and
raw audit blobs, and exposes only bounded whitelisted fields.

**Isolation:** P2 modules are imported by the observational worker and
read-only Dashboard server, never by `scan_candidates.py` or
`execute_pending.py`. It must not be used to auto-ARM, raise risk, override
BTC DEMO admission, trigger real orders, or claim future PnL.

**Future work, not P2:** actual news scores, settled-trade retrieval,
counterfactual outcomes, calibration, strategy-score weight optimization and
the separate owner-approved trading/admission policy promotion. At present
P2 measures directional indicator agreement, not predictive edge.
