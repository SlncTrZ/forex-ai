# Forex-AI — System Completion Plan

Status: **ACTIVE — execution-first direction adopted 2026-09-04**

This plan defines the shortest auditable path from the current OBSERVE system to a real, controlled trading lifecycle. `DEVELOP_PLAN.md` remains architectural/history documentation. Strategy research, profitability and LLM value are separate workstreams and must not block basic execution validation unless they are directly required for safe order placement.

## 0. Project direction

The project is **not** trying to build a strategy with 100% win rate. That is neither realistic nor a valid engineering target for a market system.

The first required capability is much simpler and more concrete:

> **The system must be able to place an order, manage it, exit it, and explain why every material action happened.**

A losing trade can be a successful system test if the complete lifecycle is correct, controlled and auditable. A profitable trade is still a system failure if the system cannot explain why it entered, how risk was determined, what the broker actually did, and why the position exited.

From this point forward, development is execution-first:

1. prove a complete trading lifecycle;
2. prove deterministic risk and broker reconciliation around that lifecycle;
3. then measure whether a strategy has positive edge;
4. then expand live exposure gradually.

Anything that does not directly move the system toward a correct complete trade lifecycle is not a blocker for the first controlled trade unless it protects a critical execution boundary.

## 1. Primary milestone — First Complete Trade Lifecycle

The next milestone is:

**signal -> explained decision -> risk decision -> order intent -> broker preflight -> order send -> broker acknowledgement/fill -> open-position tracking -> exit -> explained outcome -> full journal**

### Required evidence at each step

#### 1. Signal / setup

The journal must record:

- strategy id and version;
- symbol and timeframe;
- market timestamp / closed decision candle;
- side under consideration;
- exact setup conditions that passed;
- exact setup conditions that failed or were not required;
- relevant market evidence used by the strategy.

#### 2. Entry decision

The system must record why the trade is allowed or rejected:

- candidate id / opportunity id;
- BUY or SELL explicitly;
- intended entry;
- stop loss;
- take profit / exit objective if applicable;
- risk amount / risk fraction;
- strategy reason codes;
- market snapshot fingerprint.

#### 3. Risk decision

The deterministic risk engine must record:

- approval or rejection;
- normalized volume;
- account / equity context;
- existing exposure;
- daily / weekly limits;
- spread / price / safety checks;
- all reason codes;
- risk-profile fingerprint.

Win probability is not part of execution correctness.

#### 4. Broker order placement

Before send:

- account identity must match the explicitly bound account;
- fresh risk revalidation must pass;
- final broker `order_check` / equivalent preflight must pass;
- the send intent must be persisted before the broker call.

After send, the journal must capture the broker response exactly:

- accepted;
- rejected;
- partially filled;
- timeout / UNKNOWN;
- ticket / order / deal identifiers when available;
- broker retcode and message.

No blind retry is allowed after an ambiguous send.

#### 5. Open-position management

While a position is open, the system must always be able to answer:

- why this position exists;
- what its current protection is;
- whether SL/TP is present and valid;
- whether the original setup is still valid if the strategy uses invalidation exits;
- whether a risk kill / emergency condition exists;
- whether broker state matches journal state.

#### 6. Exit

Every exit must have one explicit reason category, for example:

- `STOP_LOSS`;
- `TAKE_PROFIT`;
- `STRATEGY_INVALIDATION`;
- `RISK_KILL`;
- `PROTECTION_FAILURE`;
- `MANUAL_OWNER_EXIT`;
- `BROKER_FORCED_EXIT`;
- other deterministic, documented reason code.

The system must not infer a vague reason after the fact if the reason was knowable at decision time.

#### 7. Final trade record

The complete chain must be reconstructable from the journal:

**setup -> candidate -> risk verdict -> order intent -> broker events -> fills -> position -> exit decision -> closed result -> P/L / R result**

The outcome may be a win or a loss. Both are acceptable for this milestone if the lifecycle is technically correct.

## 2. Acceptance criteria for the primary milestone

The First Complete Trade Lifecycle is PASS when at least one controlled DEMO trade completes the full loop and all of the following are true:

- the system independently creates a valid order intent from a strategy decision;
- the entry reason is explicit and persisted;
- deterministic risk approves the exact order parameters;
- account identity guard passes;
- fresh revalidation and final preflight pass;
- the broker accepts and opens the position;
- the position is reconciled from broker truth rather than assumed from the send response;
- the system tracks the open position and its protection;
- the position exits normally or by an explicit controlled exit path;
- the exit reason is persisted;
- all broker order/deal/position identifiers are mapped back to the same lifecycle;
- no duplicate exposure is created;
- there is no unresolved UNKNOWN state at completion;
- the full causal chain can be explained from journal records without reading source code or guessing.

**Profitability is explicitly NOT an acceptance criterion for this milestone.**

## 3. Immediate execution roadmap

There are four practical stages between the current system and the first controlled live canary.

### Stage 1 — Make the execution path operational

Goal: prove the real code path can place and close orders in DEMO.

Work:

1. explicitly bind the intended DEMO account;
2. wire the production candidate/risk result into the guarded execution service;
3. create the order intent from the approved deterministic risk result;
4. run fresh broker/account/safety/risk revalidation immediately before send;
5. run final broker preflight;
6. persist `SEND_STARTED` before the broker send;
7. reconcile broker order/deal/position truth after send;
8. implement / verify explicit close-position path;
9. persist entry and exit reason codes end-to-end;
10. ensure OBSERVE remains the default mode and trade-capable mode requires explicit owner arming.

**Acceptance:** the code is capable of opening and closing one DEMO position through the guarded path without any manual database edits or hidden bypasses.

### Stage 2 — First Complete DEMO Trade

Goal: execute one full lifecycle using the real runtime graph.

The first trade does not need to be profitable and does not need to prove strategy edge.

Required trace:

`market evidence -> strategy reason -> risk reason -> broker request -> broker response -> fill -> live position -> exit reason -> broker close -> final P/L`

After completion, audit the trace and fix only defects that break lifecycle correctness, safety, reconciliation or explainability.

### Stage 3 — Short DEMO execution campaign

Goal: prove the lifecycle is repeatable and does not fail on basic broker/runtime edge cases.

Do not require 100 trades before learning from live canary. Target approximately **10–20 complete DEMO lifecycles**, while deliberately covering the important execution classes:

- normal fill and normal close;
- broker rejection;
- timeout-but-accepted / ambiguous send reconciliation;
- partial fill if broker behavior allows reproduction;
- process/service restart during a lifecycle;
- UNKNOWN recovery;
- protection verification / missing SL-TP handling;
- explicit emergency close;
- duplicate/idempotency protection.

**Acceptance:** no duplicate exposure, no unexplained order, no unresolved broker state, and every lifecycle is reconstructable from the journal.

### Stage 4 — LIVE_CANARY

Goal: learn from the real market with the smallest practical financial exposure after the execution loop is proven.

Initial live scope:

- one explicitly approved account;
- one symbol;
- one strategy version;
- broker-minimum or otherwise deliberately tiny volume;
- strict daily loss cap;
- explicit manual owner arm;
- active kill switch;
- full event journal and alerting;
- no autonomous LLM trading authority.

The canary is not expected to prove profitability immediately. Its first purpose is to validate real-market execution, slippage, broker behavior, reconciliation and operational reliability under real exposure.

Expansion beyond canary requires evidence, not optimism.

## 4. Strategy profitability is a separate workstream

Current Strategy V1 evidence remains FAIL and must be represented accurately:

- `trend_pullback_v1`: negative untouched-test expectancy;
- `volatility_breakout_v1`: insufficient sample and negative observed test result.

That means Strategy V1 must not be promoted as a proven profitable strategy.

However, a failed or mediocre strategy does **not** prevent us from proving the execution lifecycle in DEMO.

Strategy work proceeds separately:

1. create Strategy V2 / new configuration version;
2. tune only on train/validation data;
3. preserve a new untouched future OOS period;
4. include realistic costs and slippage assumptions;
5. evaluate expectancy, drawdown, stability and sample size;
6. freeze the version before live-canary strategy approval.

The target is not 100% win rate. The target is a robust positive expectancy after realistic costs with acceptable downside behavior.

## 5. LLM is not on the critical path to first live trading

LLM/advisory remains optional and must not block execution validation.

For the initial execution and live-canary path:

- deterministic strategy + deterministic risk remain authoritative;
- LLM may observe or produce shadow advisory;
- LLM may not invent trade direction, position size or execution commands;
- provider failure must not break the deterministic lifecycle;
- BOT_ONLY must always remain viable.

Whether LLM adds measurable value is a later experiment, not a prerequisite for placing the first controlled order.

## 6. What remains safety-critical before any real-money order

The following are still hard blockers because they protect the execution boundary directly:

- explicit account binding / account identity match;
- deterministic risk decision for the exact intended order;
- execution enablement requiring explicit owner action;
- fresh risk revalidation immediately before send;
- final broker preflight;
- persistence-before-send / idempotency discipline;
- no blind retry after ambiguous send;
- broker reconciliation after send/restart;
- working stop/protection semantics;
- working emergency close / kill switch;
- complete journal linkage for entry and exit.

These are not “paper hardening”; they are part of making a real trade safely and explainably.

## 7. Items moved out of the critical path

The following may improve production quality, but they are **not blockers for the first complete DEMO lifecycle** and generally are not blockers for the first tiny LIVE_CANARY unless a concrete defect is discovered:

- 100 DEMO lifecycle target;
- long BOT_ONLY vs BOT_LLM evidence campaign;
- proving LLM alpha;
- architectural cleanup unrelated to execution correctness;
- full production-perfect observability;
- every rare broker edge case before the first canary;
- strategy win-rate targets;
- strategy statistical perfection;
- broad multi-symbol rollout;
- broad multi-strategy rollout.

Some prior operational evidence such as soak, reboot/DR, latency telemetry and shutdown hygiene remains valuable. It should continue in parallel and become a blocker only if it reveals a defect capable of corrupting execution, reconciliation or risk control.

## 8. Current deployed state — 2026-09-04

Current source/release:

- branch `main` synchronized with `origin/main`;
- commit `df5b0d8246f893738905c90ac3b03356ab5724db`;
- active release `/home/dinhtc/apps/forex-ai/releases/20260904T104215Z-df5b0d8246f8`;
- database schema version `10`;
- DB integrity check `ok`;
- observer active;
- candidate/review/ops timers active;
- `execution_enabled: false`;
- `order_intents_v1: 0` at deployment validation;
- account was not automatically bound;
- no live order was sent during remediation/deployment.

Production scanner telemetry immediately after deployment observed total three-symbol scan latency around **14.5–14.6 seconds** for two cycles. This is useful runtime evidence but is not yet a long-window p95 claim.

The execution boundary already includes explicit side, identity-guard support, fresh risk revalidation, final broker preflight and ambiguous-send protection. The next development work must connect these pieces into the first real controlled DEMO lifecycle rather than adding unrelated architecture.

## 9. Current milestone status

| Milestone | Status | Meaning |
|---|---|---|
| Release/deploy integrity | **PASS** | Current release deployed and healthy |
| Deterministic Strategy -> Risk | **PASS in deployed read-only path** | Candidate/rejection/risk chain available |
| Account binding | **NOT YET ARMED** | Must explicitly bind intended DEMO account before execution |
| Real order open path | **NOT YET PROVEN** | Next implementation target |
| Real order close path | **NOT YET PROVEN** | Must be part of same lifecycle target |
| Full entry/exit reason trace | **PARTIAL** | Decision/risk tracing exists; broker lifecycle trace must be completed |
| First Complete DEMO Trade | **NOT YET DONE** | Primary project milestone |
| Repeatable DEMO campaign | **NOT YET DONE** | Follows first successful lifecycle |
| Strategy profitability approval | **FAIL for current V1** | Separate strategy workstream |
| LIVE_CANARY | **NOT YET APPROVED** | Follows execution proof + owner Go decision |

## 10. Development decision rule

For every new task, ask:

> **Does this directly help the system place, manage, exit or explain a real trade, or protect a critical risk/reconciliation boundary?**

If yes, it belongs on the execution path.

If no, it goes to backlog or a parallel research/hardening workstream and must not indefinitely delay the first complete controlled lifecycle.

The project must avoid becoming a perfectly documented system that never trades.


## 11. Full audit update — 2026-09-04

Source implementation now includes the guarded execution runner, explicit close/exit journal, broker-driven SL/TP exit reconciliation, REAL/DEMO account-mode guards, account-binding readiness checks, execution lifecycle reconciliation, and schema v11. Full verification: **159 tests collected, all pass**, compileall PASS, `git diff --check` PASS.

Audit decision remains **LIVE NO-GO** for concrete runtime reasons, not because of unfinished architecture:

- current connected account is REAL;
- one existing XAUUSDc 0.05 position is present without SL/TP and does not appear to be Forex-AI-owned;
- runtime correctly blocks new entries with `UNPROTECTED_POSITION`;
- account binding is missing;
- persistent trading control is disarmed/kill-switched by default;
- no approved live strategy/canary artifact exists;
- no complete Forex-AI broker lifecycle has yet been observed.

The code may be deployed disarmed. The read-only execution reconciliation timer should run in production. The guarded execution timer must remain disabled until these direct P0 conditions are resolved. See `FULL_AUDIT_2026-09-04.md`.
