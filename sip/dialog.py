from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class CallState(Enum):
    INIT = 0
    RINGING = 1
    ANSWERED = 2
    TERMINATED = 3


class DialogError(ValueError):
    """Raised when a request does not fit the current SIP dialog state."""


@dataclass
class SipDialog:
    call_id: str
    local_tag: str
    remote_tag: str
    invite_branch: str
    remote_cseq: int
    local_cseq: int = 0
    state: CallState = CallState.INIT
    created_at: float = field(default_factory=time.time)
    ringing_at: Optional[float] = None
    answered_at: Optional[float] = None
    acknowledged_at: Optional[float] = None
    terminated_at: Optional[float] = None
    branch_ids: set = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.invite_branch:
            self.branch_ids.add(self.invite_branch)

    def mark_ringing(self) -> None:
        self._require_state(CallState.INIT)
        self.state = CallState.RINGING
        self.ringing_at = time.time()

    def mark_answered(self) -> None:
        self._require_state(CallState.RINGING)
        self.state = CallState.ANSWERED
        self.answered_at = time.time()

    def acknowledge(self, cseq: int) -> None:
        self._require_state(CallState.ANSWERED)
        if cseq != self.remote_cseq:
            raise DialogError(f"ACK CSeq {cseq} does not match INVITE CSeq {self.remote_cseq}")
        self.acknowledged_at = time.time()

    def terminate(self, remote_tag: str, local_tag: str, branch_id: str, cseq: int) -> None:
        self._require_state(CallState.ANSWERED)
        if self.remote_tag and remote_tag != self.remote_tag:
            raise DialogError("BYE From tag does not match the dialog remote tag")
        if self.local_tag and local_tag != self.local_tag:
            raise DialogError("BYE To tag does not match the dialog local tag")
        if cseq <= self.remote_cseq:
            raise DialogError(f"BYE CSeq {cseq} must be greater than previous remote CSeq {self.remote_cseq}")

        self.remote_cseq = cseq
        if branch_id:
            self.branch_ids.add(branch_id)
        self.state = CallState.TERMINATED
        self.terminated_at = time.time()

    def to_header(self, original_to: str) -> str:
        return ensure_header_tag(original_to, self.local_tag)

    def _require_state(self, expected: CallState) -> None:
        if self.state is not expected:
            raise DialogError(f"Dialog {self.call_id} is {self.state.name}; expected {expected.name}")


class DialogManager:
    def __init__(self) -> None:
        self.dialogs: Dict[str, SipDialog] = {}

    def create_invite(self, call_id: str, from_header: str, via_header: str, cseq_header: str) -> SipDialog:
        existing = self.dialogs.get(call_id)
        if existing and existing.state is not CallState.TERMINATED:
            raise DialogError(f"Dialog {call_id} already exists in state {existing.state.name}")

        dialog = SipDialog(
            call_id=call_id,
            local_tag=secrets.token_hex(6),
            remote_tag=extract_tag(from_header),
            invite_branch=extract_branch(via_header),
            remote_cseq=parse_cseq_number(cseq_header),
        )
        self.dialogs[call_id] = dialog
        return dialog

    def get(self, call_id: str) -> Optional[SipDialog]:
        return self.dialogs.get(call_id)

    def acknowledge(self, call_id: str, cseq_header: str) -> SipDialog:
        dialog = self._require_dialog(call_id)
        dialog.acknowledge(parse_cseq_number(cseq_header))
        return dialog

    def terminate(self, call_id: str, from_header: str, to_header: str, via_header: str, cseq_header: str) -> SipDialog:
        dialog = self._require_dialog(call_id)
        dialog.terminate(
            remote_tag=extract_tag(from_header),
            local_tag=extract_tag(to_header),
            branch_id=extract_branch(via_header),
            cseq=parse_cseq_number(cseq_header),
        )
        return dialog

    def _require_dialog(self, call_id: str) -> SipDialog:
        dialog = self.dialogs.get(call_id)
        if not dialog:
            raise DialogError(f"Dialog {call_id!r} does not exist")
        return dialog


def extract_branch(via_header: str) -> str:
    match = re.search(r"(?:^|;)\s*branch=([^;\s]+)", via_header, re.IGNORECASE)
    return match.group(1) if match else ""


def extract_tag(header_value: str) -> str:
    match = re.search(r"(?:^|;)\s*tag=([^;\s>]+)", header_value, re.IGNORECASE)
    return match.group(1) if match else ""


def ensure_header_tag(header_value: str, tag: str) -> str:
    if extract_tag(header_value):
        return header_value
    return f"{header_value};tag={tag}"


def parse_cseq_number(cseq_header: str) -> int:
    number, _, _ = cseq_header.strip().partition(" ")
    try:
        return int(number)
    except ValueError as exc:
        raise DialogError(f"Invalid CSeq header {cseq_header!r}") from exc

