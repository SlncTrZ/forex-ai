# Backtest workspace

This directory contains source-controlled tooling for acquiring and replaying historical market data.

Generated datasets are not stored inside immutable release directories. Deployment creates a persistent runtime workspace at `$FOREX_AI_BACKTEST_ROOT` when set, otherwise `$FOREX_AI_RUNTIME_ROOT/backtest` (default `~/apps/forex-ai/backtest`).

## Fetch the most recently completed trading week

```bash
PYTHONPATH=src python backtest/fetch_previous_week.py
```

On Saturday/Sunday this selects the Monday-Friday week that just ended. On Monday-Friday it selects the previous full week. Use `--week-start YYYY-MM-DD` to choose another Monday.

The default universe is `EURUSD GBPUSD XAUUSD`. Data is requested from MT5 in small chunks and includes warm-up history before Monday for M15/H1/H4 indicators.

Output is written under `backtest/data/<monday>_<friday>/` and includes:

- raw broker OHLC/spread JSON for M15, H1 and H4;
- a frozen replay JSONL plus integrity manifest per symbol;
- `source_manifest.json` with date boundaries, symbol mapping, row counts and hashes.

Use `--overwrite` to intentionally rebuild an existing weekly dataset.
