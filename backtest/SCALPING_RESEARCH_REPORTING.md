# Scalping Research Reporting Standard

Every scalping experiment must leave enough evidence to reproduce the test, understand what changed, and decide whether the configuration is worth carrying into the next prospective observation window.

## Per-run outputs

`backtest/run_scalping_experiment.py` writes these artifacts for every run:

- `resolved_config.yaml` — exact validated config used by the run;
- `report.json` — full machine-readable research report;
- `run_report.md` — human-readable detailed report;
- `trades.csv` — per-trade records unless `--no-trades-csv` is used.

The detailed report records:

- dataset source fingerprint and builder version;
- strategy config fingerprint;
- fixed and matrix parameter overrides;
- account-risk assumptions and global active-position cap when portfolio replay is used;
- combined, OOS and IS expectancy/win rate;
- profit factor and max drawdown in R;
- stop-hit and target-hit rates;
- MFE/MAE;
- signal generated/accepted/blocked counts;
- exit reasons and market-close exits;
- regime breakdown;
- normalized account-return simulation when portfolio replay is used.

Research status is descriptive only:

- `POSITIVE_BOTH_PARTITIONS`
- `POSITIVE_BOTH_PARTITIONS_LOW_MARGIN`
- `POSITIVE_COMBINED_PARTITION_MIXED`
- `PARTITION_FLIP`
- `NEGATIVE_SAMPLE`

A status is not a live-promotion decision.

## Per-experiment outputs

Every experiment directory also contains:

- `experiment_index.json`
- `summary.csv`
- `experiment_summary.md`

The experiment summary compares matrix runs without hiding OOS/IS disagreement.

## Weekly review

Use `backtest/build_scalping_weekly_review.py` with an explicit curated list of completed experiment directories. The weekly review refuses to combine different dataset fingerprints.

Outputs:

- `weekly_review.md`
- `weekly_review.json`
- `weekly_candidates.csv`

Candidate rows are deduplicated by symbol, strategy fingerprint, risk/trade and global max-active setting so the same strategy definition is not counted twice merely because it appeared in both a batch and a strategy-specific experiment.

The weekly review ranks descriptive evidence but does not modify active strategy or risk configuration. Parameter application for the next week is a separate explicit decision.

## Selection boundary

When choosing parameters for the next week:

1. Prefer configurations that remain acceptable across both OOS and IS rather than those with the highest combined expectancy or win rate.
2. Treat the current eight-week dataset as tuning/diagnostic data once a parameter is selected from it.
3. Freeze selected strategy-specific stop/target definitions before the next observation window begins.
4. Validate selected definitions prospectively in shadow/demo observation before any later live-promotion decision.
5. Keep account risk sizing separate from stop geometry: changing risk percentage changes position size; changing stop structure/buffer changes the price distance to invalidation.
6. Do not automatically promote a configuration from this reporting pipeline.
