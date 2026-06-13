#!/usr/bin/env python3
"""Replay RTP packets from a PCAP over normal UDP."""

from __future__ import annotations

import argparse
import socket
import struct
import time
from pathlib import Path
from typing import Iterator, Tuple


def iter_rtp_from_pcap(path: Path) -> Iterator[Tuple[float, bytes]]:
    with path.open("rb") as fh:
        global_header = fh.read(24)
        if len(global_header) != 24:
            raise ValueError(f"Invalid PCAP header in {path}")

        magic = global_header[:4]
        if magic == b"\xd4\xc3\xb2\xa1":
            endian = "<"
        elif magic == b"\xa1\xb2\xc3\xd4":
            endian = ">"
        else:
            raise ValueError(f"Unsupported PCAP magic in {path}")

        first_ts = None
        while True:
            record_header = fh.read(16)
            if not record_header:
                return
            if len(record_header) != 16:
                raise ValueError(f"Truncated PCAP record header in {path}")

            ts_sec, ts_usec, included_len, _original_len = struct.unpack(f"{endian}IIII", record_header)
            packet = fh.read(included_len)
            if len(packet) != included_len:
                raise ValueError(f"Truncated PCAP packet in {path}")

            rtp = extract_rtp(packet)
            if not rtp:
                continue

            timestamp = ts_sec + (ts_usec / 1_000_000)
            if first_ts is None:
                first_ts = timestamp
            yield timestamp - first_ts, rtp


def extract_rtp(packet: bytes) -> bytes:
    if len(packet) < 14:
        return b""
    ether_type = struct.unpack("!H", packet[12:14])[0]
    if ether_type != 0x0800:
        return b""

    ip_offset = 14
    if len(packet) < ip_offset + 20:
        return b""
    version_ihl = packet[ip_offset]
    if version_ihl >> 4 != 4:
        return b""
    ihl = (version_ihl & 0x0F) * 4
    protocol = packet[ip_offset + 9]
    if protocol != 17:
        return b""

    udp_offset = ip_offset + ihl
    if len(packet) < udp_offset + 8:
        return b""
    rtp_offset = udp_offset + 8
    if len(packet) < rtp_offset + 12:
        return b""
    return packet[rtp_offset:]


def replay(path: Path, host: str, port: int, duration_ms: int, source_port: int = 0, expect_echo: bool = False) -> int:
    started = time.monotonic()
    sent = 0
    echo_received = False
    max_seconds = duration_ms / 1000 if duration_ms > 0 else None
    destination = (host, port)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        if source_port:
            sock.bind(("", source_port))
        if expect_echo:
            sock.settimeout(1)
        for packet_time, rtp in iter_rtp_from_pcap(path):
            if max_seconds is not None and packet_time > max_seconds:
                break

            target_time = started + packet_time
            delay = target_time - time.monotonic()
            if delay > 0:
                time.sleep(delay)

            sock.sendto(rtp, destination)
            sent += 1

        if expect_echo:
            try:
                data, addr = sock.recvfrom(2048)
                echo_received = len(data) >= 12
                print(f"echo_received={echo_received} source={addr[0]}:{addr[1]} bytes={len(data)}")
            except socket.timeout:
                print("echo_received=False")

    print(f"sent_packets={sent} destination={host}:{port} source_port={source_port or 'auto'} pcap={path}")
    return 0 if sent and (echo_received or not expect_echo) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay RTP from a PCAP over UDP")
    parser.add_argument("--pcap", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--duration-ms", type=int, default=0)
    parser.add_argument("--source-port", type=int, default=0, help="Optional local UDP source port")
    parser.add_argument("--expect-echo", action="store_true", help="Fail unless at least one RTP packet is echoed back")
    args = parser.parse_args()
    return replay(Path(args.pcap), args.host, args.port, args.duration_ms, args.source_port, args.expect_echo)


if __name__ == "__main__":
    raise SystemExit(main())
