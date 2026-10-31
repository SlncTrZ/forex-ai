# Decision Model D3 — active AI Safety Gate / Pre-Execution Filter

## Purpose
This is **real-path enforcement**, not shadow testing. Every candidate selected
for a new MT5 entry must pass the D3 decision API, then fresh deterministic
account/risk/order checks in `GuardedExecutionService.send_once()`.

No Python, network or model component in this integration can bypass the
existing Kill Switch, ARM expiry, account binding, margin/risk control or
broker-side `order_check`. Existing position management is unaffected.

## Existing protected private configuration
The systemd unit `forex-ai-execute-pending.service` imports:
`~/.config/forex-ai/execution.env`.

- `DECISION_MODEL_URL`: full HTTPS decision endpoint; the code validates
  the expected path and never logs it.
- `DECISION_MODEL_API_KEY`: primary Bearer credential.
- `OLLAMA_KEY`: backward-compatible fallback when the primary key is absent.
- `FOREX_AI_SAFETY_GATE_MODEL`: optional model override (default `d3:4b`).
- `FOREX_AI_SAFETY_GATE_TIMEOUT_SECONDS`: optional timeout (default 8s).

Do not copy keys to the repository or audit journal. The private env file
must remain owner-read-only (mode 0600). The system uses HTTPS and refuses
redirects. No raw response, token or Authorization header is logged.

## Decision contract
The provider sends a minimal and sanitized order/market snapshot as the
`state` with two zero-generation questions:

- `action` is a `choice` between `execute` and `reject`
- `safety_score` is a `score` across Danger/Caution/Safe

The observed API returned `answers.action` with a `choice`,
`probabilities`, and `confidence`. For an ALLOW, both confidence and
P(execute) must be ≥0.8. A malformed response, missing fields or
inconsistent probabilities is unavailable and **blocks new entries**.
Remote errors/timeouts fail closed. All decisions are audited with reason,
latency and model/config fingerprint.

**Model results are not guarantees:** In a direct authenticated test, D3
allowed a synthetic BUY with stop-loss above entry. Consequently, the
local `_deterministic_block()` checks positive finite prices/volume, the
BUY/SELL stop/target ordering, and order-request consistency BEFORE the
remote model call. This rule vetoes even if the model would say execute.
Fresh risk and live broker checks still run after ALLOW.

The input explicitly labels unavailable news/SMC/volume/trend features;
the model must not invent evidence. Full feature ingestion may be added
in a separate step with verified provenance.

## Testing and rollout
Use `scripts/probe_ollama_safety_gate.py` only with a private environment
loaded into the process. The script uses synthetic data and never calls
MT5. Validate `tests/test_ollama_safety_gate.py` and the full regression
suite, then release in a clean, rollback-capable deployment.

The known `tests/test_auto_week.py` approval-symbol fixture mismatch in
the parent source must be fixed/reconciled separately; it is not an excuse
to rewrite live approval boundaries or silently skip production gates.

Production is **not** switched just by this code change. A deploy must pass
the full prescribed release process, including current approval documents
and private build assets.
