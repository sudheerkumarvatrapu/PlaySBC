#!/usr/bin/env python3
"""
PlaySBC educational SIP + RTP media server with basic G.711 transcoding.

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
import fnmatch
import hashlib
import json
import logging
import random
import re
import secrets
import socket
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rtp.analyzer import RtpAnalyzer
from rtp.packet import RtpPacket
from rtp.rtpengine import RtpengineClient, RtpengineError, parse_rtpengine_url
from sip.dialog import DialogError, DialogManager
from sip.transaction import TransactionManager

try:
    import audioop  # type: ignore
except Exception:  # pragma: no cover - audioop is unavailable in newer Python builds.
    audioop = None


CRLF = "\r\n"
PCMU = 0
PCMA = 8
SUPPORTED_CODECS = (PCMU, PCMA)
CODEC_NAMES = {
    PCMU: "PCMU",
    PCMA: "PCMA",
}
CODEC_PAYLOADS = {
    "PCMU": PCMU,
    "PCMA": PCMA,
}


@dataclass
class ServerConfig:
    sip_ip: str = "0.0.0.0"
    sip_port: int = 5060
    rtp_min: int = 10000
    rtp_max: int = 10100
    log_dir: str = ""
    default_codec: str = "PCMU"
    auth_realm: str = "playsbc"
    users: Dict[str, str] = field(default_factory=dict)
    bridge_rooms: Tuple[str, ...] = ("bridge",)
    b2bua_routes: Dict[str, str] = field(default_factory=dict)
    route_policies: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    b2bua_ladder_logs: bool = True
    media_backend: str = "internal"
    rtpengine_url: str = "udp://127.0.0.1:2223"
    rtpengine_timeout: float = 3.0
    debug: bool = False

    @property
    def default_payload(self) -> int:
        return codec_payload(self.default_codec)


SERVER_CONFIG_KEYS = {
    "sip_ip",
    "sip_port",
    "rtp_min",
    "rtp_max",
    "log_dir",
    "default_codec",
    "auth_realm",
    "users",
    "bridge_rooms",
    "b2bua_routes",
    "route_policies",
    "b2bua_ladder_logs",
    "media_backend",
    "rtpengine_url",
    "rtpengine_timeout",
    "debug",
}

MEDIA_BACKENDS = {"internal", "rtpengine"}

DTMF_EVENTS = {
    0: "0",
    1: "1",
    2: "2",
    3: "3",
    4: "4",
    5: "5",
    6: "6",
    7: "7",
    8: "8",
    9: "9",
    10: "*",
    11: "#",
    12: "A",
    13: "B",
    14: "C",
    15: "D",
}


@dataclass
class SipMessage:
    start_line: str
    headers: Dict[str, str]
    body: str
    source: Tuple[str, int]

    @property
    def method(self) -> str:
        return self.start_line.split(" ", 1)[0].upper()

    @property
    def is_response(self) -> bool:
        return self.start_line.upper().startswith("SIP/2.0 ")

    @property
    def status_code(self) -> int:
        if not self.is_response:
            return 0
        parts = self.start_line.split(" ", 2)
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

    @property
    def reason_phrase(self) -> str:
        if not self.is_response:
            return ""
        parts = self.start_line.split(" ", 2)
        return parts[2] if len(parts) > 2 else ""

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


@dataclass
class SipUri:
    user: str
    host: str
    port: int = 5060

    @property
    def uri(self) -> str:
        return f"sip:{self.user}@{self.host}:{self.port}"

    @property
    def address(self) -> Tuple[str, int]:
        return self.host, self.port


@dataclass
class Registration:
    user: str
    contact_uri: str
    source: Tuple[str, int]
    expires_at: float
    registered_at: float = field(default_factory=time.time)

    def is_expired(self, now: Optional[float] = None) -> bool:
        timestamp = time.time() if now is None else now
        return self.expires_at <= timestamp

    @property
    def target(self) -> SipUri:
        return parse_sip_uri(self.contact_uri)


@dataclass
class RoutePolicy:
    name: str
    match: str = "*"
    target: str = "registration"
    priority: int = 100
    enabled: bool = True

    @classmethod
    def from_config(cls, value: Dict[str, Any]) -> "RoutePolicy":
        return cls(
            name=str(value.get("name") or value.get("match") or "route-policy"),
            match=str(value.get("match", "*")),
            target=str(value.get("target", "registration")),
            priority=int(value.get("priority", 100)),
            enabled=bool(value.get("enabled", True)),
        )

    def matches(self, user: str) -> bool:
        return self.enabled and fnmatch.fnmatchcase(user, self.match)


@dataclass
class RouteResult:
    target: SipUri
    policy_name: str
    source: str


class RoutingEngine:
    """Resolve dialed users to outbound SIP targets.

    Policies are intentionally small and readable:
      - target="registration" uses the in-memory registrar location service.
      - target="sip:{user}@host:port" creates a static SIP target from a template.

    The legacy b2bua_routes map is still accepted as an exact static fallback.
    """

    REGISTRATION_TARGETS = {"registration", "registrar", "location"}

    def __init__(self, policies: Tuple[Dict[str, Any], ...], static_routes: Dict[str, str]):
        self.policies = sorted(
            (RoutePolicy.from_config(policy) for policy in policies),
            key=lambda policy: (policy.priority, policy.name),
        )
        self.static_routes = static_routes

    def resolve(self, user: str, registrations: Dict[str, Registration]) -> Optional[RouteResult]:
        now = time.time()
        for policy in self.policies:
            if not policy.matches(user):
                continue

            target = policy.target.strip()
            if target.lower() in self.REGISTRATION_TARGETS:
                registration = registrations.get(user)
                if registration and not registration.is_expired(now):
                    return RouteResult(registration.target, policy.name, "registrar")
                continue

            return RouteResult(parse_sip_uri(format_route_target(target, user)), policy.name, "policy")

        static_route = self.static_routes.get(user)
        if static_route:
            return RouteResult(parse_sip_uri(format_route_target(static_route, user)), "b2bua_routes", "static")

        return None


class SbcLogger:
    CATEGORY_FILES = {
        "sip": "log.sip",
        "media": "log.media",
        "transcoding": "log.transcoding",
        "platform": "log.platform",
        "networking": "log.networking",
        "udp": "log.udp",
        "tcp": "log.tcp",
        "tls": "log.tls",
        "call": "log.call",
        "sipp": "log.sipp",
    }

    def __init__(self, log_dir: Optional[Path]):
        self.log_dir = log_dir
        self.enabled = log_dir is not None
        self.paths = {category: log_dir / filename for category, filename in self.CATEGORY_FILES.items()} if log_dir else {}
        if not self.enabled:
            return
        assert log_dir is not None
        log_dir.mkdir(parents=True, exist_ok=True)
        for category, path in self.paths.items():
            path.write_text("", encoding="utf-8")
            self.write(category, "LOG START", f"file={path.name}")

    def write(self, category: str, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        if not self.enabled:
            return
        category = category.lower()
        path = self.paths.get(category) or self.paths["platform"]
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        parts = [timestamp, event]
        if call_id:
            parts.append(f"call_id={call_id}")
        if leg:
            parts.append(f"leg={leg}")
        if detail:
            parts.append(detail)
        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(" | ".join(parts) + "\n")

    def sip(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("sip", event, detail, call_id=call_id, leg=leg)

    def media(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("media", event, detail, call_id=call_id, leg=leg)

    def transcoding(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("transcoding", event, detail, call_id=call_id, leg=leg)

    def platform(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("platform", event, detail, call_id=call_id, leg=leg)

    def networking(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("networking", event, detail, call_id=call_id, leg=leg)

    def udp(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("udp", event, detail, call_id=call_id, leg=leg)

    def write_block(self, category: str, title: str, block: str, call_id: str = "") -> None:
        if not self.enabled:
            return
        category = category.lower()
        path = self.paths.get(category) or self.paths["platform"]
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        header = f"{timestamp} | {title}"
        if call_id:
            header += f" | call_id={call_id}"
        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(header + "\n")
            log_file.write(block.rstrip() + "\n")


class B2BUAFlowLog:
    LADDER_PARTICIPANTS = ("SIPp A", "B2BUA", "SIPp B")
    LADDER_STEP_WIDTH = 6
    LADDER_COLUMN_WIDTH = 28

    def __init__(
        self,
        log_dir: Optional[Path],
        inbound_call_id: str,
        target_user: str,
        route: RouteResult,
        enabled: bool = True,
        logger: Optional[SbcLogger] = None,
    ):
        self.enabled = enabled
        self.logger = logger
        self.inbound_call_id = inbound_call_id
        self.events: List[Tuple[str, str, str, str]] = []
        self.path = log_dir / "log.call" if enabled and log_dir else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write(
            "CALL START",
            (
                f"inbound_call_id={inbound_call_id} target_user={target_user} "
                f"route={route.target.uri} route_source={route.source} route_policy={route.policy_name}"
            ),
        )

    def write(self, event: str, detail: str = "") -> None:
        if not self.path:
            return
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        line = f"{timestamp} {event}"
        if detail:
            line += f" {detail}"
        with self.path.open("a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
        if self.logger and event != "SIP FLOW":
            self.logger.write(
                log_category_for_flow_event(event),
                f"B2BUA {event}",
                detail,
                call_id=self.inbound_call_id,
            )

    def sip(self, sender: str, receiver: str, message: str, detail: str = "") -> None:
        if not self.enabled:
            return
        self.events.append((sender, receiver, message, detail))
        suffix = f" {detail}" if detail else ""
        self.write("SIP FLOW", f"{sender} -> {receiver}: {message}{suffix}")
        if self.logger:
            self.logger.sip(
                "B2BUA SIP FLOW",
                f"{sender} -> {receiver}: {message}{suffix}",
                call_id=self.inbound_call_id,
                leg=f"{sender}->{receiver}",
            )

    def render_ladder(self) -> None:
        if not self.path:
            return
        ladder = self.render_ladder_text()
        with self.path.open("a", encoding="utf-8") as log_file:
            log_file.write("\n" + ladder + "\n")
        if self.logger:
            self.logger.write_block("sip", "B2BUA SIP LADDER", ladder, call_id=self.inbound_call_id)

    def render_ladder_text(self) -> str:
        lines = [
            "SIP LADDER",
            self._ladder_header(),
            self._ladder_separator(),
            self._ladder_lifeline(),
        ]
        for index, (sender, receiver, message, detail) in enumerate(self.events, start=1):
            lines.extend(self._ladder_event(index, sender, receiver, message))
        lines.append(self._ladder_lifeline())
        return "\n".join(lines)

    def _ladder_header(self) -> str:
        columns = [f"{participant:^{self.LADDER_COLUMN_WIDTH}}" for participant in self.LADDER_PARTICIPANTS]
        return f"{'Step':<{self.LADDER_STEP_WIDTH}}" + "".join(columns).rstrip()

    def _ladder_separator(self) -> str:
        return "-" * (self.LADDER_STEP_WIDTH + (self.LADDER_COLUMN_WIDTH * len(self.LADDER_PARTICIPANTS)))

    def _ladder_lifeline(self, step: str = "") -> str:
        row = self._blank_ladder_row(step)
        for position in self._ladder_positions():
            row[position] = "|"
        return "".join(row).rstrip()

    def _ladder_event(self, index: int, sender: str, receiver: str, label: str) -> List[str]:
        if sender not in self.LADDER_PARTICIPANTS or receiver not in self.LADDER_PARTICIPANTS:
            return [f"{index:02d} {sender} -> {receiver}: {label}"]

        sender_index = self.LADDER_PARTICIPANTS.index(sender)
        receiver_index = self.LADDER_PARTICIPANTS.index(receiver)
        if abs(sender_index - receiver_index) != 1:
            return [f"{index:02d} {sender} -> {receiver}: {label}"]

        return [
            self._ladder_label_line(index, sender_index, receiver_index, label),
            self._ladder_arrow_line(sender_index, receiver_index),
        ]

    def _ladder_label_line(self, index: int, sender_index: int, receiver_index: int, label: str) -> str:
        row = self._blank_ladder_row(f"{index:02d}")
        positions = self._ladder_positions()
        for position in positions:
            row[position] = "|"
        left = min(positions[sender_index], positions[receiver_index])
        right = max(positions[sender_index], positions[receiver_index])
        text_start = left + 2
        text_width = max(1, right - left - 3)
        text = self._short_label(label, text_width)
        label_start = text_start + max(0, (text_width - len(text)) // 2)
        self._put_text(row, label_start, text)
        return "".join(row).rstrip()

    def _ladder_arrow_line(self, sender_index: int, receiver_index: int) -> str:
        row = self._blank_ladder_row("")
        positions = self._ladder_positions()
        for position in positions:
            row[position] = "|"

        sender = positions[sender_index]
        receiver = positions[receiver_index]
        if sender < receiver:
            for position in range(sender + 1, receiver - 1):
                row[position] = "-"
            row[receiver - 1] = ">"
        else:
            row[receiver + 1] = "<"
            for position in range(receiver + 2, sender):
                row[position] = "-"
        return "".join(row).rstrip()

    def _blank_ladder_row(self, step: str) -> List[str]:
        width = self.LADDER_STEP_WIDTH + (self.LADDER_COLUMN_WIDTH * len(self.LADDER_PARTICIPANTS))
        row = list(" " * width)
        self._put_text(row, 0, f"{step:<{self.LADDER_STEP_WIDTH}}")
        return row

    def _ladder_positions(self) -> List[int]:
        return [
            self.LADDER_STEP_WIDTH + (index * self.LADDER_COLUMN_WIDTH) + (self.LADDER_COLUMN_WIDTH // 2)
            for index, _ in enumerate(self.LADDER_PARTICIPANTS)
        ]

    def _put_text(self, row: List[str], start: int, text: str) -> None:
        for offset, character in enumerate(text):
            position = start + offset
            if 0 <= position < len(row):
                row[position] = character

    def _short_label(self, label: str, limit: int = 14) -> str:
        cleaned = " ".join(label.split())
        return cleaned[:limit]


@dataclass
class B2BUACall:
    inbound_call_id: str
    outbound_call_id: str
    outbound_target: SipUri
    outbound_from_header: str
    target_user: str
    route_policy: str
    route_source: str
    flow_log: B2BUAFlowLog
    media_backend: str = "internal"
    rtpengine_call_id: str = ""
    rtpengine_from_tag: str = ""
    rtpengine_to_tag: str = ""
    outbound_to_header: str = ""
    outbound_contact_uri: str = ""
    outbound_cseq: int = 1
    outbound_bye_sent: bool = False
    finalized: bool = False


@dataclass
class RtpSession:
    call_id: str
    local_ip: str
    local_port: int
    preferred_payload: int = PCMU
    remote_payloads: Tuple[int, ...] = field(default_factory=tuple)
    dtmf_payload_type: Optional[int] = None
    logger: Optional[SbcLogger] = None
    leg_label: str = ""
    remote_addr: Optional[Tuple[str, int]] = None
    transport: Optional[asyncio.DatagramTransport] = None
    sequence: int = field(default_factory=lambda: random.randint(0, 65535))
    timestamp: int = field(default_factory=lambda: random.randint(0, 2**32 - 1))
    ssrc: int = field(default_factory=lambda: random.randint(1, 2**32 - 1))
    created_at: float = field(default_factory=time.time)
    acknowledged_at: Optional[float] = None
    last_rtp_at: Optional[float] = None
    packets_received: int = 0
    packets_sent: int = 0
    bytes_received: int = 0
    bytes_sent: int = 0
    payload_types_received: Dict[int, int] = field(default_factory=dict)
    dtmf_events: list = field(default_factory=list)
    dtmf_events_started: set = field(default_factory=set)
    dtmf_events_completed: set = field(default_factory=set)
    analyzer: RtpAnalyzer = field(default_factory=RtpAnalyzer)
    media_mode: str = "echo"
    bridge_id: str = ""
    peer_session: Optional["RtpSession"] = None
    relayed_packets: int = 0
    relayed_bytes: int = 0
    relay_wait_logged: bool = False
    closed: bool = False

    def log(self, event: str, detail: str = "", category: Optional[str] = None) -> None:
        if self.logger:
            self.logger.write(
                category or log_category_for_session_event(event),
                event,
                detail,
                call_id=self.call_id,
                leg=self.leg_label or self.media_mode,
            )

    def mark_ack(self) -> None:
        self.acknowledged_at = time.time()
        self.log("ACK RECEIVED")

    def record_rtp_packet(self, packet: RtpPacket, record_audio: bool = True) -> None:
        packet_index = self.packets_received + 1
        if packet_index <= 5 or packet_index % 500 == 0:
            self.log(
                "RTP PACKET RX",
                (
                    f"count={packet_index} seq={packet.sequence} timestamp={packet.timestamp} "
                    f"payload_type={CODEC_NAMES.get(packet.payload_type, packet.payload_type)} "
                    f"marker={int(packet.marker)} payload_bytes={len(packet.payload)}"
                ),
            )
        self.analyzer.observe(packet, record_audio=record_audio)
        self.record_rtp(packet.payload_type, packet.payload, record_audio=record_audio)

    def record_rtp(self, payload_type: int, payload: bytes, record_audio: bool = True) -> None:
        self.packets_received += 1
        self.bytes_received += len(payload)
        self.payload_types_received[payload_type] = self.payload_types_received.get(payload_type, 0) + 1
        self.last_rtp_at = time.time()

    def record_rtp_sent(self, payload: bytes) -> None:
        self.packets_sent += 1
        self.bytes_sent += len(payload)
        self.last_rtp_at = time.time()

    def set_peer(self, peer: "RtpSession") -> None:
        self.peer_session = peer
        event = "B2BUA PAIRED" if self.media_mode == "b2bua" else "BRIDGE PAIRED"
        detail = f"peer_call_id={peer.call_id}"
        if self.bridge_id:
            detail = f"bridge_id={self.bridge_id} {detail}"
        self.log(event, detail)

    def clear_peer(self) -> None:
        if self.peer_session:
            event = "B2BUA PEER LEFT" if self.media_mode == "b2bua" else "BRIDGE PEER LEFT"
            self.log(event, f"peer_call_id={self.peer_session.call_id}")
        self.peer_session = None

    def record_relay(self, payload: bytes) -> None:
        self.relayed_packets += 1
        self.relayed_bytes += len(payload)
        if self.relayed_packets <= 5 or self.relayed_packets % 500 == 0:
            self.log(
                "RTP PACKET RELAY",
                f"count={self.relayed_packets} payload_bytes={len(payload)}",
            )

    def handle_dtmf_payload(self, payload: bytes) -> None:
        event = parse_dtmf_event(payload)
        if event is None:
            self.log("DTMF IGNORED", "reason=short_payload")
            return

        event_id, digit, is_end, duration = event
        if event_id not in self.dtmf_events_started:
            self.dtmf_events_started.add(event_id)
            self.log("DTMF START", f"digit={digit} event={event_id}")

        if is_end:
            completion_key = (event_id, duration)
            if completion_key not in self.dtmf_events_completed:
                self.dtmf_events_completed.add(completion_key)
                self.dtmf_events.append(digit)
                self.log("DTMF END", f"digit={digit} event={event_id} duration={duration}")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.transport:
            self.transport.close()
        duration = time.time() - self.created_at
        payloads = ",".join(
            f"{CODEC_NAMES.get(payload_type, str(payload_type))}:{count}"
            for payload_type, count in sorted(self.payload_types_received.items())
        ) or "none"
        dtmf = "".join(self.dtmf_events) or "none"
        self.log(
            "CALL SUMMARY",
            (
                f"duration_seconds={duration:.3f} "
                f"media_mode={self.media_mode} "
                f"rtp_packets_received={self.packets_received} "
                f"rtp_packets_sent={self.packets_sent} "
                f"rtp_packets_relayed={self.relayed_packets} "
                f"rtp_bytes_received={self.bytes_received} "
                f"rtp_bytes_sent={self.bytes_sent} "
                f"rtp_bytes_relayed={self.relayed_bytes} "
                f"payloads_received={payloads} "
                f"dtmf={dtmf} "
                f"{self.analyzer.summary_text()}"
            ),
        )


class G711Transcoder:
    """Converts RTP payloads between PCMU and PCMA."""

    def __init__(self, logger: Optional[SbcLogger] = None):
        self.logger = logger
        self.logged_pairs: set = set()

    def convert(self, payload: bytes, src_pt: int, dst_pt: int) -> bytes:
        if src_pt == dst_pt:
            return payload

        pair = (src_pt, dst_pt)
        if audioop is None:
            logging.warning(
                "audioop is unavailable; cannot transcode payload type %s to %s",
                src_pt,
                dst_pt,
            )
            if self.logger and pair not in self.logged_pairs:
                self.logger.transcoding(
                    "TRANSCODE BYPASS",
                    f"reason=audioop_unavailable src={CODEC_NAMES.get(src_pt, src_pt)} dst={CODEC_NAMES.get(dst_pt, dst_pt)}",
                )
                self.logged_pairs.add(pair)
            return payload

        if src_pt == PCMU and dst_pt == PCMA:
            self._log_conversion(src_pt, dst_pt)
            linear = audioop.ulaw2lin(payload, 2)
            return audioop.lin2alaw(linear, 2)

        if src_pt == PCMA and dst_pt == PCMU:
            self._log_conversion(src_pt, dst_pt)
            linear = audioop.alaw2lin(payload, 2)
            return audioop.lin2ulaw(linear, 2)

        return payload

    def _log_conversion(self, src_pt: int, dst_pt: int) -> None:
        pair = (src_pt, dst_pt)
        if self.logger and pair not in self.logged_pairs:
            self.logger.transcoding(
                "TRANSCODE ACTIVE",
                f"src={CODEC_NAMES.get(src_pt, src_pt)} dst={CODEC_NAMES.get(dst_pt, dst_pt)}",
            )
            self.logged_pairs.add(pair)


class RtpProtocol(asyncio.DatagramProtocol):
    def __init__(self, session: RtpSession, transcoder: G711Transcoder):
        self.session = session
        self.transcoder = transcoder

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.session.transport = transport  # type: ignore[assignment]
        logging.info("RTP listening on %s:%s", self.session.local_ip, self.session.local_port)
        if self.session.logger:
            self.session.logger.udp(
                "UDP LISTENING",
                f"protocol=rtp local={self.session.local_ip}:{self.session.local_port}",
                call_id=self.session.call_id,
                leg=self.session.leg_label or self.session.media_mode,
            )
        self.session.log("RTP LISTENING", f"local={self.session.local_ip}:{self.session.local_port}")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            packet = RtpPacket.parse(data)
        except ValueError:
            if self.session.logger:
                self.session.logger.networking(
                    "RTP PARSE FAILED",
                    f"source={addr[0]}:{addr[1]} bytes={len(data)}",
                    call_id=self.session.call_id,
                    leg=self.session.leg_label or self.session.media_mode,
                )
            return

        first_packet = self.session.remote_addr is None
        self.session.remote_addr = addr
        if first_packet:
            if self.session.logger:
                self.session.logger.udp(
                    "UDP RX FIRST RTP",
                    f"source={addr[0]}:{addr[1]} bytes={len(data)} payload_type={packet.payload_type}",
                    call_id=self.session.call_id,
                    leg=self.session.leg_label or self.session.media_mode,
                )
            self.session.log(
                "RTP REMOTE",
                f"remote={addr[0]}:{addr[1]} first_payload_type={CODEC_NAMES.get(packet.payload_type, packet.payload_type)}",
            )

        record_audio = self.session.dtmf_payload_type != packet.payload_type
        self.session.record_rtp_packet(packet, record_audio=record_audio)

        if self.session.dtmf_payload_type == packet.payload_type:
            self.session.handle_dtmf_payload(packet.payload)
            if self.session.media_mode in {"bridge", "b2bua"}:
                self._relay_packet(packet)
            return

        if self.session.media_mode in {"bridge", "b2bua"}:
            self._relay_packet(packet)
            return

        out_payload_type = self.session.preferred_payload
        out_payload = self.transcoder.convert(packet.payload, packet.payload_type, out_payload_type)

        self.session.sequence = (self.session.sequence + 1) & 0xFFFF
        self.session.timestamp = (self.session.timestamp + len(out_payload)) & 0xFFFFFFFF
        response = RtpPacket.build(
            payload_type=out_payload_type,
            sequence=self.session.sequence,
            timestamp=self.session.timestamp,
            ssrc=self.session.ssrc,
            payload=out_payload,
        )

        if self.session.transport:
            self.session.transport.sendto(response, addr)
            self.session.record_rtp_sent(out_payload)

    def _relay_packet(self, packet: RtpPacket) -> None:
        peer = self.session.peer_session
        if not peer or not peer.transport or not peer.remote_addr:
            if not self.session.relay_wait_logged:
                self.session.log("BRIDGE WAITING", "reason=peer_rtp_not_ready")
                self.session.relay_wait_logged = True
            return

        out_payload_type = peer.preferred_payload
        out_payload = self.transcoder.convert(packet.payload, packet.payload_type, out_payload_type)
        peer.sequence = (peer.sequence + 1) & 0xFFFF
        peer.timestamp = (peer.timestamp + len(out_payload)) & 0xFFFFFFFF
        response = RtpPacket.build(
            payload_type=out_payload_type,
            sequence=peer.sequence,
            timestamp=peer.timestamp,
            ssrc=peer.ssrc,
            payload=out_payload,
            marker=packet.marker,
        )
        peer.transport.sendto(response, peer.remote_addr)
        peer.record_rtp_sent(out_payload)
        self.session.record_relay(out_payload)


class MediaServer:
    def __init__(self, local_ip: str, port_min: int, port_max: int, log_dir: Optional[Path], logger: SbcLogger):
        self.local_ip = local_ip
        self.port_min = port_min if port_min % 2 == 0 else port_min + 1
        self.port_max = port_max
        self.log_dir = log_dir
        self.logger = logger
        self.sessions: Dict[str, RtpSession] = {}
        self.bridge_waiting: Dict[str, RtpSession] = {}
        self.transcoder = G711Transcoder(logger)
        self._next_port = self.port_min

    async def create_session(
        self,
        call_id: str,
        preferred_payload: int,
        remote_payloads: Tuple[int, ...],
        dtmf_payload_type: Optional[int],
        bridge_id: str = "",
        media_mode: str = "echo",
        leg_label: str = "",
    ) -> RtpSession:
        if call_id in self.sessions:
            return self.sessions[call_id]

        loop = asyncio.get_running_loop()
        local_port = self._allocate_port()
        session = RtpSession(
            call_id=call_id,
            local_ip=self.local_ip,
            local_port=local_port,
            preferred_payload=preferred_payload,
            remote_payloads=remote_payloads,
            dtmf_payload_type=dtmf_payload_type,
            logger=self.logger,
            leg_label=leg_label,
            media_mode="bridge" if bridge_id else media_mode,
            bridge_id=bridge_id,
        )
        await loop.create_datagram_endpoint(
            lambda: RtpProtocol(session, self.transcoder),
            local_addr=(self.local_ip, local_port),
        )
        self.sessions[call_id] = session
        if bridge_id:
            self.join_bridge(session)
        return session

    def join_bridge(self, session: RtpSession) -> None:
        waiting = self.bridge_waiting.get(session.bridge_id)
        if waiting and waiting.call_id != session.call_id and not waiting.closed:
            waiting.set_peer(session)
            session.set_peer(waiting)
            self.bridge_waiting.pop(session.bridge_id, None)
            logging.info("Paired bridge %s: %s <-> %s", session.bridge_id, waiting.call_id, session.call_id)
            return

        self.bridge_waiting[session.bridge_id] = session
        session.log("BRIDGE WAITING", f"bridge_id={session.bridge_id} reason=waiting_for_second_leg")

    def get_session(self, call_id: str) -> Optional[RtpSession]:
        return self.sessions.get(call_id)

    def close_session(self, call_id: str) -> None:
        session = self.sessions.pop(call_id, None)
        if session:
            if session.bridge_id and self.bridge_waiting.get(session.bridge_id) is session:
                self.bridge_waiting.pop(session.bridge_id, None)
            if session.peer_session:
                session.peer_session.clear_peer()
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
    def __init__(
        self,
        local_ip: str,
        local_port: int,
        media: MediaServer,
        logger: SbcLogger,
        default_payload: int,
        auth_realm: str,
        users: Dict[str, str],
        bridge_rooms: Tuple[str, ...],
        b2bua_routes: Dict[str, str],
        route_policies: Tuple[Dict[str, Any], ...],
        b2bua_ladder_logs: bool,
        media_backend: str = "internal",
        rtpengine_client: Optional[RtpengineClient] = None,
    ):
        self.local_ip = local_ip
        self.local_port = local_port
        self.media = media
        self.logger = logger
        self.default_payload = default_payload
        self.auth_realm = auth_realm
        self.users = users
        self.bridge_rooms = set(bridge_rooms)
        self.b2bua_routes = b2bua_routes
        self.b2bua_ladder_logs = b2bua_ladder_logs
        self.media_backend = media_backend
        self.rtpengine_client = rtpengine_client
        self.nonces: Dict[str, float] = {}
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.registrations: Dict[str, Registration] = {}
        self.routing_engine = RoutingEngine(route_policies, b2bua_routes)
        self.dialogs = DialogManager()
        self.transactions = TransactionManager(self._send_packet)
        self.pending_outbound_responses: Dict[str, asyncio.Queue] = {}
        self.b2bua_calls_by_inbound: Dict[str, B2BUACall] = {}
        self.b2bua_calls_by_outbound: Dict[str, B2BUACall] = {}

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        logging.info("SIP listening on udp:%s:%s", self.local_ip, self.local_port)
        self.logger.platform("SIP SERVER STARTED", f"transport=udp local={self.local_ip}:{self.local_port}")
        self.logger.udp("UDP LISTENING", f"protocol=sip local={self.local_ip}:{self.local_port}")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            text = data.decode("utf-8", errors="replace")
            message = parse_sip_message(text, addr)
        except Exception:
            logging.exception("Could not parse SIP message from %s:%s", *addr)
            self.logger.networking("SIP PARSE FAILED", f"source={addr[0]}:{addr[1]} bytes={len(data)}")
            return

        self.logger.udp("UDP RX", f"protocol=sip source={addr[0]}:{addr[1]} bytes={len(data)}")
        if message.is_response:
            logging.info("SIP response %s from %s:%s", message.status_code, *addr)
            self.logger.sip(
                "SIP RX RESPONSE",
                f"status={message.status_code} reason={message.reason_phrase} source={addr[0]}:{addr[1]} cseq={message.header('cseq')}",
                call_id=message.header("call-id"),
            )
            self.handle_response(message)
            return

        logging.info("SIP %s from %s:%s", message.method, *addr)
        self.logger.sip(
            "SIP RX REQUEST",
            f"method={message.method} source={addr[0]}:{addr[1]} target={message.start_line} cseq={message.header('cseq')}",
            call_id=message.header("call-id"),
        )
        asyncio.create_task(self.handle_message(message))

    def handle_response(self, message: SipMessage) -> None:
        call_id = message.header("call-id")
        queue = self.pending_outbound_responses.get(call_id)
        if queue:
            queue.put_nowait(message)
            return

        b2bua_call = self.b2bua_calls_by_outbound.get(call_id)
        if b2bua_call:
            logging.info(
                "B2BUA outbound response %s for inbound call %s",
                message.status_code,
                b2bua_call.inbound_call_id,
            )
            if b2bua_call.outbound_bye_sent and message.status_code >= 200:
                b2bua_call.flow_log.sip("SIPp B", "B2BUA", f"{message.status_code} {message.reason_phrase or 'OK'}")
                self.finalize_b2bua_call(b2bua_call, "normal")

    async def handle_message(self, message: SipMessage) -> None:
        method = message.method

        if method != "ACK":
            _, duplicate = self.transactions.receive_request(
                method,
                message.header("via"),
                message.header("cseq"),
                message.header("call-id"),
                message.source,
            )
            if duplicate:
                logging.info("Replayed cached response for retransmitted %s", method)
                return

        if method == "REGISTER":
            self.cleanup_registrations()
            user = extract_user(message.header("to")) or extract_user(message.header("from")) or "unknown"
            auth_result = self.authenticate_register(message, user)
            if auth_result == "challenge":
                self.send_response(
                    message,
                    401,
                    "Unauthorized",
                    extra_headers={"WWW-Authenticate": self.make_authenticate_header()},
                )
                logging.info("Challenged REGISTER for %s", user)
                return
            if auth_result == "forbidden":
                self.send_response(message, 403, "Forbidden")
                logging.info("Rejected REGISTER for unknown user %s", user)
                return

            expires = parse_register_expires(message.header("expires"), message.header("contact"))
            if expires <= 0:
                self.registrations.pop(user, None)
                self.send_response(message, 200, "OK")
                logging.info("Unregistered %s", user)
                return

            contact_uri = extract_sip_uri(message.header("contact")) or f"sip:{user}@{message.source[0]}:{message.source[1]}"
            try:
                parse_sip_uri(contact_uri)
            except ValueError:
                self.send_response(message, 400, "Bad Contact")
                logging.info("Rejected REGISTER for %s with invalid contact %s", user, contact_uri)
                return

            self.registrations[user] = Registration(
                user=user,
                contact_uri=contact_uri,
                source=message.source,
                expires_at=time.time() + expires,
            )
            self.send_response(message, 200, "OK")
            logging.info("Registered %s -> %s expires=%s", user, contact_uri, expires)
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
            call_id = message.header("call-id", make_call_id())
            try:
                dialog = self.dialogs.create_invite(
                    call_id,
                    message.header("from"),
                    message.header("via"),
                    message.header("cseq"),
                )
            except DialogError as exc:
                self.send_response(message, 491, "Request Pending")
                logging.info("Rejected INVITE for %s: %s", call_id, exc)
                return

            dialog.mark_ringing()
            to_header = dialog.to_header(message.header("to"))
            self.send_response(message, 100, "Trying", to_header=to_header)

            remote_payloads = parse_sdp_payloads(message.body)
            dtmf_payload_type = parse_dtmf_payload_type(message.body)
            preferred_payload = choose_payload(remote_payloads, self.default_payload)
            target_user = extract_request_user(message.start_line) or "echo"
            self.cleanup_registrations()
            route = self.routing_engine.resolve(target_user, self.registrations)
            if route:
                if self.media_backend == "rtpengine":
                    await self.handle_b2bua_invite_rtpengine(
                        message=message,
                        dialog=dialog,
                        to_header=to_header,
                        inbound_call_id=call_id,
                        target_user=target_user,
                        route=route,
                    )
                    return
                await self.handle_b2bua_invite(
                    message=message,
                    dialog=dialog,
                    to_header=to_header,
                    inbound_call_id=call_id,
                    target_user=target_user,
                    route=route,
                    preferred_payload=preferred_payload,
                    remote_payloads=remote_payloads,
                    dtmf_payload_type=dtmf_payload_type,
                )
                return

            self.send_response(message, 180, "Ringing", to_header=to_header)
            bridge_id = target_user if target_user in self.bridge_rooms else ""
            rtp = await self.media.create_session(
                call_id,
                preferred_payload,
                remote_payloads,
                dtmf_payload_type,
                bridge_id=bridge_id,
            )
            rtp.log(
                "INVITE RECEIVED",
                (
                    f"source={message.source[0]}:{message.source[1]} "
                    f"from={message.header('from')} to={message.header('to')} "
                    f"target_user={target_user} media_mode={rtp.media_mode}"
                ),
            )
            rtp.log(
                "SDP OFFER",
                (
                    f"payloads={format_payloads(remote_payloads)} "
                    f"selected={CODEC_NAMES.get(preferred_payload, preferred_payload)} "
                    f"dtmf_payload={dtmf_payload_type if dtmf_payload_type is not None else 'none'}"
                ),
            )
            rtp.log("SIP RESPONSE", "100 Trying")
            rtp.log("SIP RESPONSE", "180 Ringing")
            sdp = make_sdp(self.local_ip, rtp.local_port, preferred_payload)
            rtp.log(
                "SDP ANSWER",
                f"local_rtp={self.local_ip}:{rtp.local_port} payload={CODEC_NAMES.get(preferred_payload, preferred_payload)}",
            )

            self.send_response(
                message,
                200,
                "OK",
                body=sdp,
                to_header=to_header,
                extra_headers={
                    "Contact": f"<sip:python-call-server@{self.local_ip}:{self.local_port}>",
                    "Content-Type": "application/sdp",
                },
            )
            dialog.mark_answered()
            rtp.log(
                "DIALOG STATE",
                (
                    f"state={dialog.state.name} local_tag={dialog.local_tag} "
                    f"remote_tag={dialog.remote_tag or 'none'} invite_branch={dialog.invite_branch or 'none'} "
                    f"remote_cseq={dialog.remote_cseq}"
                ),
            )
            rtp.log("SIP RESPONSE", "200 OK")
            return

        if method == "ACK":
            call_id = message.header("call-id")
            session = self.media.get_session(call_id)
            b2bua_call = self.b2bua_calls_by_inbound.get(call_id)
            if session or b2bua_call:
                try:
                    dialog = self.dialogs.acknowledge(call_id, message.header("cseq"))
                except DialogError as exc:
                    logging.info("Ignored invalid ACK for %s: %s", call_id, exc)
                    return
                self.transactions.acknowledge_invite(call_id, message.header("cseq"))
                if session:
                    session.mark_ack()
                    session.log("DIALOG STATE", f"state={dialog.state.name} acknowledged=true")
                if b2bua_call:
                    b2bua_call.flow_log.sip("SIPp A", "B2BUA", "ACK")
                    self.send_outbound_ack(b2bua_call)
            return

        if method == "BYE":
            call_id = message.header("call-id")
            session = self.media.get_session(call_id)
            try:
                dialog = self.dialogs.terminate(
                    call_id,
                    message.header("from"),
                    message.header("to"),
                    message.header("via"),
                    message.header("cseq"),
                )
            except DialogError as exc:
                self.send_response(message, 481, "Call/Transaction Does Not Exist")
                logging.info("Rejected BYE for %s: %s", call_id, exc)
                return
            if session:
                session.log("BYE RECEIVED", f"source={message.source[0]}:{message.source[1]}")
            b2bua_call = self.b2bua_calls_by_inbound.get(call_id)
            if b2bua_call:
                b2bua_call.flow_log.sip("SIPp A", "B2BUA", "BYE")
            self.send_response(message, 200, "OK")
            if b2bua_call:
                b2bua_call.flow_log.sip("B2BUA", "SIPp A", "200 OK", "BYE")
                self.send_outbound_bye(b2bua_call)
                self.media.close_session(b2bua_call.outbound_call_id)
                self.schedule_b2bua_finalizer(b2bua_call)
            if session:
                session.log(
                    "DIALOG STATE",
                    (
                        f"state={dialog.state.name} remote_cseq={dialog.remote_cseq} "
                        f"branches={','.join(sorted(dialog.branch_ids)) or 'none'}"
                    ),
                )
                session.log("SIP RESPONSE", "200 OK for BYE")
            self.media.close_session(call_id)
            return

        self.send_response(message, 405, "Method Not Allowed", extra_headers={"Allow": "REGISTER, OPTIONS, INVITE, ACK, BYE"})

    async def handle_b2bua_invite(
        self,
        message: SipMessage,
        dialog: Any,
        to_header: str,
        inbound_call_id: str,
        target_user: str,
        route: RouteResult,
        preferred_payload: int,
        remote_payloads: Tuple[int, ...],
        dtmf_payload_type: Optional[int],
    ) -> None:
        target = route.target
        flow_log = B2BUAFlowLog(
            self.media.log_dir,
            inbound_call_id,
            target_user,
            route,
            enabled=self.b2bua_ladder_logs,
            logger=self.logger,
        )
        flow_log.sip("SIPp A", "B2BUA", "INVITE", f"call_id={inbound_call_id} target_user={target_user}")
        flow_log.sip("B2BUA", "SIPp A", "100 Trying")

        inbound_rtp = await self.media.create_session(
            inbound_call_id,
            preferred_payload,
            remote_payloads,
            dtmf_payload_type,
            media_mode="b2bua",
            leg_label="inbound",
        )
        inbound_remote = parse_sdp_remote_addr(message.body, message.source[0])
        if inbound_remote:
            inbound_rtp.remote_addr = inbound_remote
            inbound_rtp.log("RTP REMOTE", f"remote={inbound_remote[0]}:{inbound_remote[1]} source=sdp")

        outbound_call_id = make_call_id()
        outbound_rtp = await self.media.create_session(
            outbound_call_id,
            preferred_payload,
            remote_payloads,
            dtmf_payload_type,
            media_mode="b2bua",
            leg_label="outbound",
        )

        outbound_from = f"Mini B2BUA <sip:b2bua@{self.local_ip}:{self.local_port}>;tag={secrets.token_hex(6)}"
        b2bua_call = B2BUACall(
            inbound_call_id=inbound_call_id,
            outbound_call_id=outbound_call_id,
            outbound_target=target,
            outbound_from_header=outbound_from,
            target_user=target_user,
            route_policy=route.policy_name,
            route_source=route.source,
            flow_log=flow_log,
        )
        self.b2bua_calls_by_inbound[inbound_call_id] = b2bua_call
        self.b2bua_calls_by_outbound[outbound_call_id] = b2bua_call

        response_queue: asyncio.Queue = asyncio.Queue()
        self.pending_outbound_responses[outbound_call_id] = response_queue
        inbound_rtp.log(
            "B2BUA ROUTE",
            (
                f"target_user={target_user} route={target.uri} outbound_call_id={outbound_call_id} "
                f"route_source={route.source} route_policy={route.policy_name}"
            ),
        )

        outbound_body = make_sdp(
            self.local_ip,
            outbound_rtp.local_port,
            preferred_payload,
            dtmf_payload_type=dtmf_payload_type,
            payloads=remote_payloads,
        )
        self.send_outbound_invite(b2bua_call, outbound_body)

        try:
            final_response = await self.wait_for_outbound_invite(
                response_queue,
                message,
                to_header,
                inbound_rtp,
                b2bua_call,
            )
        except asyncio.TimeoutError:
            inbound_rtp.log("B2BUA FAILURE", f"route={target.uri} reason=outbound_invite_timeout")
            flow_log.write("B2BUA FAILURE", f"route={target.uri} reason=outbound_invite_timeout")
            self.send_response(message, 480, "Temporarily Unavailable", to_header=to_header)
            self.cleanup_b2bua_call(b2bua_call)
            return
        finally:
            self.pending_outbound_responses.pop(outbound_call_id, None)

        status = final_response.status_code
        reason = final_response.reason_phrase or "Upstream Response"
        if status < 200 or status >= 300:
            b2bua_call.outbound_to_header = final_response.header("to")
            b2bua_call.outbound_contact_uri = extract_sip_uri(final_response.header("contact")) or target.uri
            inbound_rtp.log("B2BUA FAILURE", f"route={target.uri} status={status} reason={reason}")
            flow_log.write("B2BUA FAILURE", f"route={target.uri} status={status} reason={reason}")
            self.send_outbound_ack(b2bua_call)
            flow_log.sip("B2BUA", "SIPp A", f"{status} {reason}")
            self.send_response(message, status, reason, to_header=to_header)
            self.cleanup_b2bua_call(b2bua_call)
            return

        b2bua_call.outbound_to_header = final_response.header("to")
        b2bua_call.outbound_contact_uri = extract_sip_uri(final_response.header("contact")) or target.uri
        outbound_payloads = parse_sdp_payloads(final_response.body)
        outbound_rtp.remote_payloads = outbound_payloads
        outbound_rtp.preferred_payload = choose_payload(outbound_payloads, preferred_payload)
        outbound_remote = parse_sdp_remote_addr(final_response.body, final_response.source[0])
        if outbound_remote:
            outbound_rtp.remote_addr = outbound_remote
            outbound_rtp.log("RTP REMOTE", f"remote={outbound_remote[0]}:{outbound_remote[1]} source=sdp")

        inbound_rtp.set_peer(outbound_rtp)
        outbound_rtp.set_peer(inbound_rtp)

        answer_sdp = make_sdp(
            self.local_ip,
            inbound_rtp.local_port,
            inbound_rtp.preferred_payload,
            dtmf_payload_type=dtmf_payload_type,
        )
        self.send_response(
            message,
            200,
            "OK",
            body=answer_sdp,
            to_header=to_header,
            extra_headers={
                "Contact": f"<sip:python-call-server@{self.local_ip}:{self.local_port}>",
                "Content-Type": "application/sdp",
            },
        )
        flow_log.sip("B2BUA", "SIPp A", "200 OK")
        dialog.mark_answered()
        inbound_rtp.log(
            "DIALOG STATE",
            (
                f"state={dialog.state.name} local_tag={dialog.local_tag} "
                f"remote_tag={dialog.remote_tag or 'none'} invite_branch={dialog.invite_branch or 'none'} "
                f"remote_cseq={dialog.remote_cseq}"
            ),
        )
        inbound_rtp.log("SIP RESPONSE", "200 OK")
        outbound_rtp.log(
            "B2BUA ANSWERED",
            f"inbound_call_id={inbound_call_id} outbound_payload={CODEC_NAMES.get(outbound_rtp.preferred_payload, outbound_rtp.preferred_payload)}",
        )

    async def handle_b2bua_invite_rtpengine(
        self,
        message: SipMessage,
        dialog: Any,
        to_header: str,
        inbound_call_id: str,
        target_user: str,
        route: RouteResult,
    ) -> None:
        if not self.rtpengine_client:
            self.send_response(message, 500, "RTPengine Not Configured", to_header=to_header)
            return

        target = route.target
        flow_log = B2BUAFlowLog(
            self.media.log_dir,
            inbound_call_id,
            target_user,
            route,
            enabled=self.b2bua_ladder_logs,
            logger=self.logger,
        )
        flow_log.sip("SIPp A", "B2BUA", "INVITE", f"call_id={inbound_call_id} target_user={target_user}")
        flow_log.sip("B2BUA", "SIPp A", "100 Trying")
        flow_log.write("MEDIA BACKEND", f"backend=rtpengine target={target.uri}")

        from_tag = extract_header_tag(message.header("from")) or dialog.remote_tag or secrets.token_hex(6)
        try:
            offer_response = await self.rtpengine_client.offer(
                call_id=inbound_call_id,
                from_tag=from_tag,
                sdp=message.body,
            )
            outbound_body = str(offer_response.get("sdp") or "")
            if not outbound_body:
                raise RtpengineError("RTPengine offer response did not include SDP")
        except (asyncio.TimeoutError, OSError, RtpengineError) as exc:
            flow_log.write("RTPENGINE OFFER FAILED", str(exc))
            self.send_response(message, 488, "Not Acceptable Here", to_header=to_header)
            return

        outbound_call_id = make_call_id()
        outbound_from = f"Mini B2BUA <sip:b2bua@{self.local_ip}:{self.local_port}>;tag={secrets.token_hex(6)}"
        b2bua_call = B2BUACall(
            inbound_call_id=inbound_call_id,
            outbound_call_id=outbound_call_id,
            outbound_target=target,
            outbound_from_header=outbound_from,
            target_user=target_user,
            route_policy=route.policy_name,
            route_source=route.source,
            flow_log=flow_log,
            media_backend="rtpengine",
            rtpengine_call_id=inbound_call_id,
            rtpengine_from_tag=from_tag,
        )
        self.b2bua_calls_by_inbound[inbound_call_id] = b2bua_call
        self.b2bua_calls_by_outbound[outbound_call_id] = b2bua_call

        response_queue: asyncio.Queue = asyncio.Queue()
        self.pending_outbound_responses[outbound_call_id] = response_queue
        flow_log.write(
            "B2BUA ROUTE",
            f"target_user={target_user} route={target.uri} outbound_call_id={outbound_call_id} route_source={route.source}",
        )
        self.send_outbound_invite(b2bua_call, outbound_body)

        try:
            final_response = await self.wait_for_outbound_invite(
                response_queue,
                message,
                to_header,
                None,
                b2bua_call,
            )
        except asyncio.TimeoutError:
            flow_log.write("B2BUA FAILURE", f"route={target.uri} reason=outbound_invite_timeout")
            self.send_response(message, 480, "Temporarily Unavailable", to_header=to_header)
            self.cleanup_b2bua_call(b2bua_call)
            return
        finally:
            self.pending_outbound_responses.pop(outbound_call_id, None)

        status = final_response.status_code
        reason = final_response.reason_phrase or "Upstream Response"
        b2bua_call.outbound_to_header = final_response.header("to")
        b2bua_call.outbound_contact_uri = extract_sip_uri(final_response.header("contact")) or target.uri
        if status < 200 or status >= 300:
            flow_log.write("B2BUA FAILURE", f"route={target.uri} status={status} reason={reason}")
            self.send_outbound_ack(b2bua_call)
            flow_log.sip("B2BUA", "SIPp A", f"{status} {reason}")
            self.send_response(message, status, reason, to_header=to_header)
            self.cleanup_b2bua_call(b2bua_call)
            return

        to_tag = extract_header_tag(final_response.header("to")) or secrets.token_hex(6)
        b2bua_call.rtpengine_to_tag = to_tag
        try:
            answer_response = await self.rtpengine_client.answer(
                call_id=inbound_call_id,
                from_tag=from_tag,
                to_tag=to_tag,
                sdp=final_response.body,
            )
            answer_sdp = str(answer_response.get("sdp") or "")
            if not answer_sdp:
                raise RtpengineError("RTPengine answer response did not include SDP")
        except (asyncio.TimeoutError, OSError, RtpengineError) as exc:
            flow_log.write("RTPENGINE ANSWER FAILED", str(exc))
            self.send_outbound_ack(b2bua_call)
            self.send_outbound_bye(b2bua_call)
            self.send_response(message, 488, "Not Acceptable Here", to_header=to_header)
            self.cleanup_b2bua_call(b2bua_call)
            return

        self.send_response(
            message,
            200,
            "OK",
            body=answer_sdp,
            to_header=to_header,
            extra_headers={
                "Contact": f"<sip:python-call-server@{self.local_ip}:{self.local_port}>",
                "Content-Type": "application/sdp",
            },
        )
        flow_log.sip("B2BUA", "SIPp A", "200 OK")
        flow_log.write("RTPENGINE ANSWER", f"call_id={inbound_call_id} from_tag={from_tag} to_tag={to_tag}")
        dialog.mark_answered()

    async def wait_for_outbound_invite(
        self,
        response_queue: asyncio.Queue,
        inbound_request: SipMessage,
        to_header: str,
        inbound_rtp: Optional[RtpSession],
        b2bua_call: B2BUACall,
        timeout: float = 10.0,
    ) -> SipMessage:
        while True:
            response = await asyncio.wait_for(response_queue.get(), timeout=timeout)
            status = response.status_code
            reason = response.reason_phrase or "Upstream Response"
            b2bua_call.flow_log.sip("SIPp B", "B2BUA", f"{status} {reason}")
            if status < 200:
                if status != 100:
                    body = ""
                    if b2bua_call.media_backend != "rtpengine" and response.body:
                        body = response.body
                    extra_headers = {"Content-Type": response.header("content-type")} if body else None
                    b2bua_call.flow_log.sip("B2BUA", "SIPp A", f"{status} {reason}")
                    self.send_response(
                        inbound_request,
                        status,
                        reason,
                        body=body,
                        to_header=to_header,
                        extra_headers=extra_headers,
                    )
                    if inbound_rtp:
                        inbound_rtp.log("SIP RESPONSE", f"{status} {reason} from outbound")
                continue
            return response

    def send_outbound_invite(self, b2bua_call: B2BUACall, body: str) -> None:
        headers = {
            "Via": self.make_via_header(),
            "From": b2bua_call.outbound_from_header,
            "To": f"<{b2bua_call.outbound_target.uri}>",
            "Call-ID": b2bua_call.outbound_call_id,
            "CSeq": f"{b2bua_call.outbound_cseq} INVITE",
            "Contact": f"<sip:b2bua@{self.local_ip}:{self.local_port}>",
            "Max-Forwards": "69",
            "Subject": f"B2BUA outbound leg for {b2bua_call.inbound_call_id}",
            "Content-Type": "application/sdp",
        }
        packet = build_sip_request("INVITE", b2bua_call.outbound_target.uri, headers, body)
        self._send_packet(packet, b2bua_call.outbound_target.address)
        b2bua_call.flow_log.sip(
            "B2BUA",
            "SIPp B",
            "INVITE",
            f"call_id={b2bua_call.outbound_call_id} target={b2bua_call.outbound_target.uri}",
        )
        session = self.media.get_session(b2bua_call.outbound_call_id)
        if session:
            session.log("B2BUA OUTBOUND INVITE", f"target={b2bua_call.outbound_target.uri}")

    def send_outbound_ack(self, b2bua_call: B2BUACall) -> None:
        request_uri = b2bua_call.outbound_contact_uri or b2bua_call.outbound_target.uri
        headers = {
            "Via": self.make_via_header(),
            "From": b2bua_call.outbound_from_header,
            "To": b2bua_call.outbound_to_header,
            "Call-ID": b2bua_call.outbound_call_id,
            "CSeq": f"{b2bua_call.outbound_cseq} ACK",
            "Contact": f"<sip:b2bua@{self.local_ip}:{self.local_port}>",
            "Max-Forwards": "69",
        }
        self._send_packet(
            build_sip_request("ACK", request_uri, headers),
            self.outbound_destination(b2bua_call),
        )
        b2bua_call.flow_log.sip("B2BUA", "SIPp B", "ACK")
        session = self.media.get_session(b2bua_call.outbound_call_id)
        if session:
            session.mark_ack()
            session.log("B2BUA OUTBOUND ACK")

    def send_outbound_bye(self, b2bua_call: B2BUACall) -> None:
        request_uri = b2bua_call.outbound_contact_uri or b2bua_call.outbound_target.uri
        b2bua_call.outbound_cseq += 1
        b2bua_call.outbound_bye_sent = True
        headers = {
            "Via": self.make_via_header(),
            "From": b2bua_call.outbound_from_header,
            "To": b2bua_call.outbound_to_header,
            "Call-ID": b2bua_call.outbound_call_id,
            "CSeq": f"{b2bua_call.outbound_cseq} BYE",
            "Contact": f"<sip:b2bua@{self.local_ip}:{self.local_port}>",
            "Max-Forwards": "69",
        }
        self._send_packet(
            build_sip_request("BYE", request_uri, headers),
            self.outbound_destination(b2bua_call),
        )
        b2bua_call.flow_log.sip("B2BUA", "SIPp B", "BYE")
        session = self.media.get_session(b2bua_call.outbound_call_id)
        if session:
            session.log("B2BUA OUTBOUND BYE", f"target={request_uri}")

    def outbound_destination(self, b2bua_call: B2BUACall) -> Tuple[str, int]:
        if b2bua_call.outbound_contact_uri:
            try:
                return parse_sip_uri(b2bua_call.outbound_contact_uri).address
            except ValueError:
                pass
        return b2bua_call.outbound_target.address

    def cleanup_b2bua_call(self, b2bua_call: B2BUACall) -> None:
        self.finalize_b2bua_call(b2bua_call, "cleanup")
        self.b2bua_calls_by_inbound.pop(b2bua_call.inbound_call_id, None)
        self.b2bua_calls_by_outbound.pop(b2bua_call.outbound_call_id, None)
        self.pending_outbound_responses.pop(b2bua_call.outbound_call_id, None)
        self.media.close_session(b2bua_call.outbound_call_id)
        self.media.close_session(b2bua_call.inbound_call_id)

    def schedule_b2bua_finalizer(self, b2bua_call: B2BUACall, delay: float = 2.0) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._finalize_b2bua_later(b2bua_call, delay))

    async def _finalize_b2bua_later(self, b2bua_call: B2BUACall, delay: float) -> None:
        await asyncio.sleep(delay)
        self.finalize_b2bua_call(b2bua_call, "timer")

    def finalize_b2bua_call(self, b2bua_call: B2BUACall, reason: str) -> None:
        if b2bua_call.finalized:
            return
        b2bua_call.finalized = True
        b2bua_call.flow_log.write("CALL END", f"reason={reason}")
        b2bua_call.flow_log.render_ladder()
        self.schedule_rtpengine_delete(b2bua_call)
        self.b2bua_calls_by_inbound.pop(b2bua_call.inbound_call_id, None)
        self.b2bua_calls_by_outbound.pop(b2bua_call.outbound_call_id, None)
        self.pending_outbound_responses.pop(b2bua_call.outbound_call_id, None)

    def schedule_rtpengine_delete(self, b2bua_call: B2BUACall) -> None:
        if b2bua_call.media_backend != "rtpengine" or not self.rtpengine_client:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._delete_rtpengine_call(b2bua_call))

    async def _delete_rtpengine_call(self, b2bua_call: B2BUACall) -> None:
        assert self.rtpengine_client is not None
        try:
            await self.rtpengine_client.delete(
                call_id=b2bua_call.rtpengine_call_id or b2bua_call.inbound_call_id,
                from_tag=b2bua_call.rtpengine_from_tag,
                to_tag=b2bua_call.rtpengine_to_tag,
            )
            b2bua_call.flow_log.write("RTPENGINE DELETE", "status=ok")
        except (asyncio.TimeoutError, OSError, RtpengineError) as exc:
            b2bua_call.flow_log.write("RTPENGINE DELETE FAILED", str(exc))

    def cleanup_registrations(self) -> None:
        now = time.time()
        expired = [user for user, registration in self.registrations.items() if registration.is_expired(now)]
        for user in expired:
            self.registrations.pop(user, None)
            logging.info("Expired registration for %s", user)

    def make_via_header(self) -> str:
        return f"SIP/2.0/UDP {self.local_ip}:{self.local_port};branch=z9hG4bK-{secrets.token_hex(8)}"

    def authenticate_register(self, message: SipMessage, user: str) -> str:
        if not self.users:
            return "ok"
        if user not in self.users:
            return "forbidden"

        authorization = message.header("authorization")
        if not authorization:
            return "challenge"

        digest = parse_digest_header(authorization)
        nonce = digest.get("nonce", "")
        if (
            digest.get("username") != user
            or digest.get("realm") != self.auth_realm
            or nonce not in self.nonces
            or time.time() - self.nonces[nonce] > 300
        ):
            return "challenge"

        expected = make_digest_response(
            username=user,
            realm=self.auth_realm,
            password=self.users[user],
            method=message.method,
            uri=digest.get("uri", ""),
            nonce=nonce,
            nc=digest.get("nc"),
            cnonce=digest.get("cnonce"),
            qop=digest.get("qop"),
        )
        return "ok" if secrets.compare_digest(expected, digest.get("response", "")) else "challenge"

    def make_authenticate_header(self) -> str:
        nonce = secrets.token_hex(16)
        self.nonces[nonce] = time.time()
        return f'Digest realm="{self.auth_realm}", nonce="{nonce}", algorithm=MD5, qop="auth"'

    def send_response(
        self,
        request: SipMessage,
        status: int,
        reason: str,
        body: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
        to_header: Optional[str] = None,
    ) -> None:
        if not self.transport:
            return

        headers = {
            "Via": request.header("via"),
            "From": request.header("from"),
            "To": to_header or ensure_tag(request.header("to")),
            "Call-ID": request.header("call-id"),
            "CSeq": request.header("cseq"),
            "Server": "PlaySBC/0.1",
            "Content-Length": str(len(body.encode("utf-8"))),
        }
        if extra_headers:
            headers.update(extra_headers)

        lines = [f"SIP/2.0 {status} {reason}"]
        lines.extend(f"{name}: {value}" for name, value in headers.items() if value)
        packet = (CRLF.join(lines) + CRLF + CRLF + body).encode("utf-8")
        self.logger.sip(
            "SIP TX RESPONSE",
            f"status={status} reason={reason} destination={request.source[0]}:{request.source[1]} cseq={request.header('cseq')}",
            call_id=request.header("call-id"),
        )
        self._send_packet(packet, request.source)
        self.transactions.cache_response(
            request.method,
            request.header("via"),
            request.header("cseq"),
            request.header("call-id"),
            packet,
            request.source,
            status,
        )

    def _send_packet(self, packet: bytes, destination: Tuple[str, int]) -> None:
        if self.transport:
            self.logger.udp("UDP TX", f"protocol=sip destination={destination[0]}:{destination[1]} bytes={len(packet)}")
            self.transport.sendto(packet, destination)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.transactions.close()


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


def extract_header_tag(header_value: str) -> str:
    match = re.search(r"(?:^|;)\s*tag=([^;\s>]+)", header_value, re.IGNORECASE)
    return match.group(1) if match else ""


def extract_user(header_value: str) -> Optional[str]:
    match = re.search(r"sip:([^@;>]+)", header_value)
    return match.group(1) if match else None


def extract_request_user(start_line: str) -> Optional[str]:
    match = re.search(r"^\S+\s+sip:([^@;:\s>]+)", start_line, re.IGNORECASE)
    return match.group(1) if match else None


def extract_sip_uri(value: str) -> str:
    match = re.search(r"sip:[^;>\s]+", value, re.IGNORECASE)
    return match.group(0) if match else ""


def parse_sip_uri(value: str) -> SipUri:
    match = re.search(r"sip:([^@;>\s]+)@([^;:>\s]+)(?::(\d+))?", value, re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid SIP URI {value!r}")

    port = int(match.group(3)) if match.group(3) else 5060
    if port <= 0 or port > 65535:
        raise ValueError(f"Invalid SIP URI port {port}")
    return SipUri(user=match.group(1), host=match.group(2), port=port)


def format_route_target(target: str, user: str) -> str:
    return target.replace("{user}", user)


def parse_register_expires(expires_header: str, contact_header: str, default: int = 300) -> int:
    contact_match = re.search(r"(?:^|;)\s*expires=([0-9]+)", contact_header, re.IGNORECASE)
    if contact_match:
        return int(contact_match.group(1))
    if expires_header.strip().isdigit():
        return int(expires_header.strip())
    return default


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


def parse_sdp_remote_addr(sdp: str, fallback_ip: str = "") -> Optional[Tuple[str, int]]:
    media_match = re.search(r"^m=audio\s+(\d+)\s+RTP/AVP\b", sdp, re.MULTILINE)
    if not media_match:
        return None

    connection_match = re.search(r"^c=IN\s+IP[46]\s+([^\s]+)", sdp, re.MULTILINE)
    host = connection_match.group(1) if connection_match else fallback_ip
    if not host:
        return None

    port = int(media_match.group(1))
    if port <= 0 or port > 65535:
        return None
    return host, port


def parse_dtmf_payload_type(sdp: str) -> Optional[int]:
    for match in re.finditer(r"^a=rtpmap:(\d+)\s+telephone-event/8000", sdp, re.IGNORECASE | re.MULTILINE):
        return int(match.group(1))
    return None


def choose_payload(remote_payloads: Tuple[int, ...], default_payload: int = PCMU) -> int:
    if default_payload in SUPPORTED_CODECS and default_payload in remote_payloads:
        return default_payload

    for payload in remote_payloads:
        if payload in SUPPORTED_CODECS:
            return payload
    return PCMU


def codec_payload(codec_name: str) -> int:
    codec = codec_name.upper()
    if codec not in CODEC_PAYLOADS:
        supported = ", ".join(sorted(CODEC_PAYLOADS))
        raise ValueError(f"Unsupported default_codec {codec_name!r}. Supported values: {supported}")
    return CODEC_PAYLOADS[codec]


def format_payloads(payloads: Tuple[int, ...]) -> str:
    return ",".join(CODEC_NAMES.get(payload, str(payload)) for payload in payloads) or "none"


def log_category_for_session_event(event: str) -> str:
    upper = event.upper()
    if upper.startswith(("SIP", "SDP", "INVITE", "BYE", "ACK", "DIALOG")):
        return "sip"
    if "TRANSCOD" in upper:
        return "transcoding"
    if upper.startswith("RTP") or "DTMF" in upper or "BRIDGE" in upper or "B2BUA" in upper or "CALL SUMMARY" in upper:
        return "media"
    if "NETWORK" in upper:
        return "networking"
    return "platform"


def log_category_for_flow_event(event: str) -> str:
    upper = event.upper()
    if "RTPENGINE" in upper or "MEDIA" in upper:
        return "media"
    if "CALL" in upper or "ROUTE" in upper or "FAILURE" in upper:
        return "sip"
    return "platform"


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "call"


def parse_dtmf_event(payload: bytes) -> Optional[Tuple[int, str, bool, int]]:
    if len(payload) < 4:
        return None
    event_id = payload[0]
    digit = DTMF_EVENTS.get(event_id, str(event_id))
    is_end = bool(payload[1] & 0x80)
    duration = struct.unpack("!H", payload[2:4])[0]
    return event_id, digit, is_end, duration


def parse_digest_header(value: str) -> Dict[str, str]:
    value = value.strip()
    if value.lower().startswith("digest "):
        value = value[7:].strip()

    fields: Dict[str, str] = {}
    for part in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', value):
        key, _, raw_value = part.strip().partition("=")
        if not key:
            continue
        parsed_value = raw_value.strip()
        if parsed_value.startswith('"') and parsed_value.endswith('"'):
            parsed_value = parsed_value[1:-1]
        fields[key.lower()] = parsed_value
    return fields


def make_digest_response(
    username: str,
    realm: str,
    password: str,
    method: str,
    uri: str,
    nonce: str,
    nc: Optional[str] = None,
    cnonce: Optional[str] = None,
    qop: Optional[str] = None,
) -> str:
    ha1 = md5_hex(f"{username}:{realm}:{password}")
    ha2 = md5_hex(f"{method}:{uri}")
    if qop:
        return md5_hex(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return md5_hex(f"{ha1}:{nonce}:{ha2}")


def md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def make_sdp(
    local_ip: str,
    rtp_port: int,
    payload_type: int,
    dtmf_payload_type: Optional[int] = None,
    payloads: Optional[Tuple[int, ...]] = None,
) -> str:
    offered_payloads = []
    if payloads:
        offered_payloads.extend(payload for payload in payloads if payload in SUPPORTED_CODECS)
    if payload_type not in offered_payloads:
        offered_payloads.insert(0, payload_type)

    if dtmf_payload_type is not None and dtmf_payload_type not in offered_payloads:
        offered_payloads.append(dtmf_payload_type)

    codecs = {
        PCMU: "a=rtpmap:0 PCMU/8000",
        PCMA: "a=rtpmap:8 PCMA/8000",
    }
    lines = [
        "v=0",
        f"o=playsbc {int(time.time())} 1 IN IP4 {local_ip}",
        "s=PlaySBC",
        f"c=IN IP4 {local_ip}",
        "t=0 0",
        f"m=audio {rtp_port} RTP/AVP {' '.join(str(payload) for payload in offered_payloads)}",
    ]
    for payload in offered_payloads:
        if payload in codecs:
            lines.append(codecs[payload])
        elif payload == dtmf_payload_type:
            lines.append(f"a=rtpmap:{payload} telephone-event/8000")
            lines.append(f"a=fmtp:{payload} 0-16")
    lines.extend(["a=sendrecv", ""])
    return CRLF.join(lines)


def build_sip_request(method: str, request_uri: str, headers: Dict[str, str], body: str = "") -> bytes:
    lines = [f"{method} {request_uri} SIP/2.0"]
    headers_with_length = dict(headers)
    headers_with_length["Content-Length"] = str(len(body.encode("utf-8")))
    lines.extend(f"{name}: {value}" for name, value in headers_with_length.items() if value)
    return (CRLF.join(lines) + CRLF + CRLF + body).encode("utf-8")


def make_call_id() -> str:
    return hashlib.sha1(str(random.random()).encode("ascii")).hexdigest()


def load_config_file(path: Optional[str]) -> ServerConfig:
    config = ServerConfig()
    if not path:
        return config

    config_path = Path(path)
    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON config {config_path}: {exc}") from exc

    if not isinstance(raw_config, dict):
        raise ValueError(f"Config {config_path} must contain a JSON object")

    for key, value in raw_config.items():
        if key not in SERVER_CONFIG_KEYS:
            raise ValueError(f"Unknown config key {key!r} in {config_path}")
        setattr(config, key, coerce_config_value(key, value))

    validate_config(config)
    return config


def coerce_config_value(key: str, value: Any) -> Any:
    if key in {"sip_port", "rtp_min", "rtp_max"}:
        return int(value)
    if key == "rtpengine_timeout":
        return float(value)
    if key in {"debug", "b2bua_ladder_logs"}:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if key == "users":
        if not isinstance(value, dict):
            raise ValueError("users must be a JSON object mapping usernames to passwords")
        return {str(username): str(password) for username, password in value.items()}
    if key == "b2bua_routes":
        if not isinstance(value, dict):
            raise ValueError("b2bua_routes must be a JSON object mapping dialed users to SIP URIs")
        return {str(username): str(uri) for username, uri in value.items()}
    if key == "route_policies":
        if not isinstance(value, list):
            raise ValueError("route_policies must be a list of route policy objects")
        policies = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("each route policy must be a JSON object")
            policies.append(dict(item))
        return tuple(policies)
    if key == "bridge_rooms":
        if isinstance(value, str):
            return (value,)
        if isinstance(value, list):
            return tuple(str(room) for room in value)
        raise ValueError("bridge_rooms must be a string or list of strings")
    if key in {
        "sip_ip",
        "log_dir",
        "default_codec",
        "auth_realm",
        "media_backend",
        "rtpengine_url",
    }:
        return str(value)
    return value


def apply_cli_overrides(config: ServerConfig, args: argparse.Namespace) -> ServerConfig:
    overrides = {
        "sip_ip": getattr(args, "sip_ip", None),
        "sip_port": getattr(args, "sip_port", None),
        "rtp_min": getattr(args, "rtp_min", None),
        "rtp_max": getattr(args, "rtp_max", None),
        "log_dir": getattr(args, "log_dir", None),
        "default_codec": getattr(args, "default_codec", None),
        "auth_realm": getattr(args, "auth_realm", None),
        "media_backend": getattr(args, "media_backend", None),
        "rtpengine_url": getattr(args, "rtpengine_url", None),
        "rtpengine_timeout": getattr(args, "rtpengine_timeout", None),
        "debug": getattr(args, "debug", None),
    }
    for key, value in overrides.items():
        if value is not None:
            setattr(config, key, coerce_config_value(key, value))
    validate_config(config)
    return config


def validate_config(config: ServerConfig) -> None:
    config.default_codec = config.default_codec.upper()
    codec_payload(config.default_codec)
    if config.sip_port <= 0 or config.sip_port > 65535:
        raise ValueError("sip_port must be between 1 and 65535")
    if config.rtp_min <= 0 or config.rtp_max > 65535 or config.rtp_min > config.rtp_max:
        raise ValueError("RTP port range must be within 1-65535 and rtp_min must be <= rtp_max")
    if not config.auth_realm:
        raise ValueError("auth_realm must not be empty")
    config.media_backend = config.media_backend.lower()
    if config.media_backend not in MEDIA_BACKENDS:
        raise ValueError(f"media_backend must be one of {', '.join(sorted(MEDIA_BACKENDS))}")
    if config.rtpengine_timeout <= 0:
        raise ValueError("rtpengine_timeout must be greater than 0")
    if config.media_backend == "rtpengine":
        parse_rtpengine_url(config.rtpengine_url)
    for user, route_uri in config.b2bua_routes.items():
        if not user:
            raise ValueError("b2bua_routes keys must not be empty")
        parse_sip_uri(route_uri)
    for policy_config in config.route_policies:
        policy = RoutePolicy.from_config(policy_config)
        if not policy.name:
            raise ValueError("route policy name must not be empty")
        if policy.target.lower() not in RoutingEngine.REGISTRATION_TARGETS:
            parse_sip_uri(format_route_target(policy.target, "test-user"))


def resolve_log_dir(config: ServerConfig) -> Optional[Path]:
    return Path(config.log_dir) if config.log_dir else None


async def main() -> None:
    parser = argparse.ArgumentParser(description="Small SIP/RTP call server")
    parser.add_argument("--config", help="Path to a JSON config file")
    parser.add_argument("--ip", dest="sip_ip", help="IP address to bind and advertise")
    parser.add_argument("--sip-port", type=int, help="SIP UDP port")
    parser.add_argument("--rtp-min", type=int, help="First RTP UDP port")
    parser.add_argument("--rtp-max", type=int, help="Last RTP UDP port")
    parser.add_argument("--log-dir", help="Directory for per-call log files")
    parser.add_argument("--default-codec", type=str.upper, choices=sorted(CODEC_PAYLOADS), help="Preferred answer codec")
    parser.add_argument("--auth-realm", help="SIP digest authentication realm")
    parser.add_argument("--media-backend", choices=sorted(MEDIA_BACKENDS), help="B2BUA media backend")
    parser.add_argument("--rtpengine-url", help="RTPengine NG control URL, for example udp://127.0.0.1:2223")
    parser.add_argument("--rtpengine-timeout", type=float, help="RTPengine control timeout in seconds")
    parser.add_argument("--debug", action="store_true", default=None, help="Enable debug logging")
    args = parser.parse_args()

    try:
        config = apply_cli_overrides(load_config_file(args.config), args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    logging.basicConfig(
        level=logging.DEBUG if config.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    log_dir = resolve_log_dir(config)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.info("Writing call logs under %s", log_dir)
    else:
        logging.info("Persistent call logs disabled")
    sbc_logger = SbcLogger(log_dir)
    sbc_logger.platform(
        "SERVER CONFIG",
        f"sip={config.sip_ip}:{config.sip_port} rtp_range={config.rtp_min}-{config.rtp_max} media_backend={config.media_backend}",
    )

    media = MediaServer(
        config.sip_ip,
        config.rtp_min,
        config.rtp_max,
        log_dir,
        sbc_logger,
    )
    rtpengine_client = None
    if config.media_backend == "rtpengine":
        rtpengine_client = RtpengineClient(config.rtpengine_url, timeout=config.rtpengine_timeout)
        logging.info("Using RTPengine media backend at %s", config.rtpengine_url)
        sbc_logger.platform("RTPENGINE BACKEND ENABLED", f"url={config.rtpengine_url} timeout={config.rtpengine_timeout}")
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(
        lambda: SipServerProtocol(
            config.sip_ip,
            config.sip_port,
            media,
            sbc_logger,
            config.default_payload,
            config.auth_realm,
            config.users,
            config.bridge_rooms,
            config.b2bua_routes,
            config.route_policies,
            config.b2bua_ladder_logs,
            config.media_backend,
            rtpengine_client,
        ),
        local_addr=(config.sip_ip, config.sip_port),
    )

    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
