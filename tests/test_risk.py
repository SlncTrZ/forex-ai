from forex_ai.risk.engine import RiskEngine, TradeProposal


def proposal() -> TradeProposal:
    return TradeProposal(
        symbol="EURUSD", side="BUY", volume=0.01,
        entry=1.10, stop_loss=1.09, take_profit=1.12, rr=2.0,
    )


def symbol_info():
    return {"volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01}


def account():
    return {"balance": 1000.0, "equity": 1000.0}


def base_config(execution_enabled=False):
    return {
        "enabled": True,
        "execution_enabled": execution_enabled,
        "allowed_symbols": ["XAUUSD", "EURUSD", "GBPUSD"],
        "limits": {
            "min_risk_reward": 1.5,
            "max_signal_age_seconds": 30,
            "max_simultaneous_positions": 2,
            "max_daily_realized_loss_pct": 1.0,
            "max_daily_equity_drawdown_pct": 1.0,
        },
    }


def test_observe_mode_always_blocks_execution():
    result = RiskEngine(base_config(True), "OBSERVE").evaluate(
        proposal(), symbol_info=symbol_info(), account=account(), current_positions=0
    )
    assert not result.approved
    assert "MODE_NOT_LIVE" in result.reasons


def test_execution_flag_blocks_even_live_mode():
    result = RiskEngine(base_config(False), "CENT_GUARDED").evaluate(
        proposal(), symbol_info=symbol_info(), account=account(), current_positions=0
    )
    assert not result.approved
    assert "EXECUTION_DISABLED" in result.reasons


def test_safe_live_proposal_can_pass_only_when_explicitly_enabled():
    result = RiskEngine(base_config(True), "CENT_GUARDED").evaluate(
        proposal(), symbol_info=symbol_info(), account=account(), current_positions=0
    )
    assert result.approved
    assert result.approved_volume == 0.01


def test_bad_rr_is_rejected():
    p = TradeProposal("EURUSD", "BUY", 0.01, 1.10, 1.09, 1.105, 0.5)
    result = RiskEngine(base_config(True), "CENT_GUARDED").evaluate(
        p, symbol_info=symbol_info(), account=account(), current_positions=0
    )
    assert not result.approved
    assert "RR_TOO_LOW" in result.reasons
