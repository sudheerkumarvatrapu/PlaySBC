#!/usr/bin/env python3
"""SIP REGISTER digest-auth smoke client for mini_call_server.py."""

from __future__ import annotations

import argparse
import socket
from pathlib import Path

from mini_call_server import CRLF, make_digest_response, parse_digest_header


SERVER_IP = "127.0.0.1"
SERVER_PORT = 15062
CLIENT_IP = "127.0.0.1"
CLIENT_SIP_PORT = 25062
USERNAME = "1001"
PASSWORD = "secret-password"
CALL_ID = "smoke-register-001@127.0.0.1"
BRANCH = "z9hG4bK-smoke-register-001"


def build_register(cseq: int, authorization: str = "") -> str:
    headers = [
        f"REGISTER sip:{SERVER_IP}:{SERVER_PORT} SIP/2.0",
        f"Via: SIP/2.0/UDP {CLIENT_IP}:{CLIENT_SIP_PORT};branch={BRANCH}-{cseq};rport",
        "Max-Forwards: 70",
        f"From: <sip:{USERNAME}@{SERVER_IP}>;tag=register-smoke",
        f"To: <sip:{USERNAME}@{SERVER_IP}>",
        f"Call-ID: {CALL_ID}",
        f"CSeq: {cseq} REGISTER",
        f"Contact: <sip:{USERNAME}@{CLIENT_IP}:{CLIENT_SIP_PORT}>",
    ]
    if authorization:
        headers.append(f"Authorization: {authorization}")
    headers.append("Content-Length: 0")
    return CRLF.join(headers) + CRLF + CRLF


def header_value(message: str, name: str) -> str:
    prefix = f"{name.lower()}:"
    for line in message.splitlines():
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def status_line(message: str) -> str:
    return message.splitlines()[0] if message else ""


def main() -> None:
    parser = argparse.ArgumentParser(description="SIP REGISTER digest auth smoke client")
    parser.add_argument("--output-dir", default=".", help="Directory for the SIP transcript")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((CLIENT_IP, CLIENT_SIP_PORT))
    sock.settimeout(3)

    transcript = [
        "=== REGISTER DIGEST AUTH SMOKE TEST ===",
        f"Target: udp:{SERVER_IP}:{SERVER_PORT}",
        f"Client SIP: udp:{CLIENT_IP}:{CLIENT_SIP_PORT}",
        "",
    ]

    first_request = build_register(1)
    sock.sendto(first_request.encode("utf-8"), (SERVER_IP, SERVER_PORT))
    first_response = sock.recvfrom(8192)[0].decode("utf-8", errors="replace")
    transcript += ["--- REGISTER REQUEST 1 ---", first_request, "--- REGISTER RESPONSE 1 ---", first_response]
    if not status_line(first_response).startswith("SIP/2.0 401"):
        raise RuntimeError(f"Expected 401 challenge, got {status_line(first_response)}")

    challenge = parse_digest_header(header_value(first_response, "WWW-Authenticate"))
    uri = f"sip:{SERVER_IP}:{SERVER_PORT}"
    response = make_digest_response(
        username=USERNAME,
        realm=challenge["realm"],
        password=PASSWORD,
        method="REGISTER",
        uri=uri,
        nonce=challenge["nonce"],
        nc="00000001",
        cnonce="smoke-client",
        qop="auth",
    )
    authorization = (
        f'Digest username="{USERNAME}", realm="{challenge["realm"]}", nonce="{challenge["nonce"]}", '
        f'uri="{uri}", response="{response}", algorithm=MD5, qop=auth, nc=00000001, cnonce="smoke-client"'
    )

    second_request = build_register(2, authorization)
    sock.sendto(second_request.encode("utf-8"), (SERVER_IP, SERVER_PORT))
    second_response = sock.recvfrom(8192)[0].decode("utf-8", errors="replace")
    transcript += ["--- REGISTER REQUEST 2 ---", second_request, "--- REGISTER RESPONSE 2 ---", second_response]
    if not status_line(second_response).startswith("SIP/2.0 200"):
        raise RuntimeError(f"Expected 200 OK, got {status_line(second_response)}")

    transcript += ["--- RESULT ---", "PASS: digest REGISTER completed successfully.", ""]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "sip_register_auth.log").write_text("\n".join(transcript), encoding="utf-8")
    print("PASS")
    print("REGISTER digest authentication completed")


if __name__ == "__main__":
    main()
