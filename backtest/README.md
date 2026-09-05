# Backtest workspace

This directory contains source-controlled tooling for acquiring and replaying historical market data.

Generated datasets are not stored inside immutable release directories. Deployment creates a persistent runtime workspace at `$FOREX_AI_BACKTEST_ROOT` when set, otherwise `$FOREX_AI_RUNTIME_ROOT/backtest` (default `~/apps/forex-ai/backtest`).

## Fetch the most recently completed trading week

```bash
PYTHONPATH=src python backtest/fetch_previous_week.py
```

On Saturday/Sunday this selects the Monday-Friday week that just ended. On Monday-Friday it selects the previous full week. Use `--week-start YYYY-MM-DD` to choose another Monday.

The permanent backtest universe is `EURUSD XAUUSD` only. Data is requested from MT5 in small chunks and includes warm-up history before Monday for M15/H1/H4 indicators.

Output is written under `backtest/data/<monday>_<friday>/` and includes:

- raw broker OHLC/spread JSON for M15, H1 and H4;
- a frozen replay JSONL plus integrity manifest per symbol;
- `source_manifest.json` with date boundaries, symbol mapping, row counts and hashes.

Use `--overwrite` to intentionally rebuild an existing weekly dataset.

## Sensitivity / setup-lifecycle analysis

Run the frozen week through the V1 strategies with a one-position-per-symbol policy and a counterfactual one-trade-per-setup-cluster view:

```bash
PYTHONPATH=src python backtest/analyze_sensitivity.py \
  --dataset-root ~/apps/forex-ai/backtest/data/2026-08-31_2026-09-04 \
  --output ~/apps/forex-ai/backtest/data/2026-08-31_2026-09-04/sensitivity_report.json
```

The sensitivity tool is research-only and never changes live strategy parameters.
