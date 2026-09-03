# Forex-AI — System Completion Plan

Status: **ACTIVE — audited against source/runtime on 2026-09-03 20:36 +07**

This plan is the execution checklist for completing the trading system. `DEVELOP_PLAN.md` remains the architectural specification/history. Strategy profitability/tuning is tracked separately from system correctness.

## 1. Audited current state

### Source / release

- `main` is clean and synchronized with `origin/main` at `9bd6b71ede16eb6b03d3b0f08d293f0e729d53ed`.
- Active release: `/home/dinhtc/apps/forex-ai/releases/20260903T132901Z-9bd6b71ede16`.
- Test inventory: **144 tests collected; full suite passes**.
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

This means Strategy V1 is now observable, but the live timer does **not yet execute the complete Strategy -> RiskEngine decision graph**.

## 2. Critical findings from the audit

### P0 — Strategy -> RiskEngine live wiring is incomplete

`DecisionOrchestrator` and `BrokerAwareRiskEngine` exist and are tested, but no deployed timer/service invokes the integrated decision graph. The current candidate timer persists Strategy V1 candidates/verdicts only.

Impact: when the first valid V1 candidate appears, there is currently no automatic persisted `risk_decisions_v1` verdict from the live scanner path.

Required fix: build a read-only production decision service that converts live MT5 state into Strategy V1 + SafetySnapshot + RiskContext and persists deterministic risk approval/rejection. It must never call execution while mode is OBSERVE/SHADOW or `execution_enabled=false`.

### P0 — Candidate-scan latency must be bounded before RiskEngine integration

The first bundled live scan measured about 39 seconds for the configured three symbols. The active RiskProfile has `max_signal_age_seconds: 30`.

Impact: using one scan-start timestamp across all symbols can make later-symbol decisions stale before RiskEngine evaluation.

Required fix:

- timestamp each symbol at its actual capture/evaluation time;
- retain one MT5 bundle call per symbol;
- remove unnecessary symbol/constants round-trips through caching or startup resolution;
- set and measure a scanner latency SLO; target p95 end-to-end scan latency < 20 seconds for all configured symbols and < 10 seconds per symbol;
- persist scan latency and reject stale candidates deterministically.

### P0 — Gate 5 runtime is not integrated with production candidates

The new advisory runtime (cache/budget/batch/circuit/fallback) is tested, but the deployed `review_pending.py` still consumes legacy `signals` and calls the legacy DeepSeek review schema (`BUY/SELL/NO_TRADE`).

Impact: Gate 5 engineering exists, but the active shadow timer is not the production advisory architecture described by the master plan.

Required fix:

- stop using legacy `signals` as the production LLM queue;
- review only valid/prefiltered V1 candidates;
- map provider output only into advisory actions (`NO_CHANGE`, `REDUCE_RISK`, policy-backed `VETO`);
- keep BOT_ONLY fallback when provider/budget/cache path is unavailable;
- legacy signal + DeepSeek pipeline becomes offline diagnostic/research only.

### P1 — Risk configuration contains two policy vocabularies

`config/risk.yaml` contains the current `profile` (1% per trade, 3% total risk, max 3 active orders) and a legacy `limits` block (0.25% per trade, max 2 simultaneous positions, etc.). Current `load_risk_profile()` uses `profile`, while legacy `risk.engine` references `limits`.

Impact: no current production caller uses the legacy engine, but the file is operationally ambiguous and dangerous for future maintenance.

Required fix: remove/deprecate the legacy policy path, migrate any still-needed non-profile settings into explicitly named production configuration, and add a test that the runtime has exactly one authoritative risk policy.

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
