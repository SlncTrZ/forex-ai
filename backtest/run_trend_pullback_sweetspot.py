#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from forex_ai.research.dataset import load_frozen_replay_dataset
from forex_ai.strategy.config import load_strategy_snapshot
from forex_ai.strategy.v1 import trend_pullback
from forex_ai.strategy.v1.contracts import StrategyConfig, fingerprint

UTC = timezone.utc
ACTUAL = {"EURUSD": "EURUSDc", "XAUUSD": "XAUUSDc"}
PRIMARY_ROOT = Path("/home/dinhtc/apps/forex-ai/backtest/data/2026-08-10_2026-09-04")
OOS_ROOT = Path("/home/dinhtc/apps/forex-ai/backtest/data/2026-07-13_2026-08-07")
OUTPUT_ROOT = Path("/home/dinhtc/apps/forex-ai/backtest/trend_pullback/sweetspot_v1")
BUFFERS = (0.10, 0.25, 0.40, 0.60)
TARGETS = (1.25, 1.50, 1.75, 2.00)
EXPIRY_MINUTES = 360
RISK_PCT = 1.0
MAX_GAP_MINUTES = 30
CLUSTER_GAP_MINUTES = 30


@dataclass(frozen=True)
class RawSignal:
    event_index: int
    side: str
    generated_at_utc: datetime
    entry: float
    structure: float
    atr: float


@dataclass
class Trade:
    symbol: str
    side: str
    entry_time_utc: str
    exit_time_utc: str
    entry: float
    stop_loss: float
    take_profit: float
    exit_price: float
    exit_reason: str
    realized_r: float
    mfe_r: float
    mae_r: float
    duration_minutes: float


@dataclass
class ReplaySummary:
    trades: int
    win_rate: float | None
    expectancy_r: float | None
    total_r: float
    profit_factor: float | None
    max_drawdown_r: float
    mean_mfe_r: float | None
    mean_mae_r: float | None
    exits: dict[str, int]
    generated: int
    clusters: int
    blocked_cluster: int
    blocked_active: int
    final_balance: float
    return_pct: float
    max_drawdown_pct: float
    by_side: dict[str, dict[str, float | int | None]]
    by_week: dict[str, dict[str, float | int | None]]


def _config(base: StrategyConfig, **updates: object) -> StrategyConfig:
    params = dict(base.parameters)
    params.update(updates)
    return StrategyConfig(base.version, params, base.instrument_class)


def _freeze_signals(events, base_config: StrategyConfig) -> tuple[list[RawSignal], str]:
    signals: list[RawSignal] = []
    for index, event in enumerate(events):
        result = trend_pullback.evaluate(event.snapshot, base_config, event.clock_utc)
        candidate = result.candidate
        if candidate is None:
            continue
        details = dict(result.evidence.values)
        signals.append(RawSignal(
            event_index=index,
            side=candidate.side,
            generated_at_utc=event.clock_utc,
            entry=candidate.reference_entry,
            structure=float(details["structure"]),
            atr=float(details["atr_m15"]),
        ))
    payload = [
        {
            "event_index": signal.event_index,
            "side": signal.side,
            "generated_at_utc": signal.generated_at_utc.isoformat(),
            "entry": signal.entry,
            "structure": signal.structure,
            "atr": signal.atr,
        }
        for signal in signals
    ]
    return signals, fingerprint(payload)


def _executable_close(event, side: str) -> float:
    return event.snapshot.bid if side == "BUY" else event.snapshot.ask


def _cohort(trades: list[Trade]) -> dict[str, float | int | None]:
    if not trades:
        return {"trades": 0, "win_rate": None, "expectancy_r": None, "total_r": 0.0, "profit_factor": None}
    values = [trade.realized_r for trade in trades]
    gross_profit = sum(v for v in values if v > 0)
    gross_loss = -sum(v for v in values if v < 0)
    return {
        "trades": len(trades),
        "win_rate": sum(v > 0 for v in values) / len(values),
        "expectancy_r": mean(values),
        "total_r": sum(values),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
    }


def _summarize(trades: list[Trade], *, generated: int, clusters: int, blocked_cluster: int, blocked_active: int) -> ReplaySummary:
    values = [trade.realized_r for trade in trades]
    gross_profit = sum(v for v in values if v > 0)
    gross_loss = -sum(v for v in values if v < 0)
    equity_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    balance = 100.0
    peak_balance = balance
    max_dd_pct = 0.0
    for value in values:
        equity_r += value
        peak_r = max(peak_r, equity_r)
        max_dd_r = max(max_dd_r, peak_r - equity_r)
        risk_amount = balance * RISK_PCT / 100.0
        balance += value * risk_amount
        peak_balance = max(peak_balance, balance)
        if peak_balance > 0:
            max_dd_pct = max(max_dd_pct, (peak_balance - balance) / peak_balance * 100.0)
    by_side = {side: _cohort([trade for trade in trades if trade.side == side]) for side in ("BUY", "SELL")}
    week_groups: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        dt = datetime.fromisoformat(trade.entry_time_utc)
        monday = (dt - timedelta(days=dt.weekday())).date().isoformat()
        week_groups[monday].append(trade)
    by_week = {key: _cohort(group) for key, group in sorted(week_groups.items())}
    return ReplaySummary(
        trades=len(trades),
        win_rate=(sum(v > 0 for v in values) / len(values)) if values else None,
        expectancy_r=mean(values) if values else None,
        total_r=sum(values),
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else None,
        max_drawdown_r=max_dd_r,
        mean_mfe_r=mean([trade.mfe_r for trade in trades]) if trades else None,
        mean_mae_r=mean([trade.mae_r for trade in trades]) if trades else None,
        exits=dict(Counter(trade.exit_reason for trade in trades)),
        generated=generated,
        clusters=clusters,
        blocked_cluster=blocked_cluster,
        blocked_active=blocked_active,
        final_balance=balance,
        return_pct=(balance / 100.0 - 1.0) * 100.0,
        max_drawdown_pct=max_dd_pct,
        by_side=by_side,
        by_week=by_week,
    )


def _replay(events, raw_signals: list[RawSignal], *, symbol: str, buffer_atr: float, target_r: float, expiry_minutes: int) -> tuple[ReplaySummary, list[Trade]]:
    by_event = {signal.event_index: signal for signal in raw_signals}
    active: dict[str, Any] | None = None
    previous_signal: RawSignal | None = None
    last_event = None
    generated = len(raw_signals)
    clusters = blocked_cluster = blocked_active = 0
    trades: list[Trade] = []

    def close_trade(event, reason: str, price: float) -> None:
        nonlocal active
        assert active is not None
        signal: RawSignal = active["signal"]
        risk = active["risk"]
        signed = price - signal.entry if signal.side == "BUY" else signal.entry - price
        trades.append(Trade(
            symbol=symbol,
            side=signal.side,
            entry_time_utc=signal.generated_at_utc.isoformat(),
            exit_time_utc=event.clock_utc.isoformat(),
            entry=signal.entry,
            stop_loss=active["stop"],
            take_profit=active["target"],
            exit_price=price,
            exit_reason=reason,
            realized_r=signed / risk,
            mfe_r=active["mfe_r"],
            mae_r=active["mae_r"],
            duration_minutes=(event.clock_utc - signal.generated_at_utc).total_seconds() / 60.0,
        ))
        active = None

    for index, event in enumerate(events):
        if last_event is not None:
            gap = (event.clock_utc - last_event.clock_utc).total_seconds() / 60.0
            if gap > MAX_GAP_MINUTES:
                if active is not None:
                    close_trade(last_event, "MARKET_CLOSE", _executable_close(last_event, active["signal"].side))
                previous_signal = None

        latest = event.snapshot.timeframes["M15"].closed_bars[-1]
        if active is not None:
            signal: RawSignal = active["signal"]
            risk = active["risk"]
            if signal.side == "BUY":
                favorable = (latest.high - signal.entry) / risk
                adverse = (signal.entry - latest.low) / risk
                stop_hit = latest.low <= active["stop"]
                target_hit = latest.high >= active["target"]
            else:
                favorable = (signal.entry - latest.low) / risk
                adverse = (latest.high - signal.entry) / risk
                stop_hit = latest.high >= active["stop"]
                target_hit = latest.low <= active["target"]
            active["mfe_r"] = max(active["mfe_r"], favorable)
            active["mae_r"] = max(active["mae_r"], adverse)
            if stop_hit:
                close_trade(event, "AMBIGUOUS_STOP_FIRST" if target_hit else "STOP", active["stop"])
            elif target_hit:
                close_trade(event, "TARGET", active["target"])
            elif event.clock_utc >= active["expires_at"]:
                close_trade(event, "EXPIRY", _executable_close(event, signal.side))

        signal = by_event.get(index)
        if signal is not None:
            same_cluster = (
                previous_signal is not None
                and signal.side == previous_signal.side
                and signal.generated_at_utc - previous_signal.generated_at_utc <= timedelta(minutes=CLUSTER_GAP_MINUTES)
            )
            previous_signal = signal
            if same_cluster:
                blocked_cluster += 1
            else:
                clusters += 1
                if active is not None:
                    blocked_active += 1
                else:
                    stop = signal.structure - buffer_atr * signal.atr if signal.side == "BUY" else signal.structure + buffer_atr * signal.atr
                    if (signal.side == "BUY" and stop >= signal.entry) or (signal.side == "SELL" and stop <= signal.entry):
                        blocked_active += 1
                    else:
                        risk = abs(signal.entry - stop)
                        target = signal.entry + risk * target_r if signal.side == "BUY" else signal.entry - risk * target_r
                        active = {
                            "signal": signal,
                            "stop": stop,
                            "target": target,
                            "risk": risk,
                            "expires_at": signal.generated_at_utc + timedelta(minutes=expiry_minutes),
                            "mfe_r": 0.0,
                            "mae_r": 0.0,
                        }
        last_event = event

    if active is not None and last_event is not None:
        close_trade(last_event, "DATASET_END", _executable_close(last_event, active["signal"].side))
    return _summarize(trades, generated=generated, clusters=clusters, blocked_cluster=blocked_cluster, blocked_active=blocked_active), trades


def _combined(oos_trades: list[Trade], ins_trades: list[Trade], oos: ReplaySummary, ins: ReplaySummary) -> ReplaySummary:
    return _summarize(
        [*oos_trades, *ins_trades],
        generated=oos.generated + ins.generated,
        clusters=oos.clusters + ins.clusters,
        blocked_cluster=oos.blocked_cluster + ins.blocked_cluster,
        blocked_active=oos.blocked_active + ins.blocked_active,
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _fmt(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def _render_run(report: dict[str, Any]) -> str:
    p = report["parameters"]
    lines = [
        f"# Trend Pullback V1 Sweet-Spot Test — {report['symbol']} — {report['run_id']}\n",
        "## Parameters",
        f"- EMA: `{p['ema_fast']}/{p['ema_slow']}`",
        f"- Pullback ATR: `{p['pullback_atr']}`",
        f"- Volatility/stop buffer ATR: `{p['volatility_buffer_atr']}`",
        f"- Structure lookback: `{p['structure_lookback_bars']}` bars",
        f"- Target: `{p['target_r']}R`",
        f"- Expiry: `{p['expiry_minutes']} min`",
        f"- Account risk simulation: `{RISK_PCT}%/trade`",
        f"- Frozen entry-signal fingerprint OOS: `{report['oos_signal_fingerprint']}`",
        f"- Frozen entry-signal fingerprint IS: `{report['is_signal_fingerprint']}`",
        "- One active trade per symbol; same-side candidate clusters within 30 minutes are deduped.",
        "- Positions are closed at the last executable quote before detected market/session gaps >30 minutes.\n",
        "## Results",
        "| Partition | Trades | Win | Exp R | PF | Total R | DD R | Return @1% | DD % |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label in ("OOS", "IS", "COMBINED"):
        m = report["results"][label]
        lines.append(f"| {label} | {m['trades']} | {_pct(m['win_rate'])} | {_fmt(m['expectancy_r'])} | {_fmt(m['profit_factor'],3)} | {_fmt(m['total_r'],2)} | {_fmt(m['max_drawdown_r'],2)} | {_fmt(m['return_pct'],2)}% | {_fmt(m['max_drawdown_pct'],2)}% |")
    c = report["results"]["COMBINED"]
    exits = c["exits"]
    stop_count = exits.get("STOP", 0) + exits.get("AMBIGUOUS_STOP_FIRST", 0)
    target_count = exits.get("TARGET", 0)
    lines += [
        "\n## Diagnostics",
        f"- Stop-hit rate: `{(stop_count / c['trades'] * 100 if c['trades'] else 0):.2f}%` ({stop_count})",
        f"- Target-hit rate: `{(target_count / c['trades'] * 100 if c['trades'] else 0):.2f}%` ({target_count})",
        f"- Exit reasons: `{exits}`",
        f"- Mean MFE: `{_fmt(c['mean_mfe_r'])}R`",
        f"- Mean MAE: `{_fmt(c['mean_mae_r'])}R`",
        f"- Generated / clusters / cluster-blocked / active-blocked: `{c['generated']} / {c['clusters']} / {c['blocked_cluster']} / {c['blocked_active']}`",
        "\n### By side",
        "| Side | Trades | Win | Exp R | PF |",
        "|---|---:|---:|---:|---:|",
    ]
    for side, m in c["by_side"].items():
        lines.append(f"| {side} | {m['trades']} | {_pct(m['win_rate'])} | {_fmt(m['expectancy_r'])} | {_fmt(m['profit_factor'],3)} |")
    lines += ["\n### Weekly dispersion", "| Week start | Trades | Win | Exp R | PF |", "|---|---:|---:|---:|---:|"]
    for week, m in c["by_week"].items():
        lines.append(f"| {week} | {m['trades']} | {_pct(m['win_rate'])} | {_fmt(m['expectancy_r'])} | {_fmt(m['profit_factor'],3)} |")
    lines += [
        "\n## Research interpretation",
        "- Entry signals are frozen across the entire 4×4 stop/RR grid; only exit geometry changes.",
        "- This is tuning/diagnostic evidence, not a live-promotion decision.",
        "- Prefer configurations with acceptable expectancy in both OOS and IS; combined profitability alone is insufficient.",
        "- Risk 1% affects normalized equity simulation only; it does not change stop distance or signal generation.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Trend Pullback V1 stop/RR/expiry sweet-spot research.")
    parser.add_argument("--output-root", default=str(OUTPUT_ROOT))
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "EURUSD"], choices=tuple(ACTUAL))
    parser.add_argument("--buffers", nargs="+", type=float, default=list(BUFFERS))
    parser.add_argument("--targets", nargs="+", type=float, default=list(TARGETS))
    parser.add_argument("--expiries", nargs="+", type=int, default=[EXPIRY_MINUTES])
    parser.add_argument("--pullback-atrs", nargs="+", type=float, default=[float(load_strategy_snapshot().config_for("trend_pullback_v1").parameters["pullback_atr"])])
    parser.add_argument("--name", default="stop_rr_stage1")
    args = parser.parse_args()
    if any(value < 0 for value in args.buffers):
        raise ValueError("buffers must be >= 0")
    if any(value <= 0 for value in args.targets):
        raise ValueError("targets must be > 0")
    if any(value <= 0 for value in args.expiries):
        raise ValueError("expiries must be > 0")
    if any(value < 0 for value in args.pullback_atrs):
        raise ValueError("pullback ATR values must be >= 0")

    snapshot = load_strategy_snapshot()
    production_base = snapshot.config_for("trend_pullback_v1")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in args.name)
    root = Path(args.output_root) / f"{timestamp}_{safe_name}"
    root.mkdir(parents=True, exist_ok=False)
    summary_rows: list[dict[str, Any]] = []
    index: dict[str, Any] = {
        "schema": "forex-ai-trend-pullback-sweetspot-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "primary_dataset": str(PRIMARY_ROOT),
        "oos_dataset": str(OOS_ROOT),
        "risk_per_trade_pct": RISK_PCT,
        "buffers": list(args.buffers),
        "targets": list(args.targets),
        "expiry_minutes": list(args.expiries),
        "pullback_atrs": list(args.pullback_atrs),
        "symbols": args.symbols,
        "runs": [],
    }

    for symbol in args.symbols:
        actual = ACTUAL[symbol]
        oos_ds = load_frozen_replay_dataset(OOS_ROOT / actual / "replay.jsonl")
        ins_ds = load_frozen_replay_dataset(PRIMARY_ROOT / actual / "replay.jsonl")
        for pullback_atr in args.pullback_atrs:
            signal_config = _config(production_base, pullback_atr=pullback_atr, expiry_minutes=max(args.expiries))
            print(json.dumps({"event": "freeze_signals", "symbol": symbol, "partition": "OOS", "pullback_atr": pullback_atr}), flush=True)
            oos_signals, oos_signal_fp = _freeze_signals(oos_ds.events, signal_config)
            print(json.dumps({"event": "freeze_signals", "symbol": symbol, "partition": "IS", "pullback_atr": pullback_atr}), flush=True)
            ins_signals, ins_signal_fp = _freeze_signals(ins_ds.events, signal_config)
            print(json.dumps({"event": "signals_frozen", "symbol": symbol, "pullback_atr": pullback_atr, "oos": len(oos_signals), "is": len(ins_signals), "oos_fp": oos_signal_fp, "is_fp": ins_signal_fp}), flush=True)

            for buffer in args.buffers:
                for target in args.targets:
                    for expiry_minutes in args.expiries:
                        cfg = _config(production_base, pullback_atr=pullback_atr, volatility_buffer_atr=buffer, target_r=target, expiry_minutes=expiry_minutes)
                        run_id = f"{symbol.lower()}_pb{int(round(pullback_atr*100)):03d}_buf{int(round(buffer*100)):03d}_rr{int(round(target*100)):03d}_exp{expiry_minutes:03d}"
                        oos, oos_trades = _replay(oos_ds.events, oos_signals, symbol=symbol, buffer_atr=buffer, target_r=target, expiry_minutes=expiry_minutes)
                        ins, ins_trades = _replay(ins_ds.events, ins_signals, symbol=symbol, buffer_atr=buffer, target_r=target, expiry_minutes=expiry_minutes)
                        combined = _combined(oos_trades, ins_trades, oos, ins)
                        report = {
                            "schema": "forex-ai-trend-pullback-sweetspot-run-v1",
                            "run_id": run_id,
                            "symbol": symbol,
                            "actual_symbol": actual,
                            "strategy_id": "trend_pullback_v1",
                            "strategy_version": cfg.version.version,
                            "strategy_config_fingerprint": cfg.fingerprint,
                            "oos_dataset_fingerprint": oos_ds.manifest.fingerprint,
                            "is_dataset_fingerprint": ins_ds.manifest.fingerprint,
                            "oos_signal_fingerprint": oos_signal_fp,
                            "is_signal_fingerprint": ins_signal_fp,
                            "parameters": dict(cfg.parameters),
                            "results": {"OOS": asdict(oos), "IS": asdict(ins), "COMBINED": asdict(combined)},
                        }
                        run_dir = root / run_id
                        run_dir.mkdir()
                        (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                        (run_dir / "run_report.md").write_text(_render_run(report), encoding="utf-8")
                        _write_csv(run_dir / "trades_oos.csv", [asdict(t) for t in oos_trades])
                        _write_csv(run_dir / "trades_is.csv", [asdict(t) for t in ins_trades])
                        row = {
                            "run_id": run_id,
                            "symbol": symbol,
                            "config_fingerprint": cfg.fingerprint,
                            "pullback_atr": pullback_atr,
                            "volatility_buffer_atr": buffer,
                            "target_r": target,
                            "expiry_minutes": expiry_minutes,
                            "oos_trades": oos.trades,
                            "oos_win_rate": oos.win_rate,
                            "oos_expectancy_r": oos.expectancy_r,
                            "is_trades": ins.trades,
                            "is_win_rate": ins.win_rate,
                            "is_expectancy_r": ins.expectancy_r,
                            "combined_trades": combined.trades,
                            "combined_win_rate": combined.win_rate,
                            "combined_expectancy_r": combined.expectancy_r,
                            "combined_profit_factor": combined.profit_factor,
                            "combined_max_drawdown_r": combined.max_drawdown_r,
                            "combined_return_pct_at_1pct_risk": combined.return_pct,
                            "combined_max_drawdown_pct_at_1pct_risk": combined.max_drawdown_pct,
                            "positive_both_partitions": bool((oos.expectancy_r or 0) > 0 and (ins.expectancy_r or 0) > 0),
                        }
                        summary_rows.append(row)
                        index["runs"].append({"run_id": run_id, "report": str(run_dir / "report.json"), **row})
                        print(json.dumps({"event": "run_complete", "run_id": run_id, "oos_exp": oos.expectancy_r, "is_exp": ins.expectancy_r, "combined_exp": combined.expectancy_r}), flush=True)

    summary_rows.sort(key=lambda r: (r["symbol"] != "XAUUSD", not r["positive_both_partitions"], -(r["combined_expectancy_r"] or -999)))
    _write_csv(root / "summary.csv", summary_rows)
    (root / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Trend Pullback V1 — Stage 1 Stop/RR Sweet-Spot Summary\n",
        f"Generated: `{index['generated_at_utc']}`",
        f"Risk simulation: `{RISK_PCT}%/trade`; pullback ATRs: `{list(args.pullback_atrs)}`; expiries: `{list(args.expiries)}`; market/session gap close enabled.",
        "Entry signals are frozen within each symbol/partition across the full requested grid.",
        "XAUUSD is the primary research target; EURUSD is a robustness/control symbol.\n",
        "| Symbol | Pullback ATR | Buffer ATR | RR | Expiry | OOS n | OOS Exp | IS n | IS Exp | Combined n | Win | Combined Exp | PF | DD R | Return @1% | Both + |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary_rows:
        lines.append(f"| {row['symbol']} | {row['pullback_atr']:.2f} | {row['volatility_buffer_atr']:.2f} | {row['target_r']:.2f} | {row['expiry_minutes']} | {row['oos_trades']} | {_fmt(row['oos_expectancy_r'])} | {row['is_trades']} | {_fmt(row['is_expectancy_r'])} | {row['combined_trades']} | {_pct(row['combined_win_rate'])} | {_fmt(row['combined_expectancy_r'])} | {_fmt(row['combined_profit_factor'],3)} | {_fmt(row['combined_max_drawdown_r'],2)} | {_fmt(row['combined_return_pct_at_1pct_risk'],2)}% | {'YES' if row['positive_both_partitions'] else 'NO'} |")
    lines += [
        "\n## Selection rule",
        "Primary ranking requires OOS and IS expectancy both > 0. Combined expectancy, PF, drawdown and weekly dispersion break ties; win rate alone does not select the configuration.",
        "No configuration from this report is automatically applied to production/live config.\n",
    ]
    (root / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"status": "complete", "output": str(root), "runs": len(summary_rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
