"""Deterministic state and policy for RFC 5359 business calling services."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional, Sequence, Tuple, Union
from urllib.parse import parse_qs, unquote


class BusinessServiceError(ValueError):
    pass


class ConsultationState(Enum):
    ACTIVE = "active"
    ORIGINAL_HELD = "original-held"
    CONSULTING = "consulting"
    RESUMING = "resuming"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    RECOVERED = "recovered"
    TERMINATED = "terminated"
    FAILED = "failed"


@dataclass
class ConsultationHold:
    """Tracks the original and consultation dialogs without conflating their state."""

    original_call_id: str
    state: ConsultationState = ConsultationState.ACTIVE
    consultation_call_id: str = ""
    target: str = ""
    failure_reason: str = ""
    termination_reason: str = ""

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
        if self.state is ConsultationState.RESUMING:
            self.state = ConsultationState.COMPLETED
            return
        if self.state is ConsultationState.RECOVERING:
            self.state = ConsultationState.RECOVERED
            return
        self._require(ConsultationState.RESUMING)

    def fail_consultation(self, reason: str) -> None:
        """Record a failed consultation while keeping the original call recoverable."""
        self._require(ConsultationState.CONSULTING)
        self.failure_reason = reason or "unspecified consultation failure"
        self.state = ConsultationState.RECOVERING

    def terminate_original(self, reason: str) -> None:
        """Resolve an original-dialog teardown racing with consultation progress."""
        if self.state is ConsultationState.TERMINATED:
            return
        if self.state in {
            ConsultationState.COMPLETED,
            ConsultationState.RECOVERED,
            ConsultationState.FAILED,
        }:
            raise BusinessServiceError(f"consultation hold is already {self.state.value}")
        self.termination_reason = reason or "original dialog terminated"
        self.state = ConsultationState.TERMINATED

    def fail(self, reason: str) -> None:
        if self.state in {
            ConsultationState.COMPLETED,
            ConsultationState.RECOVERED,
            ConsultationState.TERMINATED,
            ConsultationState.FAILED,
        }:
            raise BusinessServiceError(f"consultation hold is already {self.state.value}")
        self.failure_reason = reason or "unspecified"
        self.state = ConsultationState.FAILED

    @property
    def original_dialog_preserved(self) -> bool:
        return self.state not in {ConsultationState.ACTIVE, ConsultationState.TERMINATED} and bool(
            self.original_call_id
        )

    @property
    def consultation_dialog_active(self) -> bool:
        return self.state is ConsultationState.CONSULTING and bool(self.consultation_call_id)

    def _require(self, expected: ConsultationState) -> None:
        if self.state is not expected:
            raise BusinessServiceError(
                f"consultation hold is {self.state.value}; expected {expected.value}"
            )


class HoldState(Enum):
    ACTIVE = "active"
    HELD = "held"
    MUSIC_ACTIVE = "music-active"
    RESUMING = "resuming"
    RESUMED = "resumed"
    TERMINATED = "terminated"


@dataclass
class MusicOnHold:
    """Tracks hold direction and the bounded lifetime of a music source."""

    call_id: str
    state: HoldState = HoldState.ACTIVE
    direction: str = "sendonly"
    source: str = ""

    def hold(self, direction: str = "sendonly") -> None:
        self._require(HoldState.ACTIVE)
        normalized = direction.strip().lower()
        if normalized not in {"sendonly", "recvonly", "inactive"}:
            raise BusinessServiceError(f"unsupported hold direction {direction!r}")
        self.direction = normalized
        self.state = HoldState.HELD

    def start_music(self, source: str) -> None:
        self._require(HoldState.HELD)
        if not source.strip():
            raise BusinessServiceError("music-on-hold source must not be empty")
        self.source = source.strip()
        self.state = HoldState.MUSIC_ACTIVE

    def stop_music(self) -> None:
        self._require(HoldState.MUSIC_ACTIVE)
        self.state = HoldState.RESUMING

    def resume(self) -> None:
        if self.state is HoldState.HELD:
            self.state = HoldState.RESUMED
            return
        self._require(HoldState.RESUMING)
        self.state = HoldState.RESUMED

    def terminate(self) -> None:
        self.state = HoldState.TERMINATED

    def _require(self, expected: HoldState) -> None:
        if self.state is not expected:
            raise BusinessServiceError(
                f"music-on-hold is {self.state.value}; expected {expected.value}"
            )


class TransferKind(Enum):
    UNATTENDED = "unattended"
    ATTENDED = "attended"


class TransferState(Enum):
    ACTIVE = "active"
    REQUESTED = "requested"
    ACCEPTED = "accepted"
    PROGRESS = "progress"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERED = "recovered"
    TERMINATED = "terminated"


@dataclass(frozen=True)
class TransferTarget:
    uri: str
    replaces_call_id: str = ""
    replaces_to_tag: str = ""
    replaces_from_tag: str = ""

    @property
    def kind(self) -> TransferKind:
        return TransferKind.ATTENDED if self.replaces_call_id else TransferKind.UNATTENDED


def parse_refer_to(value: str) -> TransferTarget:
    """Parse Refer-To and the RFC 3891 Replaces query used by attended transfer."""
    raw = value.strip()
    if not raw:
        raise BusinessServiceError("Refer-To header must not be empty")
    if raw.startswith("<") and ">" in raw:
        raw = raw[1 : raw.index(">")]
    decoded = unquote(raw)
    uri, separator, query = decoded.partition("?")
    if not uri.lower().startswith(("sip:", "sips:")) or "@" not in uri:
        raise BusinessServiceError("Refer-To must contain a SIP URI with a user and host")

    replaces_value = ""
    if separator:
        query_values = parse_qs(query, keep_blank_values=True)
        for name, values in query_values.items():
            if name.lower() == "replaces" and values:
                replaces_value = values[0]
                break
    if not replaces_value:
        return TransferTarget(uri=uri)

    replaces_parts = [part.strip() for part in replaces_value.split(";") if part.strip()]
    call_id = replaces_parts[0] if replaces_parts else ""
    parameters = {}
    for part in replaces_parts[1:]:
        name, _, parameter_value = part.partition("=")
        parameters[name.strip().lower()] = parameter_value.strip()
    if not call_id or not parameters.get("to-tag") or not parameters.get("from-tag"):
        raise BusinessServiceError("attended transfer Replaces requires call ID, to-tag, and from-tag")
    return TransferTarget(
        uri=uri,
        replaces_call_id=call_id,
        replaces_to_tag=parameters["to-tag"],
        replaces_from_tag=parameters["from-tag"],
    )


@dataclass
class CallTransfer:
    """Models REFER acceptance, sipfrag NOTIFY progress, and recovery."""

    original_call_id: str
    state: TransferState = TransferState.ACTIVE
    target: Optional[TransferTarget] = None
    notify_statuses: list[int] = field(default_factory=list)
    failure_reason: str = ""

    def request(self, refer_to: str) -> TransferTarget:
        self._require(TransferState.ACTIVE)
        target = parse_refer_to(refer_to)
        if target.replaces_call_id == self.original_call_id:
            raise BusinessServiceError("attended transfer cannot replace its own original dialog")
        self.target = target
        self.state = TransferState.REQUESTED
        return target

    def accept(self) -> None:
        self._require(TransferState.REQUESTED)
        self.state = TransferState.ACCEPTED

    def notify(self, status: int, reason: str = "") -> None:
        if self.state not in {TransferState.ACCEPTED, TransferState.PROGRESS}:
            raise BusinessServiceError(
                f"transfer is {self.state.value}; expected accepted or progress"
            )
        if status < 100 or status > 699:
            raise BusinessServiceError(f"invalid sipfrag status {status}")
        self.notify_statuses.append(status)
        if status < 200:
            self.state = TransferState.PROGRESS
        elif status < 300:
            self.state = TransferState.COMPLETED
        else:
            self.failure_reason = reason or f"SIP {status}"
            self.state = TransferState.FAILED

    def recover_original(self) -> None:
        self._require(TransferState.FAILED)
        self.state = TransferState.RECOVERED

    def terminate(self) -> None:
        self.state = TransferState.TERMINATED

    @property
    def kind(self) -> TransferKind:
        if self.target is None:
            raise BusinessServiceError("transfer target has not been requested")
        return self.target.kind

    def _require(self, expected: TransferState) -> None:
        if self.state is not expected:
            raise BusinessServiceError(f"transfer is {self.state.value}; expected {expected.value}")


class ForwardCondition(Enum):
    UNCONDITIONAL = "unconditional"
    BUSY = "busy"
    NO_ANSWER = "no-answer"


@dataclass(frozen=True)
class ForwardingRule:
    name: str
    match: str
    target: str
    condition: ForwardCondition = ForwardCondition.UNCONDITIONAL
    priority: int = 100
    enabled: bool = True

    @classmethod
    def from_config(cls, value: dict) -> "ForwardingRule":
        try:
            condition = ForwardCondition(str(value.get("condition", "unconditional")).lower())
        except ValueError as exc:
            raise BusinessServiceError(
                "forwarding condition must be unconditional, busy, or no-answer"
            ) from exc
        return cls(
            name=str(value.get("name") or value.get("match") or "forwarding-rule"),
            match=str(value.get("match", "*")),
            target=str(value.get("target", "")),
            condition=condition,
            priority=int(value.get("priority", 100)),
            enabled=bool(value.get("enabled", True)),
        )


@dataclass(frozen=True)
class ForwardingDecision:
    target: str
    condition: ForwardCondition
    rule_name: str
    history: Tuple[str, ...]


class CallForwarding:
    """Selects forwarding targets with deterministic loop and hop protection."""

    BUSY_STATUSES = frozenset({486, 600, 603})

    def __init__(self, rules: Iterable[Union[ForwardingRule, dict]] = (), max_hops: int = 5):
        if max_hops < 1:
            raise BusinessServiceError("forwarding max_hops must be at least 1")
        self.max_hops = max_hops
        self.rules = tuple(
            sorted(
                (
                    rule if isinstance(rule, ForwardingRule) else ForwardingRule.from_config(rule)
                    for rule in rules
                ),
                key=lambda rule: (rule.priority, rule.name),
            )
        )
        for rule in self.rules:
            if not rule.name or not rule.match or not rule.target:
                raise BusinessServiceError("forwarding rules require name, match, and target")

    def select(
        self,
        user: str,
        *,
        status: int = 0,
        timed_out: bool = False,
        history: Sequence[str] = (),
    ) -> Optional[ForwardingDecision]:
        condition = self._condition(status, timed_out)
        visited = tuple(str(value) for value in history) + (user,)
        if len(visited) > self.max_hops:
            raise BusinessServiceError("forwarding hop limit exceeded")
        for rule in self.rules:
            if not rule.enabled or rule.condition is not condition:
                continue
            if not fnmatch.fnmatchcase(user, rule.match):
                continue
            target_user = _forward_target_user(rule.target)
            if target_user in visited or rule.target in visited:
                raise BusinessServiceError(
                    f"forwarding loop detected for {user!r} via {rule.target!r}"
                )
            return ForwardingDecision(rule.target, condition, rule.name, visited)
        return None

    @classmethod
    def _condition(cls, status: int, timed_out: bool) -> ForwardCondition:
        if timed_out:
            return ForwardCondition.NO_ANSWER
        if status in cls.BUSY_STATUSES:
            return ForwardCondition.BUSY
        return ForwardCondition.UNCONDITIONAL


def _forward_target_user(target: str) -> str:
    match = target.strip()
    if match.lower().startswith(("sip:", "sips:")):
        match = match.split(":", 1)[1]
        return match.split("@", 1)[0].split(";", 1)[0]
    return match
