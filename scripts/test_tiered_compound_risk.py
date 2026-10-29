#!/usr/bin/env python3
"""
Test Suite & Verification for Tiered Compound Interest & Risk De-escalation Engine.
"""
from decimal import Decimal
import unittest
import math

from dataclasses import dataclass
from typing import Optional

D = Decimal

@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    contract_size: Decimal
    point: Decimal
    tick_size: Decimal
    tick_value: Decimal
    min_volume: Decimal = D("0.01")
    max_volume: Decimal = D("100.0")
    volume_step: Decimal = D("0.01")
    leverage: Decimal = D("100.0")
    min_margin_reserve_pct: Decimal = D("0.10")

@dataclass(frozen=True)
class SizingResult:
    approved: bool
    level: int
    risk_pct: Decimal
    risk_amount: Decimal
    raw_lots: Decimal
    executable_lots: Decimal
    effective_risk_pct: Decimal
    margin_required: Decimal
    free_margin: Decimal
    status: str
    reason: str

class TieredCompoundRiskEngine:
    def __init__(self, v0: Decimal, hysteresis_pct: Decimal = D("0.015")):
        if v0 <= 0:
            raise ValueError("Initial deposit V0 must be > 0")
        self.v0 = v0
        self.hysteresis_pct = hysteresis_pct

    def get_threshold(self, level: int) -> Decimal:
        if level >= 0:
            return self.v0 * (D("1.1") ** level)
        else:
            return self.v0 * (D("0.9") ** abs(level))

    def calculate_level(self, equity: Decimal, previous_level: Optional[int] = None) -> int:
        if equity <= 0:
            return -999
        eq_float = float(equity)
        v0_float = float(self.v0)

        if equity >= self.v0:
            raw_lvl = int(math.floor((math.log(eq_float / v0_float) / math.log(1.1)) + 1e-9))
        elif equity >= self.v0 * D("0.9"):
            raw_lvl = 0
        else:
            k = math.log(eq_float / v0_float) / math.log(0.9)
            raw_lvl = -int(math.floor(k + 1e-9))

        if previous_level is None:
            return raw_lvl

        # Hysteresis dampening
        if raw_lvl > previous_level:
            target_thresh = self.get_threshold(previous_level + 1)
            if equity < target_thresh:
                return previous_level
            return raw_lvl
        elif raw_lvl < previous_level:
            curr_thresh = self.get_threshold(previous_level)
            lower_bound_with_hysteresis = curr_thresh * (D("1") - self.hysteresis_pct)
            if equity >= lower_bound_with_hysteresis:
                return previous_level
            return raw_lvl
        return raw_lvl

    def get_risk_pct(self, level: int) -> Decimal:
        if level <= 17:
            return D("0.03")
        elif 18 <= level <= 50:
            return D("0.02")
        elif 51 <= level <= 99:
            return D("0.01")
        else:
            return D("0.005")

    def evaluate(
        self,
        equity: Decimal,
        free_margin: Decimal,
        stop_loss_points: Decimal,
        current_price: Decimal,
        spec: InstrumentSpec,
        previous_level: Optional[int] = None,
        max_acceptable_risk_pct: Decimal = D("0.05")
    ) -> SizingResult:
        if stop_loss_points <= 0 or current_price <= 0:
            return SizingResult(
                approved=False, level=0, risk_pct=D("0"), risk_amount=D("0"),
                raw_lots=D("0"), executable_lots=D("0"), effective_risk_pct=D("0"),
                margin_required=D("0"), free_margin=free_margin,
                status="REJECTED", reason="INVALID_PRICE_OR_STOP_LOSS"
            )

        level = self.calculate_level(equity, previous_level)
        risk_pct = self.get_risk_pct(level)
        risk_budget = equity * risk_pct

        point_value_per_lot = (spec.tick_value / spec.tick_size) * spec.point
        loss_per_lot = stop_loss_points * point_value_per_lot
        if loss_per_lot <= 0:
            return SizingResult(
                approved=False, level=level, risk_pct=risk_pct, risk_amount=risk_budget,
                raw_lots=D("0"), executable_lots=D("0"), effective_risk_pct=D("0"),
                margin_required=D("0"), free_margin=free_margin,
                status="REJECTED", reason="INVALID_LOSS_PER_LOT"
            )

        # Margin check for minimum volume first (Hard Physical Barrier)
        min_margin_required = (spec.min_volume * spec.contract_size * current_price) / spec.leverage
        max_usable_margin = free_margin * (D("1") - spec.min_margin_reserve_pct)
        if min_margin_required > max_usable_margin:
            return SizingResult(
                approved=False, level=level, risk_pct=risk_pct, risk_amount=risk_budget,
                raw_lots=D("0"), executable_lots=D("0"), effective_risk_pct=D("0"),
                margin_required=min_margin_required, free_margin=free_margin,
                status="REJECTED", reason="INSUFFICIENT_FREE_MARGIN_FOR_MIN_LOT"
            )

        raw_lots = risk_budget / loss_per_lot
        steps = math.floor(float(raw_lots / spec.volume_step))
        floored_lots = Decimal(str(steps)) * spec.volume_step

        status = "APPROVED"
        reason = "NORMAL_SIZING"
        executable_lots = floored_lots

        if executable_lots < spec.min_volume:
            # Rule: Continue trading with minimum lot (0.01) until margin runs out
            executable_lots = spec.min_volume
            min_loss = executable_lots * loss_per_lot
            effective_risk = min_loss / equity if equity > 0 else D("1.0")
            status = "APPROVED_FORCED_MIN"
            reason = f"FORCED_MIN_LOT_RISK_{effective_risk*100:.2f}%"

        if executable_lots > spec.max_volume:
            executable_lots = spec.max_volume
            status = "APPROVED_CAPPED_MAX"
            reason = f"CAPPED_TO_BROKER_MAX_{spec.max_volume}"

        margin_required = (executable_lots * spec.contract_size * current_price) / spec.leverage
        if margin_required > max_usable_margin:
            return SizingResult(
                approved=False, level=level, risk_pct=risk_pct, risk_amount=risk_budget,
                raw_lots=raw_lots, executable_lots=executable_lots,
                effective_risk_pct=(executable_lots * loss_per_lot) / equity,
                margin_required=margin_required, free_margin=free_margin,
                status="REJECTED", reason="INSUFFICIENT_FREE_MARGIN"
            )

        effective_risk = (executable_lots * loss_per_lot) / equity
        return SizingResult(
            approved=True, level=level, risk_pct=risk_pct, risk_amount=risk_budget,
            raw_lots=raw_lots, executable_lots=executable_lots,
            effective_risk_pct=effective_risk,
            margin_required=margin_required, free_margin=free_margin,
            status=status, reason=reason
        )

class TestTieredCompoundRiskEngine(unittest.TestCase):
    def setUp(self):
        self.engine = TieredCompoundRiskEngine(v0=D("1000"))
        self.gold_spec = InstrumentSpec(
            symbol="XAUUSD",
            contract_size=D("100"),
            point=D("0.01"),
            tick_size=D("0.01"),
            tick_value=D("1.00"),
            min_volume=D("0.01"),
            max_volume=D("100.0"),
            volume_step=D("0.01"),
            leverage=D("100.0")
        )

    def test_level_mapping_positive(self):
        self.assertEqual(self.engine.calculate_level(D("1000")), 0)
        self.assertEqual(self.engine.calculate_level(D("1099")), 0)
        self.assertEqual(self.engine.calculate_level(D("1100")), 1)
        self.assertEqual(self.engine.calculate_level(D("1210")), 2)
        self.assertEqual(self.engine.calculate_level(D("5054.48")), 17)
        self.assertEqual(self.engine.calculate_level(D("5559.92")), 18)
        self.assertEqual(self.engine.calculate_level(D("117390.86")), 50)
        self.assertEqual(self.engine.calculate_level(D("129129.95")), 51)
        self.assertEqual(self.engine.calculate_level(D("12527829.41")), 99)
        self.assertEqual(self.engine.calculate_level(D("13780612.35")), 100)

    def test_level_mapping_negative(self):
        self.assertEqual(self.engine.calculate_level(D("1000")), 0)
        self.assertEqual(self.engine.calculate_level(D("999")), 0)
        self.assertEqual(self.engine.calculate_level(D("900")), 0)
        self.assertEqual(self.engine.calculate_level(D("899")), -1)
        self.assertEqual(self.engine.calculate_level(D("810")), -2)
        self.assertEqual(self.engine.calculate_level(D("809")), -2)
        self.assertEqual(self.engine.calculate_level(D("590.49")), -5)
        self.assertEqual(self.engine.calculate_level(D("590.00")), -5)
        self.assertEqual(self.engine.calculate_level(D("205.89")), -15)

    def test_risk_de_escalation(self):
        self.assertEqual(self.engine.get_risk_pct(-10), D("0.03"))
        self.assertEqual(self.engine.get_risk_pct(-5), D("0.03"))
        self.assertEqual(self.engine.get_risk_pct(0), D("0.03"))
        self.assertEqual(self.engine.get_risk_pct(17), D("0.03"))
        self.assertEqual(self.engine.get_risk_pct(18), D("0.02"))
        self.assertEqual(self.engine.get_risk_pct(50), D("0.02"))
        self.assertEqual(self.engine.get_risk_pct(51), D("0.01"))
        self.assertEqual(self.engine.get_risk_pct(99), D("0.01"))
        self.assertEqual(self.engine.get_risk_pct(100), D("0.005"))
        self.assertEqual(self.engine.get_risk_pct(120), D("0.005"))

    def test_hysteresis(self):
        lvl = self.engine.calculate_level(D("1000"))
        self.assertEqual(lvl, 0)
        lvl = self.engine.calculate_level(D("1100"), previous_level=lvl)
        self.assertEqual(lvl, 1)
        # Drops slightly to 1090 -> Should remain level 1 because 1090 > 1100 * 0.985 = 1083.5
        lvl = self.engine.calculate_level(D("1090"), previous_level=lvl)
        self.assertEqual(lvl, 1)
        # Drops to 1080 -> Drops to level 0
        lvl = self.engine.calculate_level(D("1080"), previous_level=lvl)
        self.assertEqual(lvl, 0)

    def test_deep_drawdown_continues_trading(self):
        # Level -15, Equity = 205.90
        # Risk 3% = $6.18. SL = 500 points ($5.00/oz on gold) -> loss per lot = $500
        # raw lot = 6.18 / 500 = 0.01236 -> exec lot = 0.01
        res = self.engine.evaluate(
            equity=D("205.89"),
            free_margin=D("205.89"),
            stop_loss_points=D("500"),
            current_price=D("2000"),
            spec=self.gold_spec
        )
        self.assertTrue(res.approved)
        self.assertEqual(res.level, -15)
        self.assertEqual(res.executable_lots, D("0.01"))

    def test_insufficient_margin_halt(self):
        # Equity = $15. Margin required for 0.01 lot gold at $2000 leverage 100:1 is $20.00.
        res = self.engine.evaluate(
            equity=D("15.00"),
            free_margin=D("15.00"),
            stop_loss_points=D("500"),
            current_price=D("2000"),
            spec=self.gold_spec
        )
        self.assertFalse(res.approved)
        self.assertEqual(res.status, "REJECTED")
        self.assertIn("INSUFFICIENT_FREE_MARGIN_FOR_MIN_LOT", res.reason)

    def test_eurusd_forex_sizing(self):
        # EURUSD: point = 0.00001, tick_size = 0.00001, tick_value = $1.00.
        # SL = 250 points (25 pips) -> Loss per lot = 250 * 1.00 = $250.
        # Equity = $1000 (Level 0), Risk 3% = $30.
        # Raw lots = 30 / 250 = 0.12 lots.
        eur_spec = InstrumentSpec(
            symbol="EURUSD",
            contract_size=D("100000"),
            point=D("0.00001"),
            tick_size=D("0.00001"),
            tick_value=D("1.00"),
            min_volume=D("0.01"),
            max_volume=D("100.0"),
            volume_step=D("0.01"),
            leverage=D("100.0")
        )
        res = self.engine.evaluate(
            equity=D("1000.00"),
            free_margin=D("1000.00"),
            stop_loss_points=D("250"),
            current_price=D("1.0850"),
            spec=eur_spec
        )
        self.assertTrue(res.approved)
        self.assertEqual(res.level, 0)
        self.assertEqual(res.executable_lots, D("0.12"))
        self.assertEqual(res.risk_amount, D("30.00"))

    def test_risk_step_transition_lot_contraction(self):
        # Level 17 (Equity $5054.48): Risk 3% = $151.63. SL = 500 points on Gold -> $500/lot.
        # Raw lot = 151.63 / 500 = 0.30 lots.
        res_17 = self.engine.evaluate(
            equity=D("5054.48"),
            free_margin=D("5054.48"),
            stop_loss_points=D("500"),
            current_price=D("2000"),
            spec=self.gold_spec
        )
        self.assertEqual(res_17.level, 17)
        self.assertEqual(res_17.risk_pct, D("0.03"))
        self.assertEqual(res_17.executable_lots, D("0.30"))

        # Level 18 (Equity $5559.92): Risk drops to 2% = $111.20.
        # Raw lot = 111.20 / 500 = 0.22 lots.
        res_18 = self.engine.evaluate(
            equity=D("5559.92"),
            free_margin=D("5559.92"),
            stop_loss_points=D("500"),
            current_price=D("2000"),
            spec=self.gold_spec
        )
        self.assertEqual(res_18.level, 18)
        self.assertEqual(res_18.risk_pct, D("0.02"))
        self.assertEqual(res_18.executable_lots, D("0.22"))
        # Confirms intentional lot contraction at the tier boundary
        self.assertLess(res_18.executable_lots, res_17.executable_lots)

    def test_broker_max_lot_capping(self):
        # High equity: $1,000,000 (Level 72, Risk 1% = $10,000).
        # SL = 500 points ($500/lot) -> Raw lots = 20.0 lots (under 100).
        # But if Equity = $10,000,000 (Level 96, Risk 1% = $100,000).
        # Raw lots = 100,000 / 500 = 200 lots -> capped to 100.0 lots!
        res_high = self.engine.evaluate(
            equity=D("10000000.00"),
            free_margin=D("10000000.00"),
            stop_loss_points=D("500"),
            current_price=D("2000"),
            spec=self.gold_spec
        )
        self.assertTrue(res_high.approved)
        self.assertEqual(res_high.executable_lots, D("100.00"))
        self.assertEqual(res_high.status, "APPROVED_CAPPED_MAX")

if __name__ == "__main__":
    unittest.main()
