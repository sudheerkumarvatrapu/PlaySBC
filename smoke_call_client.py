#!/usr/bin/env python3
"""Basic SIP call smoke client for mini_call_server.py."""

from __future__ import annotations

import argparse
import re
import socket
import struct
import time

from smoke_utils import default_transcript_dir, write_transcript
SERVER_IP = "127.0.0.1"
SERVER_PORT = 15062
CLIENT_IP = "127.0.0.1"
CLIENT_SIP_PORT = 25061
CLIENT_RTP_PORT = 26000
CALL_ID = "smoke-call-001@127.0.0.1"
BRANCH = "z9hG4bK-smoke-call-001"
FROM_TAG = "from-smoke-call-001"
CRLF = "\r\n"


def build_invite() -> str:
    sdp = CRLF.join(
        [
            "v=0",
            f"o=smoke-client {int(time.time())} 1 IN IP4 {CLIENT_IP}",
            "s=Smoke Call",
            f"c=IN IP4 {CLIENT_IP}",
            "t=0 0",
            f"m=audio {CLIENT_RTP_PORT} RTP/AVP 0 8 101",
            "a=rtpmap:0 PCMU/8000",
            "a=rtpmap:8 PCMA/8000",
            "a=rtpmap:101 telephone-event/8000",
            "a=fmtp:101 0-16",
            "a=sendrecv",
            "",
        ]
    )
    headers = [
        f"INVITE sip:echo@{SERVER_IP}:{SERVER_PORT} SIP/2.0",
        f"Via: SIP/2.0/UDP {CLIENT_IP}:{CLIENT_SIP_PORT};branch={BRANCH};rport",
        "Max-Forwards: 70",
        f"From: <sip:tester@{CLIENT_IP}>;tag={FROM_TAG}",
        f"To: <sip:echo@{SERVER_IP}>",
        f"Call-ID: {CALL_ID}",
        "CSeq: 1 INVITE",
        f"Contact: <sip:tester@{CLIENT_IP}:{CLIENT_SIP_PORT}>",
        "Content-Type: application/sdp",
        f"Content-Length: {len(sdp.encode('utf-8'))}",
    ]
    return CRLF.join(headers) + CRLF + CRLF + sdp


def build_ack(to_header: str) -> str:
    headers = [
        f"ACK sip:echo@{SERVER_IP}:{SERVER_PORT} SIP/2.0",
        f"Via: SIP/2.0/UDP {CLIENT_IP}:{CLIENT_SIP_PORT};branch={BRANCH}-ack;rport",
        "Max-Forwards: 70",
        f"From: <sip:tester@{CLIENT_IP}>;tag={FROM_TAG}",
        f"To: {to_header}",
        f"Call-ID: {CALL_ID}",
        "CSeq: 1 ACK",
        f"Contact: <sip:tester@{CLIENT_IP}:{CLIENT_SIP_PORT}>",
        "Content-Length: 0",
    ]
    return CRLF.join(headers) + CRLF + CRLF


def build_bye(to_header: str) -> str:
    headers = [
        f"BYE sip:echo@{SERVER_IP}:{SERVER_PORT} SIP/2.0",
        f"Via: SIP/2.0/UDP {CLIENT_IP}:{CLIENT_SIP_PORT};branch={BRANCH}-bye;rport",
        "Max-Forwards: 70",
        f"From: <sip:tester@{CLIENT_IP}>;tag={FROM_TAG}",
        f"To: {to_header}",
        f"Call-ID: {CALL_ID}",
        "CSeq: 2 BYE",
        f"Contact: <sip:tester@{CLIENT_IP}:{CLIENT_SIP_PORT}>",
        "Content-Length: 0",
    ]
    return CRLF.join(headers) + CRLF + CRLF


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


def send_rtp_and_dtmf(remote_rtp_port: int, digit: int = 5) -> tuple[str, str]:
    rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rtp_sock.bind((CLIENT_IP, CLIENT_RTP_PORT))
    rtp_sock.settimeout(3)

    payload = bytes([0xFF] * 160)
    packet = struct.pack("!BBHII", 0x80, 0, 1, 160, 0x12345678) + payload
    rtp_sock.sendto(packet, (SERVER_IP, remote_rtp_port))
    data, addr = rtp_sock.recvfrom(2048)

    if len(data) < 12:
        raise RuntimeError("Short RTP echo packet")

    payload_type = data[1] & 0x7F
    sequence = struct.unpack("!H", data[2:4])[0]
    echo_result = (
        f"RTP echo received from udp:{addr[0]}:{addr[1]} "
        f"bytes={len(data)} payload_type={payload_type} sequence={sequence}"
    )

    start_event = bytes([digit, 10, 0, 160])
    end_event = bytes([digit, 0x80 | 10, 1, 64])
    rtp_sock.sendto(struct.pack("!BBHII", 0x80, 101, 2, 320, 0x12345678) + start_event, (SERVER_IP, remote_rtp_port))
    rtp_sock.sendto(struct.pack("!BBHII", 0x80, 101, 3, 320, 0x12345678) + end_event, (SERVER_IP, remote_rtp_port))
    rtp_sock.close()
    return echo_result, f"DTMF sent digit={digit} payload_type=101"


def main() -> None:
    parser = argparse.ArgumentParser(description="Basic SIP call smoke client")
    parser.add_argument("--output-dir", default=default_transcript_dir(), help="Optional directory for the SIP transcript")
    args = parser.parse_args()

    sip_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sip_sock.bind((CLIENT_IP, CLIENT_SIP_PORT))
    sip_sock.settimeout(3)

    transcript = [
        "=== BASIC CALL SMOKE TEST ===",
        f"Target: udp:{SERVER_IP}:{SERVER_PORT}",
        f"Client SIP: udp:{CLIENT_IP}:{CLIENT_SIP_PORT}",
        f"Client RTP: udp:{CLIENT_IP}:{CLIENT_RTP_PORT}",
        "",
    ]

    invite = build_invite()
    sip_sock.sendto(invite.encode("utf-8"), (SERVER_IP, SERVER_PORT))
    transcript += ["--- INVITE REQUEST ---", invite]

    responses = []
    final_response = ""
    while True:
        data, addr = sip_sock.recvfrom(8192)
        message = data.decode("utf-8", errors="replace")
        responses.append((addr, message))
        if parse_status(message).startswith("SIP/2.0 200"):
            final_response = message
            break

    for addr, message in responses:
        transcript += ["--- INVITE RESPONSE ---", f"From: udp:{addr[0]}:{addr[1]}", message]

    to_header = header_value(final_response, "To")
    remote_rtp_port = parse_answer_rtp_port(final_response)

    ack = build_ack(to_header)
    sip_sock.sendto(ack.encode("utf-8"), (SERVER_IP, SERVER_PORT))
    transcript += ["--- ACK REQUEST ---", ack]

    rtp_result, dtmf_result = send_rtp_and_dtmf(remote_rtp_port)
    transcript += ["--- RTP CHECK ---", rtp_result]
    transcript += ["--- DTMF CHECK ---", dtmf_result]

    bye = build_bye(to_header)
    sip_sock.sendto(bye.encode("utf-8"), (SERVER_IP, SERVER_PORT))
    transcript += ["--- BYE REQUEST ---", bye]

    data, addr = sip_sock.recvfrom(8192)
    bye_response = data.decode("utf-8", errors="replace")
    transcript += ["--- BYE RESPONSE ---", f"From: udp:{addr[0]}:{addr[1]}", bye_response]
    transcript += ["--- RESULT ---", "PASS: basic SIP call and RTP echo completed successfully.", ""]

    write_transcript(args.output_dir, "sip_basic_call.log", transcript)
    print("PASS")
    print(rtp_result)
    print(dtmf_result)


if __name__ == "__main__":
    main()
