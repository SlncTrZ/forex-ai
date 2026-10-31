# Ollama AI Safety Gate / Pre-Execution Filter

**Role: ACTIVE execution filter, not shadow mode.** This gate runs immediately
before fresh broker/risk revalidation and MT5's final `order_check` in
`GuardedExecutionService.send_once()` on the real
`scripts/execute_pending.py` entry path.

## Decision contract

- The model receives a minimal allowlisted candidate/order and market snapshot:
  strategy, symbol, BUY/SELL side, volume, entry, stop, target, tick age,
  spread in points, and account trade **mode** (not login, server or keys).
- Model output MUST match the Ollama JSON Schema response:
  `intent_id`, `request_digest`, `decision: ALLOW|BLOCK`, and
  `reason_code` (uppercase machine-readable).
- `ALLOW` **does not place a trade**; fresh account binding, ARM expiry, Kill
  Switch, Broker-Aware Risk Gate and final MT5 `order_check` are required
  after inference. The model has no tool capable of placing orders.
- `BLOCK`, unavailable model, timeout, invalid/mismatched response, missing
  configuration or audit failure **never** falls back to sending a new order.
  Only new entries are filtered; broker position management and emergency
  protection paths are not subject to this callback.
- Every ALLOW/BLOCK/UNAVAILABLE records `AI_SAFETY_GATE_DECISION` with
  intent/candidate IDs, reason code, model config fingerprint and latency.
  Raw responses and credentials are never stored in audit events.
- The required gate **cannot reduce order size** silently; only ALLOW/BLOCK
  are supported. The separate Risk Engine alone controls permitted volume.
- Historical advisory `BOT_ONLY_FALLBACK` rules do not apply to this
  mandatory pre-execution gate.

## Server activation (before production deployment)

Configure the systemd user's private execution environment
`~/.config/forex-ai/execution.env`:

```dotenv
FOREX_AI_SAFETY_GATE_URL=http://<private-IP>:11434
FOREX_AI_SAFETY_GATE_MODEL=<exact-model-tag>
FOREX_AI_SAFETY_GATE_TIMEOUT_SECONDS=8
```

Replace placeholders with the verified internal IP and exact tag returned by
`ollama list`. Do **not** add secrets to Git, documentation or chat. Plain
HTTP is limited to private, non-loopback IPs; a trusted HTTPS hostname is also
supported. Avoid exposing Ollama to the public Internet.

Verify Ollama connectivity/model/API on the intended executing host first,
using the model list and an isolated structured-output request. A model that
does not support valid JSON Schema output must be rejected. The chosen model
is kept resident for up to ten minutes per call (`keep_alive`). Timeout defaults
to 8 seconds with a 2-second connect cap.

**Production deployment is blocked until this server, exact model tag,
connectivity, inference latency, existing service health, and rollback
plan are verified.** The code branch is intentionally not installed on the
current executing host until then, to avoid unexpectedly blocking active
trading due to an unconfigured remote model.

## Ownership & safety

- Live execution remains in `scripts/execute_pending.py`; no new trading
  control, ARM or account binding bypass is introduced.
- No per-tick model calls: one evaluation for a prepared order intent.
- AI output cannot authorize expired risk leases; fresh risk/ARM checks occur
  after inference.
- If Ollama stops responding, pending **new** orders are rejected with an
  audit entry. The observer/reconciler/dashboard and already-open positions
  continue independently.
- Operator may monitor decisions through the existing audit journal.
