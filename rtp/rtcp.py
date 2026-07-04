from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import Optional, Tuple


RTCP_SR = 200
RTCP_RR = 201
RTCP_SDES = 202
SDES_CNAME = 1


@dataclass(frozen=True)
class RtcpPacket:
    packet_type: int
    source_count: int
    payload: bytes


def parse_compound_rtcp(data: bytes) -> Tuple[RtcpPacket, ...]:
    packets = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 4:
            raise ValueError("RTCP packet is shorter than its fixed header")
        first, packet_type, length_words = struct.unpack("!BBH", data[offset : offset + 4])
        if first >> 6 != 2:
            raise ValueError("RTCP version must be 2")
        packet_length = (length_words + 1) * 4
        if packet_length < 4 or offset + packet_length > len(data):
            raise ValueError("RTCP packet length exceeds compound payload")
        packets.append(RtcpPacket(packet_type, first & 0x1F, data[offset + 4 : offset + packet_length]))
        offset += packet_length
    if not packets or packets[0].packet_type not in {RTCP_SR, RTCP_RR}:
        raise ValueError("Compound RTCP must begin with a sender or receiver report")
    return tuple(packets)


def ntp_timestamp(now: Optional[float] = None) -> Tuple[int, int]:
    current = time.time() if now is None else now
    seconds = int(current) + 2_208_988_800
    fraction = int((current - int(current)) * (1 << 32))
    return seconds & 0xFFFFFFFF, fraction & 0xFFFFFFFF


def build_compound_sender_report(
    *,
    ssrc: int,
    cname: str,
    rtp_timestamp: int,
    packet_count: int,
    octet_count: int,
    now: Optional[float] = None,
) -> bytes:
    ntp_seconds, ntp_fraction = ntp_timestamp(now)
    sender_payload = struct.pack(
        "!IIIIII",
        ssrc & 0xFFFFFFFF,
        ntp_seconds,
        ntp_fraction,
        rtp_timestamp & 0xFFFFFFFF,
        packet_count & 0xFFFFFFFF,
        octet_count & 0xFFFFFFFF,
    )
    sender_report = struct.pack("!BBH", 0x80, RTCP_SR, len(sender_payload) // 4) + sender_payload

    cname_bytes = cname.encode("utf-8")[:255]
    sdes_payload = struct.pack("!I", ssrc & 0xFFFFFFFF) + bytes((SDES_CNAME, len(cname_bytes))) + cname_bytes + b"\x00"
    sdes_payload += b"\x00" * ((-len(sdes_payload)) % 4)
    sdes = struct.pack("!BBH", 0x81, RTCP_SDES, len(sdes_payload) // 4) + sdes_payload
    compound = sender_report + sdes
    parse_compound_rtcp(compound)
    return compound
