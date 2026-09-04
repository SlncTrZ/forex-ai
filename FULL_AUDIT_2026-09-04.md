# Forex-AI — Full Audit 2026-09-04

## Executive conclusion

**LIVE status: NO-GO at audit time.**

This is not because the project needs more architectural perfection. The remaining blockers are direct execution/risk facts on the currently connected broker account.

## What is now implemented

The execution-first milestone has materially advanced:

- explicit REAL/DEMO account-mode guard;
- environment-scoped execution enablement and one-symbol runtime override;
- guarded production execution runner (`scripts/execute_pending.py`);
- persisted intent before broker send;
- account identity check before execution;
- fresh deterministic risk revalidation immediately before send;
- final broker `order_check` immediately before send;
- single-send / UNKNOWN reconciliation semantics;
- explicit guarded close path that remains available when the entry kill switch is active;
- explicit exit-reason journal (`trade_closures_v1`);
- broker-driven SL/TP/other exit reconciliation;
- read-only lifecycle reconciliation runner (`scripts/reconcile_execution.py`);
- systemd templates for execution reconciliation and guarded execution;
- expected safety blocking in candidate scanner no longer needs to be reported by systemd as a service crash;
- schema version raised to 11.

## Verification

- Full test suite: **159 tests collected; all pass**.
- `python -m compileall`: PASS.
- `git diff --check`: PASS.
- Execution runner in OBSERVE: correctly refuses to run.
- LIVE_CANARY readiness against the current account with execution hypothetically enabled and symbol scope reduced to one symbol: FAIL-CLOSED.
- DEMO readiness against the current account: FAIL-CLOSED because the connected account is REAL.

## Runtime audit findings

### P0 — Existing unprotected broker position

The currently connected REAL account has one existing XAUUSDc position with:

- volume 0.05;
- no stop loss;
- no take profit;
- magic 0;
- empty comment.

This does not look like a Forex-AI-created position. Forex-AI must not modify or close it automatically without explicit owner action.

The safety kernel correctly reports `UNPROTECTED_POSITION`, which puts runtime health into BLOCKED for new entries.

**Impact:** this is a real hard blocker for opening another automated position because total downside exposure is not bounded or attributable to an existing Forex-AI intent.

### P0 — Account identity is not bound

The connected account has not been explicitly persisted through the owner-controlled account binding mechanism.

Readiness reason: `ACCOUNT_BINDING_MISSING`.

**Impact:** execution cannot prove that the broker account being traded is the account intentionally authorized by the owner.

### P0 — Trading control is deliberately disarmed

Current persistent trading-control state is absent/default, therefore:

- `armed = false`;
- kill switch active by default;
- arm expiry absent.

Readiness reasons include:

- `CONTROL_DISARMED`;
- `KILL_SWITCH_ACTIVE`;
- `ARM_EXPIRED`.

This is working as designed.

### P0 — No approved live strategy artifact

Current Strategy V1 research evidence remains negative/insufficient. No strategy-approval artifact exists for LIVE_CANARY.

Readiness reason: `STRATEGY_APPROVAL_MISSING`.

This does not block execution engineering or DEMO testing, but it does block autonomous strategy-driven real-money canary under the current release policy.

### P1 — First complete broker lifecycle still lacks real execution evidence

The code path can now represent the complete lifecycle, including broker-driven exit reasons, but the project still has no completed Forex-AI broker lifecycle in the production journal.

Current production facts at audit time:

- `order_intents_v1 = 0`;
- no Forex-AI position has been opened by the guarded runner;
- no complete entry -> fill -> position -> exit lifecycle has been observed on the broker.

The currently connected account is REAL, not DEMO, so the DEMO milestone cannot honestly be claimed on this account.

## Important operational finding

The candidate scanner was showing as `failed` in systemd because expected fail-closed safety states returned non-zero process exit codes. This conflated "trading blocked for safety" with "service crashed".

Source has been changed so expected health/sync safety blocks emit a blocked status but exit successfully; operational health remains the authoritative blocker. This preserves fail-closed trading while avoiding false service-crash noise.

## Live-canary readiness result at audit time

With LIVE_CANARY mode and execution flag hypothetically enabled for one symbol, readiness remains false for concrete reasons:

- `ACCOUNT_BINDING_MISSING`;
- runtime blocked by existing unprotected position;
- `CONTROL_DISARMED`;
- `KILL_SWITCH_ACTIVE`;
- `ARM_EXPIRED`;
- `STRATEGY_APPROVAL_MISSING`.

Therefore enabling the execution timer now would be a bypass of direct safety invariants, not progress toward a real trading system.

## What is no longer a blocker

The audit does **not** require the following before the first controlled lifecycle:

- 100 DEMO trades;
- 100% win rate;
- proven LLM alpha;
- long BOT_ONLY/BOT_LLM study;
- architectural perfection;
- broad multi-symbol rollout.

## Shortest path from this audit to first live canary

1. Resolve the existing unprotected manual/foreign broker position by owner decision (protect it or close it outside Forex-AI).
2. Explicitly bind the intended broker account.
3. Establish an execution test environment. A true DEMO account is preferred; if no DEMO account is used, the owner must knowingly accept that the first broker lifecycle test occurs on the REAL cent account.
4. Complete at least one full guarded lifecycle and verify journal trace: strategy/setup reason -> risk -> send -> fill -> position -> exit reason -> closed P/L.
5. Produce an explicitly approved strategy/canary artifact or explicitly define a separate execution-probe canary policy that is not represented as a profitable strategy.
6. Arm trading for a short expiry window, one symbol, minimum practical exposure, and enable the guarded execution timer.

## Audit decision

**Do not enable autonomous LIVE_CANARY yet.**

The code should be deployed with execution disabled/disarmed and the read-only execution reconciliation timer enabled. The guarded execution unit may be installed but must remain disabled until the P0 facts above are resolved.
