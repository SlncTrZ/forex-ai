# Backtest workspace

This directory contains source-controlled tooling for acquiring and replaying historical market data.

Generated datasets are not stored inside immutable release directories. Deployment creates a persistent runtime workspace at `$FOREX_AI_BACKTEST_ROOT` when set, otherwise `$FOREX_AI_RUNTIME_ROOT/backtest` (default `~/apps/forex-ai/backtest`).

## Fetch historical trading weeks

```bash
PYTHONPATH=src python backtest/fetch_previous_week.py
```

The default remains one completed Monday-Friday week for compatibility. Use `--weeks N` for a larger immutable range. On Saturday/Sunday the latest range ends with the Friday that just closed; on Monday-Friday it ends with the previous full Friday. Use `--week-start YYYY-MM-DD` to pin the first Monday.

The permanent backtest universe is `EURUSD XAUUSD` only. Data is requested from MT5 in small chunks and includes warm-up history before Monday for M15/H1/H4 indicators.

Output is written under `backtest/data/<first-monday>_<last-friday>/` and includes:

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

## Canonical 4-week standard

Forex-AI standardizes strategy comparison on four complete trading weeks. See `STANDARD.md` for the full contract.

Create/update the canonical dataset:

```bash
PYTHONPATH=src python backtest/fetch_previous_week.py --weeks 4 --overwrite --mark-standard
```

This writes a persistent `standard_dataset.json` pointer. Once it exists, sensitivity analysis defaults to that dataset automatically:

```bash
PYTHONPATH=src python backtest/analyze_sensitivity.py
```

## Resume after a bridge timeout

If MT5 raw M15/H1/H4 files were already written but freezing the replay was interrupted, rebuild without downloading history again:

```bash
PYTHONPATH=src python backtest/fetch_previous_week.py \
  --week-start 2026-08-10 --weeks 4 --symbols XAUUSD \
  --reuse-raw --overwrite --mark-standard
```

For long sensitivity runs it is also valid to run one standard symbol at a time with `--symbols EURUSD` or `--symbols XAUUSD`; both still resolve and verify the same canonical `standard_dataset.json`.

## Canonical baseline run

Run production V1 defaults against the canonical dataset without parameter optimization:

```bash
PYTHONPATH=src python backtest/run_standard_backtest.py
```

The result is written to `<standard-dataset>/standard_benchmark.json` and is the regression baseline for future strategy changes.
