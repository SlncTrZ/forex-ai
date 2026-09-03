# Gate 1 Maintenance Runbook — MT5 Resilience Acceptance

Status: **MAINTENANCE ACCEPTANCE PASS — 7-DAY OBSERVE SOAK IN PROGRESS**
Execution remained disabled for the entire maintenance session.

## Executed maintenance window

- Date: 2026-09-03
- Runtime: `.227`
- Mode: `OBSERVE`
- Final tested source: `6cf220e`
- Automated suite: 118/118 PASS
- Soak start: 2026-09-03 17:54:40 +07
- Earliest soak completion review: 2026-09-10 17:54:40 +07

The originally proposed 2026-09-04 window was superseded by explicit owner approval to execute immediately on 2026-09-03.

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

## Execution evidence — 2026-09-03

Maintenance exposed and fixed several real production defects before acceptance:

- installed-package config path resolution failed outside the source tree;
- release dependency installation could inherit test `PYTHONPATH` and produce false dependency satisfaction;
- MT5 cleanup could throw `EOFError` on a dead socket and crash the observer;
- upstream `lprett/mt5linux` startup was not restart-safe because stale FIFO state and a non-first-run `apply_mt5_config` return code could terminate the container under `set -e`;
- managed `mt5linux` reconnects could auto-create auxiliary containers during bridge loss, causing port races;
- tick future-drift validation incorrectly used cycle-start time instead of wall-clock at tick read.

Accepted fixes include restart-safe MT5 entrypoint handling, best-effort dead-socket cleanup, `mt5.engine: external` ownership separation so the observer only connects to the existing RPyC bridge, and injectable wall-clock validation.

Observed acceptance evidence:

- synchronized-source release deployment succeeded and release audit events were persisted;
- startup produced `CONNECTING -> SYNCING -> HEALTHY`;
- real `docker restart forex-mt5` produced degraded/reconnect/resync behavior and returned to `HEALTHY` without observer process failure after fixes;
- controlled `docker network disconnect/connect bridge forex-mt5` produced `DEGRADED -> CONNECTING -> SYNCING -> HEALTHY` with bounded backoff;
- no `mt5linux-*` auxiliary container remained after external-bridge conversion;
- no candidate decision was emitted during the degraded network-fault interval;
- SQLite `PRAGMA integrity_check` returned `ok`;
- account identity remained stable (`login/server/currency` unchanged), with current broker positions=0 and pending orders=0;
- latest safety snapshots were reconciled with no blocking reasons;
- no local execution order intent existed;
- broker order/deal persistence uses unique ticket keys, and reconciliation completed without a safety-critical unknown state.

Therefore the **Gate 1 maintenance drill is PASS**.

## Remaining Gate 1 criterion

The only remaining Gate 1 acceptance criterion is the mandatory seven continuous days of OBSERVE operation defined in `DEVELOP_PLAN.md`. The soak clock begins at `2026-09-03 17:54:40 +07` and reaches its earliest valid completion at `2026-09-10 17:54:40 +07`.

Any unreconciled data loss, account/contract drift, duplicate/lost broker fact, stuck degraded state, safety-critical journal failure, or unexpected execution activity resets the soak clock. If the interval is clean, Gate 1 can then be marked **PASS** without another destructive maintenance drill.
