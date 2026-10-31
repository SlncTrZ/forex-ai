"""V2 execution graph: never invent stages and never leak trading credentials."""
from __future__ import annotations

import json
import socket
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from forex_ai.dashboard import server
from forex_ai.dashboard.graph import read_execution_graph
from forex_ai.journal.db import initialize


def make_db(path: Path) -> Path:
    initialize(path)
    with sqlite3.connect(path) as c:
        c.executemany(
            """INSERT INTO candidate_decisions
               (candidate_id,correlation_id,strategy_id,strategy_version,symbol,side,
                generated_at_utc,expires_at_utc,evidence_hash,market_snapshot_fingerprint,payload_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            [
                ("c1","corr1","smc_pro_fvg_v2","1.0","XAUUSDm","BUY","2026-10-30T08:00:00+00:00",
                 "2026-10-30T09:00:00+00:00","hash1","finger1","{}"),
                ("c2","corr2","smc_pro_fvg_v2","1.0","XAUUSDm","SELL","2026-10-30T09:00:00+00:00",
                 "2026-10-30T10:00:00+00:00","hash2","finger2","{}"),
            ]
        )
        c.execute("""INSERT INTO risk_decisions_v1
                   (candidate_id,created_at_utc,approved,risk_profile_fingerprint,
                    safety_snapshot_fingerprint,expires_at_utc,reason_codes_json,payload_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                   ("c1","2026-10-30T08:00:03+00:00",1,"rp","safe",
                    "2026-10-30T08:10:00+00:00",'["LIMIT_OK"]',"{}"))
        c.execute("""INSERT INTO order_intents_v1
                   (intent_id,candidate_id,idempotency_key,symbol,side,volume,entry,stop_loss,take_profit,
                    state,created_at_utc,updated_at_utc,filled_volume)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("intent1","c1","unique1","XAUUSDm","BUY","0.01","5000","4900","5100","REJECTED",
                     "2026-10-30T08:00:04+00:00","2026-10-30T08:00:06+00:00","0"))
        c.execute("""INSERT INTO execution_transitions_v1
                   (intent_id,timestamp_utc,from_state,to_state,reason,payload_json)
                    VALUES(?,?,?,?,?,?)""",
                    ("intent1","2026-10-30T08:00:06+00:00","PENDING","REJECTED","SPREAD_BLOCK","{}"))
    return path


def test_journal_graph_has_real_relationships_and_no_invented_risk(tmp_path):
    db = make_db(tmp_path/"graph.db")
    result = read_execution_graph(db, limit=2)
    assert result["source"] == "forex_ai_sqlite_journal"
    assert result["scope"]["selected_candidates"] == 2
    assert result["scope"]["risk_evaluated_in_sample"] == 1
    assert result["summary"]["visible_risk_approval_rate"] == 100
    kinds = {node["id"]:node["type"] for node in result["nodes"]}
    assert kinds["strategy:smc_pro_fvg_v2"] == "strategy"
    assert kinds["candidate:c2"] == "candidate"
    assert "risk:c2" not in kinds
    assert kinds["risk:c1"] == "risk"
    assert kinds["intent:intent1"] == "intent"
    assert any(n["type"]=="execution" and n["reason"]=="SPREAD_BLOCK" for n in result["nodes"])
    relation_ids={(e["source"],e["target"],e["relation"]) for e in result["edges"]}
    assert ("strategy:smc_pro_fvg_v2","candidate:c2","GENERATED") in relation_ids
    assert ("risk:c1","intent:intent1","CREATED_INTENT") in relation_ids
    assert not any("password" in json.dumps(n).lower() for n in result["nodes"])


def test_graph_limit_and_read_only_database(tmp_path):
    db=make_db(tmp_path/"graph.db")
    for invalid in (0,-1,181,"10",True):
        if invalid is True: continue
        with pytest.raises(ValueError):
            read_execution_graph(db,limit=invalid)
    result=read_execution_graph(db,limit=1)
    assert len([n for n in result["nodes"] if n["type"]=="candidate"]) == 1
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM candidate_decisions").fetchone()[0] == 2


def _request(sock:Path,url:str)->tuple[int,dict]:
    s=socket.socket(socket.AF_UNIX);s.settimeout(4);s.connect(str(sock))
    s.sendall(("GET "+url+" HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nConnection: close\r\n\r\n").encode())
    parts=[]
    while True:
        block=s.recv(65536)
        if not block:break
        parts.append(block)
    s.close();data=b"".join(parts)
    return int(data.split(b" ")[1]),json.loads(data.split(b"\r\n\r\n",1)[1])


def test_graph_route_honors_cap_and_reads_immutable_db(tmp_path,monkeypatch):
    db=make_db(tmp_path/"graph.db")
    monkeypatch.setattr(server,"load_runtime_config",lambda:SimpleNamespace(db_path=db))
    sock=tmp_path/"api.sock"
    srv=server.ThreadingUnixServer(str(sock),server.Handler)
    thread=threading.Thread(target=srv.serve_forever,daemon=True)
    thread.start()
    try:
        status,data=_request(sock,"/api/graph?limit=2")
        assert status==200
        assert data["summary"]["visible_nodes"] >= 6
        assert data["scope"]["total_candidates"]==2
        status,data=_request(sock,"/api/graph?limit=100000")
        assert status==400 and data["error"]=="GRAPH_LIMIT_OUT_OF_RANGE"
    finally:
        srv.shutdown();srv.server_close();thread.join(3)
