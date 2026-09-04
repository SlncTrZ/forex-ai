# Forex-AI — Current Status

Last updated: 2026-09-03

## Runtime

- DEV source-of-truth: `<DEV_TREE>` (backed by `DEV_HOST`)
- Runtime host: `RUNTIME_HOST` / `RUNTIME_HOST`
- Current release: `$HOME/apps/forex-ai/releases/20260903T045757Z`
- Current symlink: `$HOME/apps/forex-ai/current`
- Runtime venv: `$HOME/.venvs/forex-ai-runtime`
- SQLite: `$HOME/.local/share/forex-ai/forex.db`
- MT5 container: `forex-mt5`
- Observer service: active
- Candidate scanner timer: active, every 30 seconds
- DeepSeek pending-review timer: active + enabled; reviews only newly created signals in SHADOW mode

## Account / symbols

- Broker/server: Exness / `Exness-MT5Real36`
- Account currency: `USC` (cent account)
- `XAUUSD -> XAUUSDc`
- `EURUSD -> EURUSDc`
- `GBPUSD -> GBPUSDc`
- Minimum lot: 0.01 for all three
- Forex-AI real execution remains disabled

## Logging / audit guarantees

SQLite schema version: 5.

The system has an append-only `audit_events` black-box timeline with:

- `timestamp_utc` — ISO-8601 UTC with microseconds
- `epoch_ms` — system event time in milliseconds
- `market_time_msc` — authoritative MT5 market/deal timestamp when available
- `correlation_id` — links signal -> LLM -> risk -> execution timeline
- `event_type`, `source`, `symbol`, `entity_id`, full JSON payload

Event types prepared/used include:

- `SIGNAL`
- `LLM_CONTEXT`
- `LLM_WEB_TRACE`
- `LLM_DECISION`
- `LLM_ERROR`
- `ENTRY_REQUEST`
- `ENTRY_FILLED`
- `ENTRY_REJECTED`
- `EXIT_REQUEST`
- `EXIT_FILLED`
- `EXIT_REJECTED`
- `SL_TP_UPDATE`
- `RISK_REJECT`

MT5 deal reconciliation converts real trade fills into entry/exit audit events and stores both native `time_msc` and derived UTC fill time. Funding/balance deals are intentionally excluded from entry/exit audit.

A direct database verification recorded a sample LLM audit timestamp with microsecond precision:

`2026-09-03T04:21:59.174541+00:00`

Automated tests verify signal and LLM decision correlation plus precise audit timestamps.

## Live signal bot

The read-only candidate scanner runs every 30 seconds and reads live MT5 context for all three target symbols.

Signal generation uses only closed candles for technical evidence and currently considers:

- M5 / M15 / H1 / H4 trend state
- EMA20 / EMA50
- RSI14
- ATR14
- ADX14
- prior 20-bar high/low
- 20-bar breakout state
- distance from EMA20 in ATR units
- 20 recent closed candles per timeframe
- current tick only for a proposed entry reference

Initial strategies:

- `trend_breakout`
- `trend_pullback`
- `trend_continuation`

Signals need score >= 0.65. Duplicate signals on the same closed M15 candle/strategy/direction are prevented with a deterministic `signal_key`.

Current live scan has produced no valid signal yet; the bot does not manufacture trades just to generate activity.

## Full LLM context

Every DeepSeek review is built immediately from live state and includes:

- authoritative UTC clock with microseconds and epoch milliseconds
- Asia/Ho_Chi_Minh local time
- day/date and approximate active market sessions
- live account balance/equity/margin/free margin/leverage
- cent-account USD-equivalent values for readability
- all currently open positions across the account
- positions for the reviewed symbol
- current bid/ask/spread and tick timestamp/age
- broker contract/tick/volume constraints
- M5/M15/H1/H4 closed-candle technical context
- current still-forming candle separated from closed-candle evidence
- candidate signal details: strategy, direction, score, proposed entry/SL/TP/RR and evidence
- relevant historical lessons from SQLite
- symbol-specific current macro drivers that must be verified

The prompt explicitly tells the model that injected clock + MT5 state are authoritative and that training-time knowledge must not be treated as current market truth.

## DeepSeek

Selected default model: `deepseek-v4-flash`.

Implementation uses DeepSeek Responses API with structured JSON Schema output. A server-side `web_search` tool runs under `auto`; the client rejects any final decision that has no web-search trace, so current macro/news/geopolitical context must be checked rather than guessed from training memory. The client also accepts only a JSON object that validates against the strict `ReviewDecision` schema, even if the model prepends prose after tool use.

The review output includes:

- BUY / SELL / NO_TRADE
- confidence
- thesis
- invalidation
- risk flags
- lesson references
- whether web search was used
- current-context checks with source references

DeepSeek usage fields and calculated API cost are stored with each decision.

Budget guards are configured at:

- max 100 calls/day
- max $0.25/day initially

The DeepSeek key is installed locally with mode `600`; the automatic shadow reviewer is enabled. Paid smoke validation succeeded with `deepseek-v4-flash`: decision `NO_TRADE`, confidence `0.62`, 52,560 input tokens, 3,144 output tokens, 40,192 cached input tokens, API cost `$0.005077344`, latency about 30.5s. Total tracked DeepSeek spend for the four validation attempts was about `$0.01933`.

## Safety state

- runtime mode: `OBSERVE`
- `execution_enabled: false`
- no LLM has an order-execution tool
- candidate scanner is read-only and now persists deterministic `BrokerAwareRiskEngine` verdicts
- pending DeepSeek reviewer consumes V1 candidates through `AdvisoryRuntime`; the legacy provider bridge is forced to advisory `NO_CHANGE` and has no trade authority
- persistent account binding is required for execution-capable paths; binding is never created automatically
- fresh risk revalidation + final broker preflight are mandatory before any future send

## Tests

Current remediation working tree: **152 passing tests** (`pytest -q -p no:cacheprovider`).

It covers the earlier suite plus:

- execution locks / risk modes
- symbol mapping
- MT5 history upsert
- entry/exit deal audit + idempotency
- timestamp/correlation audit
- signal -> LLM correlation
- feature calculations
- DeepSeek cost calculations
- persistent account identity binding/fail-closed mismatch handling
- database-enforced opportunity deduplication
- V1 Strategy -> deterministic RiskEngine persistence
- strict advisory schemas and persistent daily advisory budget
- fresh-risk drift rejection before broker send
- narrow broker-rollover gap classification and runtime resilience

## Next implementation stage

No owner action is currently required for data collection or DeepSeek shadow review.

The next engineering stage is the deterministic risk + execution path for the cent account:

1. finish account fingerprint enforcement and broker/spec revalidation before every order
2. implement deterministic position sizing from stop distance and cent-account equity
3. implement order preflight / duplicate prevention / spread-slippage / daily-loss guards
4. connect accepted BOT+LLM decisions to an execution adapter while retaining `execution_enabled: false`
5. exercise the full path in dry-run and reconciliation tests
6. only after those tests, explicitly enable guarded cent execution
