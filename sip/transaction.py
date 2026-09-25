from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional, Tuple

from .dialog import extract_branch, parse_cseq_number


Address = Tuple[str, int]
TransactionKey = Tuple[str, str, str, int]
SendPacket = Callable[[bytes, Address], None]


class TransactionKind(Enum):
    INVITE = "invite"
    NON_INVITE = "non-invite"


class TransactionState(Enum):
    TRYING = "trying"
    PROCEEDING = "proceeding"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    CONFIRMED = "confirmed"
    TERMINATED = "terminated"


class TransactionError(ValueError):
    """Raised when a SIP message cannot be matched to a valid transaction."""


class MergedRequestError(TransactionError):
    """Raised for the same logical request arriving with a different branch."""


@dataclass
class CachedResponse:
    payload: bytes
    destination: Address
    status: int
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class ServerTransaction:
    key: TransactionKey
    kind: TransactionKind
    method: str
    branch_id: str
    cseq: int
    call_id: str
    merge_id: str = ""
    reliable_transport: bool = False
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    expires_at: Optional[float] = None
    state: TransactionState = TransactionState.TRYING
    cached_response: Optional[CachedResponse] = None
    request_retransmissions: int = 0
    response_retransmissions: int = 0
    retransmit_task: Optional[asyncio.Task] = None
    expiry_task: Optional[asyncio.Task] = None

    def cache_response(self, payload: bytes, destination: Address, status: int, timeout: float) -> None:
        self.cached_response = CachedResponse(payload=payload, destination=destination, status=status)
        self.updated_at = time.monotonic()
        if status < 200:
            self.state = TransactionState.PROCEEDING
            return

        if self.kind is TransactionKind.INVITE and status < 300:
            self.state = TransactionState.ACCEPTED
        else:
            self.state = TransactionState.COMPLETED
        self.expires_at = self.updated_at + timeout

    def confirm(self, timeout: float) -> None:
        self.state = TransactionState.CONFIRMED
        self.updated_at = time.monotonic()
        self.expires_at = self.updated_at + timeout
        if self.retransmit_task:
            self.retransmit_task.cancel()
            self.retransmit_task = None

    def terminate(self) -> None:
        self.state = TransactionState.TERMINATED
        if self.retransmit_task:
            self.retransmit_task.cancel()
            self.retransmit_task = None
        if self.expiry_task:
            self.expiry_task.cancel()
        self.expiry_task = None


class TransactionManager:
    """RFC 3261/RFC 6026 server transaction state and retransmission manager.

    Request retransmissions reuse the most recent response. Final INVITE
    non-2xx responses use Timer G on unreliable transports until transaction
    ACK or Timer H. Successful INVITE responses remain in RFC 6026 Accepted
    state and are not consumed by transaction-layer ACK handling.
    """

    def __init__(
        self,
        send_packet: SendPacket,
        t1: float = 0.5,
        t2: float = 4.0,
        t4: float = 5.0,
        transaction_timeout: Optional[float] = None,
        schedule_retransmissions: bool = True,
    ) -> None:
        self.send_packet = send_packet
        self.t1 = t1
        self.t2 = t2
        self.t4 = t4
        self.transaction_timeout = 64 * t1 if transaction_timeout is None else transaction_timeout
        self.schedule_retransmissions = schedule_retransmissions
        self.transactions: Dict[TransactionKey, ServerTransaction] = {}

    def receive_request(
        self,
        method: str,
        via_header: str,
        cseq_header: str,
        call_id: str,
        source: Address,
        transport: Optional[str] = None,
        merge_id: str = "",
    ) -> Tuple[ServerTransaction, bool]:
        self.cleanup_expired()
        method = method.upper()
        cseq, cseq_method = parse_cseq(cseq_header)
        if method != cseq_method:
            raise TransactionError(
                f"Request method {method} does not match CSeq method {cseq_method}"
            )
        key = make_transaction_key(method, via_header, cseq_header, call_id)
        existing = self.transactions.get(key)
        if existing:
            existing.request_retransmissions += 1
            existing.updated_at = time.monotonic()
            if existing.cached_response:
                self.send_packet(existing.cached_response.payload, source)
            return existing, True
        if merge_id and any(
            candidate.merge_id == merge_id and candidate.key != key
            for candidate in self.transactions.values()
        ):
            raise MergedRequestError(f"Merged request detected for {merge_id}")

        resolved_transport = (transport or extract_via_transport(via_header)).upper()
        transaction = ServerTransaction(
            key=key,
            kind=TransactionKind.INVITE if method.upper() == "INVITE" else TransactionKind.NON_INVITE,
            method=method.upper(),
            branch_id=extract_branch(via_header),
            cseq=cseq,
            call_id=call_id,
            merge_id=merge_id,
            reliable_transport=resolved_transport in {"TCP", "TLS", "WS", "WSS"},
        )
        self.transactions[key] = transaction
        return transaction, False

    def cache_response(
        self,
        method: str,
        via_header: str,
        cseq_header: str,
        call_id: str,
        payload: bytes,
        destination: Address,
        status: int,
    ) -> None:
        key = make_transaction_key(method, via_header, cseq_header, call_id)
        transaction = self.transactions.get(key)
        if not transaction:
            transaction, _ = self.receive_request(method, via_header, cseq_header, call_id, destination)

        transaction.cache_response(payload, destination, status, self.transaction_timeout)
        if status < 200:
            return

        if transaction.kind is TransactionKind.NON_INVITE and transaction.reliable_transport:
            self.transactions.pop(transaction.key, None)
            transaction.terminate()
            return

        self._schedule_expiry(transaction, self.transaction_timeout)
        if (
            transaction.kind is TransactionKind.INVITE
            and status >= 300
            and not transaction.reliable_transport
        ):
            self._start_invite_retransmissions(transaction)

    def acknowledge_invite(
        self,
        call_id: str,
        cseq_header: str,
        via_header: str = "",
    ) -> Optional[ServerTransaction]:
        cseq, method = parse_cseq(cseq_header)
        if method != "ACK":
            raise TransactionError(f"Expected ACK CSeq method, received {method}")
        ack_branch = extract_branch(via_header) if via_header else ""
        ack_sent_by = extract_via_sent_by(via_header) if via_header else ""
        for transaction in self.transactions.values():
            if not (
                transaction.kind is TransactionKind.INVITE
                and transaction.call_id == call_id
                and transaction.cseq == cseq
            ):
                continue
            if ack_branch and transaction.branch_id != ack_branch:
                continue
            if ack_sent_by and transaction.key[1] != ack_sent_by:
                continue
            if transaction.state is TransactionState.COMPLETED:
                timer_i = 0.0 if transaction.reliable_transport else self.t4
                transaction.confirm(timer_i)
                if timer_i == 0:
                    self.transactions.pop(transaction.key, None)
                    transaction.terminate()
                else:
                    self._schedule_expiry(transaction, timer_i)
                return transaction
        return None

    def cancellable_invite(
        self, call_id: str, cseq_header: str, via_header: str
    ) -> Optional[ServerTransaction]:
        cseq, method = parse_cseq(cseq_header)
        if method != "CANCEL":
            raise TransactionError(f"Expected CANCEL CSeq method, received {method}")
        branch = extract_branch(via_header)
        sent_by = extract_via_sent_by(via_header)
        for transaction in self.transactions.values():
            if (
                transaction.kind is TransactionKind.INVITE
                and transaction.call_id == call_id
                and transaction.cseq == cseq
                and (not branch or transaction.branch_id == branch)
                and (not sent_by or transaction.key[1] == sent_by)
                and transaction.state in {TransactionState.TRYING, TransactionState.PROCEEDING}
            ):
                return transaction
        return None

    def cleanup_expired(self, now: Optional[float] = None) -> None:
        timestamp = time.monotonic() if now is None else now
        expired = [
            key
            for key, transaction in self.transactions.items()
            if transaction.expires_at is not None and transaction.expires_at <= timestamp
        ]
        for key in expired:
            transaction = self.transactions.pop(key)
            transaction.terminate()

    def close(self) -> None:
        for transaction in self.transactions.values():
            transaction.terminate()
        self.transactions.clear()

    def _start_invite_retransmissions(self, transaction: ServerTransaction) -> None:
        if not self.schedule_retransmissions or transaction.retransmit_task:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        transaction.retransmit_task = loop.create_task(self._retransmit_invite_response(transaction))

    def _schedule_expiry(self, transaction: ServerTransaction, delay: float) -> None:
        if transaction.expiry_task:
            transaction.expiry_task.cancel()
            transaction.expiry_task = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        transaction.expiry_task = loop.create_task(self._expire_transaction(transaction, delay))

    async def _expire_transaction(self, transaction: ServerTransaction, delay: float) -> None:
        try:
            await asyncio.sleep(max(0.0, delay))
            current = self.transactions.get(transaction.key)
            if current is not transaction:
                return
            if transaction.expires_at is not None and transaction.expires_at > time.monotonic():
                self._schedule_expiry(transaction, transaction.expires_at - time.monotonic())
                return
            self.transactions.pop(transaction.key, None)
            transaction.terminate()
        except asyncio.CancelledError:
            return

    async def _retransmit_invite_response(self, transaction: ServerTransaction) -> None:
        interval = self.t1
        try:
            while transaction.state is TransactionState.COMPLETED:
                await asyncio.sleep(interval)
                if transaction.state is not TransactionState.COMPLETED or not transaction.cached_response:
                    return
                if transaction.expires_at is not None and transaction.expires_at <= time.monotonic():
                    self.cleanup_expired()
                    return

                self.send_packet(transaction.cached_response.payload, transaction.cached_response.destination)
                transaction.response_retransmissions += 1
                interval = min(interval * 2, self.t2)
        except asyncio.CancelledError:
            return


def make_transaction_key(method: str, via_header: str, cseq_header: str, call_id: str) -> TransactionKey:
    cseq = parse_cseq_number(cseq_header)
    branch = extract_branch(via_header) or f"legacy:{call_id}"
    sent_by = extract_via_sent_by(via_header) or f"legacy:{call_id}"
    return branch, sent_by, method.upper(), cseq


def extract_via_sent_by(via_header: str) -> str:
    """Return the top Via sent-by value used for RFC 3261 transaction matching."""

    top_via = via_header.split(",", 1)[0].strip()
    match = re.match(r"^SIP/2\.0/\S+\s+([^;,\s]+)", top_via, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def extract_via_transport(via_header: str) -> str:
    """Return the transport token from the top Via header."""

    top_via = via_header.split(",", 1)[0].strip()
    match = re.match(r"^SIP/2\.0/([^\s]+)\s+", top_via, re.IGNORECASE)
    return match.group(1).upper() if match else ""


def parse_cseq(cseq_header: str) -> Tuple[int, str]:
    """Parse and validate the CSeq number and method token."""

    parts = cseq_header.strip().split()
    if len(parts) != 2:
        raise TransactionError(f"Invalid CSeq header {cseq_header!r}")
    try:
        number = int(parts[0])
    except ValueError as exc:
        raise TransactionError(f"Invalid CSeq number {parts[0]!r}") from exc
    if not 0 <= number <= 2**31 - 1:
        raise TransactionError(f"CSeq number {number} is outside the RFC 3261 range")
    method = parts[1].upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.!%*_+`'~-]*", method):
        raise TransactionError(f"Invalid CSeq method {parts[1]!r}")
    return number, method
