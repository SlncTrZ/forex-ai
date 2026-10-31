"""Historical retrieval is source-verified, causal, bounded, and read-only."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from forex_ai.journal.db import initialize, session
from forex_ai.research.experience_memory import read_experience

UTC = timezone.utc
AS_OF = datetime(2026, 10, 31, 12, tzinfo=UTC)


def _candidate(db, ident: str, *, symbol="XAUUSDm",
               strategy="supertrend_volume_m5", version="1",
               side="BUY", created="2026-10-28T08:00:00+00:00", stop=99.0, entry=100.0):
    data = {"reference_entry": entry, "stop_loss": stop}
    with session(db) as con:
        con.execute(
            """INSERT INTO candidate_decisions(candidate_id,correlation_id,strategy_id,
            strategy_version,strategy_config_fingerprint,symbol,side,
            generated_at_utc,expires_at_utc,evidence_hash,market_snapshot_fingerprint,payload_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ident, ident, strategy, version, "CONFIG", symbol, side, created,
             "2026-11-30T00:00:00+00:00", "HASH", "MARKET", json.dumps(data)),
        )


def _trade(db, ident: str, position: int, *,
           source="system", closed="2026-10-28T10:00:00+00:00",
           opened="2026-10-28T09:00:00+00:00", pnl=-20.0,
           intent_state="CLOSED"):
    with session(db) as con:
        con.execute(
            """INSERT INTO order_intents_v1(intent_id,candidate_id,idempotency_key,
            symbol,side,volume,entry,stop_loss,take_profit,state,created_at_utc,
            broker_position_ticket,filled_volume,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("intent-"+ident,ident,"key-"+ident,"XAUUSDm","BUY","0.01","100","99",
             "102",intent_state,opened,position,"0.01",closed),
        )
        con.execute(
            """INSERT INTO trades(symbol,source_mode,open_time,close_time,
            entry_price,exit_price,volume,net_pnl,mt5_position_id,payload_json)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ("XAUUSDm",source,opened,closed,100,98,0.01,pnl,position,"{}"),
        )


def test_causal_retrieval_excludes_manual_future_wrong_strategy_and_unclosed(tmp_path):
    db = tmp_path / "learning.db"
    initialize(db)
    _candidate(db, "TARGET", created="2026-10-31T12:00:00+00:00")
    _candidate(db, "PAST")
    _trade(db, "PAST", 101, pnl=-20)
    _candidate(db, "MANUAL")
    _trade(db, "MANUAL", 102, source="manual", pnl=200)
    _candidate(db, "FUTURE")
    _trade(db, "FUTURE", 103, closed="2026-11-01T10:00:00+00:00", pnl=300)
    _candidate(db, "UNCLOSED")
    _trade(db, "UNCLOSED", 104, intent_state="FILLED", pnl=300)
    _candidate(db, "OTHER", strategy="smc_pro_fvg_v2")
    _trade(db, "OTHER", 105, pnl=300)
    result = read_experience(db, "TARGET", now_utc=AS_OF)
    assert result["diagnostic_only"] is True
    assert result["sample_size"] == 1
    assert result["wins"] == 0 and result["losses"] == 1
    assert result["net_pnl_observed"] == -20
    assert result["matches"][0]["outcome"] == "LOSS"
    assert result["matches"][0]["evidence_class"] == "STRUCTURAL_ONLY"
    assert result["calibrated"] is False
    assert result["learned_score"] is None and result["history_score_points"] == 0
    assert result["state"] == "LOW_SUPPORT"


def test_historical_candidate_sees_only_closed_experiences_as_of_its_own_time(tmp_path):
    db = tmp_path / "learning.db"
    initialize(db)
    _candidate(db, "PAST")
    _trade(db, "PAST", 201, pnl=100)
    _candidate(db, "EARLY", created="2026-10-28T09:30:00+00:00")
    result = read_experience(db, "EARLY", now_utc=AS_OF)
    assert result["sample_size"] == 0
    assert result["matches"] == [] and result["win_rate_observed"] is None


def test_order_and_candidate_version_must_match(tmp_path):
    db = tmp_path / "learning.db"
    initialize(db)
    _candidate(db, "TARGET", created="2026-10-31T12:00:00+00:00")
    _candidate(db, "OTHER_VERSION", version="2")
    _trade(db, "OTHER_VERSION", 301)
    _candidate(db, "OTHER_SIDE", side="SELL")
    _trade(db, "OTHER_SIDE", 302)
    result = read_experience(db, "TARGET", now_utc=AS_OF)
    assert result["sample_size"] == 0


def test_invalid_target_is_fail_closed(tmp_path):
    db = tmp_path / "learning.db"
    initialize(db)
    assert read_experience(db, "UNKNOWN")["state"] == "NOT_FOUND"
    _candidate(db, "FUT", created="2026-11-01T00:00:00+00:00")
    assert read_experience(db, "FUT", now_utc=AS_OF)["state"] == "FUTURE_CANDIDATE"
    with pytest.raises(ValueError):
        read_experience(db, "TARGET", limit=100)
    with pytest.raises(ValueError):
        read_experience(db, "")


def test_matching_is_deterministic_without_insider_raw_payload(tmp_path):
    db = tmp_path / "learning.db"
    initialize(db)
    _candidate(db, "TARGET", created="2026-10-31T12:00:00+00:00")
    for i in range(5):
        name = f"PAST_{i}"
        _candidate(db, name)
        _trade(db, name, 400+i, pnl=(-1) ** i * 10)
    first = read_experience(db, "TARGET", now_utc=AS_OF, limit=3)
    second = read_experience(db, "TARGET", now_utc=AS_OF, limit=3)
    assert first == second
    assert first["sample_size"] == 5
    assert len(first["matches"]) == 3
    assert all(0 <= x["similarity"] <= 1 for x in first["matches"])
    assert "magic" not in str(first)
    assert "position_id" not in str(first)



def test_research_observer_is_idempotent_and_never_awards_history_points(tmp_path):
    from forex_ai.research.experience_observer import EVENT, scan_recent_experiences
    db = tmp_path / "research.db"
    initialize(db)
    _candidate(db, "PAST")
    _trade(db, "PAST", 501, pnl=15)
    _candidate(db, "TARGET", created="2026-10-31T12:00:00+00:00")
    first = scan_recent_experiences(db, now_utc=AS_OF)
    second = scan_recent_experiences(db, now_utc=AS_OF)
    assert first == second and len(first) == 2
    assert all(x["history_score_points"] == 0 for x in first)
    with session(db) as c:
        rows = c.execute(
            "SELECT payload_json FROM audit_events WHERE event_type=?",(EVENT,)
        ).fetchall()
    assert len(rows) == 2
    assert all(json.loads(row[0])["calibrated"] is False for row in rows)
    with pytest.raises(ValueError):
        scan_recent_experiences(db, now_utc=AS_OF, limit=0)


def test_history_lookup_does_not_mutate_trade_or_candidate_rows(tmp_path):
    db = tmp_path / "immutable.db"
    initialize(db)
    _candidate(db, "PAST")
    _trade(db, "PAST", 601)
    _candidate(db, "TARGET", created="2026-10-31T12:00:00+00:00")
    with session(db) as c:
        before = tuple(
            c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            for tbl in ("trades", "candidate_decisions", "order_intents_v1")
        )
    read_experience(db, "TARGET", now_utc=AS_OF)
    with session(db) as c:
        after = tuple(
            c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            for tbl in ("trades", "candidate_decisions", "order_intents_v1")
        )
    assert before == after



def test_ambiguous_position_to_two_intents_is_excluded(tmp_path):
    db = tmp_path / "ambiguous.db"
    initialize(db)
    _candidate(db, "PAST")
    _trade(db, "PAST", 701)
    _candidate(db, "OTHER")
    with session(db) as c:
        c.execute(
            """INSERT INTO order_intents_v1(
             intent_id,candidate_id,idempotency_key,symbol,side,volume,entry,
             stop_loss,take_profit,state,created_at_utc,broker_position_ticket,
             filled_volume,updated_at_utc)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("intent-other", "OTHER", "key-other", "XAUUSDm", "BUY",
             "0.01", "100", "99", "102", "CLOSED", "2026-10-28T09:00:00+00:00",
             701, "0.01", "2026-10-28T10:00:00+00:00"),
        )
    _candidate(db, "TARGET", created="2026-10-31T12:00:00+00:00")
    result = read_experience(db, "TARGET", now_utc=AS_OF)
    assert result["sample_size"] == 0


def test_ambiguous_broker_position_lineage_is_excluded(tmp_path):
    db = tmp_path / "ambiguous.db"
    initialize(db)
    _candidate(db, "TARGET", created="2026-10-31T12:00:00+00:00")
    _candidate(db, "PAST")
    _candidate(db, "PAST_DUP")
    _trade(db, "PAST", 702, pnl=50)
    # A second intent sharing one position is ambiguous. Its trade cannot
    # be claimed by either strategy/candidate.
    with session(db) as con:
        con.execute(
            """INSERT INTO order_intents_v1(
            intent_id,candidate_id,idempotency_key,symbol,side,volume,entry,
            stop_loss,take_profit,state,created_at_utc,broker_position_ticket,
            filled_volume,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("intent-PAST_DUP", "PAST_DUP", "key-PAST_DUP", "XAUUSDm", "BUY",
             "0.01", "100", "99", "102", "CLOSED",
             "2026-10-28T09:00:00+00:00", 702, "0.01",
             "2026-10-28T10:00:00+00:00"),
        )
    result = read_experience(db, "TARGET", now_utc=AS_OF)
    assert result["sample_size"] == 0 and result["matches"] == []
    assert result["history_score_points"] == 0


def test_backfilled_verified_outcome_generates_new_idempotent_audit(tmp_path):
    from forex_ai.research.experience_observer import EVENT, scan_recent_experiences
    db = tmp_path / "backfill.db"
    initialize(db)
    _candidate(db, "PAST")
    _candidate(db, "TARGET", created="2026-10-31T12:00:00+00:00")
    before = scan_recent_experiences(db, now_utc=AS_OF)
    assert any(x["candidate_id"] == "TARGET" and x["sample_size"] == 0 for x in before)
    _trade(db, "PAST", 703, pnl=5)
    after = scan_recent_experiences(db, now_utc=AS_OF)
    assert any(x["candidate_id"] == "TARGET" and x["sample_size"] == 1 for x in after)
    scan_recent_experiences(db, now_utc=AS_OF)
    with session(db) as con:
        rows = con.execute(
            "SELECT payload_json FROM audit_events WHERE event_type=? ORDER BY id", (EVENT,)
        ).fetchall()
    target = [json.loads(row[0]) for row in rows if json.loads(row[0])["candidate_id"] == "TARGET"]
    assert len(target) == 2
    assert [x["sample_size"] for x in target] == [0, 1]
    assert all(x["history_score_points"] == 0 for x in target)
