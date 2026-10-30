from forex_ai.strategy.v2.keylevel_detector import detect_swings, extract_structure


def test_detect_swings_pure_logic():
    # Gia lap 30 cay nen: Dinh tai nen 15 (gia 100), Day tai nen 20 (gia 50)
    highs = [10.0] * 35
    lows = [8.0] * 35
    
    # Tao dinh tai vi tri 15
    highs[15] = 100.0
    # Tao day tai vi tri 22
    lows[22] = 2.0
    
    sw_highs, sw_lows = detect_swings(highs, lows, left_bars=10, right_bars=5)
    assert len(sw_highs) == 1
    assert sw_highs[0].index == 15
    assert sw_highs[0].price == 100.0
    
    assert len(sw_lows) == 1
    assert sw_lows[0].index == 22
    assert sw_lows[0].price == 2.0


def test_extract_structure_and_trendline():
    # 2 dinh: nen 10 (gia 100), nen 20 (gia 90) -> Down Trendline
    highs = [10.0] * 30
    lows = [5.0] * 30
    
    highs[10] = 100.0
    highs[20] = 90.0
    
    struct = extract_structure(highs, lows, left_bars=5, right_bars=3)
    assert struct.resistance == 90.0
    assert struct.resistance_trendline is not None
    # Slope = (90 - 100) / (20 - 10) = -1.0 per bar
    assert struct.resistance_trendline.slope == -1.0
    # Tai bar 25: price = 90 + (-1.0) * (25 - 20) = 85.0
    assert struct.resistance_trendline.price_at(25) == 85.0
