from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _default_config_path(filename: str) -> Path:
    config_dir = os.getenv("FOREX_AI_CONFIG_DIR")
    if config_dir:
        return Path(config_dir).expanduser() / filename
    project_root = os.getenv("FOREX_AI_PROJECT_ROOT")
    candidates: list[Path] = []
    if project_root:
        candidates.append(Path(project_root).expanduser() / "config" / filename)
    candidates.extend([
        PROJECT_ROOT / "config" / filename,
        Path.cwd() / "config" / filename,
        Path.home() / "apps" / "forex-ai" / "current" / "config" / filename,
    ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str
    symbols: tuple[str, ...]
    db_path: Path
    log_dir: Path
    poll_seconds: int
    mt5_host: str
    mt5_port: int
    mt5_ui_host: str
    mt5_ui_port: int
    mt5_engine: str


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def _resolve_path(value: str) -> Path:
    """Expand `~` and environment variables, then return an absolute Path.

    Keeps runtime paths portable across hosts instead of baking in a specific
    user home directory.
    """
    return Path(os.path.expandvars(os.path.expanduser(str(value))))


def load_runtime_config(path: Path | None = None) -> RuntimeConfig:
    raw = _load_yaml(path or _default_config_path("app.yaml"))
    runtime = raw.get("runtime", {})
    mt5 = raw.get("mt5", {})
    mode = os.getenv("FOREX_AI_MODE", raw.get("mode", "OBSERVE")).upper()
    if mode not in {
        "OBSERVE", "SHADOW", "DEMO", "LIVE_CANARY", "GUARDED_LIVE", "LIVE_EXPERIMENT",
        "CENT_GUARDED", "CENT_EXPERIMENT",
    }:
        raise ValueError(f"Unsupported FOREX_AI_MODE={mode}")

    db_path = _resolve_path(os.getenv("FOREX_AI_DB_PATH", runtime.get("db_path", "~/.local/share/forex-ai/forex.db")))
    log_dir = _resolve_path(os.getenv("FOREX_AI_LOG_DIR", runtime.get("log_dir", "~/.local/state/forex-ai/logs")))
    symbols_env = os.getenv("FOREX_AI_SYMBOLS")
    symbols = tuple(item.strip() for item in symbols_env.split(",") if item.strip()) if symbols_env else tuple(raw.get("symbols", ["XAUUSD", "EURUSD", "GBPUSD"]))
    if not symbols:
        raise ValueError("At least one trading symbol is required")
    return RuntimeConfig(
        mode=mode,
        symbols=symbols,
        db_path=db_path,
        log_dir=log_dir,
        poll_seconds=int(os.getenv("FOREX_AI_POLL_SECONDS", str(runtime.get("poll_seconds", 5)))),
        mt5_host=os.getenv("MT5_HOST", str(mt5.get("host", "127.0.0.1"))),
        mt5_port=int(os.getenv("MT5_PORT", str(mt5.get("port", 18812)))),
        mt5_ui_host=os.getenv("MT5_UI_HOST", str(mt5.get("ui_host", "127.0.0.1"))),
        mt5_ui_port=int(os.getenv("MT5_UI_PORT", str(mt5.get("ui_port", 8080)))),
        mt5_engine=os.getenv("MT5_ENGINE", str(mt5.get("engine", "docker"))),
    )


def load_risk_config(path: Path | None = None) -> dict[str, Any]:
    return _load_yaml(path or _default_config_path("risk.yaml"))


def load_risk_profile(path: Path | None = None):
    """Load the explicit V1 RiskProfile; no live-capable defaults are invented."""
    from forex_ai.risk.profile import RiskProfile

    raw = _load_yaml(path or _default_config_path("risk.yaml"))
    profile = raw.get("profile")
    if not isinstance(profile, dict):
        raise ValueError("risk profile is missing; execution remains disarmed")
    return RiskProfile.model_validate(profile)



def load_execution_enabled(path: Path | None = None) -> bool:
    raw_env = os.getenv("FOREX_AI_EXECUTION_ENABLED")
    if raw_env is not None:
        return raw_env.strip().lower() in {"1", "true", "yes", "on"}
    raw = load_risk_config(path)
    return bool(raw.get("execution_enabled", False))


def load_llm_config(path: Path | None = None) -> dict[str, Any]:
    return _load_yaml(path or _default_config_path("llm.yaml"))
