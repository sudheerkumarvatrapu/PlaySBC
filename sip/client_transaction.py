from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional

from .transaction import (
    Address,
    SendPacket,
    TransactionError,
    TransactionKey,
    TransactionKind,
    extract_via_transport,
    make_transaction_key,
    parse_cseq,
)


class ClientTransactionState(Enum):
    CALLING = "calling"
    TRYING = "trying"
    PROCEEDING = "proceeding"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    TERMINATED = "terminated"


TimeoutHandler = Callable[["ClientTransaction"], None]
FinalHandler = Callable[["ClientTransaction", int], None]
TransportErrorHandler = Callable[["ClientTransaction", Exception], None]


@dataclass
class ClientTransaction:
    key: TransactionKey
    kind: TransactionKind
    method: str
    call_id: str
    via_header: str
    cseq_header: str
    request: bytes
    destination: Address
    reliable_transport: bool
    state: ClientTransactionState
    retransmissions: int = 0
    last_status: Optional[int] = None
    retransmit_task: Optional[asyncio.Task] = field(default=None, repr=False)
    timeout_task: Optional[asyncio.Task] = field(default=None, repr=False)
    cleanup_task: Optional[asyncio.Task] = field(default=None, repr=False)

    def cancel_retransmission(self) -> None:
        if self.retransmit_task:
            self.retransmit_task.cancel()
            self.retransmit_task = None

    def terminate(self) -> None:
        self.state = ClientTransactionState.TERMINATED
        for name in ("retransmit_task", "timeout_task", "cleanup_task"):
            task = getattr(self, name)
            if task:
                task.cancel()
            setattr(self, name, None)


class ClientTransactionManager:
    """Deterministic RFC 3261 client transaction timers and response matching."""

    def __init__(
        self,
        send_packet: SendPacket,
        *,
        t1: float = 0.5,
        t2: float = 4.0,
        t4: float = 5.0,
        timer_b: Optional[float] = None,
        timer_f: Optional[float] = None,
        timer_d: float = 32.0,
        timer_c: float = 180.0,
        on_timeout: Optional[TimeoutHandler] = None,
        on_non_2xx_final: Optional[FinalHandler] = None,
        on_transport_error: Optional[TransportErrorHandler] = None,
    ) -> None:
        self.send_packet = send_packet
        self.t1 = t1
        self.t2 = t2
        self.t4 = t4
        self.timer_b = 64 * t1 if timer_b is None else timer_b
        self.timer_f = 64 * t1 if timer_f is None else timer_f
        self.timer_d = timer_d
        self.timer_c = timer_c
        self.on_timeout = on_timeout
        self.on_non_2xx_final = on_non_2xx_final
        self.on_transport_error = on_transport_error
        self.transactions: Dict[TransactionKey, ClientTransaction] = {}

    def start_request(
        self,
        method: str,
        via_header: str,
        cseq_header: str,
        call_id: str,
        payload: bytes,
        destination: Address,
        *,
        reliable_transport: Optional[bool] = None,
    ) -> ClientTransaction:
        method = method.upper()
        _, cseq_method = parse_cseq(cseq_header)
        if method != cseq_method:
            raise TransactionError(
                f"Request method {method} does not match CSeq method {cseq_method}"
            )
        key = make_transaction_key(method, via_header, cseq_header, call_id)
        if key in self.transactions:
            raise TransactionError(f"Client transaction {key!r} already exists")

        kind = TransactionKind.INVITE if method == "INVITE" else TransactionKind.NON_INVITE
        if reliable_transport is None:
            reliable_transport = extract_via_transport(via_header) in {"TCP", "TLS", "WS", "WSS"}
        transaction = ClientTransaction(
            key=key,
            kind=kind,
            method=method,
            call_id=call_id,
            via_header=via_header,
            cseq_header=cseq_header,
            request=payload,
            destination=destination,
            reliable_transport=reliable_transport,
            state=(
                ClientTransactionState.CALLING
                if kind is TransactionKind.INVITE
                else ClientTransactionState.TRYING
            ),
        )
        self.transactions[key] = transaction
        self.send_packet(payload, destination)
        self._start_timers(transaction)
        return transaction

    def receive_response(
        self,
        status: int,
        via_header: str,
        cseq_header: str,
        call_id: str,
    ) -> Optional[ClientTransaction]:
        _, method = parse_cseq(cseq_header)
        key = make_transaction_key(method, via_header, cseq_header, call_id)
        transaction = self.transactions.get(key)
        if not transaction:
            return None
        if not 100 <= status <= 699:
            raise TransactionError(f"Invalid SIP response status {status}")
        if transaction.state in {
            ClientTransactionState.ACCEPTED,
            ClientTransactionState.COMPLETED,
        }:
            transaction.last_status = status
            return transaction

        transaction.last_status = status
        if status < 200:
            transaction.state = ClientTransactionState.PROCEEDING
            if transaction.kind is TransactionKind.INVITE:
                transaction.cancel_retransmission()
                if transaction.timeout_task:
                    transaction.timeout_task.cancel()
                transaction.timeout_task = asyncio.get_running_loop().create_task(
                    self._timeout(transaction, self.timer_c, "timer-c")
                )
            return transaction

        transaction.cancel_retransmission()
        if transaction.timeout_task:
            transaction.timeout_task.cancel()
            transaction.timeout_task = None

        if transaction.kind is TransactionKind.INVITE and status < 300:
            transaction.state = ClientTransactionState.ACCEPTED
            self._schedule_cleanup(transaction, 64 * self.t1)
        else:
            transaction.state = ClientTransactionState.COMPLETED
            if transaction.kind is TransactionKind.INVITE and self.on_non_2xx_final:
                self.on_non_2xx_final(transaction, status)
            delay = (
                self.timer_d
                if transaction.kind is TransactionKind.INVITE
                else self.t4
            )
            if transaction.reliable_transport:
                delay = 0.0
            self._schedule_cleanup(transaction, delay)
        return transaction

    def close(self) -> None:
        for transaction in self.transactions.values():
            transaction.terminate()
        self.transactions.clear()

    def transport_error(self, destination: Address, error: Exception) -> list[ClientTransaction]:
        """Terminate active transactions for a failed transport destination."""
        failed = [
            transaction for transaction in self.transactions.values()
            if transaction.destination == destination
            and transaction.state not in {ClientTransactionState.COMPLETED, ClientTransactionState.ACCEPTED}
        ]
        for transaction in failed:
            self.transactions.pop(transaction.key, None)
            transaction.terminate()
            if self.on_transport_error:
                self.on_transport_error(transaction, error)
        return failed

    def _start_timers(self, transaction: ClientTransaction) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            self.transactions.pop(transaction.key, None)
            raise RuntimeError("Client transactions require a running event loop") from exc

        timeout = self.timer_b if transaction.kind is TransactionKind.INVITE else self.timer_f
        transaction.timeout_task = loop.create_task(self._timeout(transaction, timeout, "timer-b" if transaction.kind is TransactionKind.INVITE else "timer-f"))
        if not transaction.reliable_transport:
            transaction.retransmit_task = loop.create_task(self._retransmit(transaction))

    async def _retransmit(self, transaction: ClientTransaction) -> None:
        interval = self.t1
        try:
            while transaction.state in {
                ClientTransactionState.CALLING,
                ClientTransactionState.TRYING,
                ClientTransactionState.PROCEEDING,
            }:
                await asyncio.sleep(interval)
                if transaction.state not in {
                    ClientTransactionState.CALLING,
                    ClientTransactionState.TRYING,
                    ClientTransactionState.PROCEEDING,
                }:
                    return
                self.send_packet(transaction.request, transaction.destination)
                transaction.retransmissions += 1
                if transaction.kind is TransactionKind.INVITE:
                    interval *= 2
                else:
                    interval = min(interval * 2, self.t2)
                    if transaction.state is ClientTransactionState.PROCEEDING:
                        interval = self.t2
        except asyncio.CancelledError:
            return

    async def _timeout(self, transaction: ClientTransaction, delay: float, _timer: str) -> None:
        try:
            await asyncio.sleep(delay)
            if self.transactions.get(transaction.key) is not transaction:
                return
            self.transactions.pop(transaction.key, None)
            transaction.terminate()
            if self.on_timeout:
                self.on_timeout(transaction)
        except asyncio.CancelledError:
            return

    def _schedule_cleanup(self, transaction: ClientTransaction, delay: float) -> None:
        if delay <= 0:
            self.transactions.pop(transaction.key, None)
            transaction.terminate()
            return
        loop = asyncio.get_running_loop()
        transaction.cleanup_task = loop.create_task(self._cleanup(transaction, delay))

    async def _cleanup(self, transaction: ClientTransaction, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            if self.transactions.get(transaction.key) is transaction:
                self.transactions.pop(transaction.key, None)
                transaction.terminate()
        except asyncio.CancelledError:
            return
