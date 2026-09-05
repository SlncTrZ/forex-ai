from forex_ai.research.scalping_experiment_report import flatten_strategy_summaries, render_run_report


def _report(oos_exp=0.1, is_exp=0.05, combined_exp=0.08, pf=1.2):
    summary = {
        "trades": 10,
        "win_rate": 0.5,
        "expectancy_r": combined_exp,
        "total_r": 0.8,
        "profit_factor": pf,
        "max_drawdown_r": 2.0,
        "mean_mfe_r": 1.0,
        "mean_mae_r": 0.5,
        "signals_generated": 12,
        "signals_accepted": 10,
        "signals_blocked_active_position": 2,
        "exit_reasons": {"STOP": 4, "TARGET": 5, "MARKET_CLOSE": 1},
        "by_regime": {
            "UP": {"trades": 5, "win_rate": 0.6, "expectancy_r": 0.1, "profit_factor": 1.3},
            "DOWN": {"trades": 5, "win_rate": 0.4, "expectancy_r": 0.02, "profit_factor": 1.05},
        },
    }
    return {
        "experiment_name": "example",
        "run_id": "run_001",
        "generated_at_utc": "2026-09-05T00:00:00+00:00",
        "dataset_source_fingerprint": "data-fp",
        "dataset_builder_version": "builder-v1",
        "strategy_config_fingerprint": "config-fp",
        "trades_total": 10,
        "fixed_overrides": {"strategies.test.parameters.target_r": 1.5},
        "matrix_values": {},
        "portfolio": {
            "risk_per_trade_pct": 2,
            "max_active_total": 3,
            "initial_balance": 100,
            "daily_loss_limit_enabled": False,
            "weekly_loss_limit_enabled": False,
            "close_before_market_gap": True,
        },
        "accounts": {
            "XAUUSD": {
                "initial_balance": 100,
                "final_balance": 102,
                "return_pct": 2,
                "max_drawdown_pct_realized": 5,
                "max_active_seen": 2,
                "max_nominal_open_risk_pct": 4,
                "blocked_portfolio_limit": 1,
            }
        },
        "symbols": {
            "XAUUSD": {
                "test_strategy": {
                    "version": "1.0.0",
                    "combined": summary,
                    "partitions": {
                        "OOS": {**summary, "expectancy_r": oos_exp, "win_rate": 0.6},
                        "IS": {**summary, "expectancy_r": is_exp, "win_rate": 0.4},
                    },
                }
            }
        },
    }


def test_flatten_marks_positive_both_partitions():
    row = flatten_strategy_summaries(_report())[0]
    assert row["status"] == "POSITIVE_BOTH_PARTITIONS"
    assert row["stop_rate"] == 0.4
    assert row["target_rate"] == 0.5


def test_flatten_marks_partition_mixed_when_combined_positive():
    row = flatten_strategy_summaries(_report(oos_exp=0.1, is_exp=-0.05, combined_exp=0.02))[0]
    assert row["status"] == "POSITIVE_COMBINED_PARTITION_MIXED"


def test_render_run_report_contains_reproducibility_and_guardrails():
    text = render_run_report(_report(), resolved_config_path="/tmp/config.yaml")
    assert "Dataset fingerprint: `data-fp`" in text
    assert "Risk per trade: `2%`" in text
    assert "Stop-hit rate: `40.00%`" in text
    assert "POSITIVE_BOTH_PARTITIONS" in text
    assert "not a live-promotion decision" in text
