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
    COMPLETED = "completed"
    CONFIRMED = "confirmed"
    TERMINATED = "terminated"


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
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    expires_at: Optional[float] = None
    state: TransactionState = TransactionState.TRYING
    cached_response: Optional[CachedResponse] = None
    request_retransmissions: int = 0
    response_retransmissions: int = 0
    retransmit_task: Optional[asyncio.Task] = None

    def cache_response(self, payload: bytes, destination: Address, status: int, timeout: float) -> None:
        self.cached_response = CachedResponse(payload=payload, destination=destination, status=status)
        self.updated_at = time.monotonic()
        if status < 200:
            self.state = TransactionState.PROCEEDING
            return

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


class TransactionManager:
    """Small RFC 3261-inspired UDP server transaction cache.

    Request retransmissions reuse the most recent response. Final INVITE
    responses are also retransmitted on a T1/T2 schedule until ACK or expiry.
    """

    def __init__(
        self,
        send_packet: SendPacket,
        t1: float = 0.5,
        t2: float = 4.0,
        transaction_timeout: float = 32.0,
        schedule_retransmissions: bool = True,
    ) -> None:
        self.send_packet = send_packet
        self.t1 = t1
        self.t2 = t2
        self.transaction_timeout = transaction_timeout
        self.schedule_retransmissions = schedule_retransmissions
        self.transactions: Dict[TransactionKey, ServerTransaction] = {}

    def receive_request(
        self,
        method: str,
        via_header: str,
        cseq_header: str,
        call_id: str,
        source: Address,
    ) -> Tuple[ServerTransaction, bool]:
        self.cleanup_expired()
        key = make_transaction_key(method, via_header, cseq_header, call_id)
        existing = self.transactions.get(key)
        if existing:
            existing.request_retransmissions += 1
            existing.updated_at = time.monotonic()
            if existing.cached_response:
                self.send_packet(existing.cached_response.payload, source)
            return existing, True

        cseq = parse_cseq_number(cseq_header)
        transaction = ServerTransaction(
            key=key,
            kind=TransactionKind.INVITE if method.upper() == "INVITE" else TransactionKind.NON_INVITE,
            method=method.upper(),
            branch_id=extract_branch(via_header),
            cseq=cseq,
            call_id=call_id,
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
        if transaction.kind is TransactionKind.INVITE and status >= 200:
            self._start_invite_retransmissions(transaction)

    def acknowledge_invite(self, call_id: str, cseq_header: str) -> Optional[ServerTransaction]:
        cseq = parse_cseq_number(cseq_header)
        for transaction in self.transactions.values():
            if transaction.kind is TransactionKind.INVITE and transaction.call_id == call_id and transaction.cseq == cseq:
                transaction.confirm(self.t1)
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
