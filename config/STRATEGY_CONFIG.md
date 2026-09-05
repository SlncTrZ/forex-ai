# Strategy configuration and hot reload

`config/strategy.yaml` is the repository template. Production/runtime uses a persistent copy at:

`~/.config/forex-ai/strategy.yaml`

or the path specified by `FOREX_AI_STRATEGY_CONFIG`.

Deployment seeds the persistent file only when it does not exist. Subsequent deploys never overwrite the active strategy configuration.

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
