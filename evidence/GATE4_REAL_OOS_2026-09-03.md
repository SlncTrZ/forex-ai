# Gate 4 Real OOS Evidence — 2026-09-03

Status: **FAIL — STRATEGY EDGE NOT YET APPROVED**

## Dataset

- Broker-history source: MT5 read-only, `EURUSDc`.
- Frozen replay schema: `forex-ai-replay-v1`.
- Records: 2,951 replay events.
- Range: 2026-07-22T18:45:00Z through 2026-09-03T12:15:00Z.
- Historical M15 spread is embedded into replay bid/ask; baseline replay does not double-count spread.
- Dataset SHA-256: `7108935cf9d41395a31767766dbc471f2264c1d34a2c4ed19deaa0695213d16c`.
- Semantic event fingerprint: `22ce6dd4a58c46cff2184a8a6decfab45e341707863621b920a52a885a082755`.
- Event construction excludes unclosed H1/H4/M15 bars at each replay clock.

## Split policy

Time-ordered 60/20/20 split:

- train: first 60%;
- validation: next 20%;
- untouched test: final 20%;
- no parameter selection was performed using the final test split in this run.

Acceptance policy used for this evidence run:

- at least 30 test trades;
- test expectancy >= 0R;
- 95% bootstrap expectancy lower bound > 0.

## trend_pullback_v1

| Split | Trades | Expectancy R | Profit factor | 95% expectancy CI |
|---|---:|---:|---:|---:|
| Train | 217 | -0.1724 | 0.5288 | [-0.2554, -0.0796] |
| Validation | 69 | +0.0698 | 1.3347 | [-0.0773, +0.2258] |
| Untouched test | 65 | **-0.0611** | **0.7913** | **[-0.2139, +0.0870]** |

Acceptance: **FAIL**

Reasons:

- `OOS_EXPECTANCY_BELOW_THRESHOLD`
- `OOS_EXPECTANCY_CI_NOT_POSITIVE`

Evidence fingerprint: `88484407378c0e29ed7712e981074c94e89eaabcdbaf8f6d8b250449f9a27838`.

### Cost sensitivity — untouched test

| Added slippage / rejection | Trades | Expectancy R | Profit factor | Max DD R-equivalent |
|---|---:|---:|---:|---:|
| 0 / 0% | 65 | -0.0611 | 0.7913 | 7.0015 |
| 1 point / 0% | 65 | -0.1121 | 0.6492 | 8.7748 |
| 3 points / 1% | 65 | -0.2141 | 0.4304 | 13.9171 |
| 5 points / 3% | 63 | -0.3145 | 0.2756 | 19.8111 |

The result degrades monotonically under cost stress.

### Monte Carlo — untouched test

5,000 deterministic bootstrap paths over the 65 test trades:

- median expectancy: -0.0619R;
- 5th/95th percentile expectancy: -0.1977R / +0.0763R;
- median max drawdown: 7.48R;
- 95th percentile max drawdown: 14.54R;
- probability of positive expectancy/net result: **23.12%**.

Conclusion: `trend_pullback_v1` is **not approved** by current real OOS evidence.

## volatility_breakout_v1

| Split | Trades | Expectancy R | Profit factor | 95% expectancy CI |
|---|---:|---:|---:|---:|
| Train | 9 | +0.0068 | 1.0917 | [-0.1114, +0.1124] |
| Validation | 8 | +0.0885 | 3.6737 | [-0.0237, +0.2027] |
| Untouched test | 2 | **-0.1796** | **0.0000** | **[-0.2784, -0.0807]** |

Acceptance: **FAIL**

Reasons:

- `INSUFFICIENT_OOS_TRADES`
- `OOS_EXPECTANCY_BELOW_THRESHOLD`
- `OOS_EXPECTANCY_CI_NOT_POSITIVE`

Evidence fingerprint: `866e99303a1c06936024d508e854a0d4ef15a0763be77db34b0f47e67537f007`.

Cost sensitivity remains negative under every tested slippage/rejection stress level. Monte Carlo on only two test trades is not statistically useful beyond confirming the observed test sample is negative; positive-path probability was 0% for the bootstrap sample.

Conclusion: `volatility_breakout_v1` is **not approved**, primarily because the sample is far too sparse and the untouched test observations are negative.

## Gate decision

The immutable real-data pipeline, walk-forward split, untouched-test execution, cost sensitivity and Monte Carlo machinery are functioning and reproducible. The **strategies do not pass Gate 4 on this dataset**.

This evidence must not be used to tune parameters on the same final-test period. Any strategy revision must be versioned and evaluated on a newly reserved untouched period or a formally extended dataset with a new final-test boundary.
