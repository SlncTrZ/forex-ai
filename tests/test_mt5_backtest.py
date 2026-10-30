from forex_ai.research.mt5_backtest import BacktestRequest


def test_backtest_request_keeps_dca_hard_bound():
    request = BacktestRequest("XAUUSDm", 9, 51, 68, 9, 5, 1.0, 2, 1000, "AU_MY", True, 3, 3)
    assert request.max_dca_layers == 3
