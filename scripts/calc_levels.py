import json
import math
import random

with open('backtest/data/comprehensive_2y_benchmark_all4.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

def get_level(equity, v0=10000.0):
    if equity <= 0:
        return -999
    if equity >= v0:
        return int(math.floor(math.log(equity / v0) / math.log(1.1) + 1e-9))
    else:
        return -int(math.ceil(math.log(equity / v0) / math.log(0.9) - 1e-9))

def get_risk_pct(lvl):
    if lvl <= 17:
        return 0.03
    elif 18 <= lvl <= 50:
        return 0.02
    elif 51 <= lvl <= 99:
        return 0.01
    else:
        return 0.005

def simulate_strategy_compound(trades, v0=10000.0, friction_r=0.04):
    equity = v0
    peak = equity
    mdd = 0.0
    
    for r in trades:
        lvl = get_level(equity, v0)
        risk_pct = get_risk_pct(lvl)
        risk_dollar = equity * risk_pct
        net_r = (r - friction_r) if r > 0 else (r - friction_r)
        trade_pnl = net_r * risk_dollar
        equity += trade_pnl
        
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > mdd:
            mdd = dd
            
    final_lvl = get_level(equity, v0)
    return equity, final_lvl, mdd

print("=== ĐÁNH GIÁ LEVEL LÃI KÉP THEO TỪNG CẤU HÌNH CHIẾN LƯỢC ===")

# Kịch bản 1: Chỉ chạy Supertrend M5 (XAUUSD) - Chiến lược Bát Cơm
xau_st = data['symbols']['XAUUSD']['strategies']['supertrend_volume_m5']
xau_st_trades = [1.8] * xau_st['winning_trades'] + [-1.0] * xau_st['losing_trades']
# Xáo trộn Monte Carlo 1,000 lần
mc_st_eq, mc_st_lvl, mc_st_mdd = [], [], []
for i in range(1000):
    shuffled = list(xau_st_trades)
    random.seed(i)
    random.shuffle(shuffled)
    eq, lvl, mdd = simulate_strategy_compound(shuffled)
    mc_st_eq.append(eq)
    mc_st_lvl.append(lvl)
    mc_st_mdd.append(mdd)

mc_st_eq.sort()
mc_st_lvl.sort()
mc_st_mdd.sort()
print(f"\n1. Supertrend Volume M5 (Chỉ Vàng - XAUUSD):")
print(f"   - Số lệnh: {len(xau_st_trades)} lệnh | Win Rate: {xau_st['win_rate_pct']}% | PF: {xau_st['profit_factor']}")
print(f"   - Vốn Median sau 2 năm: ${mc_st_eq[500]:,.2f} -> ĐẠT LEVEL {mc_st_lvl[500]} (+{((mc_st_eq[500]/10000)-1)*100:.1f}%)")
print(f"   - Kịch bản thuận lợi (P90): ${mc_st_eq[900]:,.2f} -> ĐẠT LEVEL {mc_st_lvl[900]} (+{((mc_st_eq[900]/10000)-1)*100:.1f}%)")
print(f"   - Max Drawdown Median: {mc_st_mdd[500]*100:.2f}% (P95 MDD: {mc_st_mdd[950]*100:.2f}%)")

# Kịch bản 2: Bộ đôi Supertrend M5 + MTF Trend Continuation (XAUUSD + BTCUSD)
duo_trades = []
for sym in ['XAUUSD', 'BTCUSD']:
    for st in ['supertrend_volume_m5', 'mtf_trend_continuation_v2']:
        s = data['symbols'][sym]['strategies'][st]
        duo_trades.extend([1.8] * s['winning_trades'])
        duo_trades.extend([-1.0] * s['losing_trades'])

mc_duo_eq, mc_duo_lvl, mc_duo_mdd = [], [], []
for i in range(1000):
    shuffled = list(duo_trades)
    random.seed(i)
    random.shuffle(shuffled)
    eq, lvl, mdd = simulate_strategy_compound(shuffled)
    mc_duo_eq.append(eq)
    mc_duo_lvl.append(lvl)
    mc_duo_mdd.append(mdd)

mc_duo_eq.sort()
mc_duo_lvl.sort()
mc_duo_mdd.sort()
print(f"\n2. Bộ Đôi Chọn Lọc: Supertrend M5 + MTF Trend Continuation (XAUUSD + BTCUSD):")
print(f"   - Số lệnh: {len(duo_trades)} lệnh ({sum(1 for r in duo_trades if r > 0)} thắng, {sum(1 for r in duo_trades if r < 0)} thua)")
print(f"   - Vốn Median sau 2 năm: ${mc_duo_eq[500]:,.2f} -> ĐẠT LEVEL {mc_duo_lvl[500]} (+{((mc_duo_eq[500]/10000)-1)*100:.1f}%)")
print(f"   - Kịch bản thuận lợi (P90): ${mc_duo_eq[900]:,.2f} -> ĐẠT LEVEL {mc_duo_lvl[900]} (+{((mc_duo_eq[900]/10000)-1)*100:.1f}%)")
print(f"   - Max Drawdown Median: {mc_duo_mdd[500]*100:.2f}% (P95 MDD: {mc_duo_mdd[950]*100:.2f}%)")

# Kịch bản 3: Toàn bộ danh mục V2 (Gồm cả SMC Pro FVG trên Vàng và BTC)
portfolio_trades = []
for sym in ['XAUUSD', 'BTCUSD']:
    for st in ['supertrend_volume_m5', 'smc_pro_fvg_v2', 'mtf_trend_continuation_v2']:
        s = data['symbols'][sym]['strategies'][st]
        target_r = 2.0 if 'smc' in st else 1.8
        portfolio_trades.extend([target_r] * s['winning_trades'])
        portfolio_trades.extend([-1.0] * s['losing_trades'])

mc_p_eq, mc_p_lvl, mc_p_mdd = [], [], []
# Với portfolio nhiều lệnh, áp dụng rủi ro 1.5% - 2.0% để tránh drawndown sâu
def simulate_portfolio_conservative(trades, v0=10000.0):
    equity = v0
    peak = equity
    mdd = 0.0
    for r in trades:
        lvl = get_level(equity, v0)
        # Giảm một nửa risk khi nhiều chiến lược chạy đồng thời (correlation dampening)
        risk_pct = get_risk_pct(lvl) * 0.5  # 1.5% tại L<=17
        risk_dollar = equity * risk_pct
        net_r = (r - 0.04) if r > 0 else (r - 0.04)
        trade_pnl = net_r * risk_dollar
        equity += trade_pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > mdd:
            mdd = dd
    return equity, get_level(equity, v0), mdd

for i in range(1000):
    shuffled = list(portfolio_trades)
    random.seed(i)
    random.shuffle(shuffled)
    eq, lvl, mdd = simulate_portfolio_conservative(shuffled)
    mc_p_eq.append(eq)
    mc_p_lvl.append(lvl)
    mc_p_mdd.append(mdd)

mc_p_eq.sort()
mc_p_lvl.sort()
mc_p_mdd.sort()
print(f"\n3. Toàn Bộ Danh Mục V2 (XAUUSD + BTCUSD) với Phân Bổ Danh Mục An Toàn (1.5% risk):")
print(f"   - Số lệnh: {len(portfolio_trades)} lệnh | Win Rate: {(sum(1 for r in portfolio_trades if r > 0)/len(portfolio_trades))*100:.1f}%")
print(f"   - Vốn Median sau 2 năm: ${mc_p_eq[500]:,.2f} -> ĐẠT LEVEL {mc_p_lvl[500]} (+{((mc_p_eq[500]/10000)-1)*100:.1f}%)")
print(f"   - Kịch bản xuất sắc (P90): ${mc_p_eq[900]:,.2f} -> ĐẠT LEVEL {mc_p_lvl[900]} (+{((mc_p_eq[900]/10000)-1)*100:.1f}%)")
print(f"   - Max Drawdown Median: {mc_p_mdd[500]*100:.2f}% (P95 MDD: {mc_p_mdd[950]*100:.2f}%)")
