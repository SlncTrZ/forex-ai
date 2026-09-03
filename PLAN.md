# Forex-AI — Live Cent Account Research Plan

## 1. Goal

Build a low-cost live Forex research/trading system for exactly three primary symbols:

- XAUUSD
- EURUSD
- GBPUSD

The first objective is not to maximize profit. The first objective is to measure whether an LLM adds net value after API cost when trading a real cent account with strict deterministic risk controls.

Core comparison modes:

1. BOT_ONLY — deterministic signal + deterministic risk + execution
2. BOT_LLM — deterministic signal + LLM reviewer + deterministic risk + execution
3. LLM_PROPOSAL — LLM may propose a trade from a prepared market snapshot, but every proposal still passes the same deterministic risk engine
4. SHADOW — non-executed counterfactual decisions are logged so BOT and LLM can be compared without increasing capital risk

## 2. Host and deployment decision

Target host: RUNTIME_HOST (RUNTIME_HOST)

Verified environment:

- OS: Ubuntu 24.04.4 LTS
- Kernel: Linux 7.0.0-30-generic x86_64
- CPU: 8 logical CPUs
- RAM: 7.6 GiB + 4 GiB swap
- Python: 3.12.3
- SQLite: 3.45.1
- Project path: <DEV_TREE>
- Project storage mount: //DEV_HOST/Develop
- Available project storage: ~212 GiB at time of inspection
- Docker: available without sudo for the current user
- Host Wine: not required for the selected deployment
- SlncTrZ-MCP: running on the same host

Conclusion: RUNTIME_HOST is sufficient for the V1 workload. The workload is I/O-light and CPU-light: 3 symbols, feature calculation, SQLite journaling, LLM orchestration, and one MT5 terminal. GPU is not required unless local inference is introduced later.

Important MT5 constraint: the official MetaTrader5 Python wheels are Windows x86-64. The selected implementation isolates that Windows/Wine runtime inside Docker instead of installing Wine directly on the Ubuntu host.

Implemented V1 MT5 path:

Ubuntu RUNTIME_HOST
  -> Docker: lprett/mt5linux:latest
       -> Wine + MetaTrader 5 + official MetaTrader5 package
       -> RPyC bridge
       -> noVNC UI
  -> native Linux Python mt5linux 1.1.1 client
  -> Forex-AI

Network policy implemented:

- MT5 RPyC: 127.0.0.1:18812 only
- noVNC page: RUNTIME_HOST:8080 only
- noVNC WebSocket: RUNTIME_HOST:5901 only
- raw VNC is not published

### Development vs runtime boundary

`<DEV_TREE>` is the main **development source-of-truth**. It is backed by PC `DEV_HOST` and should contain source code, Git history, docs, tests, and configuration templates.

Long-running trading runtime must be independent from that SMB/CIFS mount:

- DEV source: `<DEV_TREE>`
- runtime release root on `RUNTIME_HOST`: `$HOME/apps/forex-ai`
- runtime venv: `$HOME/.venvs/forex-ai-runtime`
- runtime SQLite: `$HOME/.local/share/forex-ai/forex.db`
- runtime logs/state: `$HOME/.local/state/forex-ai/`
- MT5 runtime: Docker container on `RUNTIME_HOST`

Deployment copies a tested source snapshot into an immutable local release directory on `RUNTIME_HOST`, then atomically repoints `$HOME/apps/forex-ai/current`. This permits rollback and ensures an SMB outage on `DEV_HOST` cannot kill the live process.

SSH between devices may be used when convenient, but it is not required for this project because `RUNTIME_HOST` already sees the development tree through `<DEV_TREE>`. A direct BatchMode SSH test from current MCP user `$USER` to `DEV_HOST` currently fails authentication, so no deployment path should assume SSH until the correct remote user/key is configured.

## 3. Safety boundary

No LLM is allowed to invoke MT5 order execution directly.

All trading flow must be:

market data -> signal/proposal -> LLM decision (optional) -> deterministic risk engine -> execution adapter -> MT5

The deterministic risk engine has final authority and can reject, resize, or block every trade regardless of model output.

Initial controls to implement before the first real order:

- allowlist symbols only: XAUUSD, EURUSD, GBPUSD
- account-login allowlist
- cent-account confirmation flag
- max risk per trade
- max daily realized loss
- max daily equity drawdown
- max simultaneous positions
- max total correlated USD exposure
- max spread per symbol
- minimum risk/reward
- minimum stop distance / broker stop-level validation
- lot-size clamp using symbol volume_min / volume_max / volume_step
- duplicate-order prevention
- one active strategy decision per symbol/time-window
- trading kill switch
- stale-price rejection
- disconnected-terminal rejection
- insufficient-margin rejection
- execution deviation/slippage guard

Start with very conservative limits. Risk values must be configurable and must not be embedded in LLM prompts as the only source of truth.

## 4. Project structure

<DEV_TREE>/

  PLAN.md
  README.md
  .env.example
  .gitignore
  pyproject.toml
  config/
    app.yaml
    symbols.yaml
    risk.yaml
    llm.yaml
  src/forex_ai/
    __init__.py
    app.py
    config.py
    logging.py

    mt5/
      client.py
      bootstrap.py
      health.py
      symbols.py
      account.py
      market.py
      execution.py

    market/
      candles.py
      features.py
      regimes.py
      snapshots.py

    strategy/
      base.py
      signal_engine.py
      setups/
        trend_pullback.py
        breakout.py

    intelligence/
      context_builder.py
      reviewer.py
      proposal.py
      prompts.py
      schemas.py
      cost.py

    risk/
      engine.py
      sizing.py
      exposure.py
      guards.py

    journal/
      db.py
      migrations.py
      repository.py
      trade_journal.py
      lessons.py
      counterfactual.py

    learning/
      post_trade_review.py
      lesson_selector.py
      metrics.py

    runtime/
      scheduler.py
      event_loop.py
      state.py
      kill_switch.py

  data/
    exports/
  logs/                  # development placeholder only

Runtime data on RUNTIME_HOST local disk (not SMB):
  $HOME/.local/share/forex-ai/forex.db
  $HOME/.local/state/forex-ai/logs/
  scripts/
    init_db.py
    check_mt5.py
    smoke_market.py
    smoke_llm.py
    run_paper_shadow.py
    run_live_cent.py
    emergency_stop.py
  tests/

## 5. Database design — SQLite is source of truth

Use SQLite for V1. Do not add ChromaDB until semantic retrieval demonstrates a real need.

`<DEV_TREE>` is an SMB/CIFS mount from `//DEV_HOST/Develop`; therefore the live SQLite database must NOT be placed in the project directory. SQLite WAL/locking stays on the local filesystem of RUNTIME_HOST at `$HOME/.local/share/forex-ai/forex.db`. Project storage may receive backups/exports, not the active DB.

Suggested tables:

### accounts

- snapshot_id
- timestamp
- login
- server
- currency
- balance
- equity
- margin
- free_margin
- margin_level

### market_snapshots

- id
- timestamp
- symbol
- bid
- ask
- spread
- timeframe
- candle/feature payload JSON
- market_regime

### signals

- id
- timestamp
- symbol
- strategy
- direction
- score
- proposed_entry
- proposed_sl
- proposed_tp
- rr
- feature_snapshot_id

### llm_decisions

- id
- timestamp
- signal_id nullable
- symbol
- mode
- model
- prompt_version
- action
- confidence
- thesis
- risks JSON
- input_tokens
- output_tokens
- cached_tokens
- api_cost_usd
- latency_ms
- raw_response_hash

### risk_decisions

- id
- timestamp
- source_decision_id
- approved
- reason_codes JSON
- requested_lot
- approved_lot
- calculated_risk

### orders

- id
- timestamp
- mt5_order_ticket
- mt5_deal_ticket nullable
- symbol
- side
- volume
- requested_price
- executed_price
- sl
- tp
- deviation
- retcode
- execution_payload JSON

### trades

- id
- symbol
- source_mode
- open_time
- close_time
- entry_price
- exit_price
- volume
- sl
- tp
- gross_pnl
- commission
- swap
- net_pnl
- mfe
- mae
- exit_reason

### lessons

- id
- timestamp
- trade_id nullable
- symbol nullable
- setup
- regime
- lesson_type
- lesson_text
- evidence JSON
- confidence
- active
- superseded_by nullable

### shadow_decisions

- id
- timestamp
- source_snapshot_id
- actor (BOT/LLM)
- hypothetical_action
- hypothetical_entry/sl/tp
- result_after_horizon
- hypothetical_pnl

### system_events

- id
- timestamp
- severity
- component
- event_type
- payload JSON

## 6. LLM context contract

Never send raw uncontrolled terminal state to the model.

ContextBuilder prepares a compact structured snapshot containing only information available at decision time:

- account balance/equity/free margin
- current open positions
- current symbol bid/ask/spread
- broker symbol constraints
- current session/time
- selected candle data
- deterministic technical features
- market regime
- candidate setup if present
- relevant recent trade statistics
- selected lessons from prior similar trades
- current API-cost budget

The LLM returns strict structured JSON validated with a schema.

Minimum fields:

- action: BUY | SELL | NO_TRADE
- confidence: 0..1
- thesis
- invalidation
- risk_flags[]
- lesson_references[]

The model does not choose unrestricted lot size. Position sizing remains deterministic.

## 7. Memory / learning loop

V1 learning is retrieval-based, not model fine-tuning.

Every closed trade creates an immutable factual record first.

Then a post-trade review job may produce a lesson such as:

- setup classification
- what condition invalidated the thesis
- whether entry timing was poor
- whether spread/slippage mattered
- whether the LLM prevented or caused a bad trade
- whether a previous lesson was useful

Lessons must never overwrite raw history.

At each new decision, lesson_selector retrieves a small number of relevant lessons using deterministic filters first:

symbol -> setup -> regime -> direction -> recency/performance

Only add ChromaDB/vector search in a later phase if deterministic retrieval becomes insufficient for natural-language lessons.

## 8. Event-driven architecture

Do not call an LLM on every tick or every candle.

Collector continuously/periodically updates low-cost local state.

LLM is invoked only on defined events, for example:

- deterministic candidate setup reaches threshold
- important position-management event
- scheduled sparse market review (optional)
- post-trade review

This keeps API cost bounded and measurable.

## 9. Trading modes

### Mode A — OBSERVE

Connect MT5, read account/market data, write journal. No order calls.

### Mode B — SHADOW

Generate BOT and LLM decisions. No real order calls. Track hypothetical outcomes from live market data.

### Mode C — CENT_GUARDED

Allow cent-account execution through all hard risk checks.

### Mode D — CENT_EXPERIMENT

A/B assignment of eligible signals between BOT_ONLY and BOT_LLM, while recording shadow decisions from the other actor.

Production must default to OBSERVE after restart unless an explicit configuration permits CENT_GUARDED/CENT_EXPERIMENT.

## 10. Measurement framework

Track per symbol and per decision mode:

- trades
- win rate
- average win/loss
- expectancy
- profit factor
- max drawdown
- realized PnL
- floating drawdown
- MFE/MAE
- slippage
- spread paid
- API calls
- input/output tokens
- API cost
- cost per accepted trade
- cost per closed trade
- net PnL after API cost

Primary research metric:

LLM Net Value = PnL(BOT+LLM) - PnL(BOT baseline equivalent) - LLM API cost

Also measure:

- bad losses avoided by LLM rejects
- profitable trades incorrectly rejected by LLM
- losses introduced by LLM proposals
- model confidence calibration

## 11. Phase plan

### Phase 0 — Bootstrap and infrastructure

Deliverables:

- project skeleton
- Python virtual environment
- dependencies
- config loader
- SQLite schema/migrations
- structured logging
- secrets strategy (.env not committed)
- MT5/Wine bridge installed and documented

Acceptance:

- app starts
- DB migrations succeed repeatedly
- MT5 terminal reachable
- account_info and terminal_info can be read
- no trading permission is needed to pass Phase 0

### Phase 1 — MT5 read-only collector

Implement:

- terminal health
- account snapshot
- symbol discovery / broker symbol mapping
- tick retrieval
- M1/M5/M15/H1/H4 candles
- positions
- trade/deal history
- broker constraints

Acceptance:

- continuous read-only operation for at least one full session without duplicate/broken records
- reconnect after MT5 restart/network interruption
- symbols XAUUSD/EURUSD/GBPUSD mapped correctly even if broker uses suffixes (for example XAUUSD.a)

### Phase 2 — Journal and metrics

Implement:

- SQLite repositories
- system events
- account snapshots
- market snapshots
- trade/deal synchronization
- API cost accounting

Acceptance:

- every MT5 position/deal can be reconciled to local records
- no destructive overwrite of historical facts

### Phase 3 — Deterministic market/strategy engine

Implement only a small number of explicit setups first.

Initial candidates:

1. trend pullback
2. breakout / failed breakout detector

Features may include:

- EMA trend state
- RSI
- ATR / volatility percentile
- ADX
- candle structure
- recent swing high/low
- distance to support/resistance
- session
- spread state

Acceptance:

- deterministic output for identical input snapshot
- signal includes full evidence and a proposed invalidation/SL framework

### Phase 4 — LLM reviewer

Implement:

- provider abstraction
- one economical model first
- structured response schema
- token/cost counters
- timeout/retry
- circuit breaker
- prompt versioning

LLM first role: reviewer, not unrestricted trader.

Acceptance:

- invalid model output cannot reach execution
- every request/decision has a journal record and estimated/actual API cost

### Phase 5 — Deterministic risk engine

This phase must complete before live execution.

Implement:

- risk per trade
- daily loss limit
- equity drawdown limit
- symbol lot normalization
- stop-level/freeze-level checks
- margin check
- concurrent position limit
- exposure/correlation guard
- spread/slippage guard
- kill switch

Acceptance:

- deliberate unsafe test proposals are rejected
- LLM cannot bypass configured limits

### Phase 6 — Execution adapter

Implement:

- order_check / preflight where available
- market order send
- SL/TP placement verification
- idempotency key / duplicate order prevention
- retcode handling
- reconciliation
- emergency close/disable controls

Acceptance:

- first test should be performed on demo if broker infrastructure requires validation; real cent execution begins only after exact same code path passes
- every order and deal is journaled

### Phase 7 — Live cent experiment

Start with one symbol if desired (prefer the symbol/account contract whose tick/lot economics are verified most clearly), then enable all three.

Suggested experimental progression:

1. OBSERVE
2. SHADOW BOT vs LLM
3. CENT_GUARDED with smallest broker-legal exposure
4. BOT_ONLY vs BOT_LLM A/B experiment
5. Optional LLM_PROPOSAL arm

Never raise risk simply because sample size is small.

### Phase 8 — Post-trade learning

Implement:

- post-trade review
- lesson extraction
- lesson quality checks
- retrieval into future contexts
- statistics on whether a lesson improved later decisions

Only after sufficient journal growth consider semantic/vector indexing.

## 12. MT5 initialization on RUNTIME_HOST

### Implemented route

1. Docker image `lprett/mt5linux:latest` is pulled on RUNTIME_HOST.
2. Container `forex-mt5` runs with `--restart unless-stopped`.
3. RPyC is bound to localhost only; noVNC is bound only to the LAN address.
4. Native Linux Python uses `mt5linux==1.1.1`.
5. MT5 is currently waiting for the owner to log into the chosen cent account through noVNC.
6. After login, `scripts/check_mt5.py` will discover account details and actual broker symbol names.
7. `scripts/collect_once.py` will then validate read-only account/tick/candle persistence into SQLite.
8. Order execution remains disabled until Phase 5 and Phase 6 acceptance criteria pass.

Do not install the official MetaTrader5 Python wheel into native Linux Python; it remains inside the containerized Windows/Wine runtime.

### Bootstrap commands — project side

The venv is intentionally on local disk, not the SMB project mount:

source $HOME/.venvs/forex-ai/bin/activate
cd <DEV_TREE>
python -m pip install -e .

Initial dependency set should stay small:

- pydantic
- pydantic-settings
- PyYAML
- pandas
- numpy
- httpx
- tenacity
- python-dotenv
- mt5linux (or selected equivalent bridge client after validation)
- provider SDK for the chosen LLM

Do not add LangChain/LangGraph/ChromaDB in V1.

### Service model

Run independent services/processes rather than one fragile monolith:

- Docker container `forex-mt5` with restart policy `unless-stopped`
- Forex-AI native Python runtime from `$HOME/apps/forex-ai/current`
- systemd **user** service for the Forex-AI process

`$USER` already has `Linger=yes`, so a user service can survive logout/reboot without requiring the project to run from the SMB development path. A service template lives at `deploy/forex-ai-observe.service`; it remains disabled until MT5 login/read-only validation succeeds.

On boot:

Docker MT5 container -> bridge health check -> local Forex-AI release

Forex-AI should fail closed: if bridge/terminal/account identity is not healthy, no execution is possible.

## 13. Secrets

Never store broker or API credentials in Git.

The SlncTrZ-MCP secret policy blocks writing `.env*` files in this workspace. A names-only template is stored at `config/runtime.env.template`; real credentials must remain outside the repo on local RUNTIME_HOST storage or be entered directly into MT5.

Runtime secret values may include:

- MT5_LOGIN
- MT5_PASSWORD
- MT5_SERVER
- LLM_API_KEY

.env.example contains names only, no secrets.

Restrict local file permissions.

For longer-term operation, migrate secrets to a systemd EnvironmentFile or dedicated secret mechanism.

## 14. SlncTrZ-MCP role

SlncTrZ-MCP is an operations/debug/control tool, not part of the critical trading decision path.

It may be used to:

- inspect logs
- read SQLite reports
- edit configuration/code during development
- restart authorized services
- run health/smoke scripts

The live system must continue operating predictably without ChatGPT or MCP being connected.

Do not route each trading decision through SlncTrZ-MCP.

## 15. V1 definition of done

V1 is complete when:

- MT5 operates reliably on RUNTIME_HOST
- Forex-AI reads all required live account and market state
- XAUUSD/EURUSD/GBPUSD symbol mapping is stable
- SQLite is the authoritative journal
- BOT_ONLY and BOT_LLM modes share the same risk/execution path
- LLM requests and cost are measurable
- hard risk engine is enforced independently of the LLM
- cent trades can execute/reconcile safely
- shadow/counterfactual decisions are recorded
- post-trade lessons can be stored and retrieved
- a report can answer: Did the LLM add net value after API cost?

## 16. Immediate implementation order

Next work session should execute only this sequence:

1. Create project skeleton and Git hygiene.
2. Create Python venv and minimal dependencies.
3. Create SQLite schema and migrations.
4. Install/validate Wine + MT5 on RUNTIME_HOST.
5. Validate the Linux MT5 bridge.
6. Implement read-only check_mt5.py.
7. Confirm exact cent-account broker/server/symbol names.
8. Implement collector + journal.
9. Only then implement strategy/LLM/risk/execution.

No real order should be sent before items 1–8 are stable and the risk engine is implemented/tested.
