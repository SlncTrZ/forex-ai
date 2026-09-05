from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from forex_ai.config import RuntimeConfig
from forex_ai.learning.lesson_selector import select_lessons
from forex_ai.market.features import summarize_features
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.symbols import resolve_symbol


ACCOUNT_FIELDS = [
    "currency", "balance", "equity", "profit", "margin", "margin_free",
    "margin_level", "leverage", "trade_allowed", "trade_expert", "server",
]
POSITION_FIELDS = [
    "symbol", "type", "volume", "price_open", "sl", "tp", "price_current",
    "swap", "profit", "comment",
]
MACRO_REQUIREMENTS = {
    "XAUUSD": [
        "USD direction / DXY context",
        "US nominal and real yields",
        "Federal Reserve policy expectations",
        "US inflation/labor releases",
        "risk-off/geopolitical shocks",
    ],
    "EURUSD": [
        "Federal Reserve vs ECB policy expectations",
        "US vs Euro-area rates/yields",
        "US and Euro-area inflation/labor/growth releases",
        "USD broad direction / DXY context",
    ],
}

SYMBOL_FIELDS = [
    "digits", "point", "trade_contract_size", "trade_tick_size", "trade_tick_value",
    "volume_min", "volume_max", "volume_step", "trade_stops_level", "trade_freeze_level",
    "currency_base", "currency_profit", "currency_margin",
]


def _pick(source: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: source.get(field) for field in fields}


def _account_context(account: dict[str, Any]) -> dict[str, Any]:
    out = _pick(account, ACCOUNT_FIELDS)
    if out.get("currency") == "USC":
        for key in ("balance", "equity", "profit", "margin", "margin_free"):
            value = out.get(key)
            if isinstance(value, (int, float)):
                out[f"{key}_usd_equivalent"] = value / 100.0
    return out


def build_symbol_context(
    client: MT5Client,
    cfg: RuntimeConfig,
    base_symbol: str,
    *,
    lesson_limit: int = 5,
) -> dict[str, Any]:
    available = client.symbols()
    actual = resolve_symbol(base_symbol, available)
    if actual is None:
        raise ValueError(f"Unable to resolve broker symbol for {base_symbol}")

    account = client.account_info() or {}
    all_positions_raw = client.positions()
    all_positions = [_pick(position, POSITION_FIELDS) for position in all_positions_raw]
    positions = [
        _pick(position, POSITION_FIELDS)
        for position in all_positions_raw
        if position.get("symbol") in {actual, base_symbol}
    ]
    info = client.symbol_info(actual) or {}
    tick = client.tick(actual) or {}
    constants = client.constants()

    timeframes: dict[str, Any] = {}
    for label in ("M5", "M15", "H1", "H4"):
        bars = client.bars(actual, constants[label], 240)
        closed_bars = bars[:-1] if len(bars) > 1 else bars
        current_bar = bars[-1] if bars else None
        timeframes[label] = {
            "closed_features": summarize_features(closed_bars),
            "current_candle": current_bar,
            "closed_bar_count": len(closed_bars),
        }

    bid = tick.get("bid")
    ask = tick.get("ask")
    point = info.get("point")
    spread_price = None
    spread_points = None
    if isinstance(bid, (int, float)) and isinstance(ask, (int, float)):
        spread_price = ask - bid
        if isinstance(point, (int, float)) and point:
            spread_points = spread_price / point

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    utc_hour = now_utc.hour + now_utc.minute / 60
    active_sessions = []
    if 0 <= utc_hour < 9:
        active_sessions.append("Asia")
    if 7 <= utc_hour < 16:
        active_sessions.append("London")
    if 12 <= utc_hour < 21:
        active_sessions.append("New_York")
    tick_time_msc = tick.get("time_msc")
    tick_age_ms = None
    if isinstance(tick_time_msc, (int, float)):
        tick_age_ms = int(now_utc.timestamp() * 1000) - int(tick_time_msc)

    return {
        "decision_clock": {
            "utc_iso": now_utc.isoformat(timespec="microseconds"),
            "utc_epoch_ms": int(now_utc.timestamp() * 1000),
            "local_timezone": "Asia/Ho_Chi_Minh",
            "local_iso": now_local.isoformat(timespec="microseconds"),
            "weekday_utc": now_utc.strftime("%A"),
            "weekday_local": now_local.strftime("%A"),
            "active_sessions_approx": active_sessions,
            "instruction": "Treat this clock and tool results as authoritative current time. Do not infer current market conditions from model training knowledge.",
        },
        "snapshot_time_utc": now_utc.isoformat(timespec="microseconds"),
        "mode": cfg.mode,
        "base_symbol": base_symbol,
        "symbol": actual,
        "macro_context_requirements": {
            "drivers_to_verify": MACRO_REQUIREMENTS.get(base_symbol, []),
            "external_macro_news_available": True,
            "verification_tool": "DeepSeek Responses built-in web_search in the default shadow reviewer",
            "rule": "Do not assert current macro/news facts unless current web-search/tool evidence provides them. If they are material but remain unverified, flag CURRENT_MACRO_UNVERIFIED.",
        },
        "account": _account_context(account),
        "all_open_positions": all_positions,
        "positions_for_symbol": positions,
        "tick": {
            "bid": bid,
            "ask": ask,
            "spread_price": spread_price,
            "spread_points": spread_points,
            "time": tick.get("time"),
            "time_msc": tick_time_msc,
            "age_ms_at_snapshot": tick_age_ms,
        },
        "contract": _pick(info, SYMBOL_FIELDS),
        "timeframes": timeframes,
        "relevant_lessons": select_lessons(cfg.db_path, symbol=actual, limit=lesson_limit),
    }
