"""Private Unix-socket HTTP control plane. No TCP listener, no credential files."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import socketserver
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from forex_ai.config import load_execution_enabled, load_risk_profile, load_runtime_config
from forex_ai.dashboard.control import ControlDenied, arm_preflight, change_control
from forex_ai.dashboard.graph import read_execution_graph
from forex_ai.dashboard.ticks import TickCollector, TickHub
from forex_ai.execution.account_mode import detect_account_trade_mode
from forex_ai.journal.integration_repository import load_trading_control
from forex_ai.risk.account_guard import account_matches
from forex_ai.runtime.ops import assess_runtime_health

UTC = timezone.utc
BASE = Path(__file__).resolve().parents[3] / "dashboard" / "assets"
BUILT_UI = BASE.parent / "ui" / "dist"
SOCKET = Path(os.getenv("FOREX_AI_DASHBOARD_SOCKET", f"/run/user/{getattr(os, 'getuid', lambda: 1000)()}/forex-ai-dashboard.sock"))
HOSTS = {"127.0.0.1:8765", "localhost:8765"}
SERVICES = (
    "forex-ai-observe.service", "forex-ai-candidate-scan.timer",
    "forex-ai-execute-pending.timer", "forex-ai-execution-reconcile.timer",
    "forex-ai-ops-health.timer", "forex-ai-db-backup.timer",
    "forex-ai-auto-live-week.timer",
)
SENSITIVE = re.compile(r"(passw|token|secret|credential|api.?key|login|server|auth|private)", re.I)
TICK_HUB = TickHub()
STREAM_SLOTS = __import__('threading').BoundedSemaphore(8)


def _db() -> sqlite3.Connection:
    p = load_runtime_config().db_path
    if not p.is_file():
        raise OSError("database unavailable")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=3)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=3000")
    return con


def _safe_payload(payload: str) -> str:
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            safe = {k: v for k, v in data.items() if k in ("reason", "reasons", "status", "state", "health_state", "action", "error", "outcome_class", "decision", "reason_code", "latency_ms", "model_fingerprint")}
            return json.dumps(safe, ensure_ascii=False)[:220]
    except (ValueError, TypeError):
        pass
    return ""


def read_status() -> dict:
    cfg = load_runtime_config()
    control = load_trading_control(cfg.db_path)
    now = datetime.now(UTC)
    health = assess_runtime_health(cfg.db_path, now_utc=now)
    with _db() as con:
        account = con.execute("SELECT timestamp,balance,equity,margin,free_margin,currency,raw_json FROM accounts ORDER BY snapshot_id DESC LIMIT 1").fetchone()
        stats = con.execute("SELECT COUNT(*) AS n FROM order_intents_v1 WHERE state IN ('UNKNOWN','SEND_STARTED','PARTIALLY_FILLED')").fetchone()
        latest = con.execute("SELECT timestamp_utc,health_state,reason FROM runtime_heartbeats ORDER BY id DESC LIMIT 1").fetchone()
        position_count = con.execute("SELECT COUNT(*) FROM external_position_state_v1 WHERE active=1").fetchone()[0]
        last_events = con.execute("SELECT COUNT(*) FROM audit_events WHERE timestamp_utc > ?", ((now-timedelta(hours=24)).isoformat(),)).fetchone()[0]
    type_name, bound, age = "UNAVAILABLE", False, None
    details = None
    if account:
        try:
            raw = json.loads(account["raw_json"])
            type_name = detect_account_trade_mode(raw).name
            bound = account_matches(raw)
            timestamp = datetime.fromisoformat(str(account["timestamp"])).astimezone(UTC)
            age = max(0, int((now - timestamp).total_seconds()))
        except Exception:
            type_name = "UNKNOWN"
        details = {k: account[k] for k in ("balance","equity","margin","free_margin","currency")}
    enabled = load_execution_enabled()
    return {
        "observed_at": now.isoformat(), "infrastructure": "RUNNING" if health.healthy else "DEGRADED",
        "account": {"type": type_name, "bound": bound, "age_seconds": age, "financials": details},
        "control": {"armed": control.armed, "kill_switch": control.kill_switch, "maintenance": control.maintenance_mode,
                    "expiry": control.arm_expires_at_utc.isoformat() if control.arm_expires_at_utc else None,
                    "reason": control.reason, "entries_allowed_by_control": control.allows_new_entries(now_utc=now),
                    "execution_enabled": enabled},
        "health": {"healthy": health.healthy, "reasons": list(health.reasons), "heartbeat_state": health.latest_heartbeat_state,
                   "heartbeat_age_seconds": health.latest_heartbeat_age_seconds, "db_integrity": health.db_integrity,
                   "free_gb": round(health.free_bytes / (1024**3), 2), "unresolved_intents": health.unresolved_execution_intents},
        "metrics": {"uncertain_orders": int(stats["n"]), "external_positions": int(position_count), "audit_24h": int(last_events)},
        "heartbeat": dict(latest) if latest else None,
        "risk": _risk_summary(), "services": read_services(),
        "release": _release_info(), "ticks": TICK_HUB.snapshot(),
    }


def _risk_summary() -> dict:
    try:
        p = load_risk_profile()
        return {"max_per_trade_pct": str(p.max_risk_per_trade_pct), "max_open_pct": str(p.max_total_open_risk_pct),
                "max_orders": p.max_active_orders, "max_drawdown_pct": str(p.max_drawdown_pct)}
    except Exception:
        return {}


def _release_info() -> dict:
    p = Path.home() / "apps" / "forex-ai" / "current" / "release_manifest.json"
    try:
        d = json.loads(p.read_text())
        return {"version": p.parent.resolve().name, "fingerprint": str(d.get("release_fingerprint", ""))[:12]}
    except Exception:
        return {"version": "unknown", "fingerprint": ""}


def read_services() -> list[dict]:
    try:
        run = subprocess.run(["systemctl","--user","is-active",*SERVICES], capture_output=True,
                             text=True, timeout=3, check=False)
        statuses = run.stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        statuses = []
    return [{"name": s.replace("forex-ai-","").replace(".service","").replace(".timer",""),
             "unit": s, "status": statuses[i] if i < len(statuses) else "unknown"} for i,s in enumerate(SERVICES)]


def read_events(params: dict) -> dict:
    source = params.get("source", ["audit"])[0]
    if source not in ("audit","system"):
        raise ControlDenied("INVALID_LOG_SOURCE", 400)
    try:
        limit = min(150, max(1, int(params.get("limit", ["60"])[0])))
    except ValueError:
        raise ControlDenied("INVALID_LIMIT", 400) from None
    with _db() as con:
        if source == "audit":
            rows = con.execute("SELECT timestamp_utc,event_type,source,symbol,payload_json FROM audit_events ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
            events = [{"time":r["timestamp_utc"],"level":"AUDIT","component":r["source"],"event":r["event_type"],
                       "symbol":r["symbol"],"details":_safe_payload(r["payload_json"])} for r in rows]
        else:
            rows = con.execute("SELECT timestamp,severity,component,event_type,payload_json FROM system_events ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
            events = [{"time":r["timestamp"],"level":r["severity"],"component":r["component"],"event":r["event_type"],
                       "symbol":None,"details":_safe_payload(r["payload_json"])} for r in rows]
    return {"events": events, "source": source}


def read_regimes() -> dict:
    """Sanitized read-only D3 regime telemetry from the same audit journal."""
    latest: dict[str, dict] = {}
    with _db() as con:
        rows = con.execute(
            "SELECT timestamp_utc,symbol,payload_json FROM audit_events "
            "WHERE event_type='D3_REGIME_DECISION' ORDER BY id DESC LIMIT 160"
        ).fetchall()
    for row in rows:
        symbol = str(row["symbol"] or "")
        if not symbol or symbol in latest or len(symbol) > 32:
            continue
        try:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                continue
            regime = payload.get("regime")
            if regime not in ("UPTREND", "DOWNTREND", "SIDEWAY", "UNCERTAIN"):
                continue
            fields = ("regime", "quant_regime", "model_regime", "confidence",
                      "reason_code", "closed_h1_utc", "model_fingerprint",
                      "feature_fingerprint", "latency_ms", "policy_version")
            record = {name: payload.get(name) for name in fields}
            record["symbol"] = symbol
            record["observed_at"] = str(row["timestamp_utc"])
            # A returned prior decision is history, never proof of current state.
            try:
                closed = datetime.fromisoformat(str(record["closed_h1_utc"])).astimezone(UTC)
                record["age_seconds"] = max(0, int((datetime.now(UTC) - closed).total_seconds()))
            except (ValueError, TypeError):
                record["age_seconds"] = None
            latest[symbol] = record
        except (ValueError, TypeError):
            continue
    return {"source": "forex_ai_regime_audit", "generated_at": datetime.now(UTC).isoformat(),
            "regimes": list(latest.values())}


def read_confluence() -> dict:
    """Read-only bounded D3 candidate score decisions; no raw model input."""
    fields = (
        "candidate_id", "strategy_id", "symbol", "regime", "score",
        "alignment_points", "value_points", "momentum_points", "trigger_points",
        "risk_multiplier", "verdict", "reason_code", "model_fingerprint",
        "input_fingerprint", "latency_ms", "policy_version",
    )
    decisions = []
    with _db() as con:
        rows = con.execute(
            "SELECT timestamp_utc,payload_json FROM audit_events "
            "WHERE event_type='D3_CONFLUENCE_DECISION' ORDER BY id DESC LIMIT 40"
        ).fetchall()
    for row in rows:
        try:
            data = json.loads(row["payload_json"])
            if not isinstance(data, dict):
                continue
            if data.get("verdict") not in ("FULL", "REDUCE", "VETO"):
                continue
            score = data.get("score")
            if type(score) is not int or not 0 <= score <= 100:
                continue
            item = {k: data.get(k) for k in fields}
            item["observed_at"] = str(row["timestamp_utc"])
            decisions.append(item)
        except (ValueError, TypeError):
            continue
    return {
        "source": "forex_ai_confluence_audit",
        "generated_at": datetime.now(UTC).isoformat(),
        "decisions": decisions,
    }


def read_orders() -> dict:
    with _db() as con:
        rows = con.execute("""SELECT symbol,side,volume,state,created_at_utc,updated_at_utc,last_reason
                              FROM order_intents_v1 ORDER BY updated_at_utc DESC LIMIT 35""").fetchall()
        pos = con.execute("""SELECT symbol,side,volume,ownership,last_seen_at_utc
                             FROM external_position_state_v1 WHERE active=1
                             ORDER BY last_seen_at_utc DESC LIMIT 35""").fetchall()
    return {"orders": [dict(r) for r in rows], "positions": [dict(r) for r in pos]}


class ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 32
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = "ForexAIOperator/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # No request body, account identity or secret should reach service logs.
        pass

    def _send(self, code: int, payload: bytes, mime: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code: int, data: dict):
        self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode())

    def _trusted_host(self) -> bool:
        return self.headers.get("Host", "") in HOSTS

    def _stream_ticks(self):
        """SSE uses bounded in-memory fanout; slow browsers get reset/gap markers."""
        if not STREAM_SLOTS.acquire(blocking=False):
            return self._json(429, {"error": "MAX_STREAM_CLIENTS"})
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            raw_cursor = self.headers.get("Last-Event-ID", "")
            try:
                cursor = int(raw_cursor) if raw_cursor else TICK_HUB.snapshot()["latest_id"]
                if cursor < 0:
                    cursor = 0
            except ValueError:
                cursor = TICK_HUB.snapshot()["latest_id"]
            # Snapshot has no ID: it is not a replayable market event.
            snapshot = TICK_HUB.snapshot()
            if cursor > snapshot["latest_id"]:
                # Source restarted and the browser retained an event ID from
                # the previous process. It must not wait for an old cursor.
                self.wfile.write(b"event: gap\ndata: " + json.dumps({
                    "reason": "STREAM_RESTART", "snapshot": snapshot,
                }).encode() + b"\n\n")
                cursor = snapshot["latest_id"]
            self.wfile.write(b"event: snapshot\ndata: " + json.dumps(snapshot).encode() + b"\n\n")
            self.wfile.flush()
            while True:
                events, gap = TICK_HUB.wait_after(cursor, timeout=8)
                if gap:
                    snapshot = TICK_HUB.snapshot()
                    self.wfile.write(b"event: gap\ndata: " + json.dumps({
                        "reason": "CLIENT_BUFFER_OVERRUN", "snapshot": snapshot,
                    }).encode() + b"\n\n")
                    cursor = max(0, snapshot["latest_id"]-len(events))
                if not events:
                    self.wfile.write(b": heartbeat\n\n")
                for event in events:
                    cursor = event["id"]
                    payload = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
                    self.wfile.write(f"id: {cursor}\nevent: {event['type']}\ndata: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        finally:
            STREAM_SLOTS.release()

    def do_GET(self):
        if not self._trusted_host():
            return self._json(403, {"error":"UNTRUSTED_HOST"})
        path = urlsplit(self.path)
        # Prefer the compiled React UI; keep the existing standalone operator
        # interface as an explicit fallback for releases without a frontend build.
        if BUILT_UI.joinpath("index.html").is_file() and (
            path.path == "/" or path.path == "/app" or
            (path.path.startswith("/assets/") and path.path not in ("/assets/app.css", "/assets/app.js"))
        ):
            rel = "index.html" if path.path in ("/", "/app") else path.path.lstrip("/")
            asset = (BUILT_UI / rel).resolve()
            if not asset.is_relative_to(BUILT_UI.resolve()) or not asset.is_file():
                return self._json(404, {"error":"STATIC_ASSET_NOT_FOUND"})
            mime = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            return self._send(200, asset.read_bytes(), mime + "; charset=utf-8" if
                              mime in ("text/html", "text/css", "application/javascript", "text/javascript") else mime)
        files = {"/":"index.html","/assets/app.css":"app.css","/assets/app.js":"app.js"}
        if path.path in files:
            name = files[path.path]
            try:
                payload = (BASE / name).read_bytes()
            except OSError:
                return self._json(503, {"error":"STATIC_ASSET_MISSING"})
            return self._send(200, payload, "text/html; charset=utf-8" if name.endswith(".html") else
                              "text/css; charset=utf-8" if name.endswith(".css") else "application/javascript; charset=utf-8")
        try:
            if path.path == "/api/stream/ticks":
                return self._stream_ticks()
            if path.path == "/api/ticks":
                return self._json(200, TICK_HUB.snapshot())
            if path.path == "/api/graph":
                query = parse_qs(path.query)
                try:
                    n = int(query.get("limit", ["110"])[0])
                    if n < 1 or n > 180:
                        raise ValueError
                except ValueError:
                    return self._json(400, {"error": "GRAPH_LIMIT_OUT_OF_RANGE"})
                return self._json(200, read_execution_graph(load_runtime_config().db_path, limit=n))
            if path.path == "/api/status":
                return self._json(200, read_status())
            if path.path == "/api/events":
                return self._json(200, read_events(parse_qs(path.query)))
            if path.path == "/api/orders":
                return self._json(200, read_orders())
            if path.path == "/api/regimes":
                return self._json(200, read_regimes())
            if path.path == "/api/confluence":
                return self._json(200, read_confluence())
            if path.path == "/api/preflight":
                result = arm_preflight()
                return self._json(200, {"account_type": result.account_type, "ready": result.ready, "reasons":list(result.reasons)})
            return self._json(404, {"error":"NOT_FOUND"})
        except ControlDenied as exc:
            return self._json(exc.status, {"error":exc.reason})
        except Exception:
            return self._json(503, {"error":"READ_UNAVAILABLE"})

    def do_POST(self):
        if not self._trusted_host():
            return self._json(403, {"error":"UNTRUSTED_HOST"})
        host = self.headers.get("Host", "")
        if (self.headers.get("Origin") != "http://" + host or
            self.headers.get("X-Forex-AI-Action") != "operator-v1" or
            self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json" or
            self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin","none")):
            return self._json(403, {"error":"ORIGIN_OR_CSRF_CHECK_FAILED"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 2048:
                return self._json(413, {"error":"INVALID_BODY_SIZE"})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                return self._json(400, {"error":"INVALID_BODY"})
            path = urlsplit(self.path).path
            if not path.startswith("/api/control/"):
                return self._json(404, {"error":"NOT_FOUND"})
            action = path.removeprefix("/api/control/")
            result = change_control(action, data)
            return self._json(200, {"ok":True, "result":result})
        except ControlDenied as exc:
            return self._json(exc.status, {"ok":False, "error":exc.reason})
        except (ValueError, TypeError):
            return self._json(400, {"error":"INVALID_JSON"})
        except Exception:
            return self._json(503, {"error":"CONTROL_UNAVAILABLE"})


def serve(path: Path = SOCKET) -> None:
    if path.exists():
        # Never remove the socket of an already-running dashboard process.
        test = socket.socket(socket.AF_UNIX)
        try:
            test.settimeout(0.4)
            test.connect(str(path))
        except OSError:
            path.unlink()
        else:
            raise RuntimeError("DASHBOARD_ALREADY_RUNNING")
        finally:
            test.close()
    old = os.umask(0o177)
    try:
        server = ThreadingUnixServer(str(path), Handler)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    collector = TickCollector(TICK_HUB)
    collector.start()
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        collector.stop()
        server.server_close()
        path.unlink(missing_ok=True)
