# Forex-AI — System Completion Plan

Status: **ACTIVE — remediation implementation updated 2026-09-04; deployment/evidence gates still pending**

This plan is the execution checklist for completing the trading system. `DEVELOP_PLAN.md` remains the architectural specification/history. Strategy profitability/tuning is tracked separately from system correctness.

### 2026-09-04 remediation implementation status

The working tree now implements the audited WP-01..WP-07 engineering remediations documented in `AUDIT_REMEDIATION_PLAN.md`:

- persistent account binding verification and owner-only binding command;
- per-symbol capture timestamps, slim strict symbol discovery and one-universe MT5 scan bundle;
- database-enforced opportunity identity and stable retry candidate identity;
- production V1 Strategy -> `BrokerAwareRiskEngine` scanner wiring with persisted risk verdicts;
- one production `RiskProfile` vocabulary (`limits` removed from production config);
- V1 candidate -> `AdvisoryRuntime` queue with persistent daily budget and a no-trade-authority legacy DeepSeek bridge;
- explicit risk side plus mandatory fresh risk revalidation and final broker preflight before send.

Validation in the working tree: **152 tests pass** and `git diff --check` passes. A read-only scanner-mode broker resync with a temporary DB completed HEALTHY in **19.0s** for the configured three-symbol universe. This is one observed runtime sample, not yet a p95 SLO claim.

These changes are not equivalent to live approval. Deployment/soak evidence, explicit account binding, DEMO execution fault campaign, host-reboot DR and new untouched strategy OOS evidence remain separate gates. `execution_enabled: false` remains unchanged.

## 1. Audited current state

### Source / release

- `main` is clean and synchronized with `origin/main` at `9bd6b71ede16eb6b03d3b0f08d293f0e729d53ed`.
- Active release: `/home/dinhtc/apps/forex-ai/releases/20260903T132901Z-9bd6b71ede16`.
- Last audited deployed release had 144 tests; current remediation working tree has **152 tests and the full suite passes**.
- Deployment/rollback hardening is implemented and prior target-host drills passed.

### Runtime

- `forex-ai-observe.service`: active.
- DB `PRAGMA integrity_check`: `ok`.
- Recent runtime heartbeat cycles: `SYNCING -> HEALTHY`.
- Health timer: active every minute.
- DB backup timer: active daily at 03:15 local time.
- Candidate scan timer: active.
- Review-pending timer: active.
- Trading control row: absent, therefore the code default is disarmed + kill switch active.
- `execution_enabled: false` in `config/risk.yaml`.
- `order_intents_v1`: empty.

### Production decision pipeline

Production Strategy V1 scanning is now wired and deployed:

- `trend_pullback_v1`
- `volatility_breakout_v1`
- accepted setup -> `candidate_decisions`
- rejected setup -> `V1_STRATEGY_REJECTED` with stable reason codes
- one canonical verdict per `strategy + symbol + closed M15 candle`
- forming candle excluded
- legacy signal scanner disabled by default in the production candidate timer

Current journal facts:

- `candidate_decisions`: 0
- `risk_decisions_v1`: 0
- production V1 rejection audit rows exist and include `REGIME_NOT_ALIGNED`, `NO_RANGE_BREAK`, and earlier `OVEREXTENDED_BREAKOUT` evidence.

The deployed release described above remains Strategy-only. The remediation working tree now executes the complete read-only Strategy -> RiskEngine decision graph; deployment verification is still pending.

## 2. Critical findings from the audit

### P0 — Strategy -> RiskEngine live wiring — IMPLEMENTED, DEPLOYMENT EVIDENCE PENDING

The remediation working tree now routes the production V1 scanner through `DecisionOrchestrator` + `BrokerAwareRiskEngine`, using synchronized broker state, `SafetySnapshot` and journal-derived `RiskContext`, and persists deterministic `risk_decisions_v1`. OBSERVE/SHADOW still stop before execution and `execution_enabled=false` remains unchanged.

Remaining gate: deploy the synchronized release and prove the path on real V1 candidate/rejection cycles during soak.

### P0 — Candidate-scan latency must be bounded before RiskEngine integration

The first bundled live scan measured about 39 seconds for the configured three symbols. The active RiskProfile has `max_signal_age_seconds: 30`.

Impact: using one scan-start timestamp across all symbols can make later-symbol decisions stale before RiskEngine evaluation.

Required fix:

- timestamp each symbol at its actual capture/evaluation time;
- retain one MT5 bundle call per symbol;
- remove unnecessary symbol/constants round-trips through caching or startup resolution;
- set and measure a scanner latency SLO; target p95 end-to-end scan latency < 20 seconds for all configured symbols and < 10 seconds per symbol;
- persist scan latency and reject stale candidates deterministically.

### P0 — Gate 5 candidate wiring — IMPLEMENTED WITH ZERO-AUTHORITY COMPATIBILITY BRIDGE

The remediation working tree moves `review_pending.py` from legacy `signals` to unexpired V1 `candidate_decisions`, uses `AdvisoryRuntime`, persists daily budget state, and makes zero provider calls when no eligible V1 candidate exists. The existing legacy DeepSeek BUY/SELL/NO_TRADE schema is wrapped by a compatibility adapter that collapses every available response to advisory `NO_CHANGE`; it cannot create direction, size, REDUCE_RISK or VETO authority.

Remaining gate: a native source-backed `NO_CHANGE/REDUCE_RISK/VETO` provider schema may be added later only after separate validation. The compatibility bridge is deliberately safer than granting the legacy model production authority.

### P1 — Risk policy ambiguity — IMPLEMENTED

`config/risk.yaml` now has one production `profile` authority; the legacy `limits` block is removed. An architecture invariant test rejects production imports of `forex_ai.risk.engine`; production risk remains `BrokerAwareRiskEngine` + `RiskProfile`.

### P1 — Master plan is stale after the V1 scanner repair

`DEVELOP_PLAN.md` still lists 139 tests and older release identifiers/status wording. It also overstates some Gate 5 integration readiness.

Required fix: reconcile it only after the P0 wiring changes above, so the master status reflects the actual deployed graph rather than code modules in isolation.

## 3. Execution sequence

### Phase A — Close production decision wiring (do now, during Gate 1 soak)

1. Optimize live MT5 scan latency and timestamp semantics.
2. Build one read-only `production_decision_scan` path using the existing `DecisionOrchestrator`.
3. For each configured symbol, capture atomically enough for one decision cycle:
   - tick;
   - required closed M15/H1/H4 bars;
   - account snapshot;
   - symbol contract;
   - positions + pending orders;
   - current safety snapshot/reconciliation state;
   - realized/open-risk context.
4. Evaluate Strategy V1 and persist every strategy rejection.
5. Persist accepted candidates.
6. Run deterministic RiskEngine for accepted candidates and persist every approval/rejection in `risk_decisions_v1`.
7. Do **not** create an order intent in OBSERVE/SHADOW.
8. Add audit chain IDs so one opportunity can be traced:
   `market clock -> strategy verdict -> candidate -> risk verdict -> advisory/counterfactual`.
9. Add integration tests for a valid synthetic candidate proving `candidate_decisions` and `risk_decisions_v1` are both persisted while execution remains untouched.

**Phase A acceptance**

- A deliberately constructed valid setup produces a persisted candidate and persisted risk verdict through the same deployed scanner path.
- A rejected setup has a stable reason code.
- A degraded/stale state produces no candidate/risk approval.
- `order_intents_v1` remains unchanged in OBSERVE/SHADOW.
- p95 scan latency satisfies the defined SLO.

### Phase B — Replace legacy LLM shadow wiring

1. Disable `forex-ai-review-pending.timer` from consuming legacy signals as the normal production shadow workflow.
2. Add V1 candidate advisory queue/selection.
3. Apply deterministic risk/calendar/spread prefilter before paid review.
4. Use the tested AdvisoryRuntime cache/budget/circuit-breaker layer.
5. Persist BOT_ONLY baseline for every eligible candidate.
6. Persist BOT_LLM advisory and counterfactual separately.
7. Provider failure/budget exhaustion must yield BOT_ONLY-compatible `NO_CHANGE`, never invent trade direction or size.

**Phase B acceptance**

- Zero paid calls when there is no eligible V1 candidate.
- Legacy BUY/SELL/NO_TRADE output cannot enter RiskEngine.
- Provider timeout/budget exhaustion leaves deterministic BOT_ONLY path available.
- Candidate/advisory/risk records share the same correlation chain.

### Phase C — Configuration and operational cleanup

1. Collapse risk config to one authoritative production schema.
2. Add config provenance/fingerprint to candidate/risk/advisory audit records where missing.
3. Measure candidate scanner/service duration and timer behavior continuously.
4. Add scanner-stalled/overrun alert.
5. Reconcile `DEVELOP_PLAN.md`, README/status docs and test count.
6. Keep legacy scanner available only behind an explicit research flag.

**Phase C acceptance**

- One authoritative risk profile.
- No production service imports legacy risk/signal decision authority.
- Full suite + deploy smoke pass.
- Runtime source/release/config fingerprints are traceable.

### Phase D — Finish Gate 1 and Gate 6 validation

Gate 1 soak remains time-based and must not be shortened.

- Soak start: `2026-09-03 17:54:40 +07`.
- Earliest review: `2026-09-10 17:54:40 +07`.
- Review full interval for unreconciled data loss, account/contract drift, duplicate/lost broker facts, stuck degraded state, journal failure, stale-decision leakage and unexpected execution.

After Gate 1 is closed:

1. perform host-reboot DR;
2. verify MT5, observer, timers and scanner recover automatically;
3. verify startup reconciliation occurs before any trade-capable state;
4. verify DB integrity and no duplicate/lost facts;
5. complete owner alert transport choice if an external webhook/mail transport is desired.

**Phase D acceptance**

- Gate 1 = PASS only after the complete seven-day interval is clean.
- Gate 6 = PASS after host reboot recovery/reconciliation evidence is recorded.

### Phase E — Gate 3 broker execution validation

This is separate from strategy profitability.

Use the guarded execution path in a DEMO environment for:

- accepted/rejected `order_check`/`order_send` retcodes;
- timeout-but-accepted reconciliation;
- partial fills;
- restart mid-lifecycle;
- duplicate/idempotency test;
- orphan broker state;
- missing SL/TP repair;
- emergency close;
- at least 100 complete DEMO lifecycles.

**Phase E acceptance**

- zero duplicate exposure;
- no unresolved UNKNOWN/orphan state;
- every broker fact maps to one intent or explicit orphan alert;
- protection failure blocks new entries and triggers the configured recovery path.

### Phase F — Strategy tuning/evidence (separate workstream)

Current V1 strategy evidence remains FAIL. Do not rewrite that fact as a system defect.

- `trend_pullback_v1`: untouched test expectancy negative.
- `volatility_breakout_v1`: insufficient test sample and negative observed test result.

For any tuning:

1. create a new strategy/config version;
2. use train/validation only for tuning;
3. reserve a new untouched period or formally extend the dataset with a new final boundary;
4. repeat realistic-cost sensitivity and Monte Carlo;
5. create a strategy-approval artifact only if acceptance passes.

No production approval may be created from the already-consumed final-test window.

### Phase G — Rollout

Rollout stages remain:

1. OBSERVE — current.
2. SHADOW — complete production V1 BOT_ONLY/BOT_LLM evidence.
3. DEMO — complete execution lifecycle evidence.
4. LIVE_CANARY — only after system gates and strategy approval satisfy the configured release policy.
5. GUARDED_LIVE — expand only after canary operational evidence.

The small account may be useful as a later owner-controlled canary for broker/runtime learning, but it must not be used to bypass missing deterministic risk, execution, reconciliation or strategy-approval gates.

## 4. Gate status after this audit

| Gate | Accurate current status | Remaining blocker |
|---|---|---|
| Gate 0 Release | **PASS** | Maintain invariant |
| Gate 1 MT5/data | **Maintenance PASS; soak running** | Seven-day interval review |
| Gate 2 Risk core | **Code/read-only tests PASS** | Production timer integration must exercise it on real V1 candidates |
| Gate 3 Execution | **Engineering/fake-fault PASS** | Real DEMO lifecycle/fault evidence |
| Gate 4 Strategy | **Research pipeline PASS; current strategies FAIL** | New strategy version + new untouched evidence after tuning |
| Gate 5 Advisory | **Module engineering PASS; live integration INCOMPLETE** | Replace legacy review queue with V1 candidate advisory path |
| Gate 6 Ops/DR | **Deployed except host reboot** | Host-reboot reconciliation drill after soak |
| Gate 7 Rollout | **BLOCKED** | Gates 1/3/4/5/6 acceptance |

## 5. Definition of system-complete vs strategy-approved

### System-complete

The software system may be called **system-complete** when:

- production V1 scanner -> RiskEngine -> advisory/counterfactual graph is deployed and auditable;
- execution remains correctly isolated/guarded;
- Gate 1 soak passes;
- Gate 3 DEMO execution acceptance passes;
- Gate 5 production advisory integration behaves correctly (whether or not LLM adds value);
- Gate 6 host DR passes;
- no unresolved P0/P1 safety/integration defect exists.

### Strategy-approved

A strategy is **strategy-approved** only when its own new OOS evidence passes after realistic costs.

These labels must never be merged. A safe complete system can contain an unapproved strategy and therefore remain blocked from production trading.

## 6. Immediate next work

**Do not tune strategy yet.**

Execute in this order:

1. **P0: optimize scanner latency/timestamping.**
2. **P0: wire deployed V1 candidates into deterministic RiskEngine and persist risk verdicts.**
3. **P0: replace legacy LLM review timer with V1 advisory shadow integration.**
4. **P1: remove dual risk-policy ambiguity and add provenance/latency alerts.**
5. Update master docs and deploy the synchronized release.
6. Let Gate 1 soak continue uninterrupted to 2026-09-10 17:54:40 +07.
7. After soak: host-reboot DR.
8. Then run the guarded DEMO execution campaign.
9. Tune/version strategy separately and produce new untouched OOS evidence.
10. Formal Go/No-Go before any production live rollout.
