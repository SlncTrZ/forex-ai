# Gate 1 Maintenance Runbook — MT5 Resilience Acceptance

Status: SCHEDULED-PROPOSED / NOT YET EXECUTED
Owner approval required before destructive fault steps.
Execution mode must remain disabled for the entire window.

## Proposed maintenance window

- Date: 2026-09-04
- Time: 09:00–10:00 Asia/Ho_Chi_Minh (UTC+7)
- Pre-check: 08:45–09:00
- Fault drill: 09:00–09:30
- Reconciliation / acceptance review: 09:30–09:45
- Rollback reserve: 09:45–10:00

Reason for this window: market data should be available for fresh-tick/recovery verification, while the drill remains isolated to the test/OBSERVE runtime. No trading execution is permitted.

## Hard prerequisites

1. `execution_enabled=false` in the deployed release.
2. Persistent control state remains `armed=false` and/or `kill_switch=true`.
3. No unresolved `SEND_STARTED` or `UNKNOWN` execution intents.
4. Repository source is clean and synchronized with upstream; the production deploy script must not be bypassed.
5. Full automated suite passes on the exact release candidate.
6. Release fingerprint and MT5 image digest are recorded.
7. Current runtime DB is backed up and SQLite integrity check passes.
8. Current release and `previous` release symlinks are recorded before activation.
9. Active OBSERVE process PID and MT5 container state are recorded.

If any prerequisite fails, the maintenance is NO-GO and no container/network fault is injected.

## Phase A — pre-maintenance baseline

Record:

- Git SHA / release fingerprint;
- schema version;
- account identity fingerprint;
- contracts fingerprint;
- strict symbol mapping;
- MT5 container ID/image digest;
- observer PID;
- last healthy heartbeat;
- last market timestamp;
- current positions/pending orders/history counts;
- DB integrity result and backup path.

Expected runtime state before fault injection: `HEALTHY` with a reconciled SafetySnapshot.

## Phase B — deploy resilient OBSERVE release

Deploy only through the hardened transactional release script.

Acceptance immediately after deploy:

1. process starts with execution disabled;
2. runtime goes through `CONNECTING -> SYNCING` before `HEALTHY`;
3. no strategy/execution decision is allowed before synchronization completes;
4. strict broker symbol mapping resolves successfully;
5. account and contract fingerprints match the approved baseline;
6. positions, pending orders, recent orders/deals and required bars/ticks reconcile;
7. heartbeat records show fresh MT5 and journal timestamps.

Rollback immediately if startup cannot reach a reconciled `HEALTHY` state.

## Phase C — MT5 container restart fault

1. Record final pre-fault heartbeat.
2. Restart the MT5 container once.
3. Observe runtime state transitions.

Required behavior:

- runtime leaves `HEALTHY` and enters `DEGRADED` and/or `DISCONNECTED`;
- no decisions are published while uncertain;
- bounded backoff is used;
- runtime reconnects;
- a full authoritative resync occurs before `HEALTHY` is restored;
- account/contract identity is revalidated;
- broker positions/orders/deals are reconciled;
- no duplicate journal facts or false orphan positions appear.

Failure to recover automatically is a Gate 1 failure.

## Phase D — controlled network interruption

Use a bounded interruption affecting only the MT5 bridge path. Do not alter broker/account configuration.

Required behavior is the same as the container restart drill:

`HEALTHY -> DEGRADED/DISCONNECTED -> CONNECTING -> SYNCING -> HEALTHY`

The runtime must never skip `SYNCING` after loss of authoritative broker connectivity.

## Phase E — reconciliation checks

Compare pre/post-fault facts:

- account identity fingerprint;
- contracts fingerprint;
- positions;
- pending orders;
- recent broker orders;
- recent deals;
- last closed candle per required timeframe;
- heartbeat sequence;
- journal duplicates/missing facts.

Gate 1 drill passes only if all authoritative broker facts reconcile and there is no safety-critical unknown state.

## Phase F — rollback triggers

Rollback to the prior release immediately if any of the following occurs:

- runtime cannot recover to reconciled `HEALTHY`;
- account or contract identity changes unexpectedly;
- journal DB integrity fails;
- missing/duplicate broker facts remain after resync;
- runtime emits decisions while degraded/syncing;
- restart loop/backoff becomes unbounded;
- release fingerprint cannot be mapped to audit records;
- execution is found enabled or armed unexpectedly.

After rollback, verify the previous release starts in OBSERVE and resynchronizes successfully.

## Gate 1 maintenance acceptance

The maintenance drill is PASS only when:

- container restart recovery passes;
- controlled network interruption recovery passes;
- no decisions occur during uncertain state;
- post-recovery reconciliation is exact;
- account/contract fingerprints are stable;
- heartbeat/audit evidence is complete;
- rollback path remains functional.

After PASS, begin the required seven-day continuous OBSERVE soak. Any unreconciled safety incident resets the soak clock.

## Current scheduling blocker

At runbook creation time, local `main` is ahead of `origin/main` and `DEVELOP_PLAN.md` is modified. The hardened production deploy intentionally rejects unsynchronized or dirty source. Therefore the maintenance window is scheduled as proposed but cannot become GO until the exact release candidate is committed, pushed/synchronized, tested, and the prechecks above pass.
