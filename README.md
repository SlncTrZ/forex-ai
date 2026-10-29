# Forex-AI (v0.0.4)

Account-neutral deterministic Forex & Crypto research and automated trading engine for:

- **XAUUSD** (Spot Gold)
- **BTCUSD** (Bitcoin 24/7)
- **EURUSD** (Euro FX)
- **USDJPY** (Yen FX)

The project combines institutional quantitative strategies with a strict mathematical Level-based compounding engine (`TieredCompoundRiskEngine`), backed by empirical backtesting and deterministic risk controls.

## Architecture & Sizing Engine

The system features an automated **Tiered Compound Risk Engine** (`_private/lãi kép.xlsx`):
- Dynamic position sizing calculated from exact stop-loss distance and equity Level.
- Progressive risk reduction:
  * Level 0–17 ($V_0 \to \approx 5.05 V_0$): **3.0%** risk per trade
  * Level 18–50 ($\approx 5.56 V_0 \to \approx 117 V_0$): **2.0%** risk per trade (De-escalation)
  * Level 51–99: **1.0%** risk per trade
  * Level 100+: **0.5%** risk per trade
- Drawdown resilience: When $L < -5$, preserves 3% risk and enforces minimum lot (0.01) until margin exhaustion.
- Multi-order concurrency: `max_active_orders: 3`, `max_total_open_risk_pct: 9%`.

The execution path is strictly deterministic:

```text
market/signal -> strategy (V2 Suite) -> deterministic RiskEngine (Tiered Compound) -> execution adapter -> MT5
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

The script resolves broker-specific variants of XAUUSD/EURUSD and prints account/symbol contract details without placing any orders.

Valid account profile and supported instruments:

- Broker / Server: Exness (`Exness-MT5Trial7` / `Exness-MT5Real36`)
- Account currency: USD / USC
- Symbol mapping:
  * `XAUUSD -> XAUUSDm / XAUUSDc` (Min SL 6.0$, tick size 0.01)
  * `BTCUSD -> BTCUSDm / BTCUSDc` (Min SL 150.0$, 24/7 continuous trading)
  * `EURUSD -> EURUSDm / EURUSDc` (Min SL 0.0015, spread < 0.7 pips)
  * `USDJPY -> USDJPYm / USDJPYc` (Min SL 0.15, Tokyo Fix session window)
- Position sizing: Dynamic lot computed from `TieredCompoundRiskEngine` with broker contract tick values.

## Configuration

- `config/strategy.yaml` — production and exploration strategy parameters
- `config/risk.yaml` — hard risk limits, Tiered Compound activation, symbol spread overrides
- `config/app.yaml` — runtime mode, symbols, local storage, MT5 bridge
- `config/symbols.yaml` — supported instruments/timeframes
- `config/runtime.env.template` — environment variable names only

## Operational Runbook & Deploy

Production services are managed on host `.227` via systemd:
- `forex-ai-observe.service` (Continuous state monitoring)
- `forex-ai-candidate-scan.timer` (M5/M15 strategy evaluation every 30s)
- `forex-ai-execute-pending.timer` (Guarded order dispatching every 10s)
- `forex-ai-execution-reconcile.timer` (Broker deal reconciliation every 15s)
