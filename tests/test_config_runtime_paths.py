from __future__ import annotations

from pathlib import Path

from forex_ai import config


def test_config_dir_env_overrides_installed_package_location(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text(
        "mode: OBSERVE\nsymbols: [EURUSD]\nruntime:\n  db_path: /tmp/forex.db\n  log_dir: /tmp/forex-logs\nmt5:\n  host: 127.0.0.1\n  port: 18812\n  ui_host: 127.0.0.1\n  ui_port: 8080\n  engine: docker\n",
        encoding="utf-8",
    )
    (config_dir / "risk.yaml").write_text(
        "profile:\n  max_risk_per_trade_pct: 1\n  max_total_open_risk_pct: 3\n  daily_loss_limit_pct: 3\n  weekly_loss_limit_pct: 5\n  max_active_orders: 3\n",
        encoding="utf-8",
    )
    (config_dir / "llm.yaml").write_text("provider: test\nmodel: test-model\n", encoding="utf-8")
    monkeypatch.setenv("FOREX_AI_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(config, "PROJECT_ROOT", Path("/definitely/not/the/runtime/release"))

    runtime = config.load_runtime_config()
    assert runtime.mode == "OBSERVE"
    assert runtime.symbols == ("EURUSD",)
    assert config.load_risk_config()["profile"]["max_active_orders"] == 3
    assert config.load_llm_config()["model"] == "test-model"
