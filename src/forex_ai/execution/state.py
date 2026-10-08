"""Execution State — Quản lý trạng thái và lưu trữ lệnh OrderIntent.
Wing: code | Topic: execution | Updated: 2026-10-08 20:44
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class ExecutionState(StrEnum):
    """Trạng thái vòng đời của lệnh giao dịch (OrderIntent)."""

    INTENT_CREATED = "INTENT_CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
    SEND_STARTED = "SEND_STARTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    PROTECTION_VERIFIED = "PROTECTION_VERIFIED"
    RECONCILED = "RECONCILED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


_ALLOWED: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.INTENT_CREATED: frozenset({ExecutionState.RISK_APPROVED, ExecutionState.REJECTED}),
    ExecutionState.RISK_APPROVED: frozenset({ExecutionState.PREFLIGHT_PASSED, ExecutionState.REJECTED}),
    ExecutionState.PREFLIGHT_PASSED: frozenset({ExecutionState.SEND_STARTED, ExecutionState.REJECTED}),
    ExecutionState.SEND_STARTED: frozenset({
        ExecutionState.ACCEPTED,
        ExecutionState.PARTIALLY_FILLED,
        ExecutionState.FILLED,
        ExecutionState.REJECTED,
        ExecutionState.UNKNOWN,
    }),
    ExecutionState.ACCEPTED: frozenset({ExecutionState.PARTIALLY_FILLED, ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.UNKNOWN}),
    ExecutionState.PARTIALLY_FILLED: frozenset({ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.UNKNOWN}),
    ExecutionState.FILLED: frozenset({ExecutionState.PROTECTION_VERIFIED, ExecutionState.UNKNOWN}),
    ExecutionState.PROTECTION_VERIFIED: frozenset({ExecutionState.RECONCILED, ExecutionState.CLOSED}),
    ExecutionState.UNKNOWN: frozenset({ExecutionState.ACCEPTED, ExecutionState.PARTIALLY_FILLED, ExecutionState.FILLED, ExecutionState.REJECTED, ExecutionState.RECONCILED}),
    ExecutionState.RECONCILED: frozenset({ExecutionState.PROTECTION_VERIFIED, ExecutionState.CLOSED, ExecutionState.CANCELLED}),
    ExecutionState.REJECTED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
    ExecutionState.CLOSED: frozenset(),
}


@dataclass(frozen=True)
class OrderIntent:
    """Đại diện cho lệnh giao dịch (order intent) cùng thông tin trạng thái và broker ticket."""

    intent_id: str
    candidate_id: str
    idempotency_key: str
    symbol: str
    side: str
    volume: Decimal
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    state: ExecutionState
    created_at_utc: datetime
    broker_order_ticket: int | None = None
    broker_position_ticket: int | None = None
    filled_volume: Decimal = Decimal("0")
    last_reason: str | None = None

    @staticmethod
    def derive_idempotency_key(candidate_id: str, risk_profile_fingerprint: str, safety_snapshot_fingerprint: str) -> str:
        """Tạo khóa idempotency SHA-256 từ candidate_id, risk_profile_fingerprint và safety_snapshot_fingerprint.

        Args:
            candidate_id: Định danh ứng viên lệnh.
            risk_profile_fingerprint: Fingerprint của hồ sơ rủi ro.
            safety_snapshot_fingerprint: Fingerprint của snapshot an toàn.

        Returns:
            Chuỗi băm SHA-256 dạng hex đại diện cho khóa idempotency.
        """
        payload = f"{candidate_id}|{risk_profile_fingerprint}|{safety_snapshot_fingerprint}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def transition(self, target: ExecutionState, *, reason: str | None = None, **changes) -> OrderIntent:
        """Chuyển đổi trạng thái OrderIntent sang trạng thái target hợp lệ và cập nhật các trường thay đổi.

        Args:
            target: Trạng thái ExecutionState đích cần chuyển sang.
            reason: Lý do chuyển trạng thái.
            **changes: Các thuộc tính OrderIntent cần ghi đè khi thay đổi trạng thái.

        Returns:
            Đối tượng OrderIntent mới được tạo từ replace với trạng thái và thông tin cập nhật.

        Raises:
            ValueError: Khi việc chuyển trạng thái từ trạng thái hiện tại sang target không hợp lệ.
        """
        if target not in _ALLOWED[self.state]:
            raise ValueError(f"invalid execution transition {self.state}->{target}")
        return replace(self, state=target, last_reason=reason, **changes)


class IntentRepository(Protocol):
    """Giao diện kho lưu trữ các đối tượng OrderIntent."""

    def get(self, intent_id: str) -> OrderIntent | None:
        """Lấy thông tin OrderIntent theo intent_id.

        Args:
            intent_id: Định danh của lệnh cần lấy.

        Returns:
            Đối tượng OrderIntent tương ứng hoặc None nếu không tìm thấy.
        """
        ...

    def get_by_idempotency_key(self, key: str) -> OrderIntent | None:
        """Lấy thông tin OrderIntent theo khóa idempotency_key.

        Args:
            key: Khóa idempotency của lệnh cần lấy.

        Returns:
            Đối tượng OrderIntent tương ứng hoặc None nếu không tìm thấy.
        """
        ...

    def save(self, intent: OrderIntent) -> None:
        """Lưu hoặc cập nhật thông tin OrderIntent vào kho lưu trữ.

        Args:
            intent: Đối tượng OrderIntent cần lưu.
        """
        ...

    def all(self) -> tuple[OrderIntent, ...]:
        """Lấy danh sách tất cả các lệnh OrderIntent trong kho lưu trữ.

        Returns:
            Tuple chứa các đối tượng OrderIntent.
        """
        ...


class InMemoryIntentRepository:
    """Cài đặt IntentRepository lưu trữ dữ liệu OrderIntent trong bộ nhớ (in-memory)."""

    def __init__(self):
        self._by_id: dict[str, OrderIntent] = {}
        self._by_key: dict[str, str] = {}

    def get(self, intent_id: str) -> OrderIntent | None:
        """Lấy OrderIntent theo intent_id từ bộ nhớ.

        Args:
            intent_id: Định danh của lệnh cần lấy.

        Returns:
            Đối tượng OrderIntent tương ứng hoặc None nếu không tìm thấy.
        """
        return self._by_id.get(intent_id)

    def get_by_idempotency_key(self, key: str) -> OrderIntent | None:
        """Lấy OrderIntent theo idempotency_key từ bộ nhớ.

        Args:
            key: Khóa idempotency của lệnh cần lấy.

        Returns:
            Đối tượng OrderIntent tương ứng hoặc None nếu không tìm thấy.
        """
        intent_id = self._by_key.get(key)
        return self._by_id.get(intent_id) if intent_id else None

    def save(self, intent: OrderIntent) -> None:
        """Lưu OrderIntent vào bộ nhớ và cập nhật chỉ mục idempotency_key.

        Args:
            intent: Đối tượng OrderIntent cần lưu.

        Raises:
            ValueError: Khi idempotency_key đã tồn tại cho một intent_id khác.
        """
        existing_id = self._by_key.get(intent.idempotency_key)
        if existing_id is not None and existing_id != intent.intent_id:
            raise ValueError("duplicate idempotency key")
        self._by_id[intent.intent_id] = intent
        self._by_key[intent.idempotency_key] = intent.intent_id

    def all(self) -> tuple[OrderIntent, ...]:
        """Lấy toàn bộ danh sách OrderIntent đang lưu trữ trong bộ nhớ.

        Returns:
            Tuple chứa tất cả các đối tượng OrderIntent.
        """
        return tuple(self._by_id.values())
