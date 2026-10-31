# Forex-AI Market Intelligence — v0.0.6 development candidate

**Status: implemented in development source only; NOT TAGGED, NOT DEPLOYED, NOT AUTHORIZED TO TRADE BTC.**

## Contract

- Independent `scripts/observe_market_intelligence.py` observes XAUUSDm and BTCUSDm (monitoring only) via existing MT5 adapter; the user timer runs each 5 minutes when installed and activated in a separate approved release.
- Data are per-symbol and read-only from MT5; sanitized `MARKET_INTELLIGENCE_DECISION` evidence and periodic `MARKET_INTELLIGENCE_OBSERVATION` heartbeat audits are written. Worker never calls `order_send`, touches owner control, account binding, Risk Engine, or activates strategies.
- `_closed_h1` treats MT5 bar times as candle opens; only bars whose H1 close is at or before the observation time are eligible. Quant needs at least 220 completed H1 candles; unavailable/off-session tick data cannot become a current confirmed regime.
- D3 `choice`, `confidence`, and selected `choice_probability` are separate schema fields. Probabilities must be finite, [0,1], sum to approximately 1 and the chosen class must be the maximum. Confirmation requires Quant alignment and **both confidence and chosen probability >= 0.75**. A model score is not a calibrated trade win probability.
- `REGIME_LOW_CONFIDENCE`, `REGIME_LOW_CHOICE_PROBABILITY`, `REGIME_MODEL_QUANT_CONFLICT`, malformed response and provider failures stay UNCERTAIN. Source evidence is fingerprinted by symbol + closed H1 features + model/policy. Unconfirmed classifications retry after five minutes; confirmed D3 decisions are cached by feature/model/policy while observation heartbeat is refreshed every five minutes. Policy `regime-h1-v2` invalidates old cached confirmations missing selected-choice probability.
- `/api/regimes` exposes an allowlisted audit projection (not raw model or credentials). The cockpit displays Quant, D3, confidence and selected probability separately. An audit never grants execution authority.
- Strategy eligibility is allowlisted by exact broker symbol: BTCUSD/BTCUSDm/BTCUSDc only use `btc_squeeze_breakout_m15`; approved XAU/EURUSD/USDJPY aliases exclude the BTC-only strategy. Unknown broker symbols fail closed. `scan_candidates.py` synchronizes configured trading symbols individually. A failed XAU sync creates `V1_SYMBOL_SYNC_BLOCKED`, but does not stop a separate BTC candidate scan. Every BTC candidate still uses the unchanged account binding, regime/confluence gates, Broker-Aware Risk Engine, guarded D3 pre-execution veto, order_check and send-once executor.

## Preparation for BTC strategy — not performed on production

1. Recheck broker positions and operational health at deploy time; the three previously reported BTC external/manual positions were closed by the owner and the dashboard subsequently reported zero active external positions and HEALTHY.
2. Validate broker BTC contract min lot, stop distance, spread, liquidity, market sessions and required loss budget. Current `allow_forced_min_lot: true` can exceed a nominal per-trade risk budget; do not approve live BTC entries until a strict risk budget check is evidenced. Do not alter Risk Engine or account binding implicitly.
3. After explicit release authorization, stage a reviewed private `app.yaml` trading symbol `BTCUSDm` alongside XAU, and explicitly configure/enable `btc_squeeze_breakout_m15` in the effective private strategy profile. Merely displaying BTC or having it in `risk.allowed_symbols` does not enable it.
4. Ensure `forex-ai-market-intelligence.service/.timer` are provisioned; activate timer only during approved deployment. Inspect journal and UI without exposing the owner Unix socket publicly.
5. Run a real MT5 read-only smoke, backend/UI tests, XAU-weekend/BTC-active scan regression, D3 malformed and timeout tests, owner ARM/KILL and account binding invariant tests, rollback and audit review.
6. Only after full acceptance and a separate owner go/no-go decision: create/push the immutable annotated `v0.0.6` tag at main HEAD, deploy that exact tag and verify the running release. Never retag v0.0.5 or clear owner controls.

**This file is a development implementation record; no performance uplift or profitability has been demonstrated.**


## Owner-confirmed BTC DEMO advisory admission (2026-10-31)

**Owner approved:** activate BTC strategy on the verified MT5 DEMO account and gather candidate, rejection, PnL and drawdown evidence before judging D3 thresholds. This approval does not authorize REAL-account trading.

- The base D3 closed-H1 classifier, raw evidence, model confidence and selected-choice probability are **unchanged** and recorded under `D3_REGIME_DECISION`. Low confidence is not misreported as `REGIME_CONFIRMED`.
- For BTCUSD/BTCUSDm/BTCUSDc, strategy `btc_squeeze_breakout_m15` alone, and only when the effective config mode is DEMO **and live MT5 `account_info.trade_mode == 0`**, an otherwise valid Quant/D3 agreeing UPTREND/DOWNTREND/SIDEWAY with low confidence or chosen probability is projected as `REGIME_BTC_DEMO_ADVISORY` for strategy routing/confluence. An audit `BTC_DEMO_REGIME_ADVISORY_APPLIED` records the exact exception, original reason, model/policy fingerprints and both metrics.
- Invalid schema, D3 unavailable, missing H1 warm-up, outdated data, Quant/D3 disagreement, contradictory directional trade or a different broker account mode remain vetoes. **None** of account binding, owner ARM/KILL, Risk Engine, broker contract/tick/reconciliation, D3 final pre-execution safety gate or order_send are altered.
- The published release and active private configs are still separate from source. Do not enable BTC in the private production trading symbol list until the reviewed v0.0.6 release is deployed; old v0.0.5 cannot perform isolated BTC scan.
- To enable after approved release: update effective private `app.yaml` symbols to include `BTCUSDm` and effective private `strategy.yaml` to enable `btc_squeeze_breakout_m15`. Verify the account is DEMO from MT5 (not the YAML label) and confirm all existing safety constraints.
