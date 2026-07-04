#!/usr/bin/env python3
"""Generate small RTP PCAP media fixtures for SIPp playback."""

from __future__ import annotations

import argparse
import math
import socket
import struct
from pathlib import Path


SAMPLE_RATE = 8000
PACKET_MS = 20
SAMPLES_PER_PACKET = SAMPLE_RATE * PACKET_MS // 1000
DTMF_EVENTS = {str(index): index for index in range(10)}
DTMF_EVENTS.update({"*": 10, "#": 11, "A": 12, "B": 13, "C": 14, "D": 15})


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return (~total) & 0xFFFF


def linear_to_ulaw(sample: int) -> int:
    bias = 0x84
    clip = 32635
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    sample = min(sample, clip) + bias

    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1

    mantissa = (sample >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def linear_to_alaw(sample: int) -> int:
    mask = 0xD5 if sample >= 0 else 0x55
    if sample < 0:
        sample = -sample - 1
    sample = min(sample, 32635)

    segment_ends = (0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF)
    segment = 0
    while segment < len(segment_ends) and sample > segment_ends[segment]:
        segment += 1

    if segment >= len(segment_ends):
        encoded = 0x7F
    else:
        encoded = segment << 4
        if segment < 2:
            encoded |= (sample >> 4) & 0x0F
        else:
            encoded |= (sample >> (segment + 3)) & 0x0F
    return encoded ^ mask


def tone_payload(codec: str, packet_index: int, frequency: int, amplitude: int) -> bytes:
    encoder = linear_to_ulaw if codec == "PCMU" else linear_to_alaw
    start_sample = packet_index * SAMPLES_PER_PACKET
    payload = bytearray()
    for offset in range(SAMPLES_PER_PACKET):
        sample_index = start_sample + offset
        pcm = int(amplitude * math.sin(2 * math.pi * frequency * sample_index / SAMPLE_RATE))
        payload.append(encoder(pcm))
    return bytes(payload)


def rtp_packet(
    payload: bytes,
    payload_type: int,
    sequence: int,
    timestamp: int,
    ssrc: int,
    marker: bool = False,
) -> bytes:
    marker_payload = (0x80 if marker else 0) | (payload_type & 0x7F)
    return struct.pack("!BBHII", 0x80, marker_payload, sequence & 0xFFFF, timestamp, ssrc) + payload


def dtmf_payload(event: int, duration: int, end: bool = False, volume: int = 10) -> bytes:
    flags_volume = (0x80 if end else 0) | (volume & 0x3F)
    return struct.pack("!BBH", event & 0xFF, flags_volume, duration & 0xFFFF)


def ip_udp_packet(payload: bytes, packet_id: int) -> bytes:
    src_ip = socket.inet_aton("10.0.0.1")
    dst_ip = socket.inet_aton("10.0.0.2")
    udp_length = 8 + len(payload)
    total_length = 20 + udp_length
    ip_header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total_length, packet_id & 0xFFFF, 0, 64, 17, 0, src_ip, dst_ip)
    ip_header = ip_header[:10] + struct.pack("!H", checksum(ip_header)) + ip_header[12:]
    udp_header = struct.pack("!HHHH", 4000, 4002, udp_length, 0)
    ethernet_header = b"\x02\x00\x00\x00\x00\x02" + b"\x02\x00\x00\x00\x00\x01" + struct.pack("!H", 0x0800)
    return ethernet_header + ip_header + udp_header + payload


def write_pcap(
    path: Path,
    codec: str,
    seconds: int,
    frequency: int,
    amplitude: int,
    dtmf_digit: str = "5",
    dtmf_start_ms: int = 1000,
    dtmf_duration_ms: int = 200,
) -> None:
    payload_type = 0 if codec == "PCMU" else 8
    packet_count = seconds * 1000 // PACKET_MS
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("wb") as fh:
        fh.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        event_id = DTMF_EVENTS[dtmf_digit.upper()]
        event_start = max(dtmf_start_ms // PACKET_MS, 0)
        event_packets = max(dtmf_duration_ms // PACKET_MS, 1)
        event_end = event_start + event_packets
        end_repeats = 3
        event_timestamp = event_start * SAMPLES_PER_PACKET
        for index in range(packet_count):
            if event_start <= index < event_end + end_repeats:
                event_index = min(index - event_start + 1, event_packets)
                is_end = index >= event_end
                payload = dtmf_payload(event_id, event_index * SAMPLES_PER_PACKET, end=is_end)
                rtp = rtp_packet(
                    payload,
                    101,
                    index,
                    event_timestamp,
                    0xC0DEC0DE,
                    marker=index == event_start,
                )
            else:
                payload = tone_payload(codec, index, frequency, amplitude)
                rtp = rtp_packet(payload, payload_type, index, index * SAMPLES_PER_PACKET, 0xC0DEC0DE)
            packet = ip_udp_packet(rtp, index)
            timestamp_us = index * PACKET_MS * 1000
            fh.write(struct.pack("<IIII", timestamp_us // 1_000_000, timestamp_us % 1_000_000, len(packet), len(packet)))
            fh.write(packet)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SIPp RTP PCAP fixtures")
    parser.add_argument("--output-dir", default="sipp/scenarios/pcap")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--frequency", type=int, default=440)
    parser.add_argument("--amplitude", type=int, default=10000)
    parser.add_argument("--dtmf-digit", choices=sorted(DTMF_EVENTS), default="5")
    parser.add_argument("--dtmf-start-ms", type=int, default=1000)
    parser.add_argument("--dtmf-duration-ms", type=int, default=200)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    common = (args.seconds, args.frequency, args.amplitude, args.dtmf_digit, args.dtmf_start_ms, args.dtmf_duration_ms)
    write_pcap(output_dir / f"g711u_{args.seconds}s.pcap", "PCMU", *common)
    write_pcap(output_dir / f"g711a_{args.seconds}s.pcap", "PCMA", *common)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
