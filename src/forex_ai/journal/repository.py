from __future__ import annotations

import json
from typing import Any

from forex_ai.journal.db import log_audit_event, session, utc_now


def insert_account(db_path, account: dict[str, Any]) -> int:
    with session(db_path) as con:
        cur = con.execute(
            """INSERT INTO accounts(
                timestamp,login,server,currency,balance,equity,margin,free_margin,margin_level,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                utc_now(), account.get("login"), account.get("server"), account.get("currency"),
                account.get("balance"), account.get("equity"), account.get("margin"),
                account.get("margin_free"), account.get("margin_level"),
                json.dumps(account, ensure_ascii=False, default=str),
            ),
        )
        return int(cur.lastrowid)


def insert_market_snapshot(db_path, symbol: str, tick: dict[str, Any], payload: dict[str, Any]) -> int:
    bid = tick.get("bid")
    ask = tick.get("ask")
    spread = None if bid is None or ask is None else float(ask) - float(bid)
    with session(db_path) as con:
        cur = con.execute(
            """INSERT INTO market_snapshots(
                timestamp,symbol,bid,ask,spread,timeframe,market_regime,payload_json
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                utc_now(), symbol, bid, ask, spread, payload.get("timeframe"),
                payload.get("market_regime"), json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        return int(cur.lastrowid)


def upsert_mt5_deals(db_path, deals: list[dict[str, Any]]) -> int:
    if not deals:
        return 0
    rows = []
    for d in deals:
        rows.append((
            d.get("ticket"), d.get("order"), d.get("time"), d.get("time_msc"),
            d.get("type"), d.get("entry"), d.get("magic"), d.get("position_id"),
            d.get("symbol"), d.get("volume"), d.get("price"), d.get("commission"),
            d.get("swap"), d.get("profit"), d.get("fee"), d.get("reason"),
            d.get("comment"), json.dumps(d, ensure_ascii=False, default=str),
        ))
    with session(db_path) as con:
        con.executemany(
            """INSERT INTO mt5_deals(
                ticket,order_ticket,time,time_msc,type,entry,magic,position_id,symbol,volume,
                price,commission,swap,profit,fee,reason,comment,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticket) DO UPDATE SET
                order_ticket=excluded.order_ticket,time=excluded.time,time_msc=excluded.time_msc,
                type=excluded.type,entry=excluded.entry,magic=excluded.magic,position_id=excluded.position_id,
                symbol=excluded.symbol,volume=excluded.volume,price=excluded.price,
                commission=excluded.commission,swap=excluded.swap,profit=excluded.profit,fee=excluded.fee,
                reason=excluded.reason,comment=excluded.comment,raw_json=excluded.raw_json""",
            rows,
        )
    return len(rows)


def upsert_mt5_orders(db_path, orders: list[dict[str, Any]]) -> int:
    if not orders:
        return 0
    rows = []
    for o in orders:
        rows.append((
            o.get("ticket"), o.get("time_setup"), o.get("time_done"), o.get("type"),
            o.get("state"), o.get("magic"), o.get("position_id"), o.get("position_by_id"),
            o.get("symbol"), o.get("volume_initial"), o.get("volume_current"), o.get("price_open"),
            o.get("sl"), o.get("tp"), o.get("price_current"), o.get("reason"), o.get("comment"),
            json.dumps(o, ensure_ascii=False, default=str),
        ))
    with session(db_path) as con:
        con.executemany(
            """INSERT INTO mt5_orders_history(
                ticket,time_setup,time_done,type,state,magic,position_id,position_by_id,symbol,
                volume_initial,volume_current,price_open,sl,tp,price_current,reason,comment,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticket) DO UPDATE SET
                time_setup=excluded.time_setup,time_done=excluded.time_done,type=excluded.type,
                state=excluded.state,magic=excluded.magic,position_id=excluded.position_id,
                position_by_id=excluded.position_by_id,symbol=excluded.symbol,
                volume_initial=excluded.volume_initial,volume_current=excluded.volume_current,
                price_open=excluded.price_open,sl=excluded.sl,tp=excluded.tp,
                price_current=excluded.price_current,reason=excluded.reason,comment=excluded.comment,
                raw_json=excluded.raw_json""",
            rows,
        )
    return len(rows)


def insert_position_snapshots(db_path, positions: list[dict[str, Any]]) -> int:
    if not positions:
        return 0
    now = utc_now()
    rows = [
        (
            now, p.get("ticket"), p.get("identifier"), p.get("symbol"), p.get("type"),
            p.get("volume"), p.get("price_open"), p.get("sl"), p.get("tp"),
            p.get("price_current"), p.get("swap"), p.get("profit"),
            json.dumps(p, ensure_ascii=False, default=str),
        )
        for p in positions
    ]
    with session(db_path) as con:
        con.executemany(
            """INSERT INTO position_snapshots(
                timestamp,ticket,identifier,symbol,type,volume,price_open,sl,tp,price_current,swap,profit,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    return len(rows)


def get_signal(db_path, signal_id: int) -> dict[str, Any] | None:
    with session(db_path) as con:
        row = con.execute(
            """SELECT s.*, sk.signal_key
               FROM signals s LEFT JOIN signal_keys sk ON sk.signal_id=s.id
               WHERE s.id=?""",
            (signal_id,),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    try:
        data["payload"] = json.loads(data.pop("payload_json"))
    except Exception:
        data["payload"] = {}
    return data


def pending_signals(db_path, limit: int = 10) -> list[dict[str, Any]]:
    with session(db_path) as con:
        rows = con.execute(
            """SELECT s.*, sk.signal_key
               FROM signals s
               LEFT JOIN signal_keys sk ON sk.signal_id=s.id
               WHERE NOT EXISTS (SELECT 1 FROM llm_decisions d WHERE d.signal_id=s.id)
               ORDER BY s.id ASC LIMIT ?""",
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        try:
            data["payload"] = json.loads(data.pop("payload_json"))
        except Exception:
            data["payload"] = {}
        result.append(data)
    return result


def insert_signal(
    db_path,
    *,
    signal_key: str,
    symbol: str,
    strategy: str,
    direction: str,
    score: float,
    proposed_entry: float | None,
    proposed_sl: float | None,
    proposed_tp: float | None,
    rr: float | None,
    payload: dict[str, Any],
    market_time_msc: int | None = None,
) -> tuple[int, bool]:
    with session(db_path) as con:
        existing = con.execute("SELECT signal_id FROM signal_keys WHERE signal_key=?", (signal_key,)).fetchone()
        if existing:
            return int(existing[0]), False
        cur = con.execute(
            """INSERT INTO signals(
                timestamp,symbol,strategy,direction,score,proposed_entry,proposed_sl,proposed_tp,rr,feature_snapshot_id,payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                utc_now(), symbol, strategy, direction, score, proposed_entry, proposed_sl,
                proposed_tp, rr, None, json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        signal_id = int(cur.lastrowid)
        con.execute(
            "INSERT INTO signal_keys(signal_key,signal_id,created_at_utc) VALUES(?,?,?)",
            (signal_key, signal_id, utc_now()),
        )
    log_audit_event(
        db_path,
        event_type="SIGNAL",
        source="signal_bot",
        symbol=symbol,
        entity_id=str(signal_id),
        correlation_id=signal_key,
        market_time_msc=market_time_msc,
        payload={
            "signal_id": signal_id,
            "signal_key": signal_key,
            "strategy": strategy,
            "direction": direction,
            "score": score,
            "proposed_entry": proposed_entry,
            "proposed_sl": proposed_sl,
            "proposed_tp": proposed_tp,
            "rr": rr,
            "evidence": payload,
        },
    )
    return signal_id, True


def insert_llm_decision(
    db_path,
    *,
    symbol: str,
    mode: str,
    model: str,
    prompt_version: str,
    decision: dict[str, Any],
    usage: dict[str, Any],
    signal_id: int | None = None,
    raw_response_hash: str | None = None,
    correlation_id: str | None = None,
    market_time_msc: int | None = None,
) -> int:
    with session(db_path) as con:
        cur = con.execute(
            """INSERT INTO llm_decisions(
                timestamp,signal_id,symbol,mode,model,prompt_version,action,confidence,thesis,
                risks_json,input_tokens,output_tokens,cached_tokens,api_cost_usd,latency_ms,raw_response_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                utc_now(), signal_id, symbol, mode, model, prompt_version,
                decision.get("action"), decision.get("confidence"), decision.get("thesis"),
                json.dumps(decision.get("risk_flags", []), ensure_ascii=False),
                usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                usage.get("cached_tokens", 0), usage.get("api_cost_usd", 0.0),
                usage.get("latency_ms", 0), raw_response_hash,
            ),
        )
        decision_id = int(cur.lastrowid)
    log_audit_event(
        db_path,
        event_type="LLM_DECISION",
        source="llm",
        symbol=symbol,
        entity_id=str(decision_id),
        correlation_id=correlation_id,
        market_time_msc=market_time_msc,
        payload={
            "decision_id": decision_id,
            "signal_id": signal_id,
            "mode": mode,
            "model": model,
            "prompt_version": prompt_version,
            "decision": decision,
            "usage": usage,
            "raw_response_hash": raw_response_hash,
        },
    )
    return decision_id
