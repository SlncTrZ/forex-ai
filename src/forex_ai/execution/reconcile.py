from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from forex_ai.execution.mt5 import intent_comment
from forex_ai.execution.state import ExecutionState, OrderIntent
from forex_ai.mt5.contracts import BrokerDeal, BrokerOrder, BrokerPosition


@dataclass(frozen=True)
class ReconcileResult:
    intent: OrderIntent
    blocking_reasons: tuple[str, ...] = ()
    transition_path: tuple[OrderIntent, ...] = ()


def reconcile_intent(
    intent: OrderIntent,
    *,
    orders: tuple[BrokerOrder, ...] = (),
    deals: tuple[BrokerDeal, ...] = (),
    positions: tuple[BrokerPosition, ...] = (),
) -> ReconcileResult:
    expected_comment = intent_comment(intent.intent_id)
    order = next((
        o for o in orders
        if (intent.broker_order_ticket and o.ticket == intent.broker_order_ticket)
        or (intent.state is ExecutionState.UNKNOWN and o.comment == expected_comment)
    ), None)
    position = next((
        p for p in positions
        if (intent.broker_position_ticket and p.ticket == intent.broker_position_ticket)
        or (
            intent.broker_position_ticket is None
            and intent.state not in {ExecutionState.REJECTED, ExecutionState.CANCELLED, ExecutionState.CLOSED}
            and p.comment == expected_comment
        )
    ), None)
    matched_order_ticket = intent.broker_order_ticket or (order.ticket if order is not None else None)
    matched_position_ticket = intent.broker_position_ticket or (position.ticket if position is not None else None)
    related_deals = tuple(
        d for d in deals
        if (matched_order_ticket and d.order == matched_order_ticket)
        or (matched_position_ticket and d.position_id == matched_position_ticket)
    )
    filled = sum((Decimal(str(d.volume)) for d in related_deals), Decimal("0"))

    current = intent
    blocking: list[str] = []
    transitions: list[OrderIntent] = []

    def advance(next_intent: OrderIntent) -> OrderIntent:
        transitions.append(next_intent)
        return next_intent

    if current.state is ExecutionState.UNKNOWN:
        if position is not None or filled >= current.volume:
            current = advance(current.transition(
                ExecutionState.FILLED,
                filled_volume=max(filled, current.volume),
                broker_order_ticket=matched_order_ticket,
                broker_position_ticket=matched_position_ticket,
                reason="BROKER_EVIDENCE_FILLED",
            ))
        elif order is not None:
            current = advance(current.transition(
                ExecutionState.ACCEPTED,
                broker_order_ticket=matched_order_ticket,
                reason="BROKER_EVIDENCE_ACCEPTED",
            ))
        elif related_deals:
            current = advance(current.transition(
                ExecutionState.PARTIALLY_FILLED,
                filled_volume=filled,
                broker_order_ticket=matched_order_ticket,
                broker_position_ticket=matched_position_ticket,
                reason="BROKER_EVIDENCE_PARTIAL",
            ))
        else:
            blocking.append("UNRESOLVED_UNKNOWN")

    if current.state in {ExecutionState.ACCEPTED, ExecutionState.PARTIALLY_FILLED}:
        if filled >= current.volume and current.volume > 0:
            current = advance(current.transition(ExecutionState.FILLED, filled_volume=filled, reason="FILLED_BY_RECONCILIATION"))
        elif filled > 0 and current.state is ExecutionState.ACCEPTED:
            current = advance(current.transition(ExecutionState.PARTIALLY_FILLED, filled_volume=filled, reason="PARTIAL_BY_RECONCILIATION"))

    if position is not None:
        if position.sl <= 0 or position.tp <= 0:
            blocking.append("UNPROTECTED_POSITION")
        elif current.state is ExecutionState.FILLED:
            current = advance(current.transition(ExecutionState.PROTECTION_VERIFIED, broker_position_ticket=position.ticket, reason="PROTECTION_PRESENT"))

    if current.state is ExecutionState.PROTECTION_VERIFIED:
        current = advance(current.transition(ExecutionState.RECONCILED, reason="BROKER_STATE_RECONCILED"))

    return ReconcileResult(
        intent=current,
        blocking_reasons=tuple(dict.fromkeys(blocking)),
        transition_path=tuple(transitions),
    )


def find_orphan_positions(intents: tuple[OrderIntent, ...], positions: tuple[BrokerPosition, ...]) -> tuple[BrokerPosition, ...]:
    known_tickets = {i.broker_position_ticket for i in intents if i.broker_position_ticket is not None}
    known_comments = {
        intent_comment(i.intent_id)
        for i in intents
        if i.state not in {ExecutionState.REJECTED, ExecutionState.CANCELLED, ExecutionState.CLOSED}
    }
    return tuple(
        p for p in positions
        if p.ticket not in known_tickets and (not p.comment or p.comment not in known_comments)
    )


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
