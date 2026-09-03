from forex_ai.market.features import summarize_features


def test_feature_summary_ready_and_trending_up():
    bars = []
    price = 1.0
    for i in range(100):
        price += 0.001
        bars.append({
            "time": 1_700_000_000 + i * 60,
            "open": price - 0.0005,
            "high": price + 0.0005,
            "low": price - 0.001,
            "close": price,
        })
    result = summarize_features(bars)
    assert result["ready"] is True
    assert result["trend"] == "up"
    assert result["ema20"] > result["ema50"]
    assert len(result["recent_candles"]) == 20


def test_feature_summary_not_ready_with_too_few_bars():
    bars = [{"open": 1, "high": 1, "low": 1, "close": 1} for _ in range(10)]
    assert summarize_features(bars) == {"ready": False, "bars": 10}
