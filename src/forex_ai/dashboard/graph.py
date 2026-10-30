"""Read-only, provenance-safe Forex-AI execution graph for the command center.

Graph meaning is strictly journal-derived:
Strategy -> Candidate -> Risk Decision -> Order Intent -> Execution Transition.
The risk and execution stages are absent if they have no persisted evidence.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

MAX_CANDIDATES = 180
UTC = timezone.utc


def _node(key: str, kind: str, label: str, **attrs) -> dict:
    return {"id": key, "type": kind, "label": label, **attrs}


def _edge(src: str, dst: str, relation: str) -> dict:
    return {"id": f"{src}>{relation}>{dst}", "source": src, "target": dst, "relation": relation}


def read_execution_graph(db_path: Path, *, limit: int = 110) -> dict:
    """Read bounded topology from SQLite without modifying DB or fabricating stages."""
    if not isinstance(limit, int) or not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError("graph limit must be from 1 to 180")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=3000")
    try:
        candidates = con.execute(
            """SELECT candidate_id, strategy_id, symbol, side, generated_at_utc
               FROM candidate_decisions ORDER BY generated_at_utc DESC, candidate_id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        full_count = con.execute("SELECT COUNT(*) FROM candidate_decisions").fetchone()[0]
        strategy_rows = con.execute(
            "SELECT strategy_id, COUNT(*) AS count FROM candidate_decisions GROUP BY strategy_id ORDER BY count DESC LIMIT 20"
        ).fetchall()
        intent_rows = con.execute(
            """SELECT intent_id,candidate_id,symbol,side,volume,state,updated_at_utc
               FROM order_intents_v1 WHERE candidate_id IN (
                   SELECT candidate_id FROM candidate_decisions
                   ORDER BY generated_at_utc DESC, candidate_id DESC LIMIT ?
               ) ORDER BY updated_at_utc DESC LIMIT 180""", (limit,)
        ).fetchall()
        intents_by_candidate: dict[str, list[sqlite3.Row]] = {}
        for intent in intent_rows:
            intents_by_candidate.setdefault(intent["candidate_id"], []).append(intent)

        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        status_counts: Counter[str] = Counter()
        risk_approved = 0
        risk_evaluated = 0
        def addnode(node: dict) -> None:
            nodes[node["id"]] = node
        def addedge(edge: dict) -> None:
            edges[edge["id"]] = edge

        for c in candidates:
            cid = str(c["candidate_id"])
            sid = f"strategy:{c['strategy_id']}"
            oid = f"symbol:{c['symbol']}"
            cnid = f"candidate:{cid}"
            if sid not in nodes:
                addnode(_node(sid, "strategy", str(c["strategy_id"]), strategy_id=c["strategy_id"]))
            if oid not in nodes:
                addnode(_node(oid, "symbol", str(c["symbol"])))
            addnode(_node(cnid, "candidate", cid[:9].upper(), side=c["side"], symbol=c["symbol"],
                          strategy_id=c["strategy_id"], timestamp=c["generated_at_utc"],
                          reference=cid))
            addedge(_edge(sid, cnid, "GENERATED"))
            addedge(_edge(oid, cnid, "ON_SYMBOL"))

            risk = con.execute(
                """SELECT approved, created_at_utc, reason_codes_json
                   FROM risk_decisions_v1 WHERE candidate_id=?
                   ORDER BY created_at_utc DESC LIMIT 1""", (cid,)
            ).fetchone()
            if risk is not None:
                risk_evaluated += 1
                approved = bool(risk["approved"])
                risk_approved += int(approved)
                rid = f"risk:{cid}"
                # The only risk detail surfaced is the structured reason-code set.
                import json
                try:
                    reasons = json.loads(risk["reason_codes_json"] or "[]")
                except (ValueError, TypeError):
                    reasons = []
                safe_reasons = [str(x)[:75] for x in reasons if isinstance(x, str)][:8] if isinstance(reasons, list) else []
                addnode(_node(rid, "risk", "APPROVED" if approved else "BLOCKED",
                              approved=approved, reasons=safe_reasons, timestamp=risk["created_at_utc"],
                              candidate_id=cid))
                addedge(_edge(cnid, rid, "EVALUATED"))

            for intent in intents_by_candidate.get(cid, []):
                intent_id = str(intent["intent_id"])
                inode = f"intent:{intent_id}"
                st = str(intent["state"])
                status_counts[st] += 1
                addnode(_node(inode, "intent", st, state=st, side=intent["side"],
                              volume=str(intent["volume"]), symbol=intent["symbol"],
                              timestamp=intent["updated_at_utc"], reference=intent_id))
                addedge(_edge(f"risk:{cid}" if risk else cnid, inode, "CREATED_INTENT"))
                transition = con.execute(
                    """SELECT id, to_state, timestamp_utc, reason
                       FROM execution_transitions_v1 WHERE intent_id=?
                       ORDER BY id DESC LIMIT 1""", (intent_id,)
                ).fetchone()
                if transition:
                    eid = f"transition:{transition['id']}"
                    addnode(_node(eid, "execution", str(transition["to_state"]),
                                  timestamp=transition["timestamp_utc"],
                                  reason=str(transition["reason"] or "")[:100],
                                  reference=intent_id))
                    addedge(_edge(inode, eid, "TRANSITIONED"))

        # Summary is explicitly divided into total history and visible sample.
        return {
            "schema_version": 1,
            "source": "forex_ai_sqlite_journal",
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": {"kind": "most_recent_candidates", "limit": limit,
                      "selected_candidates": len(candidates), "total_candidates": full_count,
                      "risk_evaluated_in_sample": risk_evaluated,
                      "risk_approved_in_sample": risk_approved},
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
            "summary": {
                "visible_nodes": len(nodes), "visible_edges": len(edges),
                "strategies": [{"id": str(r["strategy_id"]), "count": int(r["count"])} for r in strategy_rows],
                "visible_intents_by_state": dict(status_counts),
                "visible_risk_approval_rate": round(100 * risk_approved / risk_evaluated, 1)
                if risk_evaluated else None,
            },
        }
    finally:
        con.close()
