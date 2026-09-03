# Forex-AI — Production Development Plan

Status: Draft master implementation plan  
Created: 2026-09-03  
Target repository: `/mnt/pc-dev/Forex-AI`

## 1. Purpose

This document defines the work required to move Forex-AI from a read-only research harness into a production-ready, guarded trading system.

Production-ready means the system can:

- make deterministic and auditable decisions;
- contain loss through hard, non-LLM risk controls;
- execute and reconcile orders without blind retries or duplicates;
- fail closed when broker, market, account, data, model, or infrastructure state is uncertain;
- recover predictably after MT5, network, container, process, or host failures;
- identify the exact source, configuration, prompt, model, and runtime release behind every decision;
- demonstrate strategy performance out of sample after realistic costs.

Production-ready does **not** mean profitable. Software safety/correctness and statistical trading edge are separate release gates. Both must pass before guarded live trading.

## 2. Current baseline

### 2.1 Implemented

- Python 3.12 project structure and configuration loader.
- MT5 Linux bridge client and broker symbol resolution.
- Read-only observer for account, positions, ticks, bars, orders, and deals.
- SQLite schema with append-only audit timeline and correlation IDs.
- Deterministic technical features and initial signal generation.
- Shadow DeepSeek reviewer with structured response handling and forced hosted web search.
- API usage/cost accounting and local lesson retrieval.
- systemd user-service and timer definitions.
- Local runtime release layout on `.227`; runtime state is not stored on the SMB development mount.
- Safety defaults:
  - `FOREX_AI_MODE=OBSERVE`
  - `execution_enabled=false`
  - no LLM execution tool
- Existing automated suite: 16 tests passing as of 2026-09-03.

### 2.2 Critical gaps

- Repository has no baseline commit; all current files are untracked.
- Dependencies and MT5 container image are not locked to reproducible versions/digests.
- Observer connects once and has no explicit reconnect/resynchronization state machine.
- Strategy score is heuristic and has no backtest/walk-forward evidence.
- RiskEngine does not enforce `max_risk_per_trade_pct`.
- Missing stop/freeze level, spread, slippage, margin, open-risk, correlation, and kill-switch guards.
- No execution adapter, order lifecycle, idempotency, or unknown-outcome recovery.
- No demo execution soak, cent canary, operational alerting, or disaster-recovery drill.
- Documentation contains stale state and must be reconciled.

### 2.3 Readiness assessment

| Scope | Approximate readiness | Decision |
|---|---:|---|
| Observer/research harness | 65–70% | Usable with monitoring |
| Shadow LLM experiment | 50–60% | Requires paid smoke and soak |
| Complete V1 in `PLAN.md` | 35–40% | In development |
| Real-money production | 0% approved | No-Go |

## 3. Non-negotiable architecture

```text
Market data
  -> deterministic feature/signal engine
  -> optional LLM reviewer
  -> deterministic RiskEngine
  -> execution preflight
  -> MT5 execution adapter
  -> broker reconciliation
  -> immutable audit/journal
```

Rules:

1. The LLM must never receive an execution tool.
2. The LLM must never select or increase position size.
3. The RiskEngine recomputes all critical values; it does not trust signal or LLM claims.
4. Broker/MT5 state is authoritative for positions, orders, deals, symbol constraints, and fills.
5. Unknown state means no new order.
6. A timeout is an unknown outcome, not an automatic failure and not permission to retry.
7. SQLite remains local to `.227`; never run the active database over SMB/CIFS.
8. SlncTrZ-MCP remains outside the critical trading decision path.
9. Every live capability requires explicit arming and must fail closed.

## 4. Delivery roadmap and gates

Work must proceed in gate order. A later gate cannot override an incomplete earlier gate.

---

## Gate 0 — Source integrity and reproducible release

### Objective

Make every deployed runtime traceable, reproducible, testable, and rollback-safe.

### Implementation

- Create the initial Git baseline commit.
- Require a clean working tree for production deployment.
- Add release metadata:
  - full Git SHA;
  - configuration SHA-256;
  - dependency-lock SHA-256;
  - database schema version;
  - prompt version/hash;
  - model/provider identifier;
  - MT5 container image digest;
  - release timestamp.
- Generate a dependency lock using an agreed tool such as `uv lock` or compiled pinned requirements.
- Pin the MT5 Docker image by immutable digest; do not deploy `:latest`.
- Update `scripts/deploy_local.sh`:
  - refuse dirty/uncommitted source for production;
  - run tests before packaging;
  - build an immutable release;
  - run database preflight/migrations before activation;
  - switch the `current` symlink atomically;
  - retain a bounded number of known-good releases;
  - provide explicit rollback.
- Reconcile `PLAN.md`, `README.md`, and `STATUS.md`.
- Record release-start and release-stop audit events.

### Tests

- Dirty tree is rejected.
- Failed tests prevent deployment.
- Failed migration prevents activation.
- Interrupted deployment leaves the current release intact.
- Rollback restores the previous release and schema-compatible state.
- An audit row can be mapped to one exact release fingerprint.

### Gate acceptance

- Clean Git baseline exists.
- Dependency and container inputs are immutable.
- Deploy and rollback drills both pass.
- Current runtime fingerprint is visible in logs and audit records.

---

## Gate 1 — Market-data correctness and MT5 resilience

### Objective

Ensure all decisions use fresh, complete, correctly ordered market and account data.

### Implementation

- Add explicit MT5 connection states:
  - `DISCONNECTED`
  - `CONNECTING`
  - `SYNCING`
  - `HEALTHY`
  - `DEGRADED`
  - `BLOCKED`
- Add reconnect with bounded exponential backoff and jitter.
- After reconnect, resynchronize:
  - terminal/account identity;
  - broker server;
  - available symbols and mappings;
  - symbol contracts;
  - open positions and pending orders;
  - recent orders and deals;
  - missing candles/ticks needed by the strategy.
- Block decisions on:
  - stale tick;
  - missing/gapped candles;
  - duplicate/out-of-order candles;
  - wrong account/server;
  - terminal or symbol not tradeable;
  - unresolved symbol mapping;
  - abnormal clock drift.
- Preserve UTC as the canonical event and market time.
- Confirm that the currently forming candle is excluded from closed-candle evidence.
- Persist data-quality and connection-state events.
- Add a heartbeat containing last successful MT5 call, last tick time, last journal write, and current health state.

### Tests

- MT5 unavailable on startup.
- MT5 restarts during collection.
- Network drops before, during, and after a request.
- Empty/None/malformed MT5 responses.
- Stale tick and gapped candle series.
- Account or broker server changes unexpectedly.
- Duplicate and out-of-order history rows.
- Host/process restart during synchronization.

### Gate acceptance

- Seven continuous days of OBSERVE operation.
- Controlled MT5/container/network fault drills recover automatically.
- No duplicate/lost journal facts in reconciliation checks.
- No signal is generated while health is degraded or state is stale.

---

## Gate 2 — Deterministic RiskEngine

### Objective

Make loss containment independent of the signal engine, LLM, and execution adapter.

### Required inputs

- Proposal: symbol, side, entry, SL, TP, age, correlation ID.
- Live tick and tick age.
- Live symbol contract and trading capabilities.
- Live account equity, balance, free margin, margin level, currency, leverage.
- Open positions, pending orders, and existing open risk.
- Daily realized P/L and high-water-mark equity drawdown.
- Active risk policy and kill-switch state.

### Required guards

#### Proposal validation

- Symbol and side allowlist.
- Finite numeric values only; reject NaN/Inf/zero.
- BUY requires `SL < entry < TP`.
- SELL requires `TP < entry < SL`.
- Recompute RR independently.
- Signal age and context freshness.
- Reject material price drift between proposal and preflight.

#### Broker validation

- Normalize price by `digits` and `point`.
- Normalize volume by `volume_min`, `volume_max`, and `volume_step`.
- Enforce `trade_stops_level` and `trade_freeze_level`.
- Validate `trade_mode`, `order_mode`, execution mode, and filling mode.
- Validate market/session availability.

#### Position sizing

- Calculate loss at SL in account currency using broker-aware profit calculation.
- Size volume from configured risk percentage and current equity.
- Never round volume upward beyond the risk budget.
- Recalculate estimated loss after volume normalization.
- Reject if minimum legal lot exceeds the allowed risk.
- Calculate required margin and enforce a free-margin reserve.

#### Portfolio controls

- Risk per trade.
- Total open risk.
- Daily realized-loss limit.
- Intraday equity drawdown limit.
- Maximum simultaneous positions.
- Maximum positions/open risk per symbol.
- Correlated-exposure limits for EURUSD/GBPUSD and shared USD exposure.
- Consecutive-loss cooldown.
- Optional session/event blackout policy.
- Maximum spread and slippage per symbol.

#### Operational controls

- Persistent kill switch.
- Manual maintenance mode.
- Daily arming expiry.
- Reject when reconciliation is incomplete.
- Reject when any previous order remains in `UNKNOWN` state.
- Reject on database, audit, disk, clock, MT5, or account-health failure.

### Tests

Use unit, parameterized, property-based, and adversarial tests for:

- boundary rounding;
- cent-account currency conversion;
- minimum-lot risk overflow;
- XAUUSD versus Forex contract differences;
- invalid SL/TP direction;
- stop/freeze-level boundaries;
- stale ticks and spread spikes;
- negative/zero/NaN/Inf values;
- drawdown and daily-loss boundaries;
- correlated exposure;
- missing account/symbol data;
- kill switch and unknown order state.

### Gate acceptance

- Every deliberate unsafe proposal is rejected with a stable reason code.
- Safe-volume calculation never exceeds configured monetary risk after normalization.
- No caller can bypass or override the final RiskEngine decision.
- Risk decisions are persisted before execution.

---

## Gate 3 — Execution adapter and effectively-once behavior

### Objective

Execute broker operations safely while preventing blind retries and duplicate exposure.

### Order state machine

```text
INTENT_CREATED
  -> RISK_APPROVED
  -> PREFLIGHT_PASSED
  -> SEND_STARTED
  -> ACCEPTED | PARTIAL | REJECTED | UNKNOWN
  -> PROTECTION_VERIFIED
  -> RECONCILED
  -> CLOSED
```

### Implementation

- Persist order intent and idempotency key before any broker call.
- Attach stable `magic` and constrained `comment` identifiers.
- Build requests from normalized RiskEngine output only.
- Run margin/profit calculations and `order_check`.
- Send through one narrow execution adapter.
- Persist raw request/result hashes and MT5 retcodes.
- Handle:
  - success;
  - placed/pending;
  - partial fill;
  - requote/price change;
  - reject;
  - invalid stops/volume/filling;
  - market closed;
  - insufficient funds;
  - timeout/connection loss.
- Treat send timeout as `UNKNOWN`.
- Before retrying, query positions, active orders, order history, and deals.
- Reconcile local intent with broker tickets and fills.
- Verify SL/TP after entry fill.
- If protection cannot be established within a bounded policy:
  - block new entries;
  - issue an emergency close according to policy;
  - alert the owner;
  - retain full audit evidence.
- Implement close, partial close if required, SL/TP update, and emergency disable through the same lifecycle and audit system.

### Tests

- Duplicate execution request.
- Process crash immediately before and after `order_send`.
- Broker accepts order but client times out.
- Partial fill.
- Requote and invalid filling mode.
- Entry fills but SL/TP placement fails.
- Reconciliation finds a broker position absent locally.
- Local intent exists without broker evidence.
- Restart with unresolved `UNKNOWN` state.

### Gate acceptance

- Demo tests show no duplicate position after timeout/restart fault injection.
- Every broker order/deal maps to a local intent or creates an explicit orphan alert.
- Missing protection blocks new trading and triggers the configured emergency path.
- All documented MT5 retcode classes have deterministic handling.

---

## Gate 4 — Strategy validation and replay

### Objective

Prove that the deterministic strategy has measurable out-of-sample edge after realistic costs.

### Implementation

- Build a replay/backtest harness using the same feature and signal code as live.
- Store immutable input datasets with hashes.
- Prevent look-ahead:
  - closed candles only;
  - decision available-time respected;
  - no future spread/fill knowledge.
- Model:
  - historical spread;
  - commission;
  - swap;
  - slippage;
  - rejected fills;
  - broker volume/stop constraints.
- Add walk-forward and out-of-sample evaluation.
- Evaluate by symbol, strategy, session, volatility regime, and direction.
- Run parameter stability analysis and Monte Carlo trade-order/slippage simulations.
- Do not tune thresholds directly on the final test period.

### Required report

- trade and signal counts;
- expectancy in R and account currency;
- profit factor;
- win rate and payoff ratio;
- maximum drawdown and drawdown duration;
- tail loss;
- exposure/time in market;
- performance by symbol/session/regime;
- sensitivity to spread and slippage;
- confidence intervals;
- in-sample versus out-of-sample comparison.

### Gate acceptance

- Positive out-of-sample expectancy after all costs.
- Drawdown remains inside the approved risk envelope.
- Results are not dependent on one symbol, one short period, or one narrow parameter value.
- Replay of identical inputs produces identical decisions.
- Owner explicitly approves the strategy evidence before demo execution progression.

---

## Gate 5 — Controlled LLM reviewer

### Objective

Measure whether LLM review adds net value without adding execution authority or uncontrolled latency.

### Implementation

- Run a controlled paid API smoke test.
- Add provider timeout, retry limits, circuit breaker, and explicit fallback.
- Validate schema and semantics.
- Preserve:
  - provider/model;
  - prompt version/hash;
  - input/context hash;
  - raw response hash;
  - token usage and cost;
  - latency;
  - web-search trace and cited sources;
  - final validated decision.
- Enforce a decision deadline so stale LLM output cannot enter RiskEngine.
- Define one fixed failure policy:
  - `NO_TRADE`, or
  - BOT_ONLY if independently approved.
- Record BOT_ONLY, BOT_LLM, and counterfactual outcomes using the same signal and risk inputs.
- Keep model and pricing configuration versioned; detect provider/model drift.
- Do not use web claims as broker truth.

### Metrics

- LLM veto precision and false-veto rate.
- Incremental expectancy and drawdown effect.
- Added latency and stale-decision rate.
- API cost per reviewed signal and per unit of added value.
- Performance by strategy, symbol, session, and market regime.

### Gate acceptance

- No invalid/unvalidated response can reach RiskEngine.
- Budget and circuit breaker tests pass.
- Shadow sample demonstrates measurable benefit after API cost; otherwise remove the LLM from the trading decision path.
- The LLM still has no execution capability.

---

## Gate 6 — Production operations, security, and disaster recovery

### Objective

Make the runtime observable, recoverable, and safe under host-level failures.

### Observability

Record and alert on:

- service/container health;
- MT5 connection state and latency;
- last fresh tick and candle;
- clock drift;
- scan/review/execution latency;
- pending and unknown order states;
- local/broker reconciliation mismatches;
- open risk and daily limits;
- rejected signals by reason;
- LLM failures, latency, and cost;
- database write latency, WAL size, integrity, and disk usage;
- restart loops and release fingerprint.

### Runtime hardening

- Dedicated least-privilege runtime user.
- Secrets outside Git with mode `600`.
- MT5 bridge bound to localhost.
- noVNC limited to trusted LAN/VPN and firewall rules.
- No automatic dependency/container updates.
- Resource limits and bounded log retention.
- systemd restart throttling and watchdog/heartbeat integration.
- Startup must reconcile broker state before arming.
- Graceful shutdown blocks new work and finishes/persists in-flight state.

### Database

SQLite is acceptable while the application and database remain on the same local host and write concurrency stays low.

Add:

- WAL/busy-timeout policy;
- bounded transactions;
- scheduled backup;
- integrity checks;
- retention/archival;
- disk-full handling;
- restore drills;
- schema migration compatibility and rollback policy.

Do not move the active SQLite database to SMB/CIFS. Reconsider PostgreSQL only if multi-host writers, remote concurrent access, HA, or significantly higher event volume becomes a real requirement.

### Disaster-recovery drills

- process crash;
- MT5 container restart;
- host reboot;
- network outage;
- disk-full simulation;
- corrupted/copied database restore;
- failed deployment and rollback;
- lost response after broker accepts an order;
- changed account identity;
- stale or unavailable LLM provider.

### Gate acceptance

- Alerts reach the owner with actionable context.
- Backup restore and release rollback are demonstrated, not merely documented.
- Startup after reboot reconciles before trading.
- Trading remains blocked through every uncertain-state drill.

---

## Gate 7 — Progressive rollout

### Stage A — OBSERVE soak

Minimum entry criteria:

- Gates 0 and 1 pass.
- Seven continuous days without unreconciled data loss.
- Fault-recovery drills pass.

### Stage B — SHADOW

Minimum entry criteria:

- RiskEngine operates on proposals but execution remains disabled.
- At least 200 candidate decisions collected, subject to adequate coverage across strategies and sessions.
- BOT_ONLY and BOT_LLM counterfactuals recorded.

### Stage C — DEMO execution

Minimum entry criteria:

- Gates 2 and 3 pass.
- At least 100 complete demo order lifecycles.
- Timeout, restart, duplicate, partial-fill, and missing-SL fault tests pass.
- No unresolved broker/local reconciliation mismatch.

### Stage D — CENT_CANARY

Initial constraints:

- One approved symbol only.
- Broker minimum lot, normally `0.01`.
- Initial risk target: 0.05–0.10% per trade, subject to minimum-lot economics.
- One simultaneous position.
- Strict daily loss and drawdown limits.
- Manual daily arming.
- Immediate owner alert for every order, fill, protection change, rejection, and reconciliation mismatch.

### Stage E — CENT_GUARDED

Expand symbols or risk only after:

- canary sample and operational review;
- zero unresolved safety incident;
- actual slippage/spread/margin behavior matches the tested envelope;
- owner approves a versioned risk-policy change.

### Stage F — CENT_EXPERIMENT

- BOT_ONLY and BOT_LLM use the same execution and RiskEngine path.
- Randomization/allocation policy is fixed in advance.
- API cost and risk-adjusted outcome are included.
- Risk is never raised merely to accelerate sample collection.

## 5. Manual arming and kill-switch model

Live execution requires all independent conditions:

```text
live-capable release
AND approved mode
AND execution_enabled
AND local manual arm
AND arm not expired
AND correct account/server
AND MT5 healthy
AND fresh market state
AND database/audit healthy
AND reconciliation complete
AND no UNKNOWN order
AND kill switch clear
AND risk limits available
```

If any condition is false, new entries are blocked.

The kill switch must be:

- persisted outside normal strategy state;
- readable without the LLM;
- checked immediately before preflight and immediately before send;
- safe across restart;
- auditable;
- manually reset only after reconciliation.

## 6. Test strategy

| Layer | Required testing |
|---|---|
| Pure calculations | Unit and property-based tests |
| Risk policies | Boundary/adversarial matrix |
| MT5 adapter | Contract tests with recorded/fake responses |
| Journal | Migration, idempotency, corruption, concurrency |
| Execution | State-machine and fault-injection tests |
| Strategy | Replay, walk-forward, out-of-sample |
| LLM | Schema, timeout, budget, provider drift, replay fixtures |
| Integration | MT5 demo account and container |
| Operations | Restart, network, disk, backup/restore, rollback |
| Live rollout | Cent canary with strict limits |

Required CI checks:

- formatting/linting;
- static type checking;
- unit and integration tests;
- migration validation;
- secret scan;
- dependency vulnerability report;
- clean-tree/release metadata verification;
- deploy package fingerprint.

Coverage percentage alone is not an acceptance criterion. Safety-path behavior and fault coverage take priority.

## 7. Production Definition of Done

Forex-AI V1 is production-ready only when all items below are true:

- [ ] Git baseline, clean release, immutable dependencies, and container digest exist.
- [ ] Deployment and rollback drills pass.
- [ ] Every runtime decision carries a complete release/config fingerprint.
- [ ] MT5 reconnect and post-reconnect reconciliation pass fault tests.
- [ ] Market data freshness/completeness guards block bad inputs.
- [ ] Risk-based position sizing is broker-aware and never exceeds the configured loss budget.
- [ ] Stop/freeze, margin, spread, slippage, exposure, drawdown, and kill-switch guards pass.
- [ ] Execution lifecycle handles success, reject, partial fill, timeout, and unknown outcomes.
- [ ] Duplicate-order prevention survives process and host restart.
- [ ] SL/TP protection is verified after entry.
- [ ] Broker orders, positions, and deals reconcile with the local journal.
- [ ] Strategy passes out-of-sample evaluation after realistic costs.
- [ ] LLM remains optional, bounded, auditable, and execution-incapable.
- [ ] Monitoring and owner alerts cover all safety-critical states.
- [ ] Database backup/restore and integrity drills pass.
- [ ] OBSERVE and SHADOW soak criteria pass.
- [ ] Demo lifecycle and fault-injection criteria pass.
- [ ] Owner explicitly approves CENT_CANARY activation.
- [ ] No unresolved P0/P1 safety defect remains.

## 8. Priority backlog

| Priority | Work package | Depends on |
|---|---|---|
| P0 | Initial Git baseline and documentation reconciliation | None |
| P0 | Reproducible dependency/container/release fingerprint | Git baseline |
| P0 | MT5 health, reconnect, and reconciliation state machine | Baseline |
| P0 | Complete deterministic RiskEngine | Data contracts |
| P0 | Adversarial risk test matrix | RiskEngine |
| P0 | Execution state machine and idempotency | RiskEngine |
| P0 | Unknown-outcome recovery and SL/TP verification | Execution adapter |
| P1 | Replay/backtest and cost model | Stable signal/data contracts |
| P1 | Demo integration and fault injection | Execution adapter |
| P1 | Metrics, alerting, watchdog, backup/restore | Runtime contracts |
| P1 | DeepSeek paid smoke and circuit breaker | Stable shadow pipeline |
| P1 | Shadow BOT_ONLY versus BOT_LLM evaluation | Replay/audit contracts |
| P2 | Cent canary tooling and daily arming | All previous gates |
| P2 | Post-trade lesson-quality evaluation | Sufficient journal history |
| P3 | PostgreSQL or distributed architecture | Only if scaling requires it |

## 9. Recommended implementation sequence

1. Baseline Git commit and reconcile documentation.
2. Lock dependencies and container image; add release fingerprint.
3. Harden deployment and rollback.
4. Implement MT5 health/reconnect/resynchronization.
5. Add market-data quality and account-identity guards.
6. Complete broker-aware RiskEngine and tests.
7. Implement execution lifecycle, idempotency, and reconciliation.
8. Build replay/backtest and realistic cost model.
9. Run DeepSeek paid shadow smoke and harden fallback/circuit breaker.
10. Add observability, alerts, backups, and recovery drills.
11. Run OBSERVE soak.
12. Run SHADOW comparison.
13. Run DEMO execution/fault-injection campaign.
14. Conduct formal Go/No-Go review.
15. If approved, enable one-symbol CENT_CANARY.
16. Expand only through versioned, audited owner approval.

## 10. Rough effort

Expected implementation and validation effort: approximately 6–10 weeks for one focused engineer, excluding delays required to collect adequate market samples.

Code completion may be faster. Soak periods, fault drills, out-of-sample validation, and live broker behavior must not be shortened merely to meet a calendar target.

## 11. Immediate next action

The next development action is Gate 0:

1. review current untracked files;
2. reconcile stale documentation;
3. create the initial clean baseline commit;
4. add reproducible release metadata and dependency/container locking;
5. verify deployment and rollback without enabling execution.

Real-order code must remain disabled until Gates 0–3 pass and the owner explicitly approves progression to demo and later cent-canary stages.
