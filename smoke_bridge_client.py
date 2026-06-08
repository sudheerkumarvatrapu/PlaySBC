#!/usr/bin/env python3
"""Two-leg SIP bridge smoke client for mini_call_server.py."""

from __future__ import annotations

import argparse
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from mini_call_server import CRLF
from rtp.packet import RtpPacket
from smoke_utils import default_transcript_dir


SERVER_IP = "127.0.0.1"
SERVER_PORT = 15062
CLIENT_IP = "127.0.0.1"


def header_value(message: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", message, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else ""


def parse_status(message: str) -> str:
    return message.splitlines()[0] if message else ""


def parse_answer_rtp_port(message: str) -> int:
    match = re.search(r"^m=audio\s+(\d+)\s+RTP/AVP", message, re.MULTILINE)
    if not match:
        raise RuntimeError("No RTP port found in SDP answer")
    return int(match.group(1))


@dataclass
class BridgeLeg:
    name: str
    sip_port: int
    rtp_port: int
    call_id: str
    branch: str
    from_tag: str
    to_header: str = ""
    remote_rtp_port: int = 0

    def __post_init__(self) -> None:
        self.sip_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sip_sock.bind((CLIENT_IP, self.sip_port))
        self.sip_sock.settimeout(3)
        self.rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp_sock.bind((CLIENT_IP, self.rtp_port))
        self.rtp_sock.settimeout(3)

    def build_invite(self) -> str:
        sdp = CRLF.join(
            [
                "v=0",
                f"o=bridge-{self.name} {int(time.time())} 1 IN IP4 {CLIENT_IP}",
                f"s=Bridge {self.name}",
                f"c=IN IP4 {CLIENT_IP}",
                "t=0 0",
                f"m=audio {self.rtp_port} RTP/AVP 0 8 101",
                "a=rtpmap:0 PCMU/8000",
                "a=rtpmap:8 PCMA/8000",
                "a=rtpmap:101 telephone-event/8000",
                "a=fmtp:101 0-16",
                "a=sendrecv",
                "",
            ]
        )
        headers = [
            f"INVITE sip:bridge@{SERVER_IP}:{SERVER_PORT} SIP/2.0",
            f"Via: SIP/2.0/UDP {CLIENT_IP}:{self.sip_port};branch={self.branch};rport",
            "Max-Forwards: 70",
            f"From: <sip:{self.name}@{CLIENT_IP}>;tag={self.from_tag}",
            f"To: <sip:bridge@{SERVER_IP}>",
            f"Call-ID: {self.call_id}",
            "CSeq: 1 INVITE",
            f"Contact: <sip:{self.name}@{CLIENT_IP}:{self.sip_port}>",
            "Content-Type: application/sdp",
            f"Content-Length: {len(sdp.encode('utf-8'))}",
        ]
        return CRLF.join(headers) + CRLF + CRLF + sdp

    def establish(self) -> list[str]:
        invite = self.build_invite()
        self.sip_sock.sendto(invite.encode("utf-8"), (SERVER_IP, SERVER_PORT))
        transcript = [f"--- {self.name} INVITE REQUEST ---", invite]
        final_response = ""
        while True:
            data, addr = self.sip_sock.recvfrom(8192)
            message = data.decode("utf-8", errors="replace")
            transcript += [f"--- {self.name} INVITE RESPONSE ---", f"From: udp:{addr[0]}:{addr[1]}", message]
            if parse_status(message).startswith("SIP/2.0 200"):
                final_response = message
                break
        self.to_header = header_value(final_response, "To")
        self.remote_rtp_port = parse_answer_rtp_port(final_response)
        ack = self.build_ack()
        self.sip_sock.sendto(ack.encode("utf-8"), (SERVER_IP, SERVER_PORT))
        transcript += [f"--- {self.name} ACK REQUEST ---", ack]
        return transcript

    def build_ack(self) -> str:
        headers = [
            f"ACK sip:bridge@{SERVER_IP}:{SERVER_PORT} SIP/2.0",
            f"Via: SIP/2.0/UDP {CLIENT_IP}:{self.sip_port};branch={self.branch}-ack;rport",
            "Max-Forwards: 70",
            f"From: <sip:{self.name}@{CLIENT_IP}>;tag={self.from_tag}",
            f"To: {self.to_header}",
            f"Call-ID: {self.call_id}",
            "CSeq: 1 ACK",
            f"Contact: <sip:{self.name}@{CLIENT_IP}:{self.sip_port}>",
            "Content-Length: 0",
        ]
        return CRLF.join(headers) + CRLF + CRLF

    def build_bye(self) -> str:
        headers = [
            f"BYE sip:bridge@{SERVER_IP}:{SERVER_PORT} SIP/2.0",
            f"Via: SIP/2.0/UDP {CLIENT_IP}:{self.sip_port};branch={self.branch}-bye;rport",
            "Max-Forwards: 70",
            f"From: <sip:{self.name}@{CLIENT_IP}>;tag={self.from_tag}",
            f"To: {self.to_header}",
            f"Call-ID: {self.call_id}",
            "CSeq: 2 BYE",
            f"Contact: <sip:{self.name}@{CLIENT_IP}:{self.sip_port}>",
            "Content-Length: 0",
        ]
        return CRLF.join(headers) + CRLF + CRLF

    def send_rtp(self, sequence: int, timestamp: int, payload: bytes) -> None:
        packet = RtpPacket.build(0, sequence, timestamp, 0x22220000 + sequence, payload)
        self.rtp_sock.sendto(packet, (SERVER_IP, self.remote_rtp_port))

    def receive_rtp(self) -> RtpPacket:
        data, _ = self.rtp_sock.recvfrom(2048)
        return RtpPacket.parse(data)

    def bye(self) -> list[str]:
        request = self.build_bye()
        self.sip_sock.sendto(request.encode("utf-8"), (SERVER_IP, SERVER_PORT))
        response = self.sip_sock.recvfrom(8192)[0].decode("utf-8", errors="replace")
        return [f"--- {self.name} BYE REQUEST ---", request, f"--- {self.name} BYE RESPONSE ---", response]

    def close(self) -> None:
        self.sip_sock.close()
        self.rtp_sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Two-leg SIP bridge smoke client")
    parser.add_argument("--output-dir", default=str(default_transcript_dir()), help="Directory for the SIP transcript")
    args = parser.parse_args()

    leg_a = BridgeLeg("bridge-a", 25064, 26010, "smoke-bridge-a@127.0.0.1", "z9hG4bK-smoke-bridge-a", "tag-bridge-a")
    leg_b = BridgeLeg("bridge-b", 25065, 26012, "smoke-bridge-b@127.0.0.1", "z9hG4bK-smoke-bridge-b", "tag-bridge-b")
    transcript = ["=== TWO-LEG BRIDGE SMOKE TEST ===", ""]
    try:
        transcript += leg_a.establish()
        transcript += leg_b.establish()

        primer = bytes([0x7E] * 160)
        leg_b.send_rtp(1, 160, primer)
        time.sleep(0.05)

        bridged_payload = bytes([0x55] * 160)
        leg_a.send_rtp(2, 320, bridged_payload)
        bridged_packet = leg_b.receive_rtp()
        if bridged_packet.payload_type != 0 or bridged_packet.payload != bridged_payload:
            raise RuntimeError("Bridge RTP relay did not deliver the expected PCMU payload to leg B")

        transcript += [
            "--- BRIDGE RTP CHECK ---",
            (
                f"PASS: leg A payload relayed to leg B "
                f"payload_type={bridged_packet.payload_type} bytes={len(bridged_packet.payload)}"
            ),
        ]
        transcript += leg_a.bye()
        transcript += leg_b.bye()
        transcript += ["--- RESULT ---", "PASS: two-leg bridge call completed successfully.", ""]
        print("PASS")
        print("Two-leg RTP bridge completed")
    finally:
        leg_a.close()
        leg_b.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "sip_bridge_call.log").write_text("\n".join(transcript), encoding="utf-8")


if __name__ == "__main__":
    main()

