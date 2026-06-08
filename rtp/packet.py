from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RtpPacket:
    payload_type: int
    sequence: int
    timestamp: int
    ssrc: int
    payload: bytes
    marker: bool = False
    arrival_time: float = field(default_factory=time.time)

    @classmethod
    def parse(cls, data: bytes, arrival_time: Optional[float] = None) -> "RtpPacket":
        if len(data) < 12:
            raise ValueError("RTP packet is shorter than the fixed 12-byte header")

        version = data[0] >> 6
        if version != 2:
            raise ValueError(f"Unsupported RTP version {version}")

        has_extension = bool(data[0] & 0x10)
        csrc_count = data[0] & 0x0F
        header_len = 12 + (csrc_count * 4)
        if len(data) < header_len:
            raise ValueError("RTP packet is shorter than its CSRC header")

        if has_extension:
            if len(data) < header_len + 4:
                raise ValueError("RTP packet extension header is incomplete")
            extension_words = struct.unpack("!H", data[header_len + 2 : header_len + 4])[0]
            header_len += 4 + (extension_words * 4)
            if len(data) < header_len:
                raise ValueError("RTP packet extension payload is incomplete")

        payload_type = data[1] & 0x7F
        marker = bool(data[1] & 0x80)
        sequence, timestamp, ssrc = struct.unpack("!HII", data[2:12])
        return cls(
            payload_type=payload_type,
            sequence=sequence,
            timestamp=timestamp,
            ssrc=ssrc,
            payload=data[header_len:],
            marker=marker,
            arrival_time=time.time() if arrival_time is None else arrival_time,
        )

    @staticmethod
    def build(payload_type: int, sequence: int, timestamp: int, ssrc: int, payload: bytes, marker: bool = False) -> bytes:
        return struct.pack(
            "!BBHII",
            0x80,
            (0x80 if marker else 0x00) | (payload_type & 0x7F),
            sequence & 0xFFFF,
            timestamp & 0xFFFFFFFF,
            ssrc & 0xFFFFFFFF,
        ) + payload
