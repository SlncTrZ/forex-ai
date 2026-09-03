from forex_ai.mt5.symbols import resolve_symbol


def test_exact_symbol_wins():
    items = [{"name": "EURUSD.a"}, {"name": "EURUSD"}]
    assert resolve_symbol("EURUSD", items) == "EURUSD"


def test_shortest_suffix_is_selected():
    items = [{"name": "XAUUSD.pro"}, {"name": "XAUUSDm"}]
    assert resolve_symbol("XAUUSD", items) == "XAUUSDm"


def test_missing_returns_none():
    assert resolve_symbol("GBPUSD", [{"name": "USDJPY"}]) is None
