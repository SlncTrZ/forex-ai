"""Local web UI backed by the MT5 terminal running on node .152."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from flask import Flask, jsonify, render_template, request

from forex_ai.config import RuntimeConfig
from forex_ai.mt5.client import MT5Client
from forex_ai.research.mt5_backtest import BacktestRequest, run_mt5_backtest

app = Flask(__name__, template_folder="templates")
TUNNEL_PORT = int(os.getenv("FOREX_AI_MT5_TUNNEL_PORT", "18852"))
REMOTE_HOST = os.getenv("FOREX_AI_MT5_SSH_HOST", "dinhtc@192.168.1.152")
_tunnel: subprocess.Popen[bytes] | None = None


def _port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", TUNNEL_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def ensure_mt5_tunnel() -> None:
    """Create a loopback-only SSH forward; MT5 itself is never exposed to LAN."""
    global _tunnel
    if _port_open():
        return
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-N", "-L", f"127.0.0.1:{TUNNEL_PORT}:127.0.0.1:18812", REMOTE_HOST]
    _tunnel = subprocess.Popen(command, cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creation_flags)
    for _ in range(30):
        if _port_open():
            return
        if _tunnel.poll() is not None:
            break
        time.sleep(0.2)
    raise RuntimeError("Cannot establish private SSH tunnel to MT5 on .152")


def close_tunnel() -> None:
    if _tunnel is not None and _tunnel.poll() is None:
        _tunnel.terminate()


atexit.register(close_tunnel)


def mt5_client() -> MT5Client:
    ensure_mt5_tunnel()
    config = RuntimeConfig("OBSERVE", ("XAUUSD",), PROJECT_ROOT / "data" / "runtime" / "backtest.db", PROJECT_ROOT / "logs", 60, "127.0.0.1", TUNNEL_PORT, "127.0.0.1", 8080, "external")
    client = MT5Client(config)
    if not client.connect():
        raise RuntimeError("MT5 initialize failed through the private tunnel")
    return client


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    try:
        client = mt5_client()
        try:
            return jsonify({"status": "ok", "terminal": client.terminal_info(), "bridge": "SSH loopback tunnel to .152"})
        finally:
            client.close()
    except Exception as error:
        return jsonify({"status": "error", "message": str(error)}), 503


@app.route("/api/backtest", methods=["POST"])
def backtest():
    try:
        body = request.get_json(force=True)
        symbol = str(body.get("symbol", "XAUUSDm"))
        aliases = {"XAUUSD": "XAUUSDm", "EURUSD": "EURUSDm", "BTCUSD": "BTCUSDm"}
        request_data = BacktestRequest(
            symbol=aliases.get(symbol, symbol), h1_ema_fast=int(body["h1_ema_fast"]), h1_ema_slow=int(body["h1_ema_slow"]),
            rsi_threshold=float(body["rsi_threshold"]), m5_ema=int(body["m5_ema"]), sl_distance=float(body["sl_dist_usd"]),
            rr_ratio=float(body["rr_ratio"]), trades_to_levelup=int(body["trades_to_levelup"]),
            # Legacy key supports an already-open tab; the current UI submits USD.
            initial_equity=float(body.get("initial_capital_usd", body.get("initial_capital_cent"))),
            session_mode=str(body["session_mode"]), enable_dca=bool(body.get("enable_dca")), dca_step=float(body.get("dca_step", 3.0)),
            dca_recovery=float(body.get("dca_tp", 3.0)), history_days=int(body.get("history_days", 365)),
        )
        client = mt5_client()
        try:
            result = run_mt5_backtest(client, request_data)
        finally:
            client.close()
        return jsonify({"status": "success", "result": result})
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        return jsonify({"status": "error", "message": str(error)}), 400


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
