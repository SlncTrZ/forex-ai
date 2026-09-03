from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping

from forex_ai.execution.controller import SendOutcome
from forex_ai.execution.state import ExecutionState, OrderIntent
from forex_ai.mt5.contracts import BrokerPosition, SymbolContract, TickSnapshot

SYMBOL_FILLING_FOK_FLAG = 1
SYMBOL_FILLING_IOC_FLAG = 2


class MT5RequestError(ValueError):
    pass


class ProtectionDisposition(StrEnum):
    VERIFIED = "VERIFIED"
    REPAIR = "REPAIR"
    EMERGENCY_CLOSE = "EMERGENCY_CLOSE"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class ProtectionPolicy:
    max_repair_attempts: int = 1
    emergency_close_on_failure: bool = True

    def __post_init__(self) -> None:
        if self.max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be >= 0")


@dataclass(frozen=True)
class MT5MarketRequestPolicy:
    deviation_points: int
    magic: int
    comment_prefix: str = "FXAI"

    def __post_init__(self) -> None:
        if self.deviation_points < 0:
            raise ValueError("deviation_points must be >= 0")
        if self.magic < 0:
            raise ValueError("magic must be >= 0")
        if not self.comment_prefix or len(self.comment_prefix) > 10:
            raise ValueError("comment_prefix must be 1..10 characters")


def _decimal_float(value: Decimal) -> float:
    if not value.is_finite():
        raise MT5RequestError("request numeric value must be finite")
    return float(value)


def _aligned(value: Decimal, step: Decimal) -> bool:
    if step <= 0:
        return False
    quotient = value / step
    return quotient == quotient.to_integral_value()


def choose_filling_mode(symbol_filling_flags: int, constants: Mapping[str, int]) -> int:
    if symbol_filling_flags & SYMBOL_FILLING_IOC_FLAG:
        return int(constants["ORDER_FILLING_IOC"])
    if symbol_filling_flags & SYMBOL_FILLING_FOK_FLAG:
        return int(constants["ORDER_FILLING_FOK"])
    raise MT5RequestError("broker symbol supports neither IOC nor FOK market filling")


def intent_comment(intent_id: str, prefix: str = "FXAI") -> str:
    return f"{prefix}:{intent_id[:18]}"[:31]


def build_market_request(
    intent: OrderIntent,
    *,
    contract: SymbolContract,
    constants: Mapping[str, int],
    policy: MT5MarketRequestPolicy,
) -> dict[str, Any]:
    if intent.state not in {ExecutionState.RISK_APPROVED, ExecutionState.PREFLIGHT_PASSED}:
        raise MT5RequestError("market request requires a risk-approved intent")
    if intent.symbol != contract.symbol:
        raise MT5RequestError("intent/contract symbol mismatch")
    if intent.side not in {"BUY", "SELL"}:
        raise MT5RequestError("unsupported intent side")
    if intent.volume <= 0:
        raise MT5RequestError("intent volume must be > 0")
    if intent.side == "BUY" and not (intent.stop_loss < intent.entry < intent.take_profit):
        raise MT5RequestError("BUY request requires SL < entry < TP")
    if intent.side == "SELL" and not (intent.take_profit < intent.entry < intent.stop_loss):
        raise MT5RequestError("SELL request requires TP < entry < SL")
    volume_step = Decimal(str(contract.volume_step))
    price_step = Decimal(str(contract.trade_tick_size or contract.point))
    if not _aligned(intent.volume - Decimal(str(contract.volume_min)), volume_step):
        raise MT5RequestError("intent volume is not aligned to broker volume step")
    if any(not _aligned(price, price_step) for price in (intent.entry, intent.stop_loss, intent.take_profit)):
        raise MT5RequestError("intent prices are not aligned to broker tick size")
    if contract.filling_mode is None:
        raise MT5RequestError("symbol filling mode unavailable")

    order_type = constants["ORDER_TYPE_BUY"] if intent.side == "BUY" else constants["ORDER_TYPE_SELL"]
    comment = intent_comment(intent.intent_id, policy.comment_prefix)
    return {
        "action": int(constants["TRADE_ACTION_DEAL"]),
        "symbol": intent.symbol,
        "volume": _decimal_float(intent.volume),
        "type": int(order_type),
        "price": _decimal_float(intent.entry),
        "sl": _decimal_float(intent.stop_loss),
        "tp": _decimal_float(intent.take_profit),
        "deviation": int(policy.deviation_points),
        "magic": int(policy.magic),
        "comment": comment,
        "type_time": int(constants["ORDER_TIME_GTC"]),
        "type_filling": choose_filling_mode(int(contract.filling_mode), constants),
    }


def build_protection_request(
    position: BrokerPosition,
    *,
    stop_loss: Decimal,
    take_profit: Decimal,
    contract: SymbolContract,
    constants: Mapping[str, int],
) -> dict[str, Any]:
    price_step = Decimal(str(contract.trade_tick_size or contract.point))
    if position.ticket <= 0 or position.symbol != contract.symbol:
        raise MT5RequestError("position/contract mismatch")
    if not stop_loss.is_finite() or not take_profit.is_finite() or stop_loss <= 0 or take_profit <= 0:
        raise MT5RequestError("protection prices must be finite and > 0")
    if not _aligned(stop_loss, price_step) or not _aligned(take_profit, price_step):
        raise MT5RequestError("protection prices are not aligned to broker tick size")
    if position.side == "BUY" and not (stop_loss < Decimal(str(position.price_current)) < take_profit):
        raise MT5RequestError("BUY protection requires SL < current < TP")
    if position.side == "SELL" and not (take_profit < Decimal(str(position.price_current)) < stop_loss):
        raise MT5RequestError("SELL protection requires TP < current < SL")
    return {
        "action": int(constants["TRADE_ACTION_SLTP"]),
        "position": int(position.ticket),
        "symbol": position.symbol,
        "sl": _decimal_float(stop_loss),
        "tp": _decimal_float(take_profit),
    }


def build_close_request(
    position: BrokerPosition,
    *,
    tick: TickSnapshot,
    contract: SymbolContract,
    constants: Mapping[str, int],
    policy: MT5MarketRequestPolicy,
    volume: Decimal | None = None,
) -> dict[str, Any]:
    if position.symbol != contract.symbol or tick.symbol != contract.symbol:
        raise MT5RequestError("position/tick/contract symbol mismatch")
    close_volume = volume or Decimal(str(position.volume))
    min_volume = Decimal(str(contract.volume_min))
    max_position_volume = Decimal(str(position.volume))
    step = Decimal(str(contract.volume_step))
    if not close_volume.is_finite() or close_volume < min_volume or close_volume > max_position_volume:
        raise MT5RequestError("invalid close volume")
    if not _aligned(close_volume - min_volume, step):
        raise MT5RequestError("close volume is not aligned to broker volume step")
    if contract.filling_mode is None:
        raise MT5RequestError("symbol filling mode unavailable")
    if position.side == "BUY":
        order_type = constants["ORDER_TYPE_SELL"]
        price = Decimal(str(tick.bid))
    elif position.side == "SELL":
        order_type = constants["ORDER_TYPE_BUY"]
        price = Decimal(str(tick.ask))
    else:
        raise MT5RequestError("unsupported position side")
    comment = f"{policy.comment_prefix}:close:{position.ticket}"[:31]
    return {
        "action": int(constants["TRADE_ACTION_DEAL"]),
        "position": int(position.ticket),
        "symbol": position.symbol,
        "volume": _decimal_float(close_volume),
        "type": int(order_type),
        "price": _decimal_float(price),
        "deviation": int(policy.deviation_points),
        "magic": int(policy.magic),
        "comment": comment,
        "type_time": int(constants["ORDER_TIME_GTC"]),
        "type_filling": choose_filling_mode(int(contract.filling_mode), constants),
    }


def protection_disposition(
    position: BrokerPosition,
    *,
    expected_stop_loss: Decimal,
    expected_take_profit: Decimal,
    contract: SymbolContract,
    failed_repair_attempts: int,
    policy: ProtectionPolicy,
) -> ProtectionDisposition:
    if failed_repair_attempts < 0:
        raise ValueError("failed_repair_attempts must be >= 0")
    point = Decimal(str(contract.trade_tick_size or contract.point))
    actual_sl = Decimal(str(position.sl))
    actual_tp = Decimal(str(position.tp))
    tolerance = point / Decimal("2")
    protected = (
        actual_sl > 0
        and actual_tp > 0
        and abs(actual_sl - expected_stop_loss) <= tolerance
        and abs(actual_tp - expected_take_profit) <= tolerance
    )
    if protected:
        return ProtectionDisposition.VERIFIED
    if failed_repair_attempts < policy.max_repair_attempts:
        return ProtectionDisposition.REPAIR
    if policy.emergency_close_on_failure:
        return ProtectionDisposition.EMERGENCY_CLOSE
    return ProtectionDisposition.BLOCK


def order_check_passed(result: dict[str, Any] | None) -> bool:
    return result is not None and int(result.get("retcode", -1)) == 0


class MT5RetcodeClassifier:
    def __init__(self, constants: Mapping[str, int]):
        self.constants = {key: int(value) for key, value in constants.items()}
        self._unknown = {
            self.constants["TRADE_RETCODE_TIMEOUT"],
            self.constants["TRADE_RETCODE_CONNECTION"],
        }
        self._accepted = {
            self.constants["TRADE_RETCODE_DONE"],
            self.constants["TRADE_RETCODE_PLACED"],
        }
        self._partial = {self.constants["TRADE_RETCODE_DONE_PARTIAL"]}
        self._transient_reject = {
            self.constants["TRADE_RETCODE_REQUOTE"],
            self.constants["TRADE_RETCODE_PRICE_CHANGED"],
            self.constants["TRADE_RETCODE_PRICE_OFF"],
            self.constants["TRADE_RETCODE_TOO_MANY_REQUESTS"],
            self.constants["TRADE_RETCODE_LOCKED"] if "TRADE_RETCODE_LOCKED" in self.constants else -999999,
        }

    def classify(self, response: dict[str, Any] | None) -> SendOutcome:
        if response is None:
            return SendOutcome(False, unknown=True, reason="BROKER_EMPTY_RESPONSE")
        try:
            retcode = int(response["retcode"])
        except (KeyError, TypeError, ValueError):
            return SendOutcome(False, unknown=True, reason="BROKER_MALFORMED_RESPONSE")
        order = int(response.get("order") or 0) or None
        if retcode in self._unknown:
            return SendOutcome(False, unknown=True, reason=f"MT5_RETCODE_{retcode}")
        if retcode in self._partial:
            try:
                volume = Decimal(str(response.get("volume") or "0"))
            except Exception:
                return SendOutcome(False, unknown=True, reason="BROKER_MALFORMED_PARTIAL_VOLUME")
            if not volume.is_finite() or volume < 0:
                return SendOutcome(False, unknown=True, reason="BROKER_MALFORMED_PARTIAL_VOLUME")
            return SendOutcome(
                True,
                partial=True,
                filled_volume=volume,
                broker_order_ticket=order,
                reason=f"MT5_RETCODE_{retcode}",
            )
        if retcode in self._accepted:
            return SendOutcome(
                True,
                broker_order_ticket=order,
                reason=f"MT5_RETCODE_{retcode}",
            )
        prefix = "MT5_TRANSIENT_REJECT" if retcode in self._transient_reject else "MT5_REJECT"
        return SendOutcome(False, reason=f"{prefix}_{retcode}")
