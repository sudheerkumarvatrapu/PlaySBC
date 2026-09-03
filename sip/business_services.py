"""Deterministic state for RFC 5359 business calling services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BusinessServiceError(ValueError):
    pass


class ConsultationState(Enum):
    ACTIVE = "active"
    ORIGINAL_HELD = "original-held"
    CONSULTING = "consulting"
    RESUMING = "resuming"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ConsultationHold:
    """Tracks the original and consultation dialogs without conflating their state."""

    original_call_id: str
    state: ConsultationState = ConsultationState.ACTIVE
    consultation_call_id: str = ""
    target: str = ""
    failure_reason: str = ""

    def hold_original(self) -> None:
        self._require(ConsultationState.ACTIVE)
        self.state = ConsultationState.ORIGINAL_HELD

    def start_consultation(self, consultation_call_id: str, target: str) -> None:
        self._require(ConsultationState.ORIGINAL_HELD)
        if not consultation_call_id or consultation_call_id == self.original_call_id:
            raise BusinessServiceError("consultation dialog must have a distinct call ID")
        if not target:
            raise BusinessServiceError("consultation target must not be empty")
        self.consultation_call_id = consultation_call_id
        self.target = target
        self.state = ConsultationState.CONSULTING

    def complete_consultation(self) -> None:
        self._require(ConsultationState.CONSULTING)
        self.state = ConsultationState.RESUMING

    def resume_original(self) -> None:
        self._require(ConsultationState.RESUMING)
        self.state = ConsultationState.COMPLETED

    def fail(self, reason: str) -> None:
        if self.state in {ConsultationState.COMPLETED, ConsultationState.FAILED}:
            raise BusinessServiceError(f"consultation hold is already {self.state.value}")
        self.failure_reason = reason or "unspecified"
        self.state = ConsultationState.FAILED

    @property
    def original_dialog_preserved(self) -> bool:
        return self.state is not ConsultationState.ACTIVE and bool(self.original_call_id)

    def _require(self, expected: ConsultationState) -> None:
        if self.state is not expected:
            raise BusinessServiceError(
                f"consultation hold is {self.state.value}; expected {expected.value}"
            )
