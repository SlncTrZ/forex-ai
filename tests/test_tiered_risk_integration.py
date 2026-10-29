from datetime import datetime, timedelta, timezone
from decimal import Decimal

from forex_ai.mt5.contracts import AccountSnapshot, SafetySnapshot, SymbolContract, TickSnapshot
from forex_ai.risk.broker_engine import BrokerAwareRiskEngine, CandidateInput, RiskContext
from forex_ai.risk.profile import RiskProfile
from forex_ai.risk.tiered_compound import TieredCompoundRiskEngine

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
D = Decimal


def make_profile(**overrides) -> RiskProfile:
    data = dict(
        max_risk_per_trade_pct=D("1"),
        max_total_open_risk_pct=D("5"),
        daily_loss_limit_pct=D("10"),
        weekly_loss_limit_pct=D("20"),
        max_active_orders=3,
        min_margin_reserve_pct=D("10"),
    )
    data.update(overrides)
    return RiskProfile(**data)


def make_account(equity: Decimal = D("1000"), margin_free: Decimal = D("1000")) -> AccountSnapshot:
    return AccountSnapshot(
        login=123,
        server="Broker-Demo",
        currency="USD",
        balance=float(equity),
        equity=float(equity),
        margin=0,
        margin_free=float(margin_free),
        leverage=100,
        captured_at_utc=NOW,
    )


def make_contract() -> SymbolContract:
    return SymbolContract(
        symbol="EURUSD",
        digits=5,
        point=0.00001,
        trade_contract_size=100000,
        volume_min=0.01,
        volume_max=100,
        volume_step=0.01,
        trade_stops_level=10,
    )


def make_tick() -> TickSnapshot:
    return TickSnapshot(symbol="EURUSD", bid=1.0999, ask=1.1000, time_msc=1, captured_at_utc=NOW)


def make_safety() -> SafetySnapshot:
    return SafetySnapshot(
        account_fingerprint="a" * 64,
        contracts_fingerprint="b" * 64,
        reconciled=True,
        blocking_reasons=(),
        captured_at_utc=NOW,
    )


def make_candidate(**overrides) -> CandidateInput:
    data = dict(
        candidate_id="c1",
        symbol="EURUSD",
        side="BUY",
        reference_entry=D("1.1000"),
        stop_loss=D("1.0900"),  # 100 pips = 1000 points
        take_profit=D("1.1200"),
        expires_at_utc=NOW + timedelta(minutes=5),
    )
    data.update(overrides)
    return CandidateInput(**data)


def calc_profit(side: str, symbol: str, volume: Decimal, open_price: Decimal, close_price: Decimal) -> Decimal:
    delta = (close_price - open_price) if side == "BUY" else (open_price - close_price)
    return delta * volume * D("100000")


def calc_margin(side: str, symbol: str, volume: Decimal, price: Decimal) -> Decimal:
    return (volume * D("100000") * price) / D("100")


def test_tiered_standalone_engine():
    engine = TieredCompoundRiskEngine(v0=D("1000"))
    assert engine.calculate_level(D("1000")) == 0
    assert engine.calculate_level(D("1100")) == 1
    assert engine.calculate_level(D("5560")) == 18
    assert engine.get_risk_pct(0) == D("0.03")
    assert engine.get_risk_pct(18) == D("0.02")
    assert engine.get_risk_pct(52) == D("0.01")
    assert engine.get_risk_pct(100) == D("0.005")


def test_tiered_compound_broker_engine_integration_level_0():
    # Level 0 ($1000 equity) -> Risk 3% = $30.
    # SL = 100 pips (1.1000 - 1.0900) -> $1000 loss per lot.
    # Raw volume = 30 / 1000 = 0.03 lots.
    p = make_profile(enable_tiered_compound=True, initial_deposit_v0=D("1000"))
    engine = BrokerAwareRiskEngine(p)
    result = engine.evaluate(
        make_candidate(),
        account=make_account(equity=D("1000")),
        contract=make_contract(),
        tick=make_tick(),
        safety=make_safety(),
        context=RiskContext(daily_reference_equity=D("1000"), weekly_reference_equity=D("1000")),
        calc_profit=calc_profit,
        calc_margin=calc_margin,
        now_utc=NOW,
    )
    assert result.approved
    assert result.normalized_volume == D("0.03")


def test_tiered_compound_broker_engine_integration_level_18():
    # Level 18 ($6000 equity) -> Risk 2% = $120.
    # SL = 100 pips -> $1000 loss per lot.
    # Raw volume = 120 / 1000 = 0.12 lots.
    p = make_profile(enable_tiered_compound=True, initial_deposit_v0=D("1000"))
    engine = BrokerAwareRiskEngine(p)
    result = engine.evaluate(
        make_candidate(),
        account=make_account(equity=D("6000"), margin_free=D("6000")),
        contract=make_contract(),
        tick=make_tick(),
        safety=make_safety(),
        context=RiskContext(daily_reference_equity=D("6000"), weekly_reference_equity=D("6000")),
        calc_profit=calc_profit,
        calc_margin=calc_margin,
        now_utc=NOW,
    )
    assert result.approved
    assert result.normalized_volume == D("0.12")


def test_tiered_compound_deep_drawdown_forced_min_lot():
    # Equity $150 (Level -18, Drawdown).
    # Risk 3% = $4.50. SL = 100 pips -> loss for 0.01 lot is $10.00.
    # $10.00 > $4.50 budget.
    # With allow_forced_min_lot=True: volume is approved at 0.01 lot!
    p_forced = make_profile(enable_tiered_compound=True, initial_deposit_v0=D("1000"), allow_forced_min_lot=True)
    engine_forced = BrokerAwareRiskEngine(p_forced)
    result = engine_forced.evaluate(
        make_candidate(),
        account=make_account(equity=D("150"), margin_free=D("150")),
        contract=make_contract(),
        tick=make_tick(),
        safety=make_safety(),
        context=RiskContext(daily_reference_equity=D("150"), weekly_reference_equity=D("150")),
        calc_profit=calc_profit,
        calc_margin=calc_margin,
        now_utc=NOW,
    )
    assert result.approved
    assert result.normalized_volume == D("0.01")

    # Without allow_forced_min_lot: rejected because 0.01 lot exceeds $4.50 budget
    p_strict = make_profile(enable_tiered_compound=True, initial_deposit_v0=D("1000"), allow_forced_min_lot=False)
    engine_strict = BrokerAwareRiskEngine(p_strict)
    result_strict = engine_strict.evaluate(
        make_candidate(),
        account=make_account(equity=D("150"), margin_free=D("150")),
        contract=make_contract(),
        tick=make_tick(),
        safety=make_safety(),
        context=RiskContext(daily_reference_equity=D("150"), weekly_reference_equity=D("150")),
        calc_profit=calc_profit,
        calc_margin=calc_margin,
        now_utc=NOW,
    )
    assert not result_strict.approved
    assert "MIN_VOLUME_EXCEEDS_RISK" in result_strict.reason_codes


def test_tiered_compound_withdrawal_resets_to_level_0():
    # Ban đầu V0 = $10,000. Sau đó rút -$9,000 ra, equity còn $1,000.
    # daily_reference_equity = $10,000, daily_net_cash_flow = -$9,000 -> daily_reference = $1,000.
    # Hệ thống tự động nhận diện cash flow và reset về Level 0 (thay vì coi là lỗ -90% thành Level -22)!
    p = make_profile(enable_tiered_compound=True, initial_deposit_v0=D("10000"))
    engine = BrokerAwareRiskEngine(p)
    result = engine.evaluate(
        make_candidate(),
        account=make_account(equity=D("1000")),
        contract=make_contract(),
        tick=make_tick(),
        safety=make_safety(),
        context=RiskContext(
            daily_reference_equity=D("10000"),
            weekly_reference_equity=D("10000"),
            daily_net_cash_flow=D("-9000"),
            weekly_net_cash_flow=D("-9000"),
        ),
        calc_profit=calc_profit,
        calc_margin=calc_margin,
        now_utc=NOW,
    )
    assert result.approved
    # Với Level 0 trên $1,000, risk 3% = $30. SL 100 pips = $1000/lot -> volume = 0.03 lot!
    assert result.normalized_volume == D("0.03")
