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


@dataclass(frozen=True)
class RtcpReceiverReport:
    reporter_ssrc: int
    source_ssrc: int
    fraction_lost: int
    cumulative_lost: int
    highest_sequence: int
    jitter: int
    last_sender_report: int
    delay_since_last_sender_report: int

    @property
    def loss_percent(self) -> float:
        return (self.fraction_lost / 256.0) * 100.0

    @property
    def jitter_ms(self) -> float:
        return (self.jitter / 8000.0) * 1000.0


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


def parse_receiver_reports(packets: Tuple[RtcpPacket, ...]) -> Tuple[RtcpReceiverReport, ...]:
    reports = []
    for packet in packets:
        if packet.packet_type not in {RTCP_SR, RTCP_RR} or packet.source_count <= 0:
            continue
        reporter_offset = 0
        blocks_offset = 24 if packet.packet_type == RTCP_SR else 4
        if len(packet.payload) < blocks_offset:
            raise ValueError("RTCP report payload is shorter than its sender section")
        reporter_ssrc = struct.unpack("!I", packet.payload[reporter_offset : reporter_offset + 4])[0]
        for index in range(packet.source_count):
            offset = blocks_offset + (index * 24)
            block = packet.payload[offset : offset + 24]
            if len(block) != 24:
                raise ValueError("RTCP receiver report block is truncated")
            source_ssrc = struct.unpack("!I", block[:4])[0]
            fraction_lost = block[4]
            cumulative_raw = int.from_bytes(block[5:8], "big", signed=False)
            cumulative_lost = cumulative_raw - (1 << 24) if cumulative_raw & 0x800000 else cumulative_raw
            highest_sequence, jitter, last_sender_report, delay = struct.unpack("!IIII", block[8:24])
            reports.append(
                RtcpReceiverReport(
                    reporter_ssrc,
                    source_ssrc,
                    fraction_lost,
                    cumulative_lost,
                    highest_sequence,
                    jitter,
                    last_sender_report,
                    delay,
                )
            )
    return tuple(reports)


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


def build_compound_receiver_report(
    *,
    reporter_ssrc: int,
    source_ssrc: int,
    cname: str,
    fraction_lost: int = 0,
    cumulative_lost: int = 0,
    highest_sequence: int = 0,
    jitter: int = 0,
    last_sender_report: int = 0,
    delay_since_last_sender_report: int = 0,
) -> bytes:
    cumulative = cumulative_lost & 0xFFFFFF
    report_block = (
        struct.pack("!I", source_ssrc & 0xFFFFFFFF)
        + bytes((fraction_lost & 0xFF,))
        + cumulative.to_bytes(3, "big")
        + struct.pack(
            "!IIII",
            highest_sequence & 0xFFFFFFFF,
            jitter & 0xFFFFFFFF,
            last_sender_report & 0xFFFFFFFF,
            delay_since_last_sender_report & 0xFFFFFFFF,
        )
    )
    payload = struct.pack("!I", reporter_ssrc & 0xFFFFFFFF) + report_block
    receiver_report = struct.pack("!BBH", 0x81, RTCP_RR, len(payload) // 4) + payload
    cname_bytes = cname.encode("utf-8")[:255]
    sdes_payload = struct.pack("!I", reporter_ssrc & 0xFFFFFFFF) + bytes((SDES_CNAME, len(cname_bytes))) + cname_bytes + b"\x00"
    sdes_payload += b"\x00" * ((-len(sdes_payload)) % 4)
    sdes = struct.pack("!BBH", 0x81, RTCP_SDES, len(sdes_payload) // 4) + sdes_payload
    compound = receiver_report + sdes
    parse_compound_rtcp(compound)
    return compound
