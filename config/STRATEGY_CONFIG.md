# Strategy configuration and hot reload

`config/strategy.yaml` is the repository template. Production/runtime uses a persistent copy at:

`~/.config/forex-ai/strategy.yaml`

or the path specified by `FOREX_AI_STRATEGY_CONFIG`.

Deployment normally preserves the persistent strategy file. When `config/live-prospective-approval.json` is present and its approved production fingerprint exactly matches the repository strategy snapshot, deployment atomically syncs the approved `config/strategy.yaml` into the persistent runtime file. A fingerprint mismatch aborts deployment instead of silently changing live strategy parameters.

## Hot reload semantics

The candidate scanner and guarded execution runner are systemd `oneshot` jobs. Every invocation loads and validates the strategy YAML again. Therefore an atomic edit to the runtime YAML takes effect on the next scan/run without restarting a service.

- scanner cadence: approximately 30 seconds
- execution-runner cadence when enabled: approximately 15 seconds

No file watcher, inotify process or SIGHUP daemon is required.

## Last-known-good behavior

A valid runtime configuration is copied atomically to `strategy.last-good.yaml` beside the active file.

If a later edit is invalid:

- the new file is rejected;
- the last-known-good snapshot remains active;
- the scanner journals `STRATEGY_CONFIG_RELOAD_REJECTED`;
- execution safety is not relaxed.

When an explicit config path is supplied to research/test code, invalid config fails directly instead of silently falling back.

## Fingerprints

Every compiled `StrategyConfig` has a deterministic SHA-256 fingerprint based on strategy id/version and parameters.

Candidates persist `strategy_config_fingerprint`, so an old candidate remains causally tied to the parameters that created it even after a hot reload.

LIVE_CANARY approval is also bound to the aggregate production-strategy fingerprint. Changing production strategy YAML invalidates an older approval until a new approval explicitly references the new fingerprint.

`strategy_version` and `strategy_config_fingerprint` are intentionally separate:

- version changes when strategy logic/contract changes;
- config fingerprint changes when parameter values change.

## Parameter rules

Strategy evaluator code contains algorithm logic only. Tactical values such as EMA periods, ATR periods, range lookbacks, expansion thresholds, efficiency thresholds, target R, expiry and structure lookbacks are loaded from YAML and validated before use.

The scanner derives its MT5 history requirement from active parameters. For example, changing a slow EMA from 50 to 100 automatically increases requested raw history rather than requiring a source-code change.


## 2026-W37 prospective freeze

The repository template currently freezes three XAUUSD strategies for the next prospective observation window:

- `inside_bar_momentum_breakout_v1`: M5, stop buffer `0.10 ATR`, target `1.75R`, expiry `360m`;
- `breakout_retest_v1`: M5, stop buffer `0.50 ATR`, target `1.50R`, expiry `360m`;
- `trend_pullback_v1`: EMA `20/50`, pullback `0.60 ATR`, stop buffer `0.40 ATR`, structure lookback `5`, target `2.0R`, expiry `45m`.

`volatility_breakout_v1` remains available in the platform but is disabled in the prospective production set.

The live deployment profile is XAUUSD-only and remains fail-closed at rest: `execution_enabled: false` in the repository, `1%` maximum risk per trade, one active order, `1%` total/correlated open risk, plus the existing daily/weekly/drawdown gates. Real execution is enabled only inside the dedicated LIVE_CANARY systemd units and still requires the approved fingerprint, account binding, real-account mode, healthy runtime state, and an armed trading-control state.

`forex-ai-auto-live-week.timer` continuously enforces the weekly state machine rather than relying on a manual Monday action. Its active window starts Sunday 17:05 ET (about Monday 04:05 in Vietnam while New York is on EDT) and runs through Friday 16:00 ET. The controller still waits for fresh broker ticks and healthy runtime state before arming, restores the arm after a safe reboot or transient preflight failure, and disarms outside that window. Manual kill-switch and maintenance states always override automation and are never cleared automatically.

M5 is now part of the production market snapshot because the Inside Bar and Breakout Retest strategies make M5 decisions. Same-scan tie-break priority is deterministic: Inside Bar, then Breakout Retest, then Trend Pullback. Once one candidate is approved, it is represented as an in-scan pending exposure so later candidates are risk-rejected and journaled instead of creating duplicate XAU exposure.

Friday schedule guard uses `America/New_York` to remain DST-aware: new entries stop at 16:00 ET and managed Forex-AI positions are eligible for guarded forced close from 16:30 ET. External/manual broker positions are never closed by that path.

Research remains separate from this freeze. `config/scalping-strategies.yaml`, `backtest/run_scalping_experiment.py`, and `backtest/run_trend_pullback_sweetspot.py` can continue to test the three families, but research parameter changes do not mutate the prospective production YAML automatically.
