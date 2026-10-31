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
from forex_ai.dashboard.live_account import LiveAccountCache
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
    "forex-ai-candidate-scan.service",
    "forex-ai-execute-pending.timer", "forex-ai-execution-reconcile.timer",
    "forex-ai-ops-health.timer", "forex-ai-db-backup.timer",
    "forex-ai-auto-live-week.timer",
)
SENSITIVE = re.compile(r"(passw|token|secret|credential|api.?key|login|server|auth|private)", re.I)
TICK_HUB = TickHub()
LIVE_ACCOUNT = LiveAccountCache()
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
    live = LIVE_ACCOUNT.snapshot()
    # Never present the last journaled $0 as a current brokerage balance:
    # only the latest valid, freshly bound MT5 account is financial truth.
    if live.get("state") == "LIVE":
        type_name, bound = live["mode"], bool(live["bound"])
        age = live["age_seconds"]
        details = {k: live[k] for k in ("balance", "equity", "margin", "free_margin", "currency")}
    else:
        details = None
    services = read_services()
    scan_failed = any(s["unit"] == "forex-ai-candidate-scan.service"
                      and s["status"] == "failed" for s in services)
    recent_degraded = False
    with _db() as con:
        recent = con.execute(
            "SELECT timestamp_utc,health_state FROM runtime_heartbeats "
            "ORDER BY id DESC LIMIT 48"
        ).fetchall()
        cutoff = (now-timedelta(minutes=2)).isoformat()
        recent_degraded = any(
            r["health_state"] == "DEGRADED" and str(r["timestamp_utc"]) > cutoff
            for r in recent
        )
    # Infrastructure/process health and broker-market session health are not
    # the same. A normally closed XAU session does not stop the Command Center.
    fully_healthy = bool(health.healthy and not scan_failed
                         and live.get("state") == "LIVE")
    enabled = load_execution_enabled()
    return {
        "observed_at": now.isoformat(), "infrastructure": "RUNNING" if fully_healthy else "DEGRADED",
        "account": {"type": type_name, "bound": bound, "age_seconds": age,
                    "financials": details, "financial_source": live.get("source"),
                    "financial_state": live.get("state"), "financial_reason": live.get("reason")},
        "control": {"armed": control.armed, "kill_switch": control.kill_switch, "maintenance": control.maintenance_mode,
                    "expiry": control.arm_expires_at_utc.isoformat() if control.arm_expires_at_utc else None,
                    "reason": control.reason, "entries_allowed_by_control": control.allows_new_entries(now_utc=now),
                    "execution_enabled": enabled},
        "health": {"healthy": fully_healthy,
                   "reasons": list(health.reasons)
                              + (["CANDIDATE_SCANNER_FAILED"] if scan_failed else [])
                              + (["BROKER_EQUITY_STALE"] if live.get("state") != "LIVE" else []),
                   "market_reasons": ["BROKER_MARKET_SYNC_DEGRADED"] if recent_degraded else [],
                   "heartbeat_state": health.latest_heartbeat_state,
                   "heartbeat_age_seconds": health.latest_heartbeat_age_seconds, "db_integrity": health.db_integrity,
                   "free_gb": round(health.free_bytes / (1024**3), 2), "unresolved_intents": health.unresolved_execution_intents},
        "metrics": {"uncertain_orders": int(stats["n"]), "external_positions": int(position_count),
                    "audit_24h": int(last_events),
                    "floating_pnl": live.get("floating_pnl") if live.get("state") == "LIVE" else None,
                    "realized_pnl_utc_day": live.get("realized_pnl_utc_day") if live.get("state") == "LIVE" else None},
        "heartbeat": dict(latest) if latest else None,
        "risk": _risk_summary(), "services": services,
        "release": _release_info(), "ticks": TICK_HUB.snapshot(),
    }


def _risk_summary() -> dict:
    try:
        p = load_risk_profile()
        return {"max_per_trade_pct": str(p.max_risk_per_trade_pct), "max_open_pct": str(p.max_total_open_risk_pct),
                "max_orders": p.max_active_orders, "max_drawdown_pct": str(p.max_drawdown_pct)}
    except Exception:
        return {}


def _release_info(manifest_path: Path | None = None) -> dict:
    p = manifest_path or (Path.home() / "apps" / "forex-ai" / "current" / "release_manifest.json")
    try:
        d = json.loads(p.read_text())
        tag = d.get("release_tag")
        # A release directory timestamp or Git SHA is NEVER the product version.
        import re
        if not isinstance(tag, str) or not re.fullmatch(r"v0\.0\.[1-9][0-9]*", tag):
            tag = "UNTAGGED"
        return {
            "version": tag,
            "release_tag": tag if tag != "UNTAGGED" else None,
            "git_sha": str(d.get("git_sha", ""))[:12],
            "fingerprint": str(d.get("release_fingerprint", ""))[:12],
            "deployed_at": d.get("release_timestamp_utc"),
        }
    except (OSError, TypeError, ValueError):
        return {"version": "unknown", "release_tag": None,
                "git_sha": "", "fingerprint": "", "deployed_at": None}


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
    """Audit-backed per-symbol observations; never broker execution permission."""
    latest: dict[str, dict] = {}
    with _db() as con:
        rows = con.execute(
            "SELECT timestamp_utc,symbol,payload_json FROM audit_events "
            "WHERE event_type IN ('MARKET_INTELLIGENCE_OBSERVATION','D3_REGIME_DECISION') "
            "ORDER BY id DESC LIMIT 320"
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
                      "choice_probability", "reason_code", "closed_h1_utc",
                      "model_fingerprint", "feature_fingerprint", "latency_ms",
                      "policy_version", "market_state", "tick_age_seconds", "cache_hit")
            record = {name: payload.get(name) for name in fields}
            record["symbol"] = symbol
            record["observed_at"] = str(row["timestamp_utc"])
            record["confirmed_for_trading"] = False  # monitoring never grants order access
            record["market_state"] = record["market_state"] or "UNKNOWN"
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


def read_signal_matrix() -> dict:
    """Read the latest sanitized closed-candle matrix per symbol, never raw audit JSON."""
    from forex_ai.market.signal_matrix import COLUMNS, ROWS, POLICY

    known_cols = tuple(k for k, _ in COLUMNS)
    known_rows = {r[0] for r in ROWS}
    matrices = []
    seen: set[str] = set()
    now = datetime.now(UTC)
    with _db() as con:
        rows = con.execute(
            "SELECT timestamp_utc,symbol,payload_json FROM audit_events "
            "WHERE event_type='SIGNAL_MATRIX_OBSERVATION' ORDER BY id DESC LIMIT 48"
        ).fetchall()
    for row in rows:
        symbol = str(row["symbol"] or "")
        if symbol in seen or len(seen) >= 6 or not symbol or len(symbol) > 32:
            continue
        try:
            raw = json.loads(row["payload_json"])
            if not isinstance(raw, dict) or raw.get("policy_version") != POLICY or raw.get("symbol") != symbol:
                continue
            observed = datetime.fromisoformat(str(raw["observed_at"])).astimezone(UTC)
            age = (now - observed).total_seconds()
            if age < -60:
                continue
            columns = raw["columns"]
            entries = raw["rows"]
            if (not isinstance(columns, list) or
                    [c.get("id") for c in columns if isinstance(c, dict)] != list(known_cols) or
                    len(columns) != len(known_cols) or not isinstance(entries, list) or
                    len(entries) != len(ROWS)):
                continue
            sanitized = []
            seen_strategies = set()
            stale = age > 960 or raw.get("market_state") != "ACTIVE"
            for entry in entries:
                key = entry["strategy_id"]
                if key not in known_rows or key in seen_strategies:
                    raise ValueError("INVALID_MATRIX_ROW")
                seen_strategies.add(key)
                cells = entry["cells"]
                if not isinstance(cells, list) or len(cells) != len(known_cols):
                    raise ValueError("INVALID_MATRIX_CELLS")
                clean = []
                for id_, cell in zip(known_cols, cells, strict=True):
                    if not isinstance(cell, dict) or cell.get("indicator") != id_:
                        raise ValueError("INVALID_MATRIX_INDICATOR")
                    state = cell.get("state")
                    if state not in ("BULL", "BEAR", "NEUTRAL", "UNKNOWN", "STALE", "NA"):
                        raise ValueError("INVALID_MATRIX_STATE")
                    value = cell.get("value")
                    if value is not None and (type(value) not in (int, float) or
                                              not __import__("math").isfinite(value)):
                        raise ValueError("INVALID_MATRIX_VALUE")
                    if stale and state != "NA":
                        state = "STALE"
                    clean.append({
                        "indicator": id_, "state": state,
                        "met": state in ("BULL", "BEAR"),
                        "value": value if not stale else None,
                        "core": bool(cell.get("core")),
                        "rule": str(cell.get("rule") or "")[:128],
                        "closed_utc": str(cell.get("closed_utc"))[:40] if cell.get("closed_utc") else None,
                    })
                sanitized.append({
                    "strategy_id": key, "label": str(entry.get("label") or key)[:48],
                    "timeframe": entry["timeframe"] if entry.get("timeframe") in ("M5", "M15") else "M5",
                    "enabled": bool(entry.get("enabled")), "eligible": bool(entry.get("eligible")),
                    "cells": clean,
                })
            matrices.append({
                "symbol": symbol, "market_state": "STALE" if stale else "ACTIVE",
                "observed_at": observed.isoformat(), "age_seconds": max(0, int(age)),
                "diagnostic_only": True, "rows": sanitized,
            })
            seen.add(symbol)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            continue
    return {
        "source": "forex_ai_signal_matrix_audit", "generated_at": now.isoformat(),
        "policy_version": POLICY,
        "columns": [{"id": k, "label": k, "description": desc} for k, desc in COLUMNS],
        "matrices": matrices,
    }


def read_shadow_scores() -> dict:
    """Bounded and sanitized research-only scores from immutable audit events."""
    import math
    from forex_ai.market.shadow_scoring import EVENT, POLICY, THRESHOLD, WEIGHTS
    from forex_ai.market.signal_matrix import ROWS

    now = datetime.now(UTC)
    known_ids = {r[0] for r in ROWS}
    response = []
    seen: set[str] = set()
    with _db() as con:
        raw_rows = con.execute(
            "SELECT timestamp_utc,symbol,payload_json FROM audit_events "
            "WHERE event_type=? ORDER BY id DESC LIMIT 48", (EVENT,),
        ).fetchall()
    for item in raw_rows:
        symbol = str(item["symbol"] or "")
        if not symbol or len(symbol) > 32 or symbol in seen or len(seen) >= 6:
            continue
        try:
            raw = json.loads(item["payload_json"])
            if not isinstance(raw, dict) or raw.get("symbol") != symbol or raw.get("policy_version") != POLICY:
                continue
            when = datetime.fromisoformat(str(raw["observed_at"])).astimezone(UTC)
            age = (now - when).total_seconds()
            if age < -60:
                continue
            stale = age > 960 or raw.get("market_state") != "ACTIVE"
            rows = raw["results"]
            if not isinstance(rows, list) or len(rows) != len(ROWS):
                continue
            cleaned = []
            ids = set()
            for row in rows:
                strategy = row["strategy_id"]
                if strategy not in known_ids or strategy in ids or row.get("symbol") != symbol:
                    raise ValueError("INVALID_SHADOW_ROW")
                ids.add(strategy)
                scenarios = row["scenarios"]
                if not isinstance(scenarios, list) or len(scenarios) not in (0, 2):
                    raise ValueError("INVALID_SHADOW_SCENARIOS")
                safe_scenarios = []
                if not stale:
                    for scenario in scenarios:
                        direction = scenario.get("direction")
                        if direction not in ("BUY", "SELL") or any(
                                x.get("direction") == direction for x in safe_scenarios):
                            raise ValueError("INVALID_SHADOW_DIRECTION")
                        parts = scenario.get("components")
                        if not isinstance(parts, list) or len(parts) != len(WEIGHTS):
                            raise ValueError("INVALID_SHADOW_COMPONENTS")
                        clean_parts = []
                        for name, part in zip(WEIGHTS, parts, strict=True):
                            pts, available, maximum = (
                                part.get("points"), part.get("available"), part.get("max"))
                            if (part.get("id") != name or maximum != WEIGHTS[name] or
                                    type(pts) is not int or type(available) is not int or
                                    not 0 <= pts <= available <= maximum):
                                raise ValueError("INVALID_SHADOW_POINTS")
                            clean_parts.append({"id": name, "points": pts,
                                                "available": available, "max": maximum})
                        score = sum(p["points"] for p in clean_parts)
                        if scenario.get("score") != score or type(scenario.get("score")) is not int:
                            raise ValueError("INVALID_SHADOW_SCORE")
                        safe_scenarios.append({
                            "direction": direction, "score": score,
                            "available_points": sum(p["available"] for p in clean_parts),
                            "setup_complete": scenario.get("setup_complete") is True,
                            "components": clean_parts,
                        })
                verdict = row.get("verdict")
                if verdict not in ("STALE", "NOT_APPLICABLE", "DISABLED",
                                   "INSUFFICIENT_EVIDENCE", "NO_COMPLETE_SETUP",
                                   "SHADOW_THRESHOLD_MET", "BELOW_THRESHOLD"):
                    raise ValueError("INVALID_SHADOW_VERDICT")
                best = row.get("best_score")
                if best is not None and (type(best) is not int or not 0 <= best <= 100):
                    raise ValueError("INVALID_SHADOW_SCORE")
                if safe_scenarios and best != max(s["score"] for s in safe_scenarios):
                    raise ValueError("INVALID_SHADOW_BEST")
                if not scenarios and best is not None:
                    raise ValueError("INVALID_SHADOW_DISABLED")
                if not stale and verdict == "SHADOW_THRESHOLD_MET" and (
                        best is None or best < THRESHOLD or
                        not any(s["setup_complete"] and s["score"] == best for s in safe_scenarios)):
                    raise ValueError("INVALID_SHADOW_THRESHOLD")
                cleaned.append({
                    "strategy_id": strategy, "enabled": row.get("enabled") is True,
                    "eligible": row.get("eligible") is True,
                    "verdict": "STALE" if stale and verdict != "NOT_APPLICABLE" else verdict,
                    "best_direction": None if stale else row.get("best_direction") if row.get("best_direction") in ("BUY", "SELL") else None,
                    "best_score": None if stale else best,
                    "scenarios": safe_scenarios,
                })
            response.append({"symbol": symbol, "market_state": "STALE" if stale else "ACTIVE",
                             "observed_at": when.isoformat(), "age_seconds": max(0, int(age)),
                             "rows": cleaned, "diagnostic_only": True})
            seen.add(symbol)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            continue
    return {"source": "forex_ai_shadow_score_audit",
            "generated_at": now.isoformat(), "policy_version": POLICY,
            "threshold": THRESHOLD, "weights": dict(WEIGHTS),
            "diagnostic_only": True, "scores": response}


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


_UnixServerBase = getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)


class ThreadingUnixServer(socketserver.ThreadingMixIn, _UnixServerBase):
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
            if path.path == "/api/signal-matrix":
                return self._json(200, read_signal_matrix())
            if path.path == "/api/shadow-scores":
                return self._json(200, read_shadow_scores())
            if path.path == "/api/experience":
                from forex_ai.research.experience_memory import read_experience
                query = parse_qs(path.query)
                candidate_id = query.get("candidate_id", [None])[0]
                if (not isinstance(candidate_id, str) or
                        not 1 <= len(candidate_id) <= 128 or
                        len(query.get("candidate_id", [])) != 1 or
                        set(query) != {"candidate_id"}):
                    return self._json(400, {"error": "INVALID_EXPERIENCE_QUERY"})
                try:
                    return self._json(200, read_experience(
                        load_runtime_config().db_path, candidate_id))
                except ValueError:
                    return self._json(400, {"error": "INVALID_EXPERIENCE_INPUT"})
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
    LIVE_ACCOUNT.start()
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        LIVE_ACCOUNT.stop()
        collector.stop()
        server.server_close()
        path.unlink(missing_ok=True)
