"""Small G.711 speech helpers for AI voice gateway lab evidence."""

from __future__ import annotations

import math
import socket
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

try:
    import audioop  # type: ignore
except Exception:  # pragma: no cover - Python 3.13+ compatibility.
    audioop = None


SAMPLE_RATE = 8000
PACKET_MS = 20
SAMPLES_PER_PACKET = SAMPLE_RATE * PACKET_MS // 1000


@dataclass(frozen=True)
class RtpAudioExtraction:
    codec: str
    payload_type: int
    packets: int
    payload_bytes: int
    duration_seconds: float
    wav_path: str
    transcript: str = ""


@dataclass(frozen=True)
class RtpPrompt:
    codec: str
    payload_type: int
    packets: int
    wav_path: str
    rtp_pcap_path: str


def codec_payload_type(codec: str) -> int:
    normalized = codec.upper()
    if normalized == "PCMA":
        return 8
    return 0


def payload_type_codec(payload_type: int) -> str:
    if payload_type == 8:
        return "PCMA"
    return "PCMU"


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


def ulaw_to_linear_byte(value: int) -> int:
    value = (~value) & 0xFF
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    sample = ((mantissa << 3) + 0x84) << exponent
    sample -= 0x84
    return -sample if sign else sample


def alaw_to_linear_byte(value: int) -> int:
    value ^= 0x55
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    if exponent == 0:
        sample = (mantissa << 4) + 8
    else:
        sample = ((mantissa << 4) + 0x108) << (exponent - 1)
    return sample if sign else -sample


def pcm16_to_g711(pcm16: bytes, codec: str) -> bytes:
    normalized = codec.upper()
    if audioop is not None:
        return audioop.lin2alaw(pcm16, 2) if normalized == "PCMA" else audioop.lin2ulaw(pcm16, 2)
    encoder = linear_to_alaw if normalized == "PCMA" else linear_to_ulaw
    return bytes(encoder(sample) for (sample,) in struct.iter_unpack("<h", pcm16))


def g711_to_pcm16(payload: bytes, codec: str) -> bytes:
    normalized = codec.upper()
    if audioop is not None:
        return audioop.alaw2lin(payload, 2) if normalized == "PCMA" else audioop.ulaw2lin(payload, 2)
    decoder = alaw_to_linear_byte if normalized == "PCMA" else ulaw_to_linear_byte
    output = bytearray()
    for value in payload:
        output.extend(struct.pack("<h", decoder(value)))
    return bytes(output)


def write_wav(path: Path, pcm16: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16)


def resample_pcm16_mono(pcm16: bytes, source_rate: int, target_rate: int = SAMPLE_RATE) -> bytes:
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if source_rate == target_rate:
        return pcm16
    samples = [sample for (sample,) in struct.iter_unpack("<h", pcm16)]
    if not samples:
        return b""
    if len(samples) == 1:
        return struct.pack("<h", samples[0])

    target_count = max(1, round(len(samples) * target_rate / source_rate))
    output = bytearray()
    for index in range(target_count):
        source_position = index * source_rate / target_rate
        left = min(int(source_position), len(samples) - 1)
        right = min(left + 1, len(samples) - 1)
        fraction = source_position - left
        value = int(round(samples[left] + ((samples[right] - samples[left]) * fraction)))
        value = max(-32768, min(32767, value))
        output.extend(struct.pack("<h", value))
    return bytes(output)


def mixdown_pcm16(raw: bytes, channels: int) -> bytes:
    if channels <= 0:
        raise ValueError("speech WAV must have at least one channel")
    if channels == 1:
        return raw
    frame_size = channels * 2
    if len(raw) % frame_size:
        raw = raw[: len(raw) - (len(raw) % frame_size)]
    output = bytearray()
    for frame_index in range(0, len(raw), frame_size):
        frame = raw[frame_index : frame_index + frame_size]
        values = [sample for (sample,) in struct.iter_unpack("<h", frame)]
        mixed = int(round(sum(values) / len(values)))
        output.extend(struct.pack("<h", mixed))
    return bytes(output)


def read_wav_pcm(path: Path, target_rate: Optional[int] = None) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError("speech WAV must be PCM16")
        sample_rate = wav.getframerate()
        pcm = mixdown_pcm16(wav.readframes(wav.getnframes()), wav.getnchannels())
        if target_rate is not None and sample_rate != target_rate:
            pcm = resample_pcm16_mono(pcm, sample_rate, target_rate)
            sample_rate = target_rate
        return pcm, sample_rate


def pcap_records(path: Path) -> Iterator[tuple[float, bytes]]:
    with path.open("rb") as fh:
        header = fh.read(24)
        if len(header) != 24:
            raise ValueError(f"invalid PCAP header: {path}")
        magic = header[:4]
        if magic == b"\xd4\xc3\xb2\xa1":
            endian, precision = "<", 1_000_000
        elif magic == b"\xa1\xb2\xc3\xd4":
            endian, precision = ">", 1_000_000
        elif magic == b"\x4d\x3c\xb2\xa1":
            endian, precision = "<", 1_000_000_000
        elif magic == b"\xa1\xb2\x3c\x4d":
            endian, precision = ">", 1_000_000_000
        else:
            raise ValueError(f"unsupported PCAP magic: {path}")

        while True:
            record_header = fh.read(16)
            if not record_header:
                return
            if len(record_header) != 16:
                raise ValueError(f"truncated PCAP record header: {path}")
            ts_sec, ts_fraction, included_len, _original_len = struct.unpack(f"{endian}IIII", record_header)
            frame = fh.read(included_len)
            if len(frame) != included_len:
                raise ValueError(f"truncated PCAP frame: {path}")
            yield ts_sec + (ts_fraction / precision), frame


def extract_udp_payload(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < 14 or struct.unpack("!H", frame[12:14])[0] != 0x0800:
        return 0, b""
    ip_offset = 14
    if len(frame) < ip_offset + 20 or frame[ip_offset] >> 4 != 4:
        return 0, b""
    ihl = (frame[ip_offset] & 0x0F) * 4
    if frame[ip_offset + 9] != 17:
        return 0, b""
    udp_offset = ip_offset + ihl
    if len(frame) < udp_offset + 8:
        return 0, b""
    src_port, _dst_port, udp_length, _checksum = struct.unpack("!HHHH", frame[udp_offset : udp_offset + 8])
    payload = frame[udp_offset + 8 : udp_offset + max(udp_length, 8)]
    return src_port, payload


def iter_rtp_payloads(path: Path, payload_type: Optional[int] = None) -> Iterator[tuple[float, int, int, bytes]]:
    first_timestamp: Optional[float] = None
    for timestamp, frame in pcap_records(path):
        _src_port, udp_payload = extract_udp_payload(frame)
        if len(udp_payload) < 12 or udp_payload[0] >> 6 != 2:
            continue
        current_payload_type = udp_payload[1] & 0x7F
        if payload_type is not None and current_payload_type != payload_type:
            continue
        if current_payload_type == 101:
            continue
        sequence = struct.unpack("!H", udp_payload[2:4])[0]
        if first_timestamp is None:
            first_timestamp = timestamp
        yield timestamp - first_timestamp, sequence, current_payload_type, udp_payload[12:]


def transcript_sidecar(path: Path) -> str:
    candidates = [
        path.with_suffix(path.suffix + ".txt"),
        path.with_suffix(".txt"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
    return ""


def decode_rtp_pcap_to_wav(
    pcap_path: Path,
    wav_path: Path,
    codec: str = "PCMU",
    transcript: str = "",
) -> RtpAudioExtraction:
    payload_type = codec_payload_type(codec)
    packets = sorted(iter_rtp_payloads(pcap_path, payload_type=payload_type), key=lambda item: item[1])
    pcm = bytearray()
    payload_bytes = 0
    for _timestamp, _sequence, _payload_type, payload in packets:
        payload_bytes += len(payload)
        pcm.extend(g711_to_pcm16(payload, codec))
    write_wav(wav_path, bytes(pcm))
    duration = (len(pcm) / 2) / SAMPLE_RATE if pcm else 0.0
    return RtpAudioExtraction(
        codec=codec.upper(),
        payload_type=payload_type,
        packets=len(packets),
        payload_bytes=payload_bytes,
        duration_seconds=duration,
        wav_path=str(wav_path),
        transcript=transcript or transcript_sidecar(pcap_path),
    )


def lab_speech_pcm(text: str, seconds: float = 1.2, sample_rate: int = SAMPLE_RATE) -> bytes:
    duration = max(seconds, 0.4)
    samples = int(duration * sample_rate)
    seed = sum(ord(character) for character in text) or 440
    base = 260 + (seed % 260)
    output = bytearray()
    for index in range(samples):
        syllable = ((index // (sample_rate // 5)) % 5) + 1
        envelope = 0.30 + (0.12 * syllable)
        sample = int(
            9000
            * envelope
            * math.sin(2 * math.pi * (base + (syllable * 35)) * index / sample_rate)
        )
        output.extend(struct.pack("<h", sample))
    return bytes(output)


def rtp_packet(payload: bytes, payload_type: int, sequence: int, timestamp: int, ssrc: int, marker: bool = False) -> bytes:
    return struct.pack(
        "!BBHII",
        0x80,
        (0x80 if marker else 0) | (payload_type & 0x7F),
        sequence & 0xFFFF,
        timestamp & 0xFFFFFFFF,
        ssrc & 0xFFFFFFFF,
    ) + payload


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return (~total) & 0xFFFF


def ethernet_ipv4_udp(payload: bytes, packet_id: int, src_ip: str, src_port: int, dst_ip: str, dst_port: int) -> bytes:
    src = socket.inet_aton(src_ip)
    dst = socket.inet_aton(dst_ip)
    udp_length = 8 + len(payload)
    total_length = 20 + udp_length
    ip_header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total_length, packet_id & 0xFFFF, 0, 64, 17, 0, src, dst)
    ip_header = ip_header[:10] + struct.pack("!H", checksum(ip_header)) + ip_header[12:]
    udp_header = struct.pack("!HHHH", src_port & 0xFFFF, dst_port & 0xFFFF, udp_length, 0)
    ethernet_header = b"\x02\x00\x00\x00\x00\x02" + b"\x02\x00\x00\x00\x00\x01" + struct.pack("!H", 0x0800)
    return ethernet_header + ip_header + udp_header + payload


def write_rtp_pcap(
    path: Path,
    rtp_packets: Iterable[bytes],
    src_ip: str = "10.10.10.20",
    src_port: int = 39000,
    dst_ip: str = "10.10.10.40",
    dst_port: int = 30000,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("wb") as fh:
        fh.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for count, packet in enumerate(rtp_packets, start=1):
            frame = ethernet_ipv4_udp(packet, count, src_ip, src_port, dst_ip, dst_port)
            timestamp_us = (count - 1) * PACKET_MS * 1000
            fh.write(struct.pack("<IIII", timestamp_us // 1_000_000, timestamp_us % 1_000_000, len(frame), len(frame)))
            fh.write(frame)
    return count


def wav_to_rtp_packets(
    wav_path: Path,
    codec: str = "PCMU",
    sequence_base: int = 42000,
    timestamp_base: int = 88000,
    ssrc: int = 0xA1775001,
) -> list[bytes]:
    pcm, sample_rate = read_wav_pcm(wav_path, target_rate=SAMPLE_RATE)
    if sample_rate != SAMPLE_RATE:
        raise ValueError(f"speech WAV sample rate must be {SAMPLE_RATE}, got {sample_rate}")
    payload_type = codec_payload_type(codec)
    packets: list[bytes] = []
    bytes_per_packet = SAMPLES_PER_PACKET * 2
    for index in range(0, len(pcm), bytes_per_packet):
        chunk = pcm[index : index + bytes_per_packet]
        if len(chunk) < bytes_per_packet:
            chunk = chunk + (b"\x00" * (bytes_per_packet - len(chunk)))
        payload = pcm16_to_g711(chunk, codec)
        packet_index = index // bytes_per_packet
        packets.append(
            rtp_packet(
                payload,
                payload_type,
                sequence_base + packet_index,
                timestamp_base + (packet_index * SAMPLES_PER_PACKET),
                ssrc,
                marker=packet_index == 0,
            )
        )
    return packets


def synthesize_lab_tts_to_rtp(text: str, wav_path: Path, rtp_pcap_path: Path, codec: str = "PCMU") -> RtpPrompt:
    pcm = lab_speech_pcm(text)
    write_wav(wav_path, pcm)
    packets = wav_to_rtp_packets(wav_path, codec=codec)
    packet_count = write_rtp_pcap(rtp_pcap_path, packets)
    return RtpPrompt(
        codec=codec.upper(),
        payload_type=codec_payload_type(codec),
        packets=packet_count,
        wav_path=str(wav_path),
        rtp_pcap_path=str(rtp_pcap_path),
    )
