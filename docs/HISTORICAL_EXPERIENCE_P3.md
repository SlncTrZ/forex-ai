# P3 — Historical Experience Retrieval (Development, not yet released)

**Scope:** Read-only-source retrieval of trade outcomes with strict position/candidate lineage.
This is **not** trained machine learning, a calibrated win probability, or a trading gate.

## Production evidence before development (2026-10-31)
- `trades`: 355 closed records — 339 manual, 16 system-owned by the journal.
- Of 16 system trades, **15** had complete candidate/intent lineage via
  `trades.mt5_position_id` → `order_intents_v1.broker_position_ticket` →
  `candidate_decisions.candidate_id`; 1 had no complete candidate linkage.
- No system-owned BTCUSDm closed-trade history was observed in this snapshot.
- Old candidate payloads contain entry, stop, take-profit and fingerprints, **not**
  original RSI/EMA/ADX/Regime or full historical indicator matrix snapshots.
- Thus historical similarity is **STRUCTURAL_ONLY**; no claims of indicator-pattern similarity.

## Implementation
- `src/forex_ai/research/experience_memory.py` uses SQLite `mode=ro` and
  `PRAGMA query_only` to retrieve only system-owned, settled, uniquely joined
  trades matching the requested candidate's exact symbol, strategy ID,
  strategy version and side.
- Query/selection rejects manual trades, broker-open positions, pending intents,
  missing/ambiguous positions and future outcomes; compares strictly against
  the candidate's original generated_at_utc. No look-ahead.
- Ranking uses only **pre-entry** context: relative SL distance, UTC session
  proximity and configuration fingerprint match. The outcome is recorded only
  after retrieval; outcome profit or exit price never influences similarity rank.
- `read_experience(..., limit≤12)` has bounded scans (2,000 matching rows).
  Returned evidence contains pseudonymous match IDs, date, similarity, observed
  net PnL and outcome. It exposes **no** broker account IDs, secrets or raw MT5 payloads.
- `GET /api/experience?candidate_id=<id>` provides a bounded read-only lookup
  on authenticated Command Center transport.
- `src/forex_ai/research/experience_observer.py` and
  `scripts/observe_experience_memory.py` prepare a separate 30-minute periodic
  scanner of up to 24 recent candidates. Its audit `HISTORICAL_EXPERIENCE_RESEARCH`
  is idempotent per candidate/policy/**evidence fingerprint**; verified broker
  backfills produce a new audit while identical evidence is not duplicated.
  `deploy/forex-ai-experience-research.{service,timer}` are **not installed** in Production.

## Validity and policy
- Minimum provisional support 20 comparable system trades, but even reaching
  that does **not** make this calibrated: historical full-feature coverage is
  presently zero. Policy never generates a learned score; P2 History remains 0/5.
- Small n observed win rate and net PnL are retrospective bookkeeping,
  **not** projected win probability, expected R, or instructions to trade.
- No D3 model calls are performed by P3. Preparing a D3 review packet requires
  fuller entry-time features and owner-approved data/policy governance.
- No ARM/KILL, Risk Engine, broker position, strategy admission,
  pre-execution safety gate, or order sender is touched.

## Critical independent runtime alert
During release verification of the pre-existing `v0.0.8`, Command Center
showed `HEALTHY=false` with 36 `UNPROTECTED_POSITION` alerts for
active, external/manual BTCUSDm BUY positions (total 0.36 lots), all recorded
with missing SL/TP. No orders were changed by this P3 implementation.
Owner must review those positions separately in MT5; the new research module
is not a position-management control.

## Future milestones
1. Capture bounded **entry-time indicator snapshots** and regime/policy/model
   fingerprints for each *real* candidate/filled trade.
2. Reconcile PnL, slippage and R-multiples from broker deals; attach to exact
   candidate/intent and distinguish counterfactuals from fills.
3. Evaluate similarity/outcome validity on chronological holdout; calibrate
   uncertainty and detect drift before proposing a policy change.
4. Only with owner authorization let D3 review such evidence asynchronously;
   never let it silently revise trade admission, risk limits or weights.
