# Scalping V1 dataset

`scalping_v1` is a compact, immutable research dataset profile for M5-driven short-horizon strategy work.

## Scope

- Universe: `EURUSD`, `XAUUSD`
- Range: `2026-07-13 00:00 UTC` through `2026-09-05 00:00 UTC` exclusive
- Strategy timeframes: `M5`, `M15`, `H1`
- Higher-timeframe context only: `H4`, `D1`
- Anchor clock: M5 candle close
- Strategy history: 120 closed bars by default
- H4/D1 context history: from `market-context.yaml`

The two historical research partitions remain separate:

- `OOS`: after `2026-07-13 00:00 UTC` and before `2026-08-08 00:00 UTC`
- `IS`: after `2026-08-10 00:00 UTC` and before `2026-09-05 00:00 UTC`

Partition starts are strict. A decision at exactly `00:00` would use a candle opened before the partition and is therefore excluded. Sunday-open bars between OOS and IS remain in the raw dataset for causal context but have no research partition and must not contribute to OOS/IS metrics.

## Why segmented fetch exists

A full eight-week MT5 M5 `copy_rates_range` request exceeded the practical bridge/gateway limit and returned HTTP/RPyC upstream errors even though shorter broker requests succeeded.

Raw history is therefore downloaded with resumable checkpoints:

- M5: 14 calendar days per target segment
- M15: 28 days
- H1: 56 days
- H4: 180 days
- D1: 730 days

Warm-up is fetched separately and expanded automatically if the first warm-up interval does not contain enough broker bars.

Each checkpoint has:

```text
<dataset>/<symbol>/.segments/<TF>/<segment>.json
<dataset>/<symbol>/.segments/<TF>/<segment>.json.meta.json
```

The sidecar records the half-open segment window, row count and SHA-256. A retry validates the sidecar and file hash; valid checkpoints become `cache_hit=true`. Corrupt/missing checkpoints are fetched again without discarding valid siblings.

Segment boundaries are normalized to `[start, end)` and merge by broker timestamp, so overlap/inclusive behavior from `copy_rates_range` cannot duplicate bars.

## Fetch raw caches

Run one symbol/timeframe at a time. This is intentional: failure of one bridge request does not invalidate other completed slices.

```bash
FOREX_AI_RUNTIME_ROOT=~/apps/forex-ai PYTHONPATH=src \
  python backtest/fetch_scalping_raw.py --symbol EURUSD --timeframe M5
```

Repeat for `M15`, `H1`, `H4`, `D1` and then for `XAUUSD`.

A normal retry should show `cache_hit=true` for every already verified segment.

Use `--overwrite` only when the broker history slice is intentionally being replaced/refetched.

## Finalize the frozen dataset

After all ten raw files exist:

```bash
FOREX_AI_RUNTIME_ROOT=~/apps/forex-ai PYTHONPATH=src:. \
  python backtest/fetch_scalping_dataset.py --overwrite
```

This validates ordering, warm-up count, target-range boundaries, raw SHA-256 and segment provenance, then writes:

```text
~/apps/forex-ai/backtest/scalping/data/2026-07-13_2026-09-04/source_manifest.json
~/apps/forex-ai/backtest/scalping/scalping_dataset.json
```

## Dataset identity

The pointer/manifest expose two different hashes on purpose:

- `source_manifest_sha256`: hashes the exact manifest file, including `created_at_utc`; it changes every intentional finalize.
- `dataset_source_fingerprint`: hashes only deterministic research identity: builder version, date range, history length, partition definition, context-config fingerprint, symbol mapping/point and all ten raw timeframe SHA-256 values.

Re-finalizing unchanged raw caches must keep `dataset_source_fingerprint` identical even when `source_manifest_sha256` changes. A builder-semantic change must bump `builder_version` so research output cannot silently mix two event-construction contracts.

Current builder contract: `scalping-stream-v1`.

## Streaming instead of materialized replay

Do **not** materialize the full M5 dataset as generic `replay.jsonl`.

At 120 bars for M5/M15/H1, one event is roughly 49 KB. EURUSD alone would be about 536 MB, and both symbols would exceed 1 GB while repeating the same bars thousands of times. The generic replay loader would also materialize all events in RAM.

`forex_ai.research.scalping_dataset.ScalpingDataset.iter_events()` instead:

1. verifies source-manifest SHA;
2. verifies each raw timeframe SHA;
3. loads compact raw bars;
4. emits one causal M5 event at a time;
5. caches M15/H1 snapshots when their closed-bar index is unchanged;
6. extracts H4/D1 pivot clusters only when H4/D1 closed-bar identity changes;
7. cheaply reprojects cached S/R levels against each new M5 price.

This preserves deterministic replay semantics without multiplying storage or memory usage.

## Current frozen counts

Raw M5 target bars, including the unscored Sunday-open gap between partitions:

| Symbol | OOS events | IS events | Gap events | Total target M5 |
| --- | ---: | ---: | ---: | ---: |
| EURUSD | 5,721 | 5,718 | 35 | 11,474 |
| XAUUSD | 5,496 | 5,496 | 24 | 11,016 |

The gap events are deliberately not scored.

All current H4/D1 context events tested in both partitions return `READY`. Context remains evidence-only and does not alter strategy decision fingerprints or risk/execution readiness.
