# Scalping batch 1 baseline

This batch is the first common-harness baseline on `scalping_v1`. It is research-only and does not change production scanner, risk or execution behavior.

## Frozen identities

- Dataset builder: `scalping-stream-v1`
- Dataset source fingerprint: `f0b80e86bd623cf3d75325a9ed16cd3a2ddd0cd983432c748ef0dac57cbc0a94`
- Strategy config fingerprint: `f34b3135c5894819423cf11ad01f3b77062b88b1b2ec257110c627c4aaa4ccd0`
- Config: `config/scalping-strategies.yaml`
- Decision clock: M5 close
- Strategy inputs: M5/M15/H1
- H4/D1: derived support/resistance context only
- Intrabar ambiguity policy: `stop_first`
- Maximum tolerated market gap: 30 minutes; an open scalp is closed at the previous executable quote before a larger gap.

No threshold grid or post-result tuning was used before this batch.

## Common lifecycle

Every strategy uses the same research lifecycle:

`signal -> one active trade per strategy/symbol -> spread-aware exit -> stop/target/expiry/gap -> MFE/MAE -> mark-R horizons`

Mark-R horizons are 15, 30, 45, 60, 90 and 120 minutes. If a trade has already closed, later horizons remain fixed at the realized R result.

Historical MT5 bars are bid-based. BUY entries use ask and BUY exits use bid. SELL entries use bid; SELL bar extrema/exit checks are adjusted by the observed spread to approximate ask-side execution.

## Baseline strategy identities

### `inside_bar_momentum_breakout_v1`

- M5 mother candle with minimum range/body momentum
- strict inside bar
- following M5 close breaks the mother range in the mother direction
- stop beyond the inside bar plus ATR buffer
- target 1.25R, expiry 45 minutes

### `ema_cross_scalp_v1` historical batch-1 definition (`1.0.0`)

- fresh M5 EMA9/EMA21 crossover
- no H1/H4 trend gate
- stop beyond the crossover candle plus ATR buffer
- target 1.5R, expiry 45 minutes

This section preserves the original batch-1 definition/results for reproducibility. The active research definition was later bumped to `1.1.0` and changed to EMA5/EMA9/EMA21.

### `breakout_retest_v1`

- M5 break of a 20-bar range
- retest within a configurable ATR tolerance
- subsequent close confirms continuation
- target 1.5R, expiry 60 minutes

### `pinbar_reversal_v1`

- M5 pinbar geometry
- must be near the appropriate derived H4/D1 support/resistance zone
- H4/D1 direction is not used
- target 1.5R, expiry 45 minutes

## Lifecycle baseline results

### EURUSD

| Strategy | OOS expectancy | IS expectancy | Combined trades | Combined expectancy | Combined total R | PF |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Inside Bar Momentum Breakout | -0.269R | -0.245R | 73 | -0.260R | -18.95R | 0.56 |
| EMA9/21 Cross | -0.428R | -0.590R | 581 | -0.515R | -299.08R | 0.31 |
| Breakout Retest | -0.380R | -0.534R | 400 | -0.456R | -182.29R | 0.36 |
| Pinbar Reversal | -0.632R | -0.388R | 236 | -0.529R | -124.76R | 0.31 |

All four EURUSD baselines are rejected in their current form. Their mark-R curves are already negative at short horizons, so the problem is not merely a late expiry.

### XAUUSD

| Strategy | OOS expectancy | IS expectancy | Combined trades | Combined expectancy | Combined total R | PF |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Inside Bar Momentum Breakout | -0.022R | -0.018R | 82 | -0.020R | -1.60R | 0.96 |
| EMA9/21 Cross | -0.110R | -0.044R | 467 | -0.077R | -35.88R | 0.87 |
| Breakout Retest | +0.067R | -0.106R | 415 | -0.030R | -12.42R | 0.95 |
| Pinbar Reversal | -0.027R | -0.083R | 184 | -0.053R | -9.74R | 0.91 |

No complete XAUUSD baseline is positive in both partitions. XAUUSD is nevertheless materially closer to break-even than EURUSD.

## First persistent hypothesis: XAUUSD Inside-Bar BUY

The full inside-bar strategy is approximately flat because BUY and SELL behave very differently.

| Partition | Trades | Expectancy | Total R | Win rate | PF | Max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| OOS BUY | 14 | +0.257R | +3.60R | 57.1% | 1.68 | 2.00R |
| IS BUY | 21 | +0.353R | +7.40R | 71.4% | 2.23 | 2.03R |
| Combined BUY | 35 | +0.314R | +11.00R | 65.7% | 1.98 | 2.03R |

The SELL side is negative in both partitions:

- OOS SELL: approximately `-0.216R/trade`
- IS SELL: approximately `-0.306R/trade`

XAUUSD Inside-Bar BUY mark-R also remains positive at every measured horizon:

| Partition | 15m | 30m | 45m |
| --- | ---: | ---: | ---: |
| OOS BUY | +0.321R | +0.281R | +0.257R |
| IS BUY | +0.275R | +0.348R | +0.353R |

This is the only batch-1 branch that is positive in both historical partitions. The sample is only 35 lifecycle trades, so it is a validation candidate, not a production edge claim.

## Other observations

- XAUUSD Breakout Retest BUY is strong in OOS (`+0.227R/trade`) but negative in IS (`-0.129R/trade`), so the apparent edge is regime-unstable.
- XAUUSD Pinbar SELL is positive in OOS (`+0.136R/trade`) but roughly flat/slightly negative in IS (`-0.021R/trade`).
- XAUUSD EMA cross remains negative on both partitions despite being less damaging than EURUSD.
- EURUSD SELL is generally worse than BUY across this batch, but even EURUSD BUY remains negative for every tested strategy.

## Next research rule

Do not grid-search all parameters against these same eight weeks. The next targeted work should preserve batch-1 parameters and investigate the persistent XAUUSD Inside-Bar BUY branch first:

1. feature distributions for winners vs losers;
2. mother-candle range/body, inside-bar compression and breakout distance;
3. H1 state as a feature, not a gate;
4. distance to H4/D1 support/resistance;
5. time/session behavior;
6. a small, pre-declared exit counterfactual (for example 15/30/45 minute management) only after recording the hypothesis;
7. validation on a new untouched period before any production promotion.

Research outputs:

- `/home/dinhtc/apps/forex-ai/backtest/scalping/results/batch1/batch1_report.json`
- `/home/dinhtc/apps/forex-ai/backtest/scalping/results/batch1/batch1_trades.csv`
