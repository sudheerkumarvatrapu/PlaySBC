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
import json
import logging
import random
import re
import socket
import struct
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
    log_dir: str = "logs"
    recording_dir: str = "recordings"
    default_codec: str = "PCMU"
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
    "recording_dir",
    "default_codec",
    "debug",
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

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


@dataclass
class CallArtifacts:
    call_id: str
    log_dir: Path
    recording_dir: Path
    log_path: Path = field(init=False)
    wav_path: Path = field(init=False)
    wav_file: Optional[wave.Wave_write] = field(default=None, init=False)
    recording_warning_logged: bool = field(default=False, init=False)
    unsupported_payloads_logged: set = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        safe_call_id = safe_filename(self.call_id)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.recording_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / f"{safe_call_id}.log"
        self.wav_path = self.recording_dir / f"{safe_call_id}.wav"
        self.log_path.write_text("", encoding="utf-8")
        self.log("CALL START", f"call_id={self.call_id}")

    def log(self, event: str, detail: str = "") -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        line = f"{timestamp} {event}"
        if detail:
            line += f" {detail}"
        with self.log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")

    def record_payload(self, payload_type: int, payload: bytes) -> None:
        if not payload:
            return

        if audioop is None:
            if not self.recording_warning_logged:
                self.log("RECORDING SKIPPED", "audioop unavailable; cannot convert G.711 to WAV")
                self.recording_warning_logged = True
            return

        if payload_type == PCMU:
            pcm = audioop.ulaw2lin(payload, 2)
        elif payload_type == PCMA:
            pcm = audioop.alaw2lin(payload, 2)
        else:
            if payload_type not in self.unsupported_payloads_logged:
                self.log("RECORDING SKIPPED", f"unsupported_payload_type={payload_type}")
                self.unsupported_payloads_logged.add(payload_type)
            return

        if self.wav_file is None:
            self.wav_file = wave.open(str(self.wav_path), "wb")
            self.wav_file.setnchannels(1)
            self.wav_file.setsampwidth(2)
            self.wav_file.setframerate(8000)
            self.log("RECORDING START", f"path={self.wav_path}")

        self.wav_file.writeframes(pcm)

    def close(self) -> None:
        if self.wav_file:
            self.wav_file.close()
            self.wav_file = None
            self.log("RECORDING CLOSED", f"path={self.wav_path}")


@dataclass
class RtpSession:
    call_id: str
    local_ip: str
    local_port: int
    preferred_payload: int = PCMU
    remote_payloads: Tuple[int, ...] = field(default_factory=tuple)
    artifacts: Optional[CallArtifacts] = None
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
    closed: bool = False

    def log(self, event: str, detail: str = "") -> None:
        if self.artifacts:
            self.artifacts.log(event, detail)

    def mark_ack(self) -> None:
        self.acknowledged_at = time.time()
        self.log("ACK RECEIVED")

    def record_rtp(self, payload_type: int, payload: bytes) -> None:
        self.packets_received += 1
        self.bytes_received += len(payload)
        self.payload_types_received[payload_type] = self.payload_types_received.get(payload_type, 0) + 1
        self.last_rtp_at = time.time()
        if self.artifacts:
            self.artifacts.record_payload(payload_type, payload)

    def record_rtp_sent(self, payload: bytes) -> None:
        self.packets_sent += 1
        self.bytes_sent += len(payload)
        self.last_rtp_at = time.time()

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
        self.log(
            "CALL SUMMARY",
            (
                f"duration_seconds={duration:.3f} "
                f"rtp_packets_received={self.packets_received} "
                f"rtp_packets_sent={self.packets_sent} "
                f"rtp_bytes_received={self.bytes_received} "
                f"rtp_bytes_sent={self.bytes_sent} "
                f"payloads_received={payloads}"
            ),
        )
        if self.artifacts:
            self.artifacts.close()


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
        self.session.log("RTP LISTENING", f"local={self.session.local_ip}:{self.session.local_port}")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        if len(data) < 12:
            return

        first_packet = self.session.remote_addr is None
        self.session.remote_addr = addr
        version = data[0] >> 6
        if version != 2:
            return

        csrc_count = data[0] & 0x0F
        header_len = 12 + (csrc_count * 4)
        if len(data) < header_len:
            return

        src_payload_type = data[1] & 0x7F
        payload = data[header_len:]
        if first_packet:
            self.session.log(
                "RTP REMOTE",
                f"remote={addr[0]}:{addr[1]} first_payload_type={CODEC_NAMES.get(src_payload_type, src_payload_type)}",
            )
        self.session.record_rtp(src_payload_type, payload)

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
            self.session.record_rtp_sent(out_payload)


class MediaServer:
    def __init__(self, local_ip: str, port_min: int, port_max: int, log_dir: Path, recording_dir: Path):
        self.local_ip = local_ip
        self.port_min = port_min if port_min % 2 == 0 else port_min + 1
        self.port_max = port_max
        self.log_dir = log_dir
        self.recording_dir = recording_dir
        self.sessions: Dict[str, RtpSession] = {}
        self.transcoder = G711Transcoder()
        self._next_port = self.port_min

    async def create_session(
        self,
        call_id: str,
        preferred_payload: int,
        remote_payloads: Tuple[int, ...],
    ) -> RtpSession:
        if call_id in self.sessions:
            return self.sessions[call_id]

        loop = asyncio.get_running_loop()
        local_port = self._allocate_port()
        artifacts = CallArtifacts(call_id=call_id, log_dir=self.log_dir, recording_dir=self.recording_dir)
        session = RtpSession(
            call_id=call_id,
            local_ip=self.local_ip,
            local_port=local_port,
            preferred_payload=preferred_payload,
            remote_payloads=remote_payloads,
            artifacts=artifacts,
        )
        await loop.create_datagram_endpoint(
            lambda: RtpProtocol(session, self.transcoder),
            local_addr=(self.local_ip, local_port),
        )
        self.sessions[call_id] = session
        return session

    def get_session(self, call_id: str) -> Optional[RtpSession]:
        return self.sessions.get(call_id)

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
    def __init__(self, local_ip: str, local_port: int, media: MediaServer, default_payload: int):
        self.local_ip = local_ip
        self.local_port = local_port
        self.media = media
        self.default_payload = default_payload
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
            preferred_payload = choose_payload(remote_payloads, self.default_payload)
            call_id = message.header("call-id", make_call_id())
            rtp = await self.media.create_session(call_id, preferred_payload, remote_payloads)
            rtp.log(
                "INVITE RECEIVED",
                (
                    f"source={message.source[0]}:{message.source[1]} "
                    f"from={message.header('from')} to={message.header('to')}"
                ),
            )
            rtp.log(
                "SDP OFFER",
                f"payloads={format_payloads(remote_payloads)} selected={CODEC_NAMES.get(preferred_payload, preferred_payload)}",
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
                extra_headers={
                    "Contact": f"<sip:python-call-server@{self.local_ip}:{self.local_port}>",
                    "Content-Type": "application/sdp",
                },
            )
            rtp.log("SIP RESPONSE", "200 OK")
            return

        if method == "ACK":
            session = self.media.get_session(message.header("call-id"))
            if session:
                session.mark_ack()
            return

        if method == "BYE":
            session = self.media.get_session(message.header("call-id"))
            if session:
                session.log("BYE RECEIVED", f"source={message.source[0]}:{message.source[1]}")
            self.send_response(message, 200, "OK")
            if session:
                session.log("SIP RESPONSE", "200 OK for BYE")
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


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "call"


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
    if key == "debug":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if key in {"sip_ip", "log_dir", "recording_dir", "default_codec"}:
        return str(value)
    return value


def apply_cli_overrides(config: ServerConfig, args: argparse.Namespace) -> ServerConfig:
    overrides = {
        "sip_ip": args.sip_ip,
        "sip_port": args.sip_port,
        "rtp_min": args.rtp_min,
        "rtp_max": args.rtp_max,
        "log_dir": args.log_dir,
        "recording_dir": args.recording_dir,
        "default_codec": args.default_codec,
        "debug": args.debug,
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


async def main() -> None:
    parser = argparse.ArgumentParser(description="Small SIP/RTP call server")
    parser.add_argument("--config", help="Path to a JSON config file")
    parser.add_argument("--ip", dest="sip_ip", help="IP address to bind and advertise")
    parser.add_argument("--sip-port", type=int, help="SIP UDP port")
    parser.add_argument("--rtp-min", type=int, help="First RTP UDP port")
    parser.add_argument("--rtp-max", type=int, help="Last RTP UDP port")
    parser.add_argument("--log-dir", help="Directory for per-call log files")
    parser.add_argument("--recording-dir", help="Directory for inbound RTP WAV recordings")
    parser.add_argument("--default-codec", type=str.upper, choices=sorted(CODEC_PAYLOADS), help="Preferred answer codec")
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

    media = MediaServer(
        config.sip_ip,
        config.rtp_min,
        config.rtp_max,
        Path(config.log_dir),
        Path(config.recording_dir),
    )
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(
        lambda: SipServerProtocol(config.sip_ip, config.sip_port, media, config.default_payload),
        local_addr=(config.sip_ip, config.sip_port),
    )

    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
