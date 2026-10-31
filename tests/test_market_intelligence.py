"""v0.0.6: isolated BTC/XAU observations, real H1 alignment and D3 semantics."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from forex_ai.journal.db import initialize, session
from forex_ai.market.intelligence import EVENT, observe_symbol
from forex_ai.advisory.regime_router import _model_choice_detail

UTC = timezone.utc
NOW = datetime(2026, 10, 31, 10, 30, tzinfo=UTC)


def bars(direction=-1, count=271):
    first = NOW.replace(minute=0, second=0, microsecond=0) - timedelta(hours=count - 1)
    result = []
    for i in range(count):
        close = 44000.0 + direction * 12.0 * i
        price_open = close - direction * 4
        result.append({
            "time": int((first + timedelta(hours=i)).timestamp()),
            "open": price_open, "high": max(price_open, close) + 2,
            "low": min(price_open, close) - 2, "close": close,
            "tick_volume": 200 + i % 7,
        })
    return result


def tick(now=NOW):
    return {"time_msc": int(now.timestamp() * 1000), "bid": 40000.0, "ask": 40001.0}


class Model:
    fingerprint = "synthetic-d3-policy-v1"

    def __init__(self, confidence=.5818, probability=.8385, broken=False):
        self.calls = 0
        self.confidence = confidence
        self.probability = probability
        self.broken = broken

    def decide_questions(self, *, state, questions):
        self.calls += 1
        assert "quant_regime" not in state
        assert questions["regime"]["type"] == "choice"
        return {"answers": {"regime": {
            "choice": "DOWNTREND", "confidence": self.confidence,
            "probabilities": {
                "DOWNTREND": self.probability,
                "UPTREND": .08075 if not self.broken else .6,
                "SIDEWAY": .08075 if not self.broken else .3,
            }
        }}}


def test_d3_confidence_is_not_class_probability():
    choice, confidence, probability = _model_choice_detail(Model().decide_questions(state="{}", questions={"regime": {"type": "choice"}}))
    assert choice == "DOWNTREND"
    assert round(confidence, 4) == .5818
    assert round(probability, 4) == .8385


def test_weekend_xau_isolated_from_btc_and_audited(tmp_path):
    db = tmp_path / "intel.db"
    initialize(db)
    model = Model()
    xau = observe_symbol("XAUUSDm", rows=bars(), tick=None, provider=model, db_path=db, now_utc=NOW)
    assert xau["market_state"] == "OFF_SESSION"
    assert xau["reason_code"] == "OFF_SESSION"
    assert xau["confidence"] is None
    assert model.calls == 0
    btc = observe_symbol("BTCUSDm", rows=bars(), tick=tick(), provider=model, db_path=db, now_utc=NOW)
    assert btc["market_state"] == "ACTIVE"
    assert btc["quant_regime"] == "DOWNTREND"
    assert btc["model_regime"] == "DOWNTREND"
    assert btc["reason_code"] == "REGIME_LOW_CONFIDENCE"
    assert btc["regime"] == "UNCERTAIN"
    assert btc["confidence"] == .5818 and btc["choice_probability"] == .8385
    assert btc["confirmed_for_trading"] is False
    again = observe_symbol("BTCUSDm", rows=bars(), tick=tick(), provider=model, db_path=db, now_utc=NOW + timedelta(seconds=4))
    assert again["cache_hit"] is True and model.calls == 1
    with session(db) as con:
        rows = con.execute("SELECT symbol,payload_json FROM audit_events WHERE event_type=?", (EVENT,)).fetchall()
    assert len(rows) == 2
    assert {row["symbol"] for row in rows} == {"XAUUSDm", "BTCUSDm"}
    for row in rows:
        payload = json.loads(row["payload_json"])
        assert "token" not in payload and "account" not in payload
        assert payload["confirmed_for_trading"] is False


def test_bad_d3_distribution_never_becomes_a_confirmed_regime(tmp_path):
    db = tmp_path / "intel.db"
    initialize(db)
    model = Model(broken=True)
    result = observe_symbol("BTCUSDm", rows=bars(), tick=tick(), provider=model, db_path=db, now_utc=NOW)
    assert result["regime"] == "UNCERTAIN"
    assert result["reason_code"] == "REGIME_PROBABILITY_MISMATCH"
    assert result["confidence"] is None
    assert result["confirmed_for_trading"] is False


def test_no_future_h1_bar_and_stale_btc_are_rejected(tmp_path):
    db = tmp_path / "intel.db"
    initialize(db)
    model = Model()
    data = bars()
    data[-1]["close"] = 99999.0
    data[-1]["high"] = 100000.0
    data[-1]["low"] = 1.0
    a = observe_symbol("BTCUSDm", rows=data, tick=tick(), provider=model, db_path=db, now_utc=NOW)
    b = observe_symbol("BTCUSDm", rows=bars(), tick=tick(), provider=model, db_path=db, now_utc=NOW)
    assert a["feature_fingerprint"] == b["feature_fingerprint"]
    stale = observe_symbol("BTCUSDm", rows=bars(), tick=tick(NOW-timedelta(seconds=45)),
                           provider=model, db_path=db, now_utc=NOW)
    assert stale["market_state"] == "STALE_TICK"
    assert stale["confidence"] is None and stale["regime"] == "UNCERTAIN"


def test_high_confidence_cannot_override_weak_choice_probability(tmp_path):
    db = tmp_path / "intel.db"
    initialize(db)
    model = Model(confidence=.95, probability=.60)
    def weak_choice(*, state, questions):
        return {"answers": {"regime": {
            "choice": "DOWNTREND", "confidence": .95,
            "probabilities": {"DOWNTREND": .60, "UPTREND": .2, "SIDEWAY": .2},
        }}}
    model.decide_questions = weak_choice
    result = observe_symbol("BTCUSDm", rows=bars(), tick=tick(), provider=model,
                            db_path=db, now_utc=NOW)
    assert result["reason_code"] == "REGIME_LOW_CHOICE_PROBABILITY"
    assert result["regime"] == "UNCERTAIN"
    assert result["confidence"] == .95 and result["choice_probability"] == .60


def test_d3_timeout_is_unconfirmed_and_retryable_after_ttl(tmp_path):
    from forex_ai.advisory.ollama_safety_gate import GateUnavailable
    db = tmp_path / "intel.db"
    initialize(db)
    class Timeout:
        fingerprint = "offline-d3-v1"
        calls = 0
        def decide_questions(self, **_):
            self.calls += 1
            raise GateUnavailable("D3_PROVIDER_UNAVAILABLE")
    model = Timeout()
    first = observe_symbol("BTCUSDm", rows=bars(), tick=tick(), provider=model,
                           db_path=db, now_utc=NOW)
    assert first["reason_code"] == "REGIME_D3_UNAVAILABLE"
    assert first["regime"] == "UNCERTAIN" and first["confidence"] is None
    cached = observe_symbol("BTCUSDm", rows=bars(), tick=tick(NOW+timedelta(seconds=10)),
                            provider=model, db_path=db, now_utc=NOW+timedelta(seconds=10))
    assert cached["cache_hit"] is True and model.calls == 1
    retry = observe_symbol("BTCUSDm", rows=bars(), tick=tick(NOW+timedelta(seconds=310)),
                           provider=model, db_path=db, now_utc=NOW+timedelta(seconds=310))
    assert retry["reason_code"] == "REGIME_D3_UNAVAILABLE" and model.calls == 2


def test_confirmed_decision_cached_but_observation_heartbeat_advances(tmp_path):
    from forex_ai.market.intelligence import DECISION_EVENT
    db = tmp_path / "intel.db"
    initialize(db)
    model = Model(confidence=.96, probability=.8385)
    for minute in (0, 5, 11):
        now = NOW + timedelta(minutes=minute)
        result = observe_symbol("BTCUSDm", rows=bars(), tick=tick(now), provider=model,
                                db_path=db, now_utc=now)
        assert result["reason_code"] == "REGIME_CONFIRMED"
        assert result["observed_at"] == now.isoformat()
    with session(db) as con:
        observed = con.execute("SELECT count(*) FROM audit_events WHERE event_type=?", (EVENT,)).fetchone()[0]
        decisions = con.execute("SELECT count(*) FROM audit_events WHERE event_type=?", (DECISION_EVENT,)).fetchone()[0]
    assert observed == 3 and decisions == 1 and model.calls == 1


def test_decision_cache_without_selected_probability_is_never_confirmed(tmp_path):
    import hashlib
    from forex_ai.market.intelligence import DECISION_EVENT
    from forex_ai.advisory.regime_router import POLICY_VERSION, quant_features
    from forex_ai.strategy.v1.contracts import MarketSnapshot
    from forex_ai.integration.adapters import timeframe_snapshot
    from forex_ai.journal.db import log_audit_event
    db = tmp_path / "intel.db"
    initialize(db)
    model = Model(confidence=.95, probability=.85)
    tf = timeframe_snapshot("H1", bars())
    snapshot = MarketSnapshot("BTCUSDm", NOW, int(NOW.timestamp()*1000),
                              40000., 40001., {"H1": tf})
    features = quant_features(snapshot)
    assert features is not None
    key = hashlib.sha256(
        f"{EVENT}:BTCUSDm:{features.fingerprint}:{model.fingerprint}:{POLICY_VERSION}".encode()
    ).hexdigest()
    log_audit_event(db, event_type=DECISION_EVENT, source="market_intelligence_h1",
                    symbol="BTCUSDm", entity_id=key, payload={
                        "symbol": "BTCUSDm", "regime": "DOWNTREND",
                        "model_regime": "DOWNTREND", "quant_regime": "DOWNTREND",
                        "confidence": .95, "choice_probability": None,
                        "reason_code": "REGIME_CONFIRMED",
                        "feature_fingerprint": features.fingerprint,
                        "model_fingerprint": model.fingerprint,
                        "policy_version": POLICY_VERSION, "observed_at": NOW.isoformat(),
                    })
    result = observe_symbol("BTCUSDm", rows=bars(), tick=tick(), provider=model,
                            db_path=db, now_utc=NOW)
    assert model.calls == 1
    assert result["choice_probability"] == .85
    assert result["reason_code"] == "REGIME_CONFIRMED"
