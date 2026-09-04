# Forex-AI — Audit Remediation Implementation Report

Date: 2026-09-04
Status: **WP-01..WP-07 engineering implementation complete in working tree; deployment/evidence gates pending**

## Executive result

The P0/P1 remediation plan in `AUDIT_REMEDIATION_PLAN.md` has been implemented without enabling trading. Runtime defaults remain `OBSERVE` and `execution_enabled=false`.

Validation:

- full test suite: **152/152 PASS**;
- `git diff --check`: **PASS**;
- read-only scanner-mode live MT5 resync using a temporary DB: **HEALTHY**, configured 3-symbol mapping resolved strictly, observed **19.0s** end-to-end for the resync path;
- no live order was created or sent;
- account binding was **not** auto-created.

The 19.0s measurement is one runtime sample, not a p95 claim. The scanner now persists latency telemetry so the p95 SLO can be established during soak.

## WP-01 — Persistent Account Trust Anchor

Implemented:

- persistent SHA-256 identity over broker server/login/currency;
- strict missing/invalid/mismatch/unavailable reason codes;
- constant-time fingerprint comparison;
- OBSERVE can remain readable before initial owner binding, but an existing binding is always enforced on reconnect/resync;
- execution-capable `GuardedExecutionService` requires an identity guard and fails closed when absent or failing;
- `scripts/bind_account.py` provides read-only preview by default and persists only with explicit `--confirm`;
- runtime never auto-binds the first account seen after restart.

Operational state: **binding intentionally not performed by this remediation**. The owner must verify the intended MT5 account and explicitly bind it before any execution-capable mode.

## WP-02 — Scanner Timestamp / Latency Correctness

Implemented:

- actual per-symbol capture timestamps;
- strict alias discovery filtered remotely before RPyC transfer;
- all configured symbols' info/bars fetched in one universe round-trip;
- a separate small fresh-tick bundle is fetched after bars so stale checks do not use a tick captured before a long serialization step;
- scanner fetches 51 raw bars per required timeframe, leaving exactly 50 closed bars required by Strategy V1;
- scanner skips remote broker history because realized/reference data is journal-derived; observer/reconciliation retains full history behavior;
- per-symbol and total scanner latency audit events;
- stale-candidate policy remains unchanged; TTL was not widened;
- daily broker rollover gaps are recognized only inside a narrow weekday 20:00-23:59 UTC / <=2-hour window; arbitrary intraday gaps still fail closed.

Observed progression during remediation:

- original sequential remote path: ~70+ seconds for three 200-bar symbol bundles;
- first universe 51-bar bundle: ~20.1 seconds for bars alone;
- payload trimming: ~18.1 seconds for universe bars;
- final scanner-mode resync with current positions/pending orders and no remote history: **19.0 seconds HEALTHY**.

Remaining evidence: establish p95 <20 seconds during sustained soak. A single 19.0s sample is not sufficient statistical evidence.

## WP-03 — Atomic Opportunity Identity

Implemented:

- canonical `opportunity_key` = strategy id/version + broker symbol + M15 decision timeframe + closed M15 bar time;
- candidate ID derives from stable opportunity identity + side, not volatile tick/capture/evidence payload;
- new `strategy_opportunities_v1` table with primary-key opportunity identity and unique candidate ID;
- candidate + opportunity mapping persist in the same SQLite transaction/session;
- retry of the same closed-bar opportunity cannot manufacture a new candidate because the tick moved.

Tests verify retry identity stability and one-row database persistence.

## WP-04 — Production Strategy V1 -> RiskEngine Wiring

Implemented in working tree:

- production scanner now uses `MT5ResyncCoordinator` + `DecisionOrchestrator`;
- synchronized account/contract/tick/positions/pending-orders/SafetySnapshot state feeds the decision cycle;
- journal-derived `RiskContext` supplies reference equity, realized-loss and drawdown context;
- accepted Strategy V1 candidates are persisted and passed through `BrokerAwareRiskEngine`;
- deterministic risk verdicts persist to `risk_decisions_v1`;
- account binding absence is added to the SafetySnapshot for production risk, so research candidates remain observable while risk approval fails closed;
- OBSERVE/SHADOW scanner path has no execution call.

Remaining evidence: deploy this synchronized working tree and observe real scanner cycles/candidates during soak.

## WP-05 — One Production Risk Authority

Implemented:

- legacy `limits:` block removed from `config/risk.yaml`;
- production authority is `RiskProfile` + `BrokerAwareRiskEngine`;
- architecture test rejects production imports of legacy `forex_ai.risk.engine`;
- execution consumes explicit side from `BrokerRiskResult`; side is no longer reconstructed from stop geometry.

The legacy risk module may remain for historical/research tests, but it has no production import authority.

## WP-06 — V1 AdvisoryRuntime Integration

Implemented:

- `review_pending.py` consumes unexpired V1 `candidate_decisions`, not legacy `signals`;
- no eligible V1 candidate => no API-key load, no MT5 connect, no provider call;
- advisory calls pass through `AdvisoryRuntime` cache/budget/circuit/fallback machinery;
- daily advisory budget is persisted in SQLite by provider/model/config/date and survives process restart;
- advisory and current-context schemas reject extra fields and inconsistent VERIFIED/source/web-search claims;
- legacy DeepSeek BUY/SELL/NO_TRADE reviewer is wrapped by a zero-authority compatibility adapter: every available legacy response becomes `NO_CHANGE`; it cannot create direction, lot size, REDUCE_RISK or VETO;
- advisory expiry is capped by candidate expiry.

Deliberately not enabled: native `REDUCE_RISK` / source-backed `VETO` authority. That requires a native advisory provider schema and separate validation. The compatibility bridge is intentionally conservative.

## WP-07 — Fresh Safety/Risk Revalidation Before Send

Implemented at the execution boundary:

- enabled execution requires an account identity guard;
- `BrokerRiskResult` carries explicit side;
- `send_once` requires a fresh deterministic `BrokerRiskResult` immediately before send;
- fresh approval must remain unexpired and semantically identical for candidate, side, symbol, volume, entry, SL and TP;
- any drift transitions the intent to `REJECTED` and broker send is not called;
- a second/final broker `order_check` is mandatory immediately before send;
- final trading-control/identity/reconciliation checks run again before the persistence-first send boundary;
- `SEND_STARTED` / UNKNOWN semantics remain unchanged: broker-call ambiguity is never blindly retried.

Test evidence explicitly verifies changed fresh volume => `REJECTED` / `FRESH_VOLUME_CHANGED` and zero broker-send invocation.

## Schema / migration

Journal schema version is now **10**.

Added:

- `strategy_opportunities_v1`;
- `advisory_budget_v1`.

The migration remains additive/idempotent through the existing initializer.

## Safety state after implementation

Still true:

- mode = `OBSERVE`;
- `execution_enabled=false`;
- no LLM execution tool;
- no automatic account binding;
- no order intent was created by this remediation;
- no live broker order was sent;
- current Strategy V1 research evidence remains FAIL on untouched OOS;
- Live = **NO-GO**.

## Remaining gates — not code-remediation failures

These require operational/evidence work and are intentionally not marked complete by this implementation:

1. Deploy synchronized remediation release and verify timer/service behavior.
2. Continue soak and establish scanner latency p95 from telemetry.
3. Explicit owner account binding before execution-capable modes.
4. Host reboot / disaster-recovery drill.
5. Real DEMO execution fault campaign (>=100 complete broker lifecycles including timeout-but-accepted, restart/UNKNOWN, partial fill, orphan, protection failure).
6. Strategy V2 research with a new untouched future OOS boundary; do not retune against the consumed final-test window.
7. BOT_ONLY vs BOT_LLM shadow evidence if LLM value is still being evaluated.
8. Formal Go/No-Go, then one-symbol LIVE_CANARY only after every gate passes and the owner explicitly approves it.

## Files / major components changed

- `AUDIT_REMEDIATION_PLAN.md`
- `AUDIT_REMEDIATION_REPORT.md`
- `SYSTEM_COMPLETION_PLAN.md`
- `STATUS.md`
- `config/risk.yaml`
- `scripts/bind_account.py`
- `scripts/scan_candidates.py`
- `scripts/review_pending.py`
- `src/forex_ai/risk/account_guard.py`
- `src/forex_ai/risk/broker_engine.py`
- `src/forex_ai/runtime/resilience.py`
- `src/forex_ai/runtime/risk_context.py`
- `src/forex_ai/mt5/client.py`
- `src/forex_ai/strategy/v1/contracts.py`
- `src/forex_ai/journal/db.py`
- `src/forex_ai/journal/integration_repository.py`
- `src/forex_ai/advisory/runtime.py`
- `src/forex_ai/advisory/budget_store.py`
- `src/forex_ai/integration/deepseek_advisory_provider.py`
- `src/forex_ai/integration/execution.py`
- `src/forex_ai/integration/execution_guards.py`
- `src/forex_ai/intelligence/schemas.py`
- remediation/runtime/execution tests.
