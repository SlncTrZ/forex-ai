"""Strategy V2 Package — Advanced Production Strategies.
Wing: code | Topic: strategy_v2 | Updated: 2026-10-31 11:30
"""

from forex_ai.strategy.v2 import (
    btc_squeeze_breakout_m15,
    ema_ribbon_rsi_m5,
    ichimoku_mtf,
    keylevel_breakout,
    mtf_trend_continuation,
    pro_volatility_expansion_v2,
    range_liquidity_trap_m5,
    smc_pro_fvg,
    supertrend_cci_m5,
    supertrend_pinbar_m5,
    supertrend_stoch_rsi_m5,
    supertrend_stochastic_m5,
    supertrend_volume_m5,
)


def resolve_symbol_min_stop(symbol: str, configured_val: float = 0.0) -> float:
    """Xác định khoảng cách Stop Loss tối thiểu an toàn thích ứng theo lớp tài sản."""
    sym = symbol.upper()
    if "XAU" in sym or "GOLD" in sym:
        return max(configured_val, 5.0) if configured_val >= 1.0 else 6.0
    elif "BTC" in sym:
        return max(configured_val, 150.0) if configured_val >= 10.0 else 300.0
    elif "JPY" in sym:
        return configured_val if (0.05 <= configured_val <= 2.0) else 0.15
    else:
        # Standard FX (EURUSD, GBPUSD, AUDUSD, etc.)
        return configured_val if (0.0005 <= configured_val <= 0.01) else 0.0015


__all__ = [
    "btc_squeeze_breakout_m15",
    "ema_ribbon_rsi_m5",
    "ichimoku_mtf",
    "keylevel_breakout",
    "mtf_trend_continuation",
    "pro_volatility_expansion_v2",
    "range_liquidity_trap_m5",
    "smc_pro_fvg",
    "supertrend_cci_m5",
    "supertrend_pinbar_m5",
    "supertrend_stoch_rsi_m5",
    "supertrend_stochastic_m5",
    "supertrend_volume_m5",
    "resolve_symbol_min_stop",
]
