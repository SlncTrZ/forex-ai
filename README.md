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
- Drawdown resilience: When $L < -5$, nominal risk falls to 0.5%; broker-aware risk and drawdown limits still constrain minimum-lot decisions.
- Multi-order concurrency: `max_active_orders: 3`, `max_total_open_risk_pct: 9%`.

The execution path is strictly deterministic:

```text
market/signal -> strategy (V2 Suite) -> deterministic RiskEngine (Tiered Compound) -> execution adapter -> MT5
```

No real order path should be enabled until broker contract details and all risk guards have been validated.

## Account-driven live infrastructure

Forex-AI's infrastructure runs continuously under `mode: TRADING`. This is **not** an MT5
account type. The connected broker account's `trade_mode` decides DEMO or REAL;
unknown and CONTEST accounts cannot send automated orders. The same executor is
used for both, while REAL requires separate owner-approved strategy evidence,
account binding, and all safety interlocks. A broker account switch fails closed
until the new identity is explicitly bound and authorized; neither source edits
nor deployment should auto-arm trading. See [MT5 account-driven runtime](docs/MT5_ACCOUNT_DRIVEN_RUNTIME.md).

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

## Professional Operator Dashboard

The dashboard is a separate SSH-gated operator console on production host .227, **not** the research Flask app. It provides live account/health/status data, audited ARM/DISARM/Emergency Stop, order intent lists, safety readouts, and searchable audit/system logs. There is no public HTTP listener and no additional web credential to store.

From the workstation (with an authenticated SSH login to .227), run:

```bash
ssh -N -L 127.0.0.1:8765:/run/user/1000/forex-ai-dashboard.sock dinhtc@192.168.1.227
```

Open **http://127.0.0.1:8765/** while the SSH tunnel is active. The target socket is accessible only to the owning Unix user (mode 0600). Confirm the runtime UID with `id -u` if it differs from the example. The dashboard itself requires no new password file, API key, or public network port.

ARM is allowed only after a fresh MT5 account/binding check and the existing DEMO campaign or REAL strategy/risk approval gates. Leases are 5–1440 minutes. DISARM preserves the Kill Switch. Emergency Stop prevents new orders but does not forcibly close positions. A sticky manual kill cannot be reset by the web UI. The CLI `scripts/arm_trading.py` uses the same guarded control layer and requires explicit `--confirm`.

## Operational Runbook & Deploy

Production services are managed on host `.227` via systemd:
- `forex-ai-observe.service` (Continuous state monitoring)
- `forex-ai-candidate-scan.timer` (M5/M15 strategy evaluation every 30s)
- `forex-ai-execute-pending.timer` (Guarded order dispatching every 10s)
- `forex-ai-execution-reconcile.timer` (Broker deal reconciliation every 15s)
