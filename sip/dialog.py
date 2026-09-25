from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple


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
    route_set: Tuple[str, ...] = ()
    remote_target: str = ""
    seen_remote_requests: set = field(default_factory=set)
    offer_pending: bool = False
    session_interval: int = 0
    session_refresher: str = ""
    session_expires_at: Optional[float] = None
    reliable_provisionals: Dict[int, Tuple[int, str]] = field(default_factory=dict)

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

    @property
    def dialog_id(self) -> Tuple[str, str, str]:
        return self.call_id, self.local_tag, self.remote_tag

    def set_remote_route(self, record_route: str, contact: str, *, reverse: bool = False) -> None:
        routes = split_header_values(record_route)
        self.route_set = tuple(reversed(routes)) if reverse else tuple(routes)
        target = extract_name_addr_uri(contact)
        if target:
            self.remote_target = target

    def refresh_remote_target(self, contact: str) -> None:
        target = extract_name_addr_uri(contact)
        if target:
            self.remote_target = target

    def next_local_cseq(self) -> int:
        self.local_cseq += 1
        return self.local_cseq

    def accept_remote_request(self, method: str, cseq: int, branch: str = "") -> bool:
        """Return False for an already-applied request; reject stale CSeq values."""
        key = method.upper(), cseq, branch
        if key in self.seen_remote_requests:
            return False
        if cseq <= self.remote_cseq:
            raise DialogError(f"{method} CSeq {cseq} is not newer than {self.remote_cseq}")
        self.remote_cseq = cseq
        self.seen_remote_requests.add(key)
        if branch:
            self.branch_ids.add(branch)
        return True

    def begin_offer(self) -> None:
        if self.offer_pending:
            raise DialogError("Offer/answer exchange already pending")
        self.offer_pending = True

    def complete_offer(self) -> None:
        self.offer_pending = False

    def set_session_timer(self, interval: int, refresher: str, now: Optional[float] = None) -> None:
        if interval < 90:
            raise DialogError("Session-Expires must be at least 90 seconds")
        if refresher not in {"uac", "uas"}:
            raise DialogError("Session refresher must be uac or uas")
        self.session_interval = interval
        self.session_refresher = refresher
        self.session_expires_at = (time.time() if now is None else now) + interval

    def remember_reliable_provisional(self, rseq: int, cseq: int, method: str) -> None:
        if rseq <= 0 or rseq in self.reliable_provisionals:
            raise DialogError("Invalid or duplicate reliable provisional RSeq")
        self.reliable_provisionals[rseq] = cseq, method.upper()

    def acknowledge_reliable_provisional(self, rack: str) -> None:
        parts = rack.split()
        if len(parts) != 3:
            raise DialogError("Invalid RAck")
        try:
            rseq, cseq = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise DialogError("Invalid RAck number") from exc
        expected = self.reliable_provisionals.get(rseq)
        if expected != (cseq, parts[2].upper()):
            raise DialogError("RAck does not match a reliable provisional response")
        self.reliable_provisionals.pop(rseq)

    def _require_state(self, expected: CallState) -> None:
        if self.state is not expected:
            raise DialogError(f"Dialog {self.call_id} is {self.state.name}; expected {expected.name}")


class DialogManager:
    def __init__(self) -> None:
        self.dialogs: Dict[str, SipDialog] = {}
        self.by_id: Dict[Tuple[str, str, str], SipDialog] = {}

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
        self.by_id[dialog.dialog_id] = dialog
        return dialog

    def get(self, call_id: str) -> Optional[SipDialog]:
        return self.dialogs.get(call_id)

    def get_by_id(self, call_id: str, local_tag: str, remote_tag: str) -> Optional[SipDialog]:
        return self.by_id.get((call_id, local_tag, remote_tag))

    def establish_fork(
        self, call_id: str, remote_tag: str, record_route: str = "", contact: str = "",
    ) -> SipDialog:
        primary = self._require_dialog(call_id)
        key = call_id, primary.local_tag, remote_tag
        existing = self.by_id.get(key)
        if existing:
            return existing
        fork = SipDialog(
            call_id=call_id,
            local_tag=primary.local_tag,
            remote_tag=remote_tag,
            invite_branch=primary.invite_branch,
            remote_cseq=primary.remote_cseq,
            local_cseq=primary.local_cseq,
            state=primary.state,
        )
        fork.set_remote_route(record_route, contact, reverse=True)
        self.by_id[key] = fork
        return fork

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


def split_header_values(value: str) -> Tuple[str, ...]:
    """Split a SIP comma-list without splitting a URI or quoted display name."""
    result = []
    start = 0
    angle_depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted and char == "<":
            angle_depth += 1
        elif not quoted and char == ">":
            angle_depth = max(0, angle_depth - 1)
        elif not quoted and angle_depth == 0 and char == ",":
            item = value[start:index].strip()
            if item:
                result.append(item)
            start = index + 1
    item = value[start:].strip()
    if item:
        result.append(item)
    return tuple(result)


def extract_name_addr_uri(value: str) -> str:
    match = re.search(r"<\s*(sips?:[^>\s]+)\s*>", value, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.match(r"\s*(sips?:[^\s,]+)", value, re.IGNORECASE)
    return match.group(1) if match else ""


def parse_session_expires(value: str, min_se: int = 90) -> Tuple[int, str]:
    """Parse RFC 4028 Session-Expires and enforce the negotiated floor."""
    parts = [part.strip() for part in value.split(";")]
    if not parts or not parts[0].isdigit() or int(parts[0]) <= 0:
        raise DialogError("Invalid Session-Expires")
    interval = int(parts[0])
    refresher = "uac"
    for part in parts[1:]:
        if part.lower().startswith("refresher="):
            refresher = part.partition("=")[2].lower()
            if refresher not in {"uac", "uas"}:
                raise DialogError("Invalid session refresher")
    if interval < max(90, min_se):
        raise DialogError("Session interval too small")
    return interval, refresher


def in_dialog_route(remote_target: str, route_set: Tuple[str, ...]) -> Tuple[str, Tuple[str, ...], str]:
    """Return Request-URI, Route headers, and next-hop URI (RFC 3261 12.2.1.1)."""
    if not remote_target:
        raise DialogError("Dialog has no remote target")
    if not route_set:
        return remote_target, (), remote_target
    first_uri = extract_name_addr_uri(route_set[0])
    if not first_uri:
        raise DialogError("Invalid first Route URI")
    if re.search(r"(?:^|;)lr(?:[;=?]|$)", first_uri, re.IGNORECASE):
        return remote_target, route_set, first_uri
    return first_uri, (*route_set[1:], f"<{remote_target}>"), first_uri
