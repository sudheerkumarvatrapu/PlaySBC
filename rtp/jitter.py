from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set

from .packet import RtpPacket


RTP_SEQUENCE_MODULO = 65536
RTP_HALF_RANGE = 32768


@dataclass
class JitterMetrics:
    packets: int = 0
    expected_packets: int = 0
    packet_loss: int = 0
    sequence_gaps: int = 0
    out_of_order: int = 0
    late_packets: int = 0
    duplicate_packets: int = 0
    jitter_ms: float = 0.0

    @property
    def packet_loss_percent(self) -> float:
        if self.expected_packets <= 0:
            return 0.0
        return max(0.0, min(100.0, (self.packet_loss / self.expected_packets) * 100.0))


@dataclass
class RtpJitterBuffer:
    clock_rate: int = 8000
    max_late_sequence_window: int = 64
    expected_sequence: Optional[int] = None
    highest_sequence: Optional[int] = None
    previous_transit: Optional[float] = None
    jitter_samples: float = 0.0
    seen_sequences: Set[int] = field(default_factory=set)
    metrics: JitterMetrics = field(default_factory=JitterMetrics)

    def observe(self, packet: RtpPacket) -> JitterMetrics:
        self.metrics.packets += 1
        self._observe_sequence(packet.sequence)
        self._observe_jitter(packet)
        return self.metrics

    def _observe_sequence(self, sequence: int) -> None:
        if sequence in self.seen_sequences:
            self.metrics.duplicate_packets += 1
            return
        self.seen_sequences.add(sequence)

        if self.expected_sequence is None:
            self.expected_sequence = (sequence + 1) % RTP_SEQUENCE_MODULO
            self.highest_sequence = sequence
            self.metrics.expected_packets = 1
            return

        delta = (sequence - self.expected_sequence) % RTP_SEQUENCE_MODULO
        if delta == 0:
            self.expected_sequence = (sequence + 1) % RTP_SEQUENCE_MODULO
            self.highest_sequence = sequence
            self.metrics.expected_packets += 1
            return

        if delta < RTP_HALF_RANGE:
            missing = delta
            self.metrics.packet_loss += missing
            self.metrics.sequence_gaps += 1
            self.metrics.expected_packets += missing + 1
            self.expected_sequence = (sequence + 1) % RTP_SEQUENCE_MODULO
            self.highest_sequence = sequence
            return

        self.metrics.out_of_order += 1
        distance_late = (self.expected_sequence - sequence) % RTP_SEQUENCE_MODULO
        if distance_late <= self.max_late_sequence_window:
            self.metrics.late_packets += 1

    def _observe_jitter(self, packet: RtpPacket) -> None:
        arrival_samples = packet.arrival_time * self.clock_rate
        transit = arrival_samples - packet.timestamp
        if self.previous_transit is not None:
            delta = abs(transit - self.previous_transit)
            self.jitter_samples += (delta - self.jitter_samples) / 16.0
            self.metrics.jitter_ms = (self.jitter_samples / self.clock_rate) * 1000.0
        self.previous_transit = transit
