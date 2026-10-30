# Forex-AI: MT5 account-driven runtime (2026-10-30)

## Contract

Forex-AI infrastructure runs continuously. The connected MT5 broker account is the
only authority for account type, via `account_info()["trade_mode"]`:

- `0` — DEMO: simulated broker orders through the **real MT5 execution pipeline**.
- `2` — REAL: actual broker orders through **the same execution pipeline**, with
  separate, explicit owner-controlled strategy approval and account binding.
- `1` — CONTEST: no automated entries or weekend closes until separately
  authorized and implemented. Missing/invalid account modes fail closed.

`config/app.yaml: mode: TRADING` describes infrastructure, **not** an MT5 account.
Legacy `FOREX_AI_MODE` values remain accepted for compatibility but are ignored
by the active execution runner, weekend-close runner, and observer when deciding
whether an account is DEMO or REAL. Never use these values to authorize orders.

## Entry and exit safety

The execution runner detects the broker account mode from the fresh MT5 state.
For DEMO, the existing campaign ID and control checks remain mandatory. For REAL,
the existing canary strategy approval, strategy/config fingerprints, one-symbol
limit, and live trading window remain mandatory. Both require
`execution_enabled`, a matching persistent account binding, an unexpired arm,
no kill-switch, and a healthy broker/runtime state. Orders additionally pass
deterministic broker-aware risk, fresh pre-send risk, final broker preflight, and
intent idempotency.

During the same execution attempt, the initial broker account type is locked
as an expected value. Re-reading MT5 before execution or close must match it;
otherwise the action fails closed. An MT5 account change does not auto-create
a binding or grant a REAL execution approval.

`auto_live_week.py` only manages REAL-account control state; on DEMO/CONTEST
it reports `not_applicable` and performs no control-state write. If MT5
cannot be read it reports `blocked`, again without modifying shared control.
For REAL, existing approval, calendar, and manual kill controls remain in place.

The observer continues capturing broker truth regardless of configured mode,
even when an execution gate is disarmed.

## Deployment boundaries and migration

- Canonical development source: `/mnt/pc-dev/SlncTrZ/Forex-AI` (mounted from .171).
- Production runtime on .227: `~/apps/forex-ai/current`.
- No production configuration, running unit, account binding, arm state,
  database, or release symlink is modified by this source-only refactor.
- Never infer an account's type or execution authority from a directory,
  runtime environment label, broker name, or a static YAML value.
- Prior to a production rollout, review any active REAL auto-week approvals
  and manually reconcile existing `trading_control_state`; do not clear
  a kill-switch as part of a deployment.
- Validate on disposable data and mocked MT5 first, then perform an
  operator-controlled deployment with read-only telemetry checks.
  Any decision to ARM and allow REAL orders is a separate owner action.

## Tick-by-tick dashboard channel

- Only the dashboard service maintains the market-data SSE fan-out: one
  read-only MT5 adapter, `copy_ticks_range`, a bounded ring buffer and
  explicit gap/collector-health events.
- Viewer sessions never connect directly to MT5 or execute trading commands.
- The tick channel and 12-second operational polling are independent.
  The broker `time_msc` and local `received_ms` expose timing and
  clock-skew limitations; neither is an exchange-certified round-trip latency.
- History saturation, disconnection, and slow-client drops are reported instead
  of falsely claiming complete tick history. No per-tick SQLite writes.
- A dashboard deploy or reconnect cannot modify ARM, Kill Switch, bindings,
  strategy approval or open broker positions.

## Known follow-up

Legacy manual preflight commands retain their names but now evaluate explicit
DEMO or REAL eligibility independent of `cfg.mode`. Other legacy metadata
(`context_builder`, older research mode labels) can be migrated separately.
Consolidating risk/backtest sizing, snapshot reuse and timer scheduling is
out of scope for this account-type fix.
