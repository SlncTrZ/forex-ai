# Forex-AI

Live cent-account Forex research harness for:

- XAUUSD
- EURUSD
- GBPUSD

The project compares deterministic bot decisions with LLM-assisted decisions while keeping execution behind a deterministic risk gate.

## Development vs runtime

Development happens in the checked-out Git tree. The live runtime is a separate
local release so that a development-storage outage cannot take down a long-running
service. Mutable state (SQLite DB, logs) lives on the runtime host's local disk,
never in the repo.

Key layout:

- Runtime release root: `~/apps/forex-ai`
- Python venv: `~/.venvs/forex-ai`
- SQLite: `~/.local/share/forex-ai/forex.db`
- Logs: `~/.local/state/forex-ai/logs`
- MT5 container: `forex-mt5`
- MT5 bridge: `127.0.0.1:18812`
- MT5 noVNC UI: `http://127.0.0.1:8080`

The active SQLite database and long-running service must never run directly from a
network-mounted development path; keep them on the runtime host's local filesystem.

## Safety defaults

The repository currently defaults to:

```text
FOREX_AI_MODE=OBSERVE
execution_enabled=false
```

The LLM must never call MT5 execution directly. The required path is:

```text
market/signal -> optional LLM -> deterministic RiskEngine -> execution adapter -> MT5
```

No real order path should be enabled until broker contract details and all risk guards have been validated.

## Python environment

For development/smoke work:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For a long-running runtime service, deploy a local release first and install/run
from that local release so that a development-storage outage cannot stop trading
infrastructure.

Run tests:

```bash
pytest
```

## Database

Initialize idempotently:

```bash
python scripts/init_db.py
```

Inspect tables:

```bash
sqlite3 ~/.local/share/forex-ai/forex.db '.tables'
```

## Deployment model

Development flow:

```text
dev checkout (Git tree)
        |
        | develop + test + commit
        v
local release on runtime host
        |
        +--> native Forex-AI service
        +--> local SQLite/logs
        +--> Docker MT5 container
```

Deploy a tested source snapshot into an immutable local release directory, then
atomically repoint a `current` symlink. `scripts/deploy_local.sh` does this. This
permits rollback and ensures a development-storage outage cannot stop the live
process.

## MT5 runtime

MT5 is isolated in Docker using `lprett/mt5linux:latest`.

Network bindings are intentionally restricted:

- RPyC: `127.0.0.1:18812` only
- noVNC page: `127.0.0.1:8080` only (override via `FOREX_AI_BIND_IP`)
- noVNC WebSocket: `127.0.0.1:5901` only (override via `FOREX_AI_BIND_IP`)
- raw VNC is not published

Manage the container:

```bash
./scripts/mt5_container.sh status
./scripts/mt5_container.sh start
./scripts/mt5_container.sh restart
./scripts/mt5_container.sh logs 100
```

The noVNC password is stored locally outside the project at:

```text
~/.config/forex-ai/mt5_ui_password
```

Do not commit trading credentials. Enter broker credentials directly in MT5 or later load them from a protected local secret mechanism.

## Read-only MT5 check

After logging into the broker account in MT5:

```bash
python scripts/check_mt5.py
```

Expected final status:

```text
STATUS=READY_READ_ONLY
```

The script resolves broker-specific variants of XAUUSD/EURUSD/GBPUSD and prints account/symbol contract details without placing any orders.

Validated live account profile on 2026-09-03:

- Broker/server: Exness / `Exness-MT5Real36`
- Account currency: `USC` (cent account)
- Symbol mapping: `XAUUSD -> XAUUSDc`, `EURUSD -> EURUSDc`, `GBPUSD -> GBPUSDc`
- Minimum volume: `0.01` for all three symbols
- `EURUSDc` and `GBPUSDc` contract size: `1000`
- `XAUUSDc` contract size: `1`
- Current observer mode: read-only; no order path enabled

Non-secret validated mapping/specs are stored in `config/broker.exness-cent.yaml`. Runtime still reads live MT5 properties on every startup rather than trusting static config alone.

## One-shot live collection

After MT5 login and successful health check:

```bash
python scripts/collect_once.py
```

This records an account snapshot plus current tick and M15 bars into local SQLite.

## Configuration

- `config/app.yaml` — runtime mode, symbols, local storage, MT5 bridge
- `config/symbols.yaml` — supported instruments/timeframes
- `config/risk.yaml` — hard risk limits; execution remains off by default
- `config/llm.yaml` — LLM disabled by default; future budget/memory controls
- `config/runtime.env.template` — environment variable names only

## Research modes

1. `OBSERVE` — read and journal only
2. `SHADOW` — bot/LLM decisions are recorded but not executed
3. `CENT_GUARDED` — real cent-account execution through hard risk controls
4. `CENT_EXPERIMENT` — controlled BOT_ONLY vs BOT_LLM experiment

See `PLAN.md` for the full implementation plan.
