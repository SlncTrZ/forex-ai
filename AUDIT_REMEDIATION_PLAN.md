# Forex-AI — P0/P1 Remediation & Production Integration Plan

Status: WP-01..WP-07 IMPLEMENTED IN WORKING TREE — deployment/evidence gates remain active. See `AUDIT_REMEDIATION_REPORT.md`.

## Objective

Complete one coherent production decision graph while preserving fail-closed defaults:

```text
Persistent Trust Anchors
        -> MT5 Resync / SafetySnapshot
        -> MarketSnapshot
        -> Strategy
        -> Unique Opportunity
        -> Deterministic Prefilter
        -> Optional Advisory
        -> BrokerAwareRiskEngine
        -> Persisted Risk Verdict
        -> OBSERVE / SHADOW STOP
        -> Fresh Safety + Risk Revalidation
        -> Execution
        -> Reconciliation
        -> Protection Verification
```

Non-negotiable invariants:

- One production RiskEngine and one authoritative risk policy.
- One database-enforced opportunity identity for a strategy/symbol/decision bar.
- Account identity is explicitly bound and persists across process restarts.
- The LLM cannot create direction, lot size, or execution authority.
- Risk decisions are persisted before execution.
- OBSERVE/SHADOW never create an order intent or call `order_send`.
- Safety and monetary risk are refreshed immediately before an execution-capable send.
- UNKNOWN execution outcomes are reconciled, never blindly retried.
- Live progression remains blocked until a strategy version passes new untouched OOS evidence.

## Work packages

### WP-01 — Persistent Account Trust Anchor (P0)

- Make persistent account binding an explicit trust anchor instead of trusting the first account observed after restart.
- Enforce at startup/resync, before risk evaluation, and before execution-capable operations.
- Stable failure reasons: `ACCOUNT_BINDING_MISSING`, `ACCOUNT_BINDING_INVALID`, `ACCOUNT_IDENTITY_MISMATCH`, `ACCOUNT_IDENTITY_UNAVAILABLE`.
- Never auto-bind in production runtime.

Acceptance: restart/reconnect on a different account remains blocked.

### WP-02 — Scanner Timestamp / Latency Correctness (P0)

- Timestamp each symbol at actual capture/evaluation time.
- Instrument per-symbol and total scan latency.
- Cache only stable MT5 metadata and invalidate on reconnect/drift.
- Preserve stale-candidate rejection; do not widen TTL to hide latency.
- Target p95: <10s/symbol and <20s/full configured scan.

Acceptance: later symbols cannot inherit an early scan timestamp and stale proposals cannot pass risk.

### WP-03 — Atomic Opportunity Identity (P1/P0 enabler)

- Add canonical `opportunity_key` based on strategy id/version, base symbol, decision timeframe and closed decision-bar time.
- Enforce uniqueness in SQLite.
- Persist strategy verdict/candidate under a transaction-safe identity so crash/retry cannot create a second tradable opportunity.

Acceptance: concurrent or crash-retry evaluation of one opportunity produces one business identity.

### WP-04 — Production Strategy -> Risk Wiring (P0)

- Build one deployed read-only production decision path.
- Persist Strategy V1 candidate/rejection and deterministic `BrokerAwareRiskEngine` verdict from the same decision cycle.
- Build RiskContext from live account, contract, positions, pending orders, safety snapshot and reference-equity state.
- Add correlation chain: cycle -> opportunity -> candidate -> risk.
- OBSERVE/SHADOW must stop after persistence and never create order intents.

Acceptance: a synthetic valid setup through the production scanner persists both candidate and risk verdict while `order_intents_v1` is unchanged.

### WP-05 — Single Risk Authority (P1)

- Production code uses only `BrokerAwareRiskEngine` + `RiskProfile`.
- Remove/deprecate the legacy `limits` policy vocabulary and production imports of `risk.engine.RiskEngine`.
- Persist profile/config provenance with each risk decision.

Acceptance: exactly one authoritative production risk policy/engine exists.

### WP-06 — V1 AdvisoryRuntime Integration (P0/P1)

- Stop using legacy `signals` as the production LLM queue.
- Review only eligible V1 candidates after deterministic prefilter.
- LLM output authority is advisory only: `NO_CHANGE`, `REDUCE_RISK`, policy-backed `VETO`.
- BOT_ONLY remains available on provider failure/budget exhaustion.
- Make provider schemas strict and budget accounting persistent/day-scoped.

Acceptance: zero paid calls without eligible V1 candidates; legacy BUY/SELL/NO_TRADE cannot enter production RiskEngine.

### WP-07 — Fresh Safety/Risk Revalidation Before Send (P0)

- Treat a risk approval as a short-lived lease, not permanent permission.
- Immediately before send-capable operations refresh account binding, broker state, tick, positions/orders, reconciliation and monetary risk.
- Re-run deterministic risk validation before `order_check`/`order_send`; material change rejects/rebuilds the intent.
- Final control check immediately before persistence-first `SEND_STARTED` and `order_send`.

Acceptance: account switch, new exposure, spread spike, price drift, margin drop, arm expiry, kill switch, contract/risk-profile change all result in NO SEND.

## Gate sequence after WP-01..07

1. Full integration/fault-test suite.
2. Synchronized OBSERVE deployment.
3. Complete Gate 1 soak and host-reboot DR.
4. Guarded DEMO campaign with >=100 complete broker lifecycles and required fault scenarios.
5. Strategy V2 research using train/validation only, then new untouched future OOS evidence.
6. SHADOW BOT_ONLY vs BOT_LLM counterfactual evidence.
7. Formal Go/No-Go.
8. One-symbol LIVE_CANARY only after all gates and explicit owner approval.

## Definition of engineering done

- Persistent account trust anchor enforced.
- Production scanner runs Strategy -> Risk end-to-end.
- Database-enforced unique opportunity identity.
- One production RiskEngine / one production risk policy.
- V1 candidate stream feeds AdvisoryRuntime; legacy signal/LLM path has no production authority.
- OBSERVE/SHADOW cannot create execution intents.
- Safety and monetary risk are revalidated immediately before send.
- Every send remains persistence-first and UNKNOWN never blind-retries.
- Reconciliation/protection survive restart.
- Complete release/config/account/market/opportunity/candidate/advisory/risk/intent/broker audit chain is reconstructable.
- Full tests pass with no unresolved P0/P1 safety/integration defect.

## Live remains NO-GO until

- Gate 1 soak PASS.
- Host reboot DR PASS.
- Gate 3 real DEMO campaign PASS.
- Strategy version PASS on new untouched OOS after realistic costs.
- Strategy approval artifact exists.
- Account binding and owner-approved RiskProfile exist.
- LIVE_CANARY readiness passes.
- Explicit owner Go decision is recorded.
