#!/usr/bin/env python3
"""Send periodic compound RTCP sender reports for PlaySBC media tests."""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from rtp.rtcp import build_compound_receiver_report, build_compound_sender_report, parse_compound_rtcp


def send_reports(
    *,
    local_ip: str,
    source_port: int,
    target_ip: str,
    target_port: int,
    ssrc: int,
    cname: str,
    duration_seconds: float,
    interval_seconds: float,
    expect_reply: bool,
    receiver_report: bool = False,
) -> int:
    started = time.monotonic()
    next_send = started
    sent = 0
    received = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((local_ip, source_port))
        sock.setblocking(False)
        while time.monotonic() - started < duration_seconds:
            now = time.monotonic()
            if now >= next_send:
                elapsed = max(now - started, 0.0)
                media_packets = int(elapsed * 50)
                if receiver_report:
                    report = build_compound_receiver_report(
                        reporter_ssrc=ssrc,
                        source_ssrc=ssrc ^ 0x01010101,
                        cname=cname,
                        highest_sequence=media_packets,
                        jitter=8,
                    )
                else:
                    report = build_compound_sender_report(
                        ssrc=ssrc,
                        cname=cname,
                        rtp_timestamp=media_packets * 160,
                        packet_count=media_packets,
                        octet_count=media_packets * 160,
                    )
                sock.sendto(report, (target_ip, target_port))
                sent += 1
                next_send += interval_seconds
            try:
                data, _addr = sock.recvfrom(4096)
                parse_compound_rtcp(data)
                received += 1
            except BlockingIOError:
                pass
            time.sleep(0.01)

    print(
        f"rtcp_sent={sent} rtcp_received={received} "
        f"source={local_ip}:{source_port} target={target_ip}:{target_port} ssrc={ssrc}"
    )
    if sent == 0:
        return 1
    if expect_reply and received == 0:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-ip", default="0.0.0.0")
    parser.add_argument("--source-port", type=int, required=True)
    parser.add_argument("--target-ip", required=True)
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--ssrc", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--cname", required=True)
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--expect-reply", action="store_true")
    parser.add_argument("--receiver-report", action="store_true")
    args = parser.parse_args()
    return send_reports(
        local_ip=args.local_ip,
        source_port=args.source_port,
        target_ip=args.target_ip,
        target_port=args.target_port,
        ssrc=args.ssrc,
        cname=args.cname,
        duration_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
        expect_reply=args.expect_reply,
        receiver_report=args.receiver_report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
