"""Active D3 regime admission tests: closed bars, routing, audit and hard veto."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from forex_ai.advisory.regime_router import (
    RegimeDecision, _model_choice, quant_features, resolve_regime, route_candidate,
)
from forex_ai.integration.engine import DecisionOrchestrator, StrategyBinding
from forex_ai.journal.db import initialize, session
from forex_ai.mt5.contracts import AccountSnapshot
from forex_ai.risk.broker_engine import RiskContext
from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, StrategyConfig, StrategyVersion, TimeframeSnapshot
from test_integration_phase import (
    NOW, _fixture_strategy, _profile, _account, _contract, _tick, _safety,
    _calc_profit, _calc_margin,
)

UTC = timezone.utc


def market_with_trend(*, incomplete_bar=False, capture_time=None):
    at = capture_time or datetime(2026, 10, 30, 15, 0, tzinfo=UTC)
    bars = []
    for i in range(260):
        end = at - timedelta(hours=(260 - i))
        close = 100.0 + i * 0.4
        bars.append(Candle(end, close - 0.1, close + 0.3, close - 0.35, close, 100 + i % 7))
    if incomplete_bar:
        end = at - timedelta(minutes=30)
        bars.append(Candle(end, 999, 1001, 997, 1000, 800))
    return MarketSnapshot("XAUUSDm", at, int(at.timestamp() * 1000), 203.5, 203.6,
                          {"H1": TimeframeSnapshot("H1", tuple(bars))})


def verdict(regime="UPTREND"):
    return {
        "answers": {
            "regime": {
                "choice": regime, "confidence": 0.94,
                "probabilities": {
                    "UPTREND": .94 if regime == "UPTREND" else .03,
                    "DOWNTREND": .94 if regime == "DOWNTREND" else .03,
                    "SIDEWAY": .94 if regime == "SIDEWAY" else .03,
                },
            }
        }
    }


class Model:
    fingerprint = "synthetic-model-fingerprint"

    def __init__(self, choice="UPTREND"):
        self.choice = choice
        self.calls = 0

    def decide_questions(self, *, state, questions):
        self.calls += 1
        assert "password" not in state and "broker_login" not in state
        assert "quant_regime" not in state
        assert questions["regime"]["type"] == "choice"
        return verdict(self.choice)


def test_closed_h1_only_and_no_incomplete_bar_leakage():
    original = quant_features(market_with_trend())
    polluted = quant_features(market_with_trend(incomplete_bar=True))
    assert original is not None and original.quant_regime == "UPTREND"
    assert polluted is not None
    assert original.fingerprint == polluted.fingerprint
    assert original.closed_h1_utc == polluted.closed_h1_utc


def test_h1_warmup_and_freshness_fail_closed():
    market = market_with_trend()
    shortened = MarketSnapshot(market.symbol, market.captured_at_utc, market.market_time_msc,
                               market.bid, market.ask,
                               {"H1": TimeframeSnapshot("H1", market.timeframes["H1"].closed_bars[-40:])})
    assert quant_features(shortened) is None
    outdated = MarketSnapshot(market.symbol, market.captured_at_utc + timedelta(hours=5),
                              market.market_time_msc, market.bid, market.ask,
                              market.timeframes)
    assert quant_features(outdated) is None


def test_d3_choice_schema_and_confidence():
    assert _model_choice(verdict()) == ("UPTREND", .94)
    from forex_ai.advisory.ollama_safety_gate import GateUnavailable
    import pytest
    with pytest.raises(GateUnavailable):
        _model_choice({"answers": {"regime": {"choice": "UPTREND",
                       "confidence": .95, "probabilities": {"UPTREND": .2}}}})


def test_active_regime_cached_and_audited_once_per_closed_bar(tmp_path):
    db = tmp_path / "regime.db"
    initialize(db)
    m = Model()
    market = market_with_trend()
    first = resolve_regime(market, db_path=db, provider=m)
    second = resolve_regime(market, db_path=db, provider=m)
    assert first == second and first.regime == "UPTREND"
    assert first.reason_code == "REGIME_CONFIRMED"
    assert m.calls == 1
    with session(db) as con:
        rows = con.execute("SELECT payload_json FROM audit_events WHERE event_type='D3_REGIME_DECISION'").fetchall()
    assert len(rows) == 1
    assert "synthetic-model-fingerprint" in rows[0]["payload_json"]


def test_conflict_or_missing_provider_yields_uncertain(tmp_path):
    db = tmp_path / "regime.db"
    initialize(db)
    market = market_with_trend()
    disagree = resolve_regime(market, db_path=db, provider=Model("DOWNTREND"))
    assert disagree.regime == "UNCERTAIN"
    assert disagree.reason_code == "REGIME_MODEL_QUANT_CONFLICT"
    unavailable = resolve_regime(market, db_path=db, provider=None)
    assert unavailable.regime == "UNCERTAIN"
    assert unavailable.reason_code == "REGIME_D3_UNAVAILABLE"


def _regime(kind):
    return RegimeDecision("EURUSD", kind, kind, kind, .95, "REGIME_CONFIRMED",
                          "2026-10-30T15:00:00+00:00", "f"*64, "m"*16, 430)


def test_strategy_router_blocks_wrong_direction_and_sideways():
    assert route_candidate(_regime("UPTREND"), strategy_id="smc_pro_fvg_v2", side="SELL") == "REGIME_DIRECTION_VETO"
    assert route_candidate(_regime("DOWNTREND"), strategy_id="keylevel_breakout_v2", side="BUY") == "REGIME_DIRECTION_VETO"
    assert route_candidate(_regime("SIDEWAY"), strategy_id="smc_pro_fvg_v2", side="BUY") == "REGIME_SIDEWAY_VETO"
    assert route_candidate(_regime("SIDEWAY"), strategy_id="btc_squeeze_breakout_m15", side="BUY") == "REGIME_SIDEWAY_VETO"
    assert route_candidate(_regime("SIDEWAY"), strategy_id="btc_squeeze_breakout_m15",
                           side="BUY", evidence_codes=("BTC_SQUEEZE_BREAKOUT_CONFIRMED",)) is None
    assert route_candidate(_regime("UPTREND"), strategy_id="supertrend_volume_m5", side="BUY") is None
    assert route_candidate(_regime("UNCERTAIN"), strategy_id="supertrend_volume_m5", side="BUY") == "REGIME_UNCERTAIN_VETO"


def test_orchestrator_really_vetoes_before_broker_risk(tmp_path):
    db = tmp_path / "test.db"
    initialize(db)
    cfg = StrategyConfig(StrategyVersion("smc_pro_fvg_v2", "1"), {})
    market = MarketSnapshot("EURUSD", NOW, int(NOW.timestamp() * 1000), 1.0999, 1.1, {})
    engine = DecisionOrchestrator(db_path=db, risk_profile=_profile(),
                                  strategies=(StrategyBinding(_fixture_strategy, cfg),))
    decisions = engine.scan(
        market, account=_account(), contract=_contract(), tick=_tick(), safety=_safety(),
        risk_context=RiskContext(daily_reference_equity=__import__("decimal").Decimal("10000"),
                                 weekly_reference_equity=__import__("decimal").Decimal("10000")),
        calc_profit=_calc_profit, calc_margin=_calc_margin, now_utc=NOW,
        regime_decision=_regime("DOWNTREND"),
    )
    assert len(decisions) == 1
    assert decisions[0].risk_result is None
    assert decisions[0].blocked_reasons == ("REGIME_DIRECTION_VETO",)
    with session(db) as con:
        assert con.execute("SELECT count(*) FROM candidate_decisions").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM risk_decisions_v1").fetchone()[0] == 0


def test_regime_policy_v2_discards_legacy_confirmed_cache(tmp_path):
    import hashlib
    from forex_ai.advisory.regime_router import POLICY_VERSION
    from forex_ai.journal.db import log_audit_event
    db = tmp_path / "regime.db"
    initialize(db)
    market = market_with_trend()
    features = quant_features(market)
    provider = Model("UPTREND")
    # A matching current-version cache with missing probability must also be rejected.
    key = hashlib.sha256(
        f"{POLICY_VERSION}:{market.symbol}:{features.fingerprint}:{provider.fingerprint}".encode()
    ).hexdigest()
    log_audit_event(db, event_type="D3_REGIME_DECISION",
                    source="production_v1_scanner", symbol=market.symbol,
                    entity_id=key, payload={
                        "symbol": market.symbol, "regime": "UPTREND",
                        "model_regime": "UPTREND", "quant_regime": "UPTREND",
                        "confidence": .9, "reason_code": "REGIME_CONFIRMED",
                        "closed_h1_utc": features.closed_h1_utc,
                        "feature_fingerprint": features.fingerprint,
                        "model_fingerprint": provider.fingerprint,
                        "policy_version": POLICY_VERSION,
                        "latency_ms": 300,
                    })
    r = resolve_regime(market, db_path=db, provider=provider)
    assert r.regime == "UPTREND" and r.choice_probability == .94
    assert provider.calls == 1


def test_btc_demo_low_confidence_is_advisory_only_and_not_confirmed():
    from forex_ai.advisory.regime_router import btc_demo_advisory_regime
    from dataclasses import replace
    low = RegimeDecision("BTCUSDm", "UNCERTAIN", "UPTREND", "UPTREND", .31,
                         "REGIME_LOW_CONFIDENCE", "2026-10-31T08:00:00+00:00",
                         "f"*64, "m"*16, 350, choice_probability=.59, atr_h1=100.)
    effective = btc_demo_advisory_regime(
        low, broker_demo_verified=True, symbol="BTCUSDm",
        strategy_id="btc_squeeze_breakout_m15")
    assert low.regime == "UNCERTAIN"  # Historical D3 observation is unchanged.
    assert effective.regime == "UPTREND"
    assert effective.reason_code == "REGIME_BTC_DEMO_ADVISORY"
    assert effective.confidence == .31 and effective.choice_probability == .59
    assert route_candidate(effective, strategy_id="btc_squeeze_breakout_m15", side="BUY") is None
    assert route_candidate(effective, strategy_id="btc_squeeze_breakout_m15", side="SELL") == "REGIME_DIRECTION_VETO"
    assert btc_demo_advisory_regime(low, broker_demo_verified=False, symbol="BTCUSDm",
        strategy_id="btc_squeeze_breakout_m15") is low
    assert btc_demo_advisory_regime(low, broker_demo_verified=True, symbol="XAUUSDm",
        strategy_id="btc_squeeze_breakout_m15") is low
    assert btc_demo_advisory_regime(low, broker_demo_verified=True, symbol="BTCUSDm",
        strategy_id="supertrend_volume_m5") is low
    assert btc_demo_advisory_regime(replace(low, model_regime="DOWNTREND"),
        broker_demo_verified=True, symbol="BTCUSDm",
        strategy_id="btc_squeeze_breakout_m15") is not effective
    for broken in (
        replace(low, reason_code="REGIME_D3_UNAVAILABLE"),
        replace(low, reason_code="REGIME_INVALID_RESPONSE"),
        replace(low, reason_code="REGIME_MODEL_QUANT_CONFLICT"),
        replace(low, confidence=None),
        replace(low, choice_probability=None),
        replace(low, closed_h1_utc=None),
    ):
        assert btc_demo_advisory_regime(broken, broker_demo_verified=True,
            symbol="BTCUSDm", strategy_id="btc_squeeze_breakout_m15") is broken


def test_btc_demo_advisory_reaches_risk_engine_with_no_real_bypass(tmp_path):
    from forex_ai.advisory.regime_router import btc_demo_advisory_regime
    from forex_ai.strategy.v1.contracts import DecisionEvidence, build_candidate, StrategyResult
    from decimal import Decimal
    db = tmp_path / "btc.db"
    initialize(db)
    cfg = StrategyConfig(StrategyVersion("btc_squeeze_breakout_m15", "1"), {})
    market = MarketSnapshot("BTCUSDm", NOW, int(NOW.timestamp() * 1000),
                            1.0999, 1.1, {})
    low = RegimeDecision("BTCUSDm", "UNCERTAIN", "UPTREND", "UPTREND", .31,
                         "REGIME_LOW_CONFIDENCE", NOW.isoformat(),
                         "f"*64, "m"*16, 350, choice_probability=.59, atr_h1=.01)
    def btc_buy(snapshot, config, now):
        evidence = DecisionEvidence(("BTC_SQUEEZE_BREAKOUT_CONFIRMED",), {})
        candidate = build_candidate(
            snapshot=snapshot, config=config, side="BUY",
            entry=snapshot.ask, stop_loss=snapshot.ask-.01, take_profit=snapshot.ask+.02,
            generated_at_utc=now, expires_at_utc=now+timedelta(minutes=10),
            evidence=evidence,
        )
        return StrategyResult(candidate, None, evidence)
    engine = DecisionOrchestrator(
        db_path=db, risk_profile=_profile(),
        strategies=(StrategyBinding(btc_buy, cfg),))
    args = dict(
        account=_account(),
        contract=_contract().model_copy(update={"symbol": "BTCUSDm"}),
        tick=_tick().model_copy(update={"symbol": "BTCUSDm"}),
        safety=_safety(),
        risk_context=RiskContext(daily_reference_equity=Decimal("10000"),
                                 weekly_reference_equity=Decimal("10000")),
        calc_profit=_calc_profit, calc_margin=_calc_margin, now_utc=NOW)
    unapproved = engine.scan(market, regime_decision=low, **args)
    assert unapproved[0].risk_result is None
    assert unapproved[0].blocked_reasons == ("REGIME_UNCERTAIN_VETO",)
    projected = btc_demo_advisory_regime(low, broker_demo_verified=True,
        symbol=market.symbol, strategy_id="btc_squeeze_breakout_m15")
    allowed = engine.scan(market, regime_decision=projected, **args)
    assert allowed[0].risk_result is not None
    assert allowed[0].risk_result.approved
    assert low.regime == "UNCERTAIN"
