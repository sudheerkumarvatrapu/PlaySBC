from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from .jitter import RtpJitterBuffer
from .packet import RtpPacket


PCMU = 0
PCMA = 8


@dataclass
class RtpAnalyzer:
    clock_rate: int = 8000
    jitter: RtpJitterBuffer = field(default_factory=RtpJitterBuffer)
    payload_counts: Dict[int, int] = field(default_factory=dict)
    audio_packets: int = 0
    silence_packets: int = 0
    first_arrival: Optional[float] = None
    first_timestamp: Optional[int] = None
    last_arrival: Optional[float] = None
    last_timestamp: Optional[int] = None

    def observe(self, packet: RtpPacket, record_audio: bool = True) -> None:
        self.jitter.observe(packet)
        self.payload_counts[packet.payload_type] = self.payload_counts.get(packet.payload_type, 0) + 1

        if record_audio and packet.payload_type in {PCMU, PCMA}:
            if self.first_arrival is None:
                self.first_arrival = packet.arrival_time
                self.first_timestamp = packet.timestamp
            self.last_arrival = packet.arrival_time
            self.last_timestamp = packet.timestamp
            self.audio_packets += 1
            if is_silence_payload(packet.payload_type, packet.payload):
                self.silence_packets += 1

    def summary(self) -> Dict[str, float]:
        metrics = self.jitter.metrics
        jitter_ms = round(metrics.jitter_ms, 3)
        loss_percent = round(metrics.packet_loss_percent, 3)
        silence_percent = round(self.silence_percent, 3)
        return {
            "packet_loss": metrics.packet_loss,
            "packet_loss_percent": loss_percent,
            "jitter_ms": jitter_ms,
            "out_of_order": metrics.out_of_order,
            "late_packets": metrics.late_packets,
            "duplicate_packets": metrics.duplicate_packets,
            "sequence_gaps": metrics.sequence_gaps,
            "clock_drift_ppm": round(self.clock_drift_ppm, 3),
            "silence_percent": silence_percent,
            "mos": round(estimate_mos(loss_percent, jitter_ms), 2),
        }

    def summary_text(self) -> str:
        summary = self.summary()
        return " ".join(f"{key}={value}" for key, value in summary.items())

    @property
    def silence_percent(self) -> float:
        if self.audio_packets == 0:
            return 0.0
        return (self.silence_packets / self.audio_packets) * 100.0

    @property
    def clock_drift_ppm(self) -> float:
        if (
            self.first_arrival is None
            or self.last_arrival is None
            or self.first_timestamp is None
            or self.last_timestamp is None
        ):
            return 0.0
        wall_samples = (self.last_arrival - self.first_arrival) * self.clock_rate
        if wall_samples <= 0:
            return 0.0
        rtp_samples = (self.last_timestamp - self.first_timestamp) & 0xFFFFFFFF
        return ((rtp_samples - wall_samples) / wall_samples) * 1_000_000.0


def is_silence_payload(payload_type: int, payload: bytes) -> bool:
    if not payload:
        return False
    if payload_type == PCMU:
        return all(sample == 0xFF for sample in payload)
    if payload_type == PCMA:
        return all(sample in {0xD5, 0x55} for sample in payload)
    return False


def estimate_mos(packet_loss_percent: float, jitter_ms: float) -> float:
    mos = 4.5 - (packet_loss_percent * 0.035) - (jitter_ms * 0.01)
    return max(1.0, min(4.5, mos))
