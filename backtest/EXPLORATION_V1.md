# Exploration V1

`exploration_v1` is a research-only strategy layer for controlled signal exploration. It deliberately broadens strategy admission while leaving account safety, broker risk, execution arming, idempotency, reconciliation, margin, spread/slippage, loss limits, and broker preflight unchanged.

It is not registered in the production scanner.

## Trend exploration

Identity requirement: there must be a directional higher-timeframe thesis without direct H4/H1 conflict.

- Tier A: production Trend Pullback V1 conditions pass.
- Tier B: H4-led directional thesis with M15 continuation and at least pullback or reclaim evidence, even when one production confirmation is missing.
- Tier C: non-conflicting directional thesis, M15 continuation, and price still within the configured EMA20 probe distance.

Each candidate records the original failed gates plus continuous evidence such as ATR-normalized EMA slopes/separation, distance to EMA20, pullback depth, reclaim strength, candle body, and spread.

## Breakout exploration

Identity requirement: the M15 close must actually break the previous 20-bar range. This is intentionally kept hard so the family remains a breakout strategy.

Expansion, efficiency/trend alignment, extension, and cost are converted to evidence/confirmation fields instead of mandatory strategy rejection gates.

- Tier A: all four production confirmations pass.
- Tier B: at least two confirmations pass.
- Tier C: a real range break exists but fewer than two confirmations pass.

## Outcome journal

`backtest/run_exploration_v1.py` evaluates frozen datasets and writes JSONL + CSV records. Every candidate includes:

- cohort tier and failed production gates;
- feature vector at decision time;
- setup-cluster ordinal;
- 15/30/45/60/90/120-minute mark-to-market R;
- MFE and MAE in R;
- first TP/SL touch, with same-bar TP+SL reported as `AMBIGUOUS` rather than guessed.

Primary cohort summaries use only the first candidate in each same-side 30-minute setup cluster to avoid candidate inflation. All candidate-level records remain available for feature research.

## Initial 8-week finding

Across the frozen 2026-07-13..2026-08-07 OOS range and the canonical 2026-08-10..2026-09-04 range, one cohort is worth further validation:

**XAUUSD trend continuation:** production Trend Pullback would reject only because `PULLBACK_MISSING`, while H4/H1 remain aligned and reclaim/continuation evidence is present.

On the first candidate of each setup cluster, this cohort produced 24 complete 60-minute observations with a combined mean mark-to-market result of approximately `+0.167R`, while remaining positive in both monthly partitions. Weekly results are mixed, so this is a hypothesis for further OOS validation, not a production rule.

Other broad relaxations generally added noise or changed sign between IS/OOS. In particular, broad Trend tiers and time/session filters have not shown stable edge.

## Boundary

No exploration result automatically changes production thresholds. Promotion requires a separately reviewed strategy definition and fresh OOS/DEMO evidence.
