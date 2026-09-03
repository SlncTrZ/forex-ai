# Forex-AI — Production Development Plan

Status: Active master plan — core architecture and software integration complete; operational validation pending  
Created: 2026-09-03  
Last reconciled with source: 2026-09-03  
Target repository: `/mnt/pc-dev/Forex-AI`

## 1. Purpose

This document defines the work required to move Forex-AI from a read-only research harness into a production-ready, guarded trading system.

Production-ready means the system can:

- make deterministic and auditable decisions;
- contain loss through hard, non-LLM risk controls;
- execute and reconcile orders without blind retries or duplicates;
- fail closed when broker, market, account, deterministic data, risk, audit, reconciliation, or infrastructure safety state is uncertain; degrade to the approved BOT_ONLY path when only the optional model is unavailable;
- recover predictably after MT5, network, container, process, or host failures;
- identify the exact source, configuration, prompt, model, and runtime release behind every decision;
- demonstrate strategy performance out of sample after realistic costs.

Production-ready does **not** mean profitable. Software safety/correctness and statistical trading edge are separate release gates. Both must pass before guarded live trading.

## 2. Current baseline

### 2.1 Implemented

- Python 3.12 project structure and configuration loader.
- MT5 Linux bridge client, strict broker symbol resolution, broker/account/tick/contract DTOs, and broker-aware profit/margin calls.
- Read-only observer for account, positions, ticks, bars, orders, and deals.
- Deterministic MT5 health state machine with bounded backoff, account/contract drift detection, and fail-closed safety snapshots.
- Strategy V1 implemented as deterministic closed-candle `trend_pullback_v1` and `volatility_breakout_v1` candidates with evidence/fingerprints and temporal-leakage guards.
- Replay, evaluation, walk-forward, sensitivity, reporting, counterfactual, immutable replay-dataset freeze/load, manifest/hash tamper detection, and reproducible OOS evidence modules implemented; real historical dataset freeze and operational OOS evidence collection are still pending.
- Account-neutral frozen `RiskProfile` with percentage/absolute limits, max-active-order control, broker-aware sizing, margin/spread/slippage/session checks, correlation grouping, reference-equity loss limits, and existing-position loss-to-stop recomputation.
- Persistent execution lifecycle with idempotency, `SEND_STARTED` persistence, `UNKNOWN` handling, reconciliation primitives, protection/orphan blockers, and SQLite-backed order-intent/transition storage.
- Integration layer joining MT5 DTOs -> Strategy V1 -> optional advisory -> RiskEngine -> persistent decision records -> guarded execution boundary.
- Persistent manual arming / expiry / maintenance / kill-switch state defaults to disarmed and kill-switched; `execution_enabled=false` remains the shipped default.
- Advisory/Macro modules enforce `NO_CHANGE` / `REDUCE_RISK` / source-backed `VETO`; the legacy DeepSeek BUY/SELL/NO_TRADE schema cannot create direction or veto through the integration compatibility adapter.
- SQLite schema v8 with append-only audit timeline plus candidate, advisory, safety, risk, order-intent, transition, broker-call hash/retcode evidence, counterfactual, trading-control, and runtime-heartbeat persistence.
- API usage/cost accounting and local lesson retrieval.
- systemd user-service and timer definitions.
- Local runtime release layout on `.227`; runtime state is not stored on the SMB development mount.
- Safety defaults:
  - `FOREX_AI_MODE=OBSERVE`
  - `execution_enabled=false`
  - persistent `armed=false`
  - persistent `kill_switch=true` when no explicit control state exists
  - no LLM execution tool
- Automated suite after A/B/Integration + Gates 0–4 engineering merge: 115 tests passing as of 2026-09-03.

### 2.2 Remaining critical gaps

- Reproducible release controls are implemented and passed local temp-runtime deploy/rollback/audit drills; the remaining release acceptance item is to repeat the synchronized-source deployment drill on the production host before any real deployment.
- The read-only observer is now wired through the resilient MT5 coordinator with strict symbol resolution, full post-reconnect resynchronization, stale/gap/account/protection guards, cached healthy polling, and persistent heartbeats. Controlled container/network restart drills and the seven-day OBSERVE soak are still pending because the currently running observer/container were not disrupted during development.
- Gate 4 immutable dataset/OOS tooling is code-complete and tested, including byte hashes, semantic event fingerprints, overwrite refusal, tamper detection, non-overlapping splits, reproducible evidence fingerprints, and explicit acceptance policy. The remaining strategy blocker is evidence, not framework: freeze an approved real historical dataset and complete walk-forward/final-test OOS evaluation after realistic broker costs.
- RiskEngine broker-edge validation now covers pending/existing exposure, finite calculator failures, stop/target/freeze boundaries, fees, account-currency neutrality, volume-step flooring, nonlinear broker profit calculation, and live read-only profit/margin calculations on the configured XAUUSD/EURUSD/GBPUSD broker symbols. Filling-mode/retcode behavior belongs to Gate 3 execution validation and remains pending.
- Persistent effectively-once state exists, but crash-before/after-send, timeout-but-accepted, restart reconciliation, partial-fill, orphan, and missing-protection behavior still require demo/fault-injection evidence.
- Emergency SL/TP protection/close policy, owner alerting, watchdog/heartbeat, backup/restore, disk-full handling, and disaster-recovery drills remain operational work.
- Legacy signal/DeepSeek code remains for compatibility/research and must not regain production decision authority.
- OBSERVE, SHADOW, DEMO, and OOS acceptance samples have not yet been collected; real-money production remains No-Go.

### 2.3 Readiness assessment

| Scope | Approximate readiness | Decision |
|---|---:|---|
| Core deterministic architecture (Strategy/Risk/Execution) | 97%+ code complete | Risk + execution engineering/fake-fault validation passed; DEMO broker execution campaign pending |
| Software integration and persistence | 85–90% code complete | Integrated; execution remains disarmed |
| Observer/research harness | 90–95% code complete | Resilient runtime wired; destructive fault drill + seven-day soak pending |
| Strategy validation evidence | 80–85% framework complete | Immutable/OOS framework passed; real historical dataset + final evidence still required |
| Controlled LLM experiment | 60–70% | Advisory boundary ready; paid shadow evidence pending |
| Operations/release/DR | 45–55% | Gate 0 local acceptance passed; Gate 6 work remains |
| Real-money production | 0% approved | No-Go until all release/validation gates pass |

### 2.4 Gate status snapshot

| Gate | Software status | Validation status | Current decision |
|---|---|---|---|
| Gate 0 — Source/release | Hashed dependency lock, pinned MT5 image digest, canonical release manifest, dirty/sync guards, transactional deploy/rollback implemented | Local temp-runtime deploy/rollback + release audit drill passed; production-host synchronized-source drill remains before any real deployment | **LOCAL ACCEPTANCE PASS** |
| Gate 1 — MT5/data resilience | Resilient observer, full reconnect resync, strict mapping/data guards, heartbeat persistence implemented | 7/7 fake fault matrix + live read-only broker sync + client-session drop/reconnect passed; destructive container/network drill + seven-day soak pending | **CODE/SAFE-SMOKE PASS — MAINTENANCE DRILL PENDING** |
| Gate 2 — RiskEngine | Broker-aware core hardened with structured pending/existing exposure and nonlinear safe-volume search | Expanded adversarial matrix + live read-only `order_calc_profit`/`order_calc_margin` smoke on XAUUSD/EURUSD/GBPUSD passed; XAUUSD correctly remains blocked when live spread exceeds profile | **CODE/READ-ONLY VALIDATION PASS** |
| Gate 3 — Execution | Normalized MT5 request builders, filling policy, retcode classifier, persistent broker-event hashes, timeout/partial/restart reconciliation, protection repair/emergency-close policy implemented | 17/17 focused fake-fault tests + full suite + live read-only execution-contract build on XAUUSD/EURUSD/GBPUSD passed; real DEMO `order_check/order_send` timeout/partial/protection campaign still pending | **ENGINEERING/FAKE-FAULT PASS — DEMO CAMPAIGN PENDING** |
| Gate 4 — Strategy/replay | Strategy/replay/reporting + immutable dataset manifest/hash + OOS evidence/acceptance framework implemented | 5/5 focused immutable/OOS tests + 115/115 full suite passed; real historical dataset freeze, realistic-cost walk-forward, untouched final test, sensitivity/Monte Carlo evidence and owner approval remain | **ENGINEERING/IMMUTABILITY PASS — REAL OOS EVIDENCE PENDING** |
| Gate 5 — LLM advisory | Advisory safety boundary implemented | Paid shadow/cost/value evidence pending | PARTIAL |
| Gate 6 — Operations/DR | Existing service/deploy baseline only | Alerts/backup/restore/fault drills pending | PENDING |
| Gate 7 — Rollout | Modes/control state defined | No soak/demo/live stages approved | BLOCKED BY EARLIER GATES |

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
5. Unknown safety-critical state means no new order. LLM unavailability alone is not a safety-critical unknown when deterministic market, calendar, account, risk, and execution state remain healthy.
6. A timeout is an unknown outcome, not an automatic failure and not permission to retry.
7. SQLite remains local to `.227`; never run the active database over SMB/CIFS.
8. SlncTrZ-MCP remains outside the critical trading decision path.
9. Every live capability requires explicit arming and must fail closed.

### 3.1 Trading method and decision authority

V1 uses a **technical-first, macro-regime-gated trend system**:

- Primary strategy: H4/H1 trend regime with M15 trend-pullback entry.
- Secondary strategy: M15 closed-candle volatility breakout.
- M5 may refine execution timing but cannot reverse the M15 decision.
- Mean reversion, grid, martingale, averaging down, live reinforcement learning, and LLM-generated discretionary trades are excluded from V1.
- Technical rules create a candidate with deterministic entry, invalidation, SL, and TP.
- Structured economic-calendar data supplies hard event blackouts.
- Macro context and the LLM are advisory: they classify regime, rank candidates, and identify source-backed conflicts.
- The deterministic RiskEngine is the final authority for monetary risk.
- The execution adapter sends only normalized, RiskEngine-approved orders.

Decision policy:

| Technical setup | Deterministic data/risk/event gates | LLM state | V1 action |
|---|---|---|---|
| Valid | Pass | Not called, neutral, or uncertain | Trade at base risk |
| Valid | Pass | Source-backed material conflict | Reduce risk or veto according to versioned policy |
| Valid | Event blackout or unsafe state | Any | No trade |
| Invalid | Pass | Strong macro opinion | No trade |
| Valid | Pass | Timeout, provider failure, or budget exhausted | BOT_ONLY fallback at base risk |
| Valid | Calendar/event state unavailable near relevant news | Any | No trade |

An LLM response of NEUTRAL, UNCERTAIN, or unavailable must not become a default veto. LLM failure may fall back to BOT_ONLY only when all deterministic safety inputs remain healthy. No policy may force a minimum number of trades or relax risk merely to meet a return target.

### 3.2 Account-neutral RiskProfile and broker normalization

The released system must not contain owner-specific balance, account identifier, broker server, account denomination, leverage, or personal deployment values. All monetary controls are supplied through an explicit, versioned RiskProfile and resolved against live broker/account capabilities.

Live execution has no implicit RiskProfile. Missing, invalid, or unfingerprinted risk configuration keeps execution disarmed.

At startup and before arming, the system must:

- discover the account currency, denomination, equity, balance, leverage, and broker server from MT5;
- persist native account-currency values and normalized reporting values without assuming USD or a cent account;
- reject ambiguous or unexpectedly changed account identity, denomination, or contract metadata;
- read live symbol volume_min, volume_max, volume_step, trade_contract_size, tick value, stop levels, and margin parameters;
- use broker-aware profit and margin calculation for the proposed entry and SL;
- compute loss_at_stop(minimum_legal_volume);
- reject the order when minimum legal volume exceeds the configured percentage or absolute risk budget;
- never narrow a technically valid SL merely to make minimum volume fit.

Required configurable RiskProfile fields include:

| Field | Selected rollout profile | Enforcement |
|---|---:|---|
| max_risk_per_trade_pct | 1% | Maximum projected loss at SL for one trade intent |
| max_total_open_risk_pct | 3% | Combined worst-case loss at SL across active exposure |
| daily_loss_limit_pct | 3% | Blocks new risk for the remainder of the configured trading day |
| weekly_loss_limit_pct | 5% | Blocks new risk for the remainder of the configured trading week |
| max_active_orders | 3 | Counts open positions plus pending entry orders |
| margin_reserve | Configurable | Minimum free-margin or margin-level reserve |
| correlation_limits | Configurable | Caps shared currency, direction, and factor exposure |
| consecutive_loss_cooldown | Configurable | Optional pause after a configured loss sequence |

These are selected rollout-profile values, not constants in strategy or execution code. Operators may supply another validated profile before arming. Every active profile must be schema-validated, hashed, audited, and immutable for the lifetime of an order intent.

Risk-limit accounting must include realized loss, current floating loss, remaining worst-case loss to SL, and the proposed order's loss at SL. A new order is rejected when its projected loss would breach per-trade, total-open-risk, daily, weekly, margin, correlation, or active-order limits.

max_active_orders counts every exposure-creating live intent: an open position or a pending entry order. Partial fills belonging to the same broker order intent do not create additional quota, while independent tickets/intents do. Cancelling or closing an order releases quota only after broker reconciliation confirms the terminal state.

If minimum legal volume cannot fit the active RiskProfile, that symbol/setup is not tradeable for that account. Position size and leverage must never be increased merely to satisfy a desired trade frequency.

### 3.3 LLM call economy and opportunity-cost controls

The LLM must not be called on every tick or candle. Required sequence:

1. local technical scan;
2. deterministic candidate validation;
3. risk/calendar/spread prefilter;
4. cached MacroSnapshot lookup;
5. one batched LLM review only when a valid candidate exists and the snapshot is stale.

Controls:

- Produce zero paid calls when no valid technical candidate exists.
- Batch all relevant symbols into one bounded request.
- Cache a structured MacroSnapshot using a configurable TTL selected from strategy horizon, event schedule, and data-freshness requirements; invalidate it on relevant scheduled events or material source changes.
- Use compact stateless prompts; never resend an accumulating chat history.
- Require configurable call-rate, token, and cost caps for each provider and deployment profile; no paid-call allowance is hardcoded.
- Treat paid AI research cost separately from trading equity during canary validation, while still reporting it in total system economics.
- Persist gross trading P/L, broker cost, data/model cost, and net system P/L separately.
- Do not let API-budget exhaustion disable an otherwise safe BOT_ONLY trade path.

For every candidate, including vetoed and non-executed candidates, persist counterfactual entry, SL, TP, expiry, hypothetical outcome in R, actual decision, veto reason, model cost, and latency. Weekly evaluation must report:

- technical candidate count;
- deterministic rejection rate by reason;
- LLM consultation and veto rates;
- false-veto rate;
- expectancy of vetoed candidates;
- API cost per reviewed and approved trade;
- incremental expectancy and drawdown effect;
- net expectancy after broker, data, and AI costs.

If the LLM does not demonstrate positive incremental value after costs, remove it from the live decision path and retain it only for offline research and post-trade review.

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
- non-standard account-currency and denomination conversion;
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

Measure whether selective, cached LLM review adds net value without becoming a mandatory availability dependency, default veto, execution authority, or uncontrolled latency/cost source.

### Implementation

- Run a controlled paid API smoke test.
- Execute local technical and deterministic risk/calendar/spread prefilters before any paid request.
- Call the LLM only for a valid candidate when no fresh cached MacroSnapshot is available.
- Batch relevant symbols and use compact stateless context.
- Add configurable snapshot TTL/invalidation, daily call cap, monthly token/cost cap, provider timeout, bounded retry, and circuit breaker.
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
- Use BOT_ONLY fallback on LLM timeout, provider failure, invalid response, or exhausted budget when deterministic safety inputs are healthy.
- Fail closed only when market, calendar/event, account, risk, audit, reconciliation, or execution safety state is uncertain.
- Permit an LLM veto only for a source-backed material conflict accepted by a versioned policy; neutral or uncertain output is not a veto.
- Record BOT_ONLY, BOT_LLM, and counterfactual outcomes using identical signal and risk inputs.
- Never enforce a minimum monthly trade count or increase risk to compensate for rejected opportunities.
- Keep model and pricing configuration versioned; detect provider/model drift.
- Do not use web claims as broker truth.

### Metrics

- LLM consultation rate, veto precision, and false-veto rate.
- Counterfactual expectancy of vetoed candidates.
- Incremental expectancy and drawdown effect.
- Added latency and stale-decision rate.
- API cost per reviewed signal, per approved trade, and per unit of added value.
- Gross trading P/L, broker/data/model costs, and net system P/L.
- Performance by strategy, symbol, session, and market regime.

### Gate acceptance

- No invalid/unvalidated response can influence RiskEngine.
- LLM outage and budget exhaustion do not block the independently approved BOT_ONLY path.
- Budget, cache, batching, timeout, fallback, and circuit-breaker tests pass.
- Shadow sample demonstrates measurable benefit after all costs; otherwise remove the LLM from the live decision path.
- The LLM still has no execution or position-sizing capability.

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

### Stage D — LIVE_CANARY

Initial constraints:

- One approved symbol only.
- Query the live account and broker contract; do not assume denomination, minimum volume, leverage, or margin behavior.
- Require an explicit, validated, and fingerprinted RiskProfile before arming.
- Use the selected rollout profile: 1% risk per trade, 3% maximum total open risk, 3% daily loss limit, 5% weekly loss limit, and max_active_orders of 3.
- Treat every percentage and order-count limit as configuration, never a hardcoded constant.
- Reject a trade if minimum legal volume or projected portfolio loss exceeds the active profile.
- Count open positions and pending entry orders toward max_active_orders.
- Enforce configured margin reserve and correlation limits.
- Martingale, grid, and averaging down are prohibited.
- Manual daily arming.
- Immediate owner alert for every order, fill, protection change, rejection, and reconciliation mismatch.

### Stage E — GUARDED_LIVE

Expand symbols or risk only after:

- canary sample and operational review;
- zero unresolved safety incident;
- actual slippage/spread/margin behavior matches the tested envelope;
- owner approves a versioned risk-policy change.

### Stage F — LIVE_EXPERIMENT

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
| Live rollout | Account-neutral LIVE_CANARY with explicit validated RiskProfile |

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
- [ ] LLM remains optional, cached, budget-bounded, auditable, and execution-incapable.
- [ ] LLM/API failure safely falls back to the approved BOT_ONLY path when deterministic safety state is healthy.
- [ ] Account denomination and minimum-volume loss-at-stop are discovered and verified from live broker data without broker-specific assumptions.
- [ ] Gross trading P/L, broker cost, AI/data cost, and net system P/L are reported separately.
- [ ] Counterfactual results show whether LLM vetoes add or destroy value.
- [ ] Monitoring and owner alerts cover all safety-critical states.
- [ ] Database backup/restore and integrity drills pass.
- [ ] OBSERVE and SHADOW soak criteria pass.
- [ ] Demo lifecycle and fault-injection criteria pass.
- [ ] Owner explicitly approves LIVE_CANARY activation.
- [ ] No unresolved P0/P1 safety defect remains.

## 8. Priority backlog

| Priority | Work package | Depends on |
|---|---|---|
| DONE | Initial Git baseline, A/B core merge, integration/persistence boundary | None |
| DONE/VERIFY-PROD | Reproducible dependency/container/release fingerprint + local deploy/rollback drill | Git baseline |
| DONE/MAINTENANCE-VERIFY | MT5 health/reconnect/resynchronization in long-running runtime | Integrated health/data contracts |
| DONE | Deterministic RiskEngine broker-edge/adversarial matrix + live read-only calculator smoke | Integrated RiskEngine |
| CURRENT | Execution unknown-outcome, retcode, restart, partial-fill and SL/TP fault campaign | Persistent execution lifecycle |
| DONE/REAL-DATA-PENDING | Immutable replay dataset + reproducible walk-forward/OOS evidence framework | Strategy/replay tooling |
| P1 | Demo integration and fault injection | Execution adapter |
| P1 | Metrics, alerting, watchdog, backup/restore | Runtime contracts |
| P1 | Selective DeepSeek review, MacroSnapshot cache, batching, budget, and fallback | Stable shadow pipeline |
| P1 | Shadow BOT_ONLY versus BOT_LLM counterfactual evaluation | Replay/audit contracts |
| P2 | Account-neutral live-canary tooling and daily arming | All previous gates |
| P2 | Post-trade lesson-quality evaluation | Sufficient journal history |
| P3 | PostgreSQL or distributed architecture | Only if scaling requires it |

## 9. Recommended implementation sequence

1. **Current release acceptance blocker:** run the Gate 1 controlled MT5 container/network restart drill and then the seven-day OBSERVE soak during an approved maintenance/deployment window; the active observer/container were deliberately not disrupted during development.
2. Gate 3 engineering/fake-fault validation is complete. The remaining Gate 3 acceptance work is an explicit DEMO campaign using the same guarded execution path for `order_check/order_send`, timeout-but-accepted, partial fill, restart reconciliation, orphan handling, protection repair, and emergency-close fault scenarios; keep execution disabled outside that approved DEMO campaign.
3. Before any real production deployment, repeat the already-passed Gate 0 drill from a clean branch synchronized with upstream on the target host; production deploy continues to refuse dirty/ahead/behind source.
4. Freeze the approved real historical replay dataset using the completed immutable dataset framework, then produce walk-forward/OOS strategy evidence after realistic costs without tuning on the final test split.
5. Run controlled DeepSeek/advisory paid shadow smoke; verify cache/budget/fallback and counterfactual value.
6. Add observability, owner alerts, watchdog, backup/restore, disk-full handling, and recovery drills.
7. Run seven-day OBSERVE soak after Gate 0/1 acceptance.
8. Run SHADOW comparison with at least 200 candidate decisions and BOT_ONLY/BOT_LLM counterfactuals.
9. Run DEMO execution campaign with at least 100 complete lifecycles plus required fault injections.
10. Conduct formal Go/No-Go review.
11. If approved, enable one-symbol LIVE_CANARY with an explicit validated RiskProfile.
12. Expand only through a versioned, audited RiskProfile change and owner approval.

## 10. Rough effort

Expected implementation and validation effort: approximately 6–10 weeks for one focused engineer, excluding delays required to collect adequate market samples.

Code completion may be faster. Soak periods, fault drills, out-of-sample validation, and live broker behavior must not be shortened merely to meet a calendar target.

## 11. Immediate next action

Gate 0 local release acceptance and Gate 1 software/read-only smoke validation are complete. The remaining Gate 1 acceptance work requires an approved maintenance window because a real OBSERVE process is currently using the MT5 container:

1. deploy the resilient observer from a clean synchronized release while execution remains disabled;
2. confirm startup performs full authoritative resynchronization and publishes healthy heartbeats before normal polling;
3. deliberately restart the MT5 container and exercise a controlled network interruption; verify `DEGRADED/DISCONNECTED -> CONNECTING -> SYNCING -> HEALTHY` recovery with no decisions during uncertain state;
4. confirm account/contract fingerprints remain stable and all raw history/journal facts reconcile after recovery;
5. begin the required seven-day OBSERVE soak and review heartbeat/data-quality incidents at the end of the period;
6. Gates 2–4 engineering frameworks are complete; prioritize the Gate 1 maintenance drill/OBSERVE soak and Gate 6 operational hardening while collecting the real Gate 4 OOS evidence and preparing the Gate 3 DEMO campaign.

Real-order execution remains disabled. `execution_enabled=false`, persistent manual arming, reconciliation, and kill-switch controls remain independent gates; no live progression is permitted until Gates 0–3 validation and owner approval are complete.
