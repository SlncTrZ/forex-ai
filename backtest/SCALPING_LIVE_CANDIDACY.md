# Scalping live candidacy review

This review is intentionally stricter than the research reports. A strategy is not considered real-money live-ready merely because it is positive on the standard eight-week dataset.

## Current conclusion

No tested scalping strategy is ready for real-money live deployment yet.

The current evidence supports three different statuses:

- `REJECT`: current definition is consistently negative or unstable.
- `RESEARCH_ONLY`: behavior is interesting but still regime/side/sample dependent.
- `SHADOW_DEMO_CANDIDATE`: sufficiently coherent to observe prospectively, but not sufficiently validated for real-money use.

There is currently no `LIVE_CANARY_CANDIDATE` among the four batch-1 scalping families.

## Standard dataset coverage

The XAUUSD eight-week dataset is not merely one uninterrupted short-term uptrend under the current causal M15 regime classifier.

OOS regime coverage:

- DOWN: 1,532 events (27.9%)
- SIDEWAYS: 2,197 events (40.0%)
- UP: 1,767 events (32.2%)

IS regime coverage:

- DOWN: 1,653 events (30.1%)
- SIDEWAYS: 2,043 events (37.2%)
- UP: 1,800 events (32.8%)

This is adequate for short-horizon regime robustness auditing, while still not replacing future untouched validation.

## Strategy review

### Inside Bar Momentum Breakout

Baseline:

- OOS expectancy: approximately `-0.022R/trade`
- IS expectancy: approximately `-0.018R/trade`
- Combined expectancy: approximately `-0.019R/trade`

A one-factor change from 45-minute expiry to 30-minute expiry improves aggregate results:

- OOS: approximately `+0.037R/trade`
- IS: approximately `+0.045R/trade`
- Combined: approximately `+0.042R/trade`
- Profit factor: approximately `1.10`

Aggregate regime results for the 30-minute variant are positive in UP, DOWN and SIDEWAYS. However side-by-regime decomposition reveals strong instability:

Combined:

- UP BUY: `+0.255R`, n=8
- UP SELL: `-0.019R`, n=16
- DOWN BUY: `-0.032R`, n=12
- DOWN SELL: `+0.071R`, n=11
- SIDEWAYS BUY: `+0.639R`, n=15
- SIDEWAYS SELL: `-0.414R`, n=20

The OOS/IS sub-cells also change sign in several trend/side combinations. Therefore the aggregate positive result is not yet evidence of a directionally robust strategy.

Status: `SHADOW_DEMO_CANDIDATE` only if kept explicitly as a research canary. Not live-ready.

### EMA Cross Scalping

Historical batch-1 definition `1.0.0` used EMA9/EMA21. Its combined expectancy was approximately `-0.077R/trade`; 30- and 60-minute expiry variants improved it but remained negative (`-0.057R` and `-0.058R` respectively).

The active research definition `1.1.0` now uses all three EMAs:

- EMA5 = crossover trigger
- EMA9 = signal line
- EMA21 = local trend line
- BUY requires a fresh EMA5 cross above EMA9 while EMA9 > EMA21
- SELL requires a fresh EMA5 cross below EMA9 while EMA9 < EMA21

On the same XAUUSD eight-week dataset with 45-minute expiry:

- OOS: approximately `-0.133R/trade`, n=170
- IS: approximately `-0.052R/trade`, n=173
- Combined: approximately `-0.092R/trade`, PF approximately `0.84`

Management checks do not rescue the 5/9/21 definition:

- expiry 30m: approximately `-0.099R/trade`
- expiry 60m: approximately `-0.089R/trade`

All three major regime buckets remain negative on the combined sample, although counter-trend trades are still the worst cohort. Therefore EMA5/9/21 is structurally cleaner than a naked pair cross but still does not demonstrate an edge.

Status: `REJECT` as a standalone live strategy. Keep its alignment/separation features available as evidence for future multi-strategy research.

### Breakout Retest

Baseline:

- OOS: approximately `+0.067R/trade`
- IS: approximately `-0.106R/trade`
- Combined: approximately `-0.030R/trade`

Changing the range length to 12 or 30 bars makes the combined result worse.

Among tested structure variants, `breakout_search_bars=10` improves combined expectancy to approximately `+0.005R/trade`, but still flips from positive OOS (`+0.081R`) to negative IS (`-0.059R`). Retest tolerance changes do not solve the instability.

Status: `RESEARCH_ONLY`. Not shadow/demo priority yet.

### Pinbar Reversal

Baseline combined expectancy is approximately `-0.053R/trade` with significant partition/regime instability. It can look positive in selected cells but does not persist across OOS/IS.

Status: `REJECT` in current definition until the remaining baseline hypothesis work is redesigned rather than parameter-mined.

## Multi-strategy agreement

Exact same-M5, same-direction agreement among the four baseline strategies is rare:

- at least two agreeing strategies: 15 cohorts total
- three agreeing strategies: 0
- four agreeing strategies: 0

The diagnostic mean component result across the 15 two-strategy cohorts is positive, but this is far too small a sample and it is not a standalone execution simulation.

Pair-specific agreement also varies widely. Some pairs are strongly negative, while apparently positive pairs have only two to five observations.

Status: `RESEARCH_ONLY`. Do not deploy a 2-of-4 voting rule from this evidence.

## Previously observed non-scalping hypothesis

XAUUSD Trend Continuation from the earlier exploration remains a useful hypothesis because it was positive in both month partitions, but it was evaluated primarily with mark-to-market outcomes and only 24 clustered setups. It has not yet been implemented and verified as an exact guarded lifecycle strategy.

Status: `RESEARCH_ONLY` until exact lifecycle validation exists.

## What can actually move toward live

The next prospective stack should not be a single strategy. It should be a collection of independent strategy identities that can abstain and later be combined by a separate portfolio/ensemble policy.

Recommended prospective candidates:

1. `inside_bar_momentum_breakout_v1` with the 30-minute management hypothesis, as a shadow/demo research canary only.
2. A new exact-lifecycle `trend_continuation_v1` implementation, preserving direction symmetry.
3. A redesigned breakout-retest candidate after structural validation, not merely parameter tuning.

EMA cross and current pinbar should not consume live-canary capacity yet.

## Promotion boundary

A strategy or ensemble becomes a `LIVE_CANARY_CANDIDATE` only after all of the following:

- fixed strategy definition and fixed config fingerprint;
- prospective untouched data after the tuning cutoff;
- positive or acceptably stable expectancy in both bullish and bearish directional regimes, or explicit abstention without destructive drawdown;
- no dependence on BUY-only or SELL-only performance;
- acceptable weekly dispersion and drawdown;
- exact lifecycle simulation, including spread-aware exits;
- shadow/demo observation with the frozen definition;
- no hidden change to risk/execution safety gates.

The eight-week dataset is the standard tuning/diagnostic dataset. It must not be reused as if it were fresh OOS after parameters are selected from it.

## Platform versus tuned universe

Platform behavior must remain symbol-agnostic. User-configured symbols are limited by broker availability and the user's risk allowlist, not by a hard-coded global platform symbol list.

The current standard research dataset remains EURUSD + XAUUSD for reproducibility. Our tuned strategy profile is intentionally XAUUSD-focused because the current strategy families consistently sit materially closer to break-even on XAUUSD than EURUSD. This is a research choice, not a system limitation.
