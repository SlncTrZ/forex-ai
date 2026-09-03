from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from forex_ai.advisory.models import Advisory
from forex_ai.execution.state import ExecutionState, IntentRepository, OrderIntent
from forex_ai.mt5.contracts import SafetySnapshot
from forex_ai.risk.broker_engine import BrokerRiskResult
from forex_ai.strategy.v1.contracts import CandidateEnvelope

from .db import session, utc_now


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))


class SQLiteIntentRepository(IntentRepository):
    """Persistent order-intent repository used by the execution controller."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    @staticmethod
    def _from_row(row) -> OrderIntent:
        return OrderIntent(
            intent_id=row["intent_id"], candidate_id=row["candidate_id"], idempotency_key=row["idempotency_key"],
            symbol=row["symbol"], side=row["side"], volume=Decimal(row["volume"]), entry=Decimal(row["entry"]),
            stop_loss=Decimal(row["stop_loss"]), take_profit=Decimal(row["take_profit"]),
            state=ExecutionState(row["state"]), created_at_utc=datetime.fromisoformat(row["created_at_utc"]),
            broker_order_ticket=row["broker_order_ticket"], broker_position_ticket=row["broker_position_ticket"],
            filled_volume=Decimal(row["filled_volume"]), last_reason=row["last_reason"],
        )

    def get(self, intent_id: str) -> OrderIntent | None:
        with session(self.db_path) as con:
            row = con.execute("SELECT * FROM order_intents_v1 WHERE intent_id=?", (intent_id,)).fetchone()
        return self._from_row(row) if row else None

    def get_by_idempotency_key(self, key: str) -> OrderIntent | None:
        with session(self.db_path) as con:
            row = con.execute("SELECT * FROM order_intents_v1 WHERE idempotency_key=?", (key,)).fetchone()
        return self._from_row(row) if row else None

    def save(self, intent: OrderIntent) -> None:
        now = utc_now()
        with session(self.db_path) as con:
            previous = con.execute("SELECT state FROM order_intents_v1 WHERE intent_id=?", (intent.intent_id,)).fetchone()
            conflict = con.execute(
                "SELECT intent_id FROM order_intents_v1 WHERE idempotency_key=? AND intent_id<>?",
                (intent.idempotency_key, intent.intent_id),
            ).fetchone()
            if conflict:
                raise ValueError("duplicate idempotency key")
            con.execute(
                """INSERT INTO order_intents_v1(
                    intent_id,candidate_id,idempotency_key,symbol,side,volume,entry,stop_loss,take_profit,state,
                    created_at_utc,broker_order_ticket,broker_position_ticket,filled_volume,last_reason,updated_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    candidate_id=excluded.candidate_id,idempotency_key=excluded.idempotency_key,
                    symbol=excluded.symbol,side=excluded.side,volume=excluded.volume,entry=excluded.entry,
                    stop_loss=excluded.stop_loss,take_profit=excluded.take_profit,state=excluded.state,
                    broker_order_ticket=excluded.broker_order_ticket,broker_position_ticket=excluded.broker_position_ticket,
                    filled_volume=excluded.filled_volume,last_reason=excluded.last_reason,updated_at_utc=excluded.updated_at_utc""",
                (
                    intent.intent_id, intent.candidate_id, intent.idempotency_key, intent.symbol, intent.side,
                    str(intent.volume), str(intent.entry), str(intent.stop_loss), str(intent.take_profit), intent.state.value,
                    intent.created_at_utc.astimezone(timezone.utc).isoformat(), intent.broker_order_ticket,
                    intent.broker_position_ticket, str(intent.filled_volume), intent.last_reason, now,
                ),
            )
            previous_state = previous["state"] if previous else None
            if previous_state != intent.state.value:
                con.execute(
                    "INSERT INTO execution_transitions_v1(intent_id,timestamp_utc,from_state,to_state,reason,payload_json) VALUES(?,?,?,?,?,?)",
                    (intent.intent_id, now, previous_state, intent.state.value, intent.last_reason, _json(asdict(intent))),
                )

    def all(self) -> tuple[OrderIntent, ...]:
        with session(self.db_path) as con:
            rows = con.execute("SELECT * FROM order_intents_v1 ORDER BY created_at_utc,intent_id").fetchall()
        return tuple(self._from_row(row) for row in rows)


def persist_candidate(db_path: Path, candidate: CandidateEnvelope) -> None:
    with session(db_path) as con:
        con.execute(
            """INSERT INTO candidate_decisions(
                candidate_id,correlation_id,strategy_id,strategy_version,symbol,side,generated_at_utc,expires_at_utc,
                evidence_hash,market_snapshot_fingerprint,payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO NOTHING""",
            (
                candidate.candidate_id, candidate.correlation_id, candidate.strategy_id, candidate.strategy_version,
                candidate.symbol, candidate.side, candidate.generated_at_utc.isoformat(), candidate.expires_at_utc.isoformat(),
                candidate.evidence_hash, candidate.market_snapshot_fingerprint, _json(asdict(candidate)),
            ),
        )


def persist_safety_snapshot(db_path: Path, snapshot: SafetySnapshot) -> None:
    with session(db_path) as con:
        con.execute(
            "INSERT INTO safety_snapshots_v1(fingerprint,captured_at_utc,reconciled,blocking_reasons_json,payload_json) VALUES(?,?,?,?,?) ON CONFLICT(fingerprint) DO NOTHING",
            (snapshot.fingerprint, snapshot.captured_at_utc.isoformat(), int(snapshot.reconciled), _json(snapshot.blocking_reasons), _json(snapshot.model_dump(mode="json"))),
        )


def persist_risk_result(db_path: Path, result: BrokerRiskResult, *, created_at_utc: datetime) -> None:
    with session(db_path) as con:
        con.execute(
            """INSERT INTO risk_decisions_v1(
                candidate_id,created_at_utc,approved,risk_profile_fingerprint,safety_snapshot_fingerprint,
                expires_at_utc,reason_codes_json,payload_json
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(candidate_id,risk_profile_fingerprint,safety_snapshot_fingerprint) DO UPDATE SET
                created_at_utc=excluded.created_at_utc,approved=excluded.approved,expires_at_utc=excluded.expires_at_utc,
                reason_codes_json=excluded.reason_codes_json,payload_json=excluded.payload_json""",
            (
                result.candidate_id, created_at_utc.astimezone(timezone.utc).isoformat(), int(result.approved),
                result.risk_profile_fingerprint, result.safety_snapshot_fingerprint, result.expires_at_utc.isoformat(),
                _json(result.reason_codes), _json(asdict(result)),
            ),
        )


def persist_advisory(db_path: Path, advisory: Advisory, *, created_at_utc: datetime) -> None:
    with session(db_path) as con:
        con.execute(
            """INSERT INTO advisories_v1(
                candidate_id,evidence_id,created_at_utc,action,risk_multiplier,status,expires_at_utc,
                model_fingerprint,advisory_cost,payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(candidate_id,evidence_id,model_fingerprint) DO UPDATE SET
                created_at_utc=excluded.created_at_utc,action=excluded.action,risk_multiplier=excluded.risk_multiplier,
                status=excluded.status,expires_at_utc=excluded.expires_at_utc,advisory_cost=excluded.advisory_cost,
                payload_json=excluded.payload_json""",
            (
                advisory.candidate_id, advisory.evidence_id, created_at_utc.astimezone(timezone.utc).isoformat(),
                advisory.action.value, advisory.risk_multiplier, advisory.status.value, advisory.expires_at_utc.isoformat(),
                advisory.model_fingerprint, advisory.advisory_cost, _json(asdict(advisory)),
            ),
        )


@dataclass(frozen=True)
class TradingControlState:
    armed: bool = False
    arm_expires_at_utc: datetime | None = None
    kill_switch: bool = True
    maintenance_mode: bool = False
    reason: str = "UNINITIALIZED"

    def allows_new_entries(self, *, now_utc: datetime) -> bool:
        if self.kill_switch or self.maintenance_mode or not self.armed:
            return False
        return self.arm_expires_at_utc is not None and now_utc < self.arm_expires_at_utc


def load_trading_control(db_path: Path) -> TradingControlState:
    with session(db_path) as con:
        row = con.execute("SELECT * FROM trading_control_state WHERE singleton=1").fetchone()
    if row is None:
        return TradingControlState()
    expiry = datetime.fromisoformat(row["arm_expires_at_utc"]) if row["arm_expires_at_utc"] else None
    return TradingControlState(bool(row["armed"]), expiry, bool(row["kill_switch"]), bool(row["maintenance_mode"]), row["reason"])


def save_trading_control(db_path: Path, state: TradingControlState) -> None:
    with session(db_path) as con:
        con.execute(
            """INSERT INTO trading_control_state(singleton,armed,arm_expires_at_utc,kill_switch,maintenance_mode,updated_at_utc,reason)
               VALUES(1,?,?,?,?,?,?)
               ON CONFLICT(singleton) DO UPDATE SET armed=excluded.armed,arm_expires_at_utc=excluded.arm_expires_at_utc,
               kill_switch=excluded.kill_switch,maintenance_mode=excluded.maintenance_mode,updated_at_utc=excluded.updated_at_utc,
               reason=excluded.reason""",
            (
                int(state.armed), state.arm_expires_at_utc.isoformat() if state.arm_expires_at_utc else None,
                int(state.kill_switch), int(state.maintenance_mode), utc_now(), state.reason,
            ),
        )
