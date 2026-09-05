from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from forex_ai.integration.adapters import market_snapshot, tick_snapshot, timeframe_snapshot
from forex_ai.strategy.config import bundled_strategy_snapshot
from forex_ai.strategy.v1.breakout_retest import evaluate as evaluate_breakout_retest
from forex_ai.strategy.v1.contracts import StrategyResult
from forex_ai.strategy.v1.inside_bar_momentum_breakout import evaluate as evaluate_inside_bar
from forex_ai.strategy.v1.trend_pullback import evaluate as evaluate_pullback
from forex_ai.strategy.v1.volatility_breakout import evaluate as evaluate_breakout

UTC = timezone.utc


@dataclass(frozen=True)
class V1ScanResult:
    strategy_id: str
    strategy_version: str
    result: StrategyResult


def build_market_from_mt5_rows(
    *,
    symbol: str,
    tick_raw: Mapping[str, Any],
    bars_by_timeframe: Mapping[str, list[dict[str, Any]]],
    captured_at_utc: datetime,
    commission_cost: float = 0.0,
):
    tick = tick_snapshot(tick_raw, symbol=symbol, captured_at_utc=captured_at_utc)
    snapshots = {
        label: timeframe_snapshot(label, rows)
        for label, rows in bars_by_timeframe.items()
    }
    spread = max(tick.ask - tick.bid, 0.0)
    return market_snapshot(
        symbol=symbol,
        tick=tick,
        captured_at_utc=captured_at_utc,
        timeframes=snapshots,
        spread_cost=spread,
        commission_cost=commission_cost,
        metadata={"source": "live_mt5_v1_scan"},
    )


def evaluate_v1_market(market, *, now_utc: datetime) -> tuple[V1ScanResult, ...]:
    now = now_utc.astimezone(UTC)
    snapshot = bundled_strategy_snapshot()
    rows = (
        ("inside_bar_momentum_breakout_v1", evaluate_inside_bar),
        ("breakout_retest_v1", evaluate_breakout_retest),
        ("trend_pullback_v1", evaluate_pullback),
        ("volatility_breakout_v1", evaluate_breakout),
    )
    out: list[V1ScanResult] = []
    for strategy_id, evaluate in rows:
        if not snapshot.enabled(strategy_id):
            continue
        config = snapshot.config_for(strategy_id)
        result = evaluate(market, config, now)
        out.append(V1ScanResult(config.version.strategy_id, config.version.version, result))
    return tuple(out)


def scan_result_payload(row: V1ScanResult) -> dict[str, Any]:
    return {
        "strategy_id": row.strategy_id,
        "strategy_version": row.strategy_version,
        "candidate": asdict(row.result.candidate) if row.result.candidate is not None else None,
        "reason_codes": list(row.result.no_setup_reason_codes),
        "evidence": {
            "reason_codes": list(row.result.evidence.reason_codes),
            "values": dict(row.result.evidence.values),
        },
    }
