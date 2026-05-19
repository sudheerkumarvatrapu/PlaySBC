#!/usr/bin/env python3
"""
Small educational SIP + RTP media server with basic G.711 transcoding.

What it does:
  - Listens for SIP over UDP.
  - Handles REGISTER, OPTIONS, INVITE, ACK, and BYE.
  - Auto-answers calls with SDP.
  - Starts an RTP media session per call.
  - Echoes received RTP audio back to the caller.
  - Can transcode between PCMU (payload type 0) and PCMA (payload type 8)
    when Python's optional audioop module is available.

This is intentionally small and readable. It is useful for local testing and
learning, not for production SIP service.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import random
import re
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

try:
    import audioop  # type: ignore
except Exception:  # pragma: no cover - audioop is unavailable in newer Python builds.
    audioop = None


CRLF = "\r\n"
PCMU = 0
PCMA = 8
SUPPORTED_CODECS = (PCMU, PCMA)


@dataclass
class SipMessage:
    start_line: str
    headers: Dict[str, str]
    body: str
    source: Tuple[str, int]

    @property
    def method(self) -> str:
        return self.start_line.split(" ", 1)[0].upper()

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


@dataclass
class RtpSession:
    call_id: str
    local_ip: str
    local_port: int
    preferred_payload: int = PCMU
    remote_addr: Optional[Tuple[str, int]] = None
    transport: Optional[asyncio.DatagramTransport] = None
    sequence: int = field(default_factory=lambda: random.randint(0, 65535))
    timestamp: int = field(default_factory=lambda: random.randint(0, 2**32 - 1))
    ssrc: int = field(default_factory=lambda: random.randint(1, 2**32 - 1))

    def close(self) -> None:
        if self.transport:
            self.transport.close()


class G711Transcoder:
    """Converts RTP payloads between PCMU and PCMA."""

    def convert(self, payload: bytes, src_pt: int, dst_pt: int) -> bytes:
        if src_pt == dst_pt:
            return payload

        if audioop is None:
            logging.warning(
                "audioop is unavailable; cannot transcode payload type %s to %s",
                src_pt,
                dst_pt,
            )
            return payload

        if src_pt == PCMU and dst_pt == PCMA:
            linear = audioop.ulaw2lin(payload, 2)
            return audioop.lin2alaw(linear, 2)

        if src_pt == PCMA and dst_pt == PCMU:
            linear = audioop.alaw2lin(payload, 2)
            return audioop.lin2ulaw(linear, 2)

        return payload


class RtpProtocol(asyncio.DatagramProtocol):
    def __init__(self, session: RtpSession, transcoder: G711Transcoder):
        self.session = session
        self.transcoder = transcoder

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.session.transport = transport  # type: ignore[assignment]
        logging.info("RTP listening on %s:%s", self.session.local_ip, self.session.local_port)

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        if len(data) < 12:
            return

        self.session.remote_addr = addr
        version = data[0] >> 6
        if version != 2:
            return

        src_payload_type = data[1] & 0x7F
        payload = data[12:]
        out_payload_type = self.session.preferred_payload
        out_payload = self.transcoder.convert(payload, src_payload_type, out_payload_type)

        self.session.sequence = (self.session.sequence + 1) & 0xFFFF
        self.session.timestamp = (self.session.timestamp + len(out_payload)) & 0xFFFFFFFF
        header = struct.pack(
            "!BBHII",
            0x80,
            out_payload_type & 0x7F,
            self.session.sequence,
            self.session.timestamp,
            self.session.ssrc,
        )

        if self.session.transport:
            self.session.transport.sendto(header + out_payload, addr)


class MediaServer:
    def __init__(self, local_ip: str, port_min: int, port_max: int):
        self.local_ip = local_ip
        self.port_min = port_min if port_min % 2 == 0 else port_min + 1
        self.port_max = port_max
        self.sessions: Dict[str, RtpSession] = {}
        self.transcoder = G711Transcoder()
        self._next_port = self.port_min

    async def create_session(self, call_id: str, preferred_payload: int) -> RtpSession:
        if call_id in self.sessions:
            return self.sessions[call_id]

        loop = asyncio.get_running_loop()
        local_port = self._allocate_port()
        session = RtpSession(
            call_id=call_id,
            local_ip=self.local_ip,
            local_port=local_port,
            preferred_payload=preferred_payload,
        )
        await loop.create_datagram_endpoint(
            lambda: RtpProtocol(session, self.transcoder),
            local_addr=(self.local_ip, local_port),
        )
        self.sessions[call_id] = session
        return session

    def close_session(self, call_id: str) -> None:
        session = self.sessions.pop(call_id, None)
        if session:
            session.close()
            logging.info("Closed RTP session for call-id %s", call_id)

    def _allocate_port(self) -> int:
        start = self._next_port
        while True:
            port = self._next_port
            self._next_port += 2
            if self._next_port > self.port_max:
                self._next_port = self.port_min

            if self._port_is_free(port):
                return port

            if self._next_port == start:
                raise RuntimeError("No RTP ports available")

    def _port_is_free(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            try:
                sock.bind((self.local_ip, port))
            except OSError:
                return False
        return True


class SipServerProtocol(asyncio.DatagramProtocol):
    def __init__(self, local_ip: str, local_port: int, media: MediaServer):
        self.local_ip = local_ip
        self.local_port = local_port
        self.media = media
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.registrations: Dict[str, str] = {}

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        logging.info("SIP listening on udp:%s:%s", self.local_ip, self.local_port)

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            text = data.decode("utf-8", errors="replace")
            message = parse_sip_message(text, addr)
        except Exception:
            logging.exception("Could not parse SIP message from %s:%s", *addr)
            return

        logging.info("SIP %s from %s:%s", message.method, *addr)
        asyncio.create_task(self.handle_message(message))

    async def handle_message(self, message: SipMessage) -> None:
        method = message.method

        if method == "REGISTER":
            user = extract_user(message.header("to")) or extract_user(message.header("from")) or "unknown"
            self.registrations[user] = message.header("contact")
            self.send_response(message, 200, "OK")
            logging.info("Registered %s -> %s", user, self.registrations[user])
            return

        if method == "OPTIONS":
            self.send_response(
                message,
                200,
                "OK",
                extra_headers={
                    "Allow": "REGISTER, OPTIONS, INVITE, ACK, BYE",
                    "Accept": "application/sdp",
                },
            )
            return

        if method == "INVITE":
            self.send_response(message, 100, "Trying")
            self.send_response(message, 180, "Ringing")

            remote_payloads = parse_sdp_payloads(message.body)
            preferred_payload = choose_payload(remote_payloads)
            call_id = message.header("call-id", make_call_id())
            rtp = await self.media.create_session(call_id, preferred_payload)
            sdp = make_sdp(self.local_ip, rtp.local_port, preferred_payload)

            self.send_response(
                message,
                200,
                "OK",
                body=sdp,
                extra_headers={
                    "Contact": f"<sip:python-call-server@{self.local_ip}:{self.local_port}>",
                    "Content-Type": "application/sdp",
                },
            )
            return

        if method == "ACK":
            return

        if method == "BYE":
            self.send_response(message, 200, "OK")
            self.media.close_session(message.header("call-id"))
            return

        self.send_response(message, 405, "Method Not Allowed", extra_headers={"Allow": "REGISTER, OPTIONS, INVITE, ACK, BYE"})

    def send_response(
        self,
        request: SipMessage,
        status: int,
        reason: str,
        body: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        if not self.transport:
            return

        headers = {
            "Via": request.header("via"),
            "From": request.header("from"),
            "To": ensure_tag(request.header("to")),
            "Call-ID": request.header("call-id"),
            "CSeq": request.header("cseq"),
            "Server": "mini-python-call-server/0.1",
            "Content-Length": str(len(body.encode("utf-8"))),
        }
        if extra_headers:
            headers.update(extra_headers)

        lines = [f"SIP/2.0 {status} {reason}"]
        lines.extend(f"{name}: {value}" for name, value in headers.items() if value)
        packet = (CRLF.join(lines) + CRLF + CRLF + body).encode("utf-8")
        self.transport.sendto(packet, request.source)


def parse_sip_message(text: str, source: Tuple[str, int]) -> SipMessage:
    head, _, body = text.partition(CRLF + CRLF)
    lines = head.splitlines()
    start_line = lines[0].strip()
    headers: Dict[str, str] = {}

    current_name = ""
    for line in lines[1:]:
        if line.startswith((" ", "\t")) and current_name:
            headers[current_name] += " " + line.strip()
            continue

        name, _, value = line.partition(":")
        current_name = normalize_header_name(name.strip())
        headers[current_name] = value.strip()

    return SipMessage(start_line=start_line, headers=headers, body=body, source=source)


def normalize_header_name(name: str) -> str:
    compact = name.lower()
    return {
        "i": "call-id",
        "f": "from",
        "t": "to",
        "v": "via",
        "m": "contact",
        "l": "content-length",
        "c": "content-type",
    }.get(compact, compact)


def ensure_tag(to_header: str) -> str:
    if "tag=" in to_header.lower():
        return to_header
    return f"{to_header};tag={random.randint(100000, 999999)}"


def extract_user(header_value: str) -> Optional[str]:
    match = re.search(r"sip:([^@;>]+)", header_value)
    return match.group(1) if match else None


def parse_sdp_payloads(sdp: str) -> Tuple[int, ...]:
    match = re.search(r"^m=audio\s+\d+\s+RTP/AVP\s+(.+)$", sdp, re.MULTILINE)
    if not match:
        return SUPPORTED_CODECS

    payloads = []
    for token in match.group(1).split():
        try:
            payloads.append(int(token))
        except ValueError:
            continue
    return tuple(payloads)


def choose_payload(remote_payloads: Tuple[int, ...]) -> int:
    for payload in remote_payloads:
        if payload in SUPPORTED_CODECS:
            return payload
    return PCMU


def make_sdp(local_ip: str, rtp_port: int, payload_type: int) -> str:
    codecs = {
        PCMU: "a=rtpmap:0 PCMU/8000",
        PCMA: "a=rtpmap:8 PCMA/8000",
    }
    return CRLF.join(
        [
            "v=0",
            f"o=mini-call-server {int(time.time())} 1 IN IP4 {local_ip}",
            "s=Mini Python Call Server",
            f"c=IN IP4 {local_ip}",
            "t=0 0",
            f"m=audio {rtp_port} RTP/AVP {payload_type}",
            codecs[payload_type],
            "a=sendrecv",
            "",
        ]
    )


def make_call_id() -> str:
    return hashlib.sha1(str(random.random()).encode("ascii")).hexdigest()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Small SIP/RTP call server")
    parser.add_argument("--ip", default="0.0.0.0", help="IP address to bind and advertise")
    parser.add_argument("--sip-port", type=int, default=5060, help="SIP UDP port")
    parser.add_argument("--rtp-min", type=int, default=10000, help="First RTP UDP port")
    parser.add_argument("--rtp-max", type=int, default=10100, help="Last RTP UDP port")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    media = MediaServer(args.ip, args.rtp_min, args.rtp_max)
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(
        lambda: SipServerProtocol(args.ip, args.sip_port, media),
        local_addr=(args.ip, args.sip_port),
    )

    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
