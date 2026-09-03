from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from forex_ai.execution.state import ExecutionState, OrderIntent
from forex_ai.mt5.contracts import BrokerDeal, BrokerOrder, BrokerPosition


@dataclass(frozen=True)
class ReconcileResult:
    intent: OrderIntent
    blocking_reasons: tuple[str, ...] = ()


def reconcile_intent(
    intent: OrderIntent,
    *,
    orders: tuple[BrokerOrder, ...] = (),
    deals: tuple[BrokerDeal, ...] = (),
    positions: tuple[BrokerPosition, ...] = (),
) -> ReconcileResult:
    order = next((o for o in orders if intent.broker_order_ticket and o.ticket == intent.broker_order_ticket), None)
    position = next((p for p in positions if intent.broker_position_ticket and p.ticket == intent.broker_position_ticket), None)
    related_deals = tuple(d for d in deals if (intent.broker_order_ticket and d.order == intent.broker_order_ticket) or (intent.broker_position_ticket and d.position_id == intent.broker_position_ticket))
    filled = sum((Decimal(str(d.volume)) for d in related_deals), Decimal("0"))

    current = intent
    blocking: list[str] = []

    if current.state is ExecutionState.UNKNOWN:
        if position is not None or filled >= current.volume:
            current = current.transition(ExecutionState.FILLED, filled_volume=max(filled, current.volume), reason="BROKER_EVIDENCE_FILLED")
        elif order is not None:
            current = current.transition(ExecutionState.ACCEPTED, reason="BROKER_EVIDENCE_ACCEPTED")
        elif related_deals:
            current = current.transition(ExecutionState.PARTIALLY_FILLED, filled_volume=filled, reason="BROKER_EVIDENCE_PARTIAL")
        else:
            blocking.append("UNRESOLVED_UNKNOWN")

    if current.state in {ExecutionState.ACCEPTED, ExecutionState.PARTIALLY_FILLED}:
        if filled >= current.volume and current.volume > 0:
            current = current.transition(ExecutionState.FILLED, filled_volume=filled, reason="FILLED_BY_RECONCILIATION")
        elif filled > 0 and current.state is ExecutionState.ACCEPTED:
            current = current.transition(ExecutionState.PARTIALLY_FILLED, filled_volume=filled, reason="PARTIAL_BY_RECONCILIATION")

    if position is not None:
        if position.sl <= 0 or position.tp <= 0:
            blocking.append("UNPROTECTED_POSITION")
        elif current.state is ExecutionState.FILLED:
            current = current.transition(ExecutionState.PROTECTION_VERIFIED, broker_position_ticket=position.ticket, reason="PROTECTION_PRESENT")

    if current.state is ExecutionState.PROTECTION_VERIFIED:
        current = current.transition(ExecutionState.RECONCILED, reason="BROKER_STATE_RECONCILED")

    return ReconcileResult(intent=current, blocking_reasons=tuple(dict.fromkeys(blocking)))


def find_orphan_positions(intents: tuple[OrderIntent, ...], positions: tuple[BrokerPosition, ...]) -> tuple[BrokerPosition, ...]:
    known = {i.broker_position_ticket for i in intents if i.broker_position_ticket is not None}
    return tuple(p for p in positions if p.ticket not in known)


def reconciliation_blockers(
    intents: tuple[OrderIntent, ...],
    positions: tuple[BrokerPosition, ...],
    results: tuple[ReconcileResult, ...] = (),
) -> tuple[str, ...]:
    reasons: list[str] = []
    if find_orphan_positions(intents, positions):
        reasons.append("ORPHAN_BROKER_POSITION")
    if any(intent.state is ExecutionState.UNKNOWN for intent in intents):
        reasons.append("UNRESOLVED_UNKNOWN")
    if any(position.sl <= 0 or position.tp <= 0 for position in positions):
        reasons.append("UNPROTECTED_POSITION")
    for result in results:
        reasons.extend(result.blocking_reasons)
    return tuple(dict.fromkeys(reasons))
