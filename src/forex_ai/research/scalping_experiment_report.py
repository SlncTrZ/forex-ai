from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.{digits}f}%"


def _status(summary: dict[str, Any]) -> str:
    combined = summary.get("expectancy_r")
    oos = summary.get("oos_expectancy_r")
    ins = summary.get("is_expectancy_r")
    pf = summary.get("profit_factor")
    if combined is not None and combined > 0 and oos is not None and oos > 0 and ins is not None and ins > 0:
        if pf is not None and pf > 1:
            return "POSITIVE_BOTH_PARTITIONS"
        return "POSITIVE_BOTH_PARTITIONS_LOW_MARGIN"
    if combined is not None and combined > 0:
        return "POSITIVE_COMBINED_PARTITION_MIXED"
    if oos is not None and ins is not None and (oos > 0) != (ins > 0):
        return "PARTITION_FLIP"
    return "NEGATIVE_SAMPLE"


def _exit_rates(combined: dict[str, Any]) -> tuple[float | None, float | None, int, int, int]:
    exits = combined.get("exit_reasons") or {}
    trades = int(combined.get("trades") or 0)
    stop_count = int(exits.get("STOP", 0)) + int(exits.get("AMBIGUOUS_STOP_FIRST", 0))
    target_count = int(exits.get("TARGET", 0)) + int(exits.get("AMBIGUOUS_TARGET_FIRST", 0))
    market_close_count = int(exits.get("MARKET_CLOSE", 0)) + int(exits.get("MARKET_GAP", 0))
    stop_rate = stop_count / trades if trades else None
    target_rate = target_count / trades if trades else None
    return stop_rate, target_rate, stop_count, target_count, market_close_count


def flatten_strategy_summaries(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, strategies in (report.get("symbols") or {}).items():
        for strategy_id, value in strategies.items():
            combined = value["combined"]
            oos = value["partitions"]["OOS"]
            ins = value["partitions"]["IS"]
            stop_rate, target_rate, stop_count, target_count, market_close_count = _exit_rates(combined)
            row = {
                "symbol": symbol,
                "strategy_id": strategy_id,
                "version": value.get("version"),
                "strategy_config_fingerprint": value.get("config_fingerprint"),
                "trades": combined.get("trades"),
                "win_rate": combined.get("win_rate"),
                "expectancy_r": combined.get("expectancy_r"),
                "total_r": combined.get("total_r"),
                "profit_factor": combined.get("profit_factor"),
                "max_drawdown_r": combined.get("max_drawdown_r"),
                "mean_mfe_r": combined.get("mean_mfe_r"),
                "mean_mae_r": combined.get("mean_mae_r"),
                "oos_expectancy_r": oos.get("expectancy_r"),
                "is_expectancy_r": ins.get("expectancy_r"),
                "oos_win_rate": oos.get("win_rate"),
                "is_win_rate": ins.get("win_rate"),
                "stop_rate": stop_rate,
                "target_rate": target_rate,
                "stop_count": stop_count,
                "target_count": target_count,
                "market_close_count": market_close_count,
                "signals_generated": combined.get("signals_generated"),
                "signals_accepted": combined.get("signals_accepted"),
                "signals_blocked_active_position": combined.get("signals_blocked_active_position"),
                "status": "",
            }
            row["status"] = _status(row)
            rows.append(row)
    return rows


def render_run_report(
    report: dict[str, Any],
    *,
    resolved_config_path: str | Path,
) -> str:
    lines: list[str] = []
    lines.append(f"# Scalping Experiment Run — {report.get('experiment_name', 'experiment')} / {report.get('run_id', 'run')}\n")
    lines.append("## Reproducibility\n")
    lines.append(f"- Generated: `{report.get('generated_at_utc', 'n/a')}`")
    lines.append(f"- Dataset fingerprint: `{report.get('dataset_source_fingerprint', 'n/a')}`")
    lines.append(f"- Dataset builder: `{report.get('dataset_builder_version', 'n/a')}`")
    lines.append(f"- Strategy config fingerprint: `{report.get('strategy_config_fingerprint', 'n/a')}`")
    lines.append(f"- Resolved config: `{resolved_config_path}`")
    lines.append(f"- Trades total: `{report.get('trades_total', 0)}`\n")

    fixed = report.get("fixed_overrides") or {}
    matrix_values = report.get("matrix_values") or {}
    lines.append("## Test Variables\n")
    if fixed:
        lines.append("### Fixed overrides")
        for key, value in sorted(fixed.items()):
            lines.append(f"- `{key}` = `{value}`")
    else:
        lines.append("- Fixed overrides: none")
    if matrix_values:
        lines.append("\n### Matrix values for this run")
        for key, value in sorted(matrix_values.items()):
            lines.append(f"- `{key}` = `{value}`")
    else:
        lines.append("- Matrix values: none")

    portfolio = report.get("portfolio")
    lines.append("\n## Portfolio Assumptions\n")
    if portfolio:
        lines.append(f"- Risk per trade: `{_fmt(portfolio.get('risk_per_trade_pct'), 2)}%`")
        lines.append(f"- Max active total: `{portfolio.get('max_active_total')}`")
        lines.append(f"- Initial balance: `{_fmt(portfolio.get('initial_balance'), 2)}`")
        lines.append(f"- Daily loss limit enabled: `{portfolio.get('daily_loss_limit_enabled')}`")
        lines.append(f"- Weekly loss limit enabled: `{portfolio.get('weekly_loss_limit_enabled')}`")
        lines.append(f"- Close before market/session gap: `{portfolio.get('close_before_market_gap')}`")
    else:
        lines.append("- Common strategy harness (no portfolio equity sizing).")

    rows = flatten_strategy_summaries(report)
    lines.append("\n## Strategy Results\n")
    lines.append("| Symbol | Strategy | Trades | Win | Exp R | PF | DD R | OOS Exp | IS Exp | Stop | Target | Status |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in rows:
        lines.append(
            "| {symbol} | {strategy} | {trades} | {win} | {exp} | {pf} | {dd} | {oos} | {ins} | {stop} | {target} | {status} |".format(
                symbol=row["symbol"],
                strategy=row["strategy_id"],
                trades=row["trades"],
                win=_pct(row["win_rate"]),
                exp=_fmt(row["expectancy_r"]),
                pf=_fmt(row["profit_factor"], 3),
                dd=_fmt(row["max_drawdown_r"], 2),
                oos=_fmt(row["oos_expectancy_r"]),
                ins=_fmt(row["is_expectancy_r"]),
                stop=_pct(row["stop_rate"]),
                target=_pct(row["target_rate"]),
                status=row["status"],
            )
        )

    for row in rows:
        symbol = row["symbol"]
        strategy_id = row["strategy_id"]
        value = report["symbols"][symbol][strategy_id]
        combined = value["combined"]
        lines.append(f"\n### {symbol} — `{strategy_id}`\n")
        lines.append(f"- Version: `{row['version']}`")
        lines.append(f"- Trades: `{row['trades']}`")
        lines.append(f"- Win rate: `{_pct(row['win_rate'])}`")
        lines.append(f"- Expectancy: `{_fmt(row['expectancy_r'])}R/trade`")
        lines.append(f"- Profit factor: `{_fmt(row['profit_factor'], 3)}`")
        lines.append(f"- Max drawdown: `{_fmt(row['max_drawdown_r'], 2)}R`")
        lines.append(f"- OOS expectancy: `{_fmt(row['oos_expectancy_r'])}R`")
        lines.append(f"- IS expectancy: `{_fmt(row['is_expectancy_r'])}R`")
        lines.append(f"- OOS win rate: `{_pct(row['oos_win_rate'])}`")
        lines.append(f"- IS win rate: `{_pct(row['is_win_rate'])}`")
        lines.append(f"- Stop-hit rate: `{_pct(row['stop_rate'])}` (`{row['stop_count']}` exits)")
        lines.append(f"- Target-hit rate: `{_pct(row['target_rate'])}` (`{row['target_count']}` exits)")
        lines.append(f"- Market/session-close exits: `{row['market_close_count']}`")
        lines.append(f"- Mean MFE: `{_fmt(row['mean_mfe_r'])}R`")
        lines.append(f"- Mean MAE: `{_fmt(row['mean_mae_r'])}R`")
        lines.append(f"- Signals generated / accepted / blocked: `{row['signals_generated']} / {row['signals_accepted']} / {row['signals_blocked_active_position']}`")
        lines.append(f"- Research status: **{row['status']}**")
        lines.append(f"- Exit reasons: `{combined.get('exit_reasons') or {}}`")

        regimes = combined.get("by_regime") or {}
        if regimes:
            lines.append("\nRegime breakdown:")
            lines.append("| Regime | Trades | Win | Exp R | PF |")
            lines.append("|---|---:|---:|---:|---:|")
            for regime, metrics in regimes.items():
                if not metrics.get("trades"):
                    continue
                lines.append(
                    f"| {regime} | {metrics.get('trades')} | {_pct(metrics.get('win_rate'))} | {_fmt(metrics.get('expectancy_r'))} | {_fmt(metrics.get('profit_factor'), 3)} |"
                )

    accounts = report.get("accounts") or {}
    if accounts:
        lines.append("\n## Account Simulation\n")
        lines.append("| Symbol | Start | Final | Return | Max DD | Max active | Nominal open risk | Blocked by cap |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for symbol, account in accounts.items():
            lines.append(
                f"| {symbol} | {_fmt(account.get('initial_balance'),2)} | {_fmt(account.get('final_balance'),2)} | {_fmt(account.get('return_pct'),2)}% | {_fmt(account.get('max_drawdown_pct_realized'),2)}% | {account.get('max_active_seen')} | {_fmt(account.get('max_nominal_open_risk_pct'),2)}% | {account.get('blocked_portfolio_limit')} |"
            )

    lines.append("\n## Interpretation Guardrails\n")
    lines.append("- This status is descriptive research evidence, not a live-promotion decision.")
    lines.append("- A positive combined result with OOS/IS disagreement is treated as partition-mixed, not robust.")
    lines.append("- The current eight-week dataset is tuning/diagnostic data once a parameter choice is selected; selected parameters still need prospective validation.")
    lines.append("- Risk percentage changes equity sizing, not stop-price geometry. Stop-buffer and structural-stop changes alter the price distance to invalidation.\n")
    return "\n".join(lines)


def render_experiment_summary(
    *,
    name: str,
    dataset_fingerprint: str,
    run_entries: Iterable[dict[str, Any]],
) -> str:
    rows: list[dict[str, Any]] = []
    for entry in run_entries:
        for summary in entry.get("summary") or []:
            rows.append({"run_id": entry.get("run_id"), "matrix_values": entry.get("matrix_values") or {}, **summary})
    lines = [f"# Experiment Summary — {name}\n", f"Dataset fingerprint: `{dataset_fingerprint}`\n"]
    lines.append("| Run | Strategy | Trades | Win | Exp R | PF | DD R | OOS | IS | Matrix | Status |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for row in rows:
        status = _status(row)
        matrix = ", ".join(f"{key}={value}" for key, value in sorted(row["matrix_values"].items())) or "baseline/fixed"
        lines.append(
            f"| {row['run_id']} | {row['strategy_id']} | {row['trades']} | {_pct(row['win_rate'])} | {_fmt(row['expectancy_r'])} | {_fmt(row['profit_factor'],3)} | {_fmt(row['max_drawdown_r'],2)} | {_fmt(row['oos_expectancy_r'])} | {_fmt(row['is_expectancy_r'])} | {matrix} | {status} |"
        )
    lines.append("\nSelection rule: prefer configurations that remain acceptable across OOS and IS; do not rank solely by combined expectancy or win rate.\n")
    return "\n".join(lines)
