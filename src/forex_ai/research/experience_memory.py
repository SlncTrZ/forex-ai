"""Read-only historical experience retrieval with broker-verified provenance.

NO policy adaptation, ML retraining, D3 calls, or trading authorization.
Current historical candidates lack full feature snapshots, so returned similarities
are STRUCTURAL_ONLY and MUST NOT alter the P2 History component (still 0 points).
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


POLICY_VERSION = "historical-experience-v1"
MAX_HISTORY_SCAN = 2000
MAX_MATCHES = 12
MIN_EXPERIENCE_SUPPORT = 20
UTC = timezone.utc


def _utc(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 60:
        raise ValueError("INVALID_DATE")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("NAIVE_TIMESTAMP")
    return result.astimezone(UTC)


def _positive(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def _nonneg(value: object) -> float | None:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def _session_alignment(a: datetime, b: datetime) -> float:
    delta = abs((a.hour + a.minute / 60) - (b.hour + b.minute / 60))
    delta = min(delta, 24 - delta)
    return 1 - min(delta, 12) / 12


def _stop_percent(entry: object, stop: object) -> float | None:
    price = _positive(entry)
    loss = _positive(stop)
    if price is None or loss is None:
        return None
    distance = abs(price - loss) / price
    return distance if 0 < distance <= .5 else None


def read_experience(db_path: Path, candidate_id: str, *,
                    now_utc: datetime | None = None, limit: int = 8) -> dict:
    """Retrieve earlier system-owned settled trades similar to a candidate.

    Evidence is the DB join candidate -> order_intent -> MT5 position -> trade.
    The query time is the original candidate creation time, not the current time;
    future outcomes cannot be returned for a replayed candidate.
    """
    if not isinstance(candidate_id, str) or not 1 <= len(candidate_id) <= 128:
        raise ValueError("INVALID_CANDIDATE_ID")
    if type(limit) is not int or not 1 <= limit <= MAX_MATCHES:
        raise ValueError("INVALID_MATCH_LIMIT")
    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    with closing(sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        target = con.execute(
            "SELECT candidate_id,strategy_id,strategy_version,strategy_config_fingerprint,"
            "symbol,side,generated_at_utc,payload_json FROM candidate_decisions WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if target is None:
            return {"policy_version": POLICY_VERSION, "candidate_id": candidate_id,
                    "state": "NOT_FOUND", "diagnostic_only": True, "matches": []}
        when = _utc(target["generated_at_utc"])
        if when > now:
            return {"policy_version": POLICY_VERSION, "candidate_id": candidate_id,
                    "state": "FUTURE_CANDIDATE", "diagnostic_only": True, "matches": []}
        # Only system-owned/closed positions with explicit matching candidate and
        # order identity. This excludes 339+ manual trades and open positions.
        rows = con.execute(
            """SELECT t.id AS trade_id,t.symbol,t.open_time,t.close_time,t.net_pnl,
                      t.source_mode,t.mt5_position_id,
                      c.candidate_id AS historical_candidate,c.strategy_id,
                      c.strategy_version,c.strategy_config_fingerprint,
                      c.symbol AS candidate_symbol,c.side,c.generated_at_utc,
                      c.payload_json
               FROM trades t
               JOIN order_intents_v1 o
                 ON o.broker_position_ticket=t.mt5_position_id AND o.symbol=t.symbol
               JOIN candidate_decisions c
                 ON c.candidate_id=o.candidate_id AND c.symbol=t.symbol AND c.side=o.side
               WHERE t.source_mode='system' AND t.symbol=? AND c.strategy_id=?
                 AND c.strategy_version=? AND c.side=?
                 AND o.state='CLOSED'
                 AND t.close_time IS NOT NULL
                 AND NOT EXISTS (
                   SELECT 1 FROM order_intents_v1 other
                   WHERE other.broker_position_ticket=t.mt5_position_id
                     AND other.intent_id<>o.intent_id
                 )
               ORDER BY t.close_time DESC,t.id DESC LIMIT ?""",
            (target["symbol"], target["strategy_id"], target["strategy_version"],
             target["side"], MAX_HISTORY_SCAN),
        ).fetchall()
        # A broker position linked to multiple intents is not reliable
        # strategy provenance. Reject every ambiguous lineage, not merely
        # the second row encountered by an ordered JOIN.
        ambiguous_positions = {
            row[0] for row in con.execute(
                "SELECT broker_position_ticket FROM order_intents_v1 "
                "WHERE broker_position_ticket IS NOT NULL "
                "GROUP BY broker_position_ticket HAVING COUNT(*) != 1"
            ).fetchall()
        }
    import json
    try:
        raw_target = json.loads(target["payload_json"])
        if not isinstance(raw_target, dict):
            raw_target = {}
    except (ValueError, TypeError):
        raw_target = {}
    base_stop = _stop_percent(raw_target.get("reference_entry"), raw_target.get("stop_loss"))
    candidates = []
    seen_positions = set()
    for r in rows:
        if r["mt5_position_id"] in seen_positions or r["mt5_position_id"] in ambiguous_positions:
            continue
        try:
            closed = _utc(r["close_time"])
            opened = _utc(r["open_time"])
            generated = _utc(r["generated_at_utc"])
            if closed >= when or opened > closed or generated > opened:
                continue
            pnl = _nonneg(r["net_pnl"])
            if pnl is None or not r["mt5_position_id"] or r["candidate_symbol"] != r["symbol"]:
                continue
            raw = json.loads(r["payload_json"])
            if not isinstance(raw, dict):
                continue
            hist_stop = _stop_percent(raw.get("reference_entry"), raw.get("stop_loss"))
            distance_similarity = (
                1 - min(1.0, abs(base_stop - hist_stop) / max(base_stop, hist_stop))
                if base_stop is not None and hist_stop is not None else 0.0
            )
            # Similarity reflects coarse metadata, NOT indicator-regime patterns.
            session_sim = _session_alignment(when, generated)
            same_config = (bool(target["strategy_config_fingerprint"]) and
                           target["strategy_config_fingerprint"] == r["strategy_config_fingerprint"])
            similarity = round(.55 + .25 * distance_similarity + .15 * session_sim +
                               (.05 if same_config else 0), 4)
            key = hashlib.sha256(
                f"{r['trade_id']}:{r['historical_candidate']}:{r['mt5_position_id']}".encode()
            ).hexdigest()[:16]
            candidates.append({
                "experience_id": key,
                "similarity": similarity,
                "evidence_class": "STRUCTURAL_ONLY",
                "outcome": "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT",
                "net_pnl": round(pnl, 2),
                "closed_at_utc": closed.isoformat(),
                "stop_distance_known": base_stop is not None and hist_stop is not None,
                "config_match": bool(same_config),
            })
            seen_positions.add(r["mt5_position_id"])
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    candidates.sort(key=lambda x: (-x["similarity"], -datetime.fromisoformat(x["closed_at_utc"]).timestamp(),
                                   x["experience_id"]))
    top = candidates[:limit]
    n = len(candidates)
    wins = sum(x["outcome"] == "WIN" for x in candidates)
    losses = sum(x["outcome"] == "LOSS" for x in candidates)
    fully_featured = 0  # Historical candidate journal has no indicator snapshot.
    return {
        "policy_version": POLICY_VERSION, "candidate_id": candidate_id,
        "strategy_id": str(target["strategy_id"]), "symbol": str(target["symbol"]),
        "side": str(target["side"]), "as_of_utc": when.isoformat(),
        "state": "LOW_SUPPORT" if n < MIN_EXPERIENCE_SUPPORT else "FEATURES_UNAVAILABLE",
        "evidence_class": "STRUCTURAL_ONLY",
        "sample_size": n, "max_scanned": MAX_HISTORY_SCAN,
        "possible_truncation": len(rows) == MAX_HISTORY_SCAN,
        "wins": wins, "losses": losses, "flat": n - wins - losses,
        "win_rate_observed": round(wins / n, 4) if n else None,
        "net_pnl_observed": round(sum(x["net_pnl"] for x in candidates), 2),
        "full_feature_coverage": fully_featured,
        "calibrated": False, "learned_score": None, "history_score_points": 0,
        "diagnostic_only": True, "matches": top,
    }
