#!/usr/bin/env python3
"""SIP transaction cache and invalid-dialog smoke client."""

from __future__ import annotations

import argparse
import socket

from mini_call_server import CRLF
from smoke_utils import default_transcript_dir, write_transcript


SERVER_IP = "127.0.0.1"
SERVER_PORT = 15062
CLIENT_IP = "127.0.0.1"
CLIENT_SIP_PORT = 25063
CALL_ID = "smoke-transaction-001@127.0.0.1"
BRANCH = "z9hG4bK-smoke-transaction-001"


def build_options() -> str:
    headers = [
        f"OPTIONS sip:echo@{SERVER_IP}:{SERVER_PORT} SIP/2.0",
        f"Via: SIP/2.0/UDP {CLIENT_IP}:{CLIENT_SIP_PORT};branch={BRANCH};rport",
        "Max-Forwards: 70",
        f"From: <sip:tester@{CLIENT_IP}>;tag=transaction-smoke",
        f"To: <sip:echo@{SERVER_IP}>",
        f"Call-ID: {CALL_ID}",
        "CSeq: 1 OPTIONS",
        f"Contact: <sip:tester@{CLIENT_IP}:{CLIENT_SIP_PORT}>",
        "Content-Length: 0",
    ]
    return CRLF.join(headers) + CRLF + CRLF


def build_unknown_bye() -> str:
    headers = [
        f"BYE sip:echo@{SERVER_IP}:{SERVER_PORT} SIP/2.0",
        f"Via: SIP/2.0/UDP {CLIENT_IP}:{CLIENT_SIP_PORT};branch={BRANCH}-bye;rport",
        "Max-Forwards: 70",
        f"From: <sip:tester@{CLIENT_IP}>;tag=transaction-smoke",
        f"To: <sip:echo@{SERVER_IP}>;tag=unknown-server-tag",
        f"Call-ID: {CALL_ID}-unknown",
        "CSeq: 2 BYE",
        f"Contact: <sip:tester@{CLIENT_IP}:{CLIENT_SIP_PORT}>",
        "Content-Length: 0",
    ]
    return CRLF.join(headers) + CRLF + CRLF


def status_line(message: str) -> str:
    return message.splitlines()[0] if message else ""


def receive(sock: socket.socket) -> str:
    return sock.recvfrom(8192)[0].decode("utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(description="SIP transaction cache smoke client")
    parser.add_argument("--output-dir", default=default_transcript_dir(), help="Optional directory for the SIP transcript")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((CLIENT_IP, CLIENT_SIP_PORT))
    sock.settimeout(3)

    options = build_options()
    sock.sendto(options.encode("utf-8"), (SERVER_IP, SERVER_PORT))
    first_response = receive(sock)
    if not status_line(first_response).startswith("SIP/2.0 200"):
        raise RuntimeError(f"Expected OPTIONS 200 OK, got {status_line(first_response)}")

    sock.sendto(options.encode("utf-8"), (SERVER_IP, SERVER_PORT))
    replay_response = receive(sock)
    if replay_response != first_response:
        raise RuntimeError("Retransmitted OPTIONS did not receive the cached byte-for-byte response")

    bye = build_unknown_bye()
    sock.sendto(bye.encode("utf-8"), (SERVER_IP, SERVER_PORT))
    bye_response = receive(sock)
    if not status_line(bye_response).startswith("SIP/2.0 481"):
        raise RuntimeError(f"Expected unknown-dialog BYE 481, got {status_line(bye_response)}")

    transcript = [
        "=== SIP TRANSACTION SMOKE TEST ===",
        f"Target: udp:{SERVER_IP}:{SERVER_PORT}",
        f"Client SIP: udp:{CLIENT_IP}:{CLIENT_SIP_PORT}",
        "",
        "--- OPTIONS REQUEST ---",
        options,
        "--- OPTIONS RESPONSE ---",
        first_response,
        "--- RETRANSMITTED OPTIONS REQUEST ---",
        options,
        "--- CACHED OPTIONS RESPONSE ---",
        replay_response,
        "--- UNKNOWN-DIALOG BYE REQUEST ---",
        bye,
        "--- UNKNOWN-DIALOG BYE RESPONSE ---",
        bye_response,
        "--- RESULT ---",
        "PASS: cached OPTIONS replay and unknown-dialog BYE rejection completed successfully.",
        "",
    ]
    write_transcript(args.output_dir, "sip_transactions.log", transcript)
    print("PASS")
    print("OPTIONS cached replay matched byte-for-byte")
    print("Unknown-dialog BYE rejected with 481")


if __name__ == "__main__":
    main()
