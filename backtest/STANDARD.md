# Canonical backtest standard

Forex-AI uses one immutable four-completed-trading-week dataset as the default research baseline.

Canonical policy:

- Universe: `EURUSD`, `XAUUSD` only.
- Range: four complete Monday-Friday trading weeks, ending with the most recently completed/closing Friday.
- Timeframes: M15, H1, H4.
- Warm-up: 60 bars per timeframe before the range.
- Replay history: 60 bars.
- Source: broker MT5 OHLC/spread history.
- Integrity: raw-file SHA-256, frozen replay manifest/hash, and a hashed `source_manifest.json`.
- Position comparison: at most one open position per symbol.
- Setup-lifecycle comparison: candidate bursts with the same side and no more than 30 minutes between candidates are also evaluated as one setup cluster.
- Strategy parameters in the canonical baseline remain the production V1 defaults. Sensitivity results are research evidence only and never mutate live parameters automatically.

Create/replace the standard dataset intentionally:

```bash
PYTHONPATH=src python backtest/fetch_previous_week.py --weeks 4 --overwrite --mark-standard
```

Because MT5 bridge calls can be slow, it is valid to fetch one symbol at a time and mark the standard only after both exist:

```bash
PYTHONPATH=src python backtest/fetch_previous_week.py --weeks 4 --symbols EURUSD --overwrite
PYTHONPATH=src python backtest/fetch_previous_week.py --weeks 4 --symbols XAUUSD --overwrite --mark-standard
```

The canonical pointer is persisted outside immutable releases at:

`$FOREX_AI_BACKTEST_ROOT/standard_dataset.json`

or, by default:

`~/apps/forex-ai/backtest/standard_dataset.json`

With that pointer present, the standard sensitivity run requires no dataset path:

```bash
PYTHONPATH=src python backtest/analyze_sensitivity.py
```

## Canonical baseline run

Run production V1 defaults against the canonical dataset without parameter optimization:

```bash
PYTHONPATH=src python backtest/run_standard_backtest.py
```

The result is written to `<standard-dataset>/standard_benchmark.json` and is the regression baseline for future strategy changes.
