from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from forex_ai.journal.db import session, utc_now
from forex_ai.advisory.provider import BudgetState

UTC = timezone.utc


@dataclass(frozen=True)
class SQLiteDailyBudgetStore:
    db_path: Path
    provider_id: str
    model_id: str
    config_fingerprint: str

    def _key(self, now_utc: datetime) -> str:
        return now_utc.astimezone(UTC).date().isoformat()

    def load(self, *, now_utc: datetime) -> BudgetState:
        with session(self.db_path) as con:
            row = con.execute(
                """SELECT calls,tokens,cost FROM advisory_budget_v1
                   WHERE budget_date_utc=? AND provider_id=? AND model_id=? AND config_fingerprint=?""",
                (self._key(now_utc), self.provider_id, self.model_id, self.config_fingerprint),
            ).fetchone()
        if row is None:
            return BudgetState()
        return BudgetState(calls=int(row[0]), tokens=int(row[1]), cost=float(row[2]))

    def save(self, state: BudgetState, *, now_utc: datetime) -> None:
        with session(self.db_path) as con:
            con.execute(
                """INSERT INTO advisory_budget_v1(
                       budget_date_utc,provider_id,model_id,config_fingerprint,calls,tokens,cost,updated_at_utc
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(budget_date_utc,provider_id,model_id,config_fingerprint) DO UPDATE SET
                       calls=excluded.calls,tokens=excluded.tokens,cost=excluded.cost,updated_at_utc=excluded.updated_at_utc""",
                (
                    self._key(now_utc), self.provider_id, self.model_id, self.config_fingerprint,
                    state.calls, state.tokens, state.cost, utc_now(),
                ),
            )
