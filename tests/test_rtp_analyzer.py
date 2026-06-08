import unittest

from rtp.analyzer import RtpAnalyzer
from rtp.packet import RtpPacket


class RtpPacketTests(unittest.TestCase):
    def test_parse_and_build_rtp_packet(self):
        raw = RtpPacket.build(0, 42, 160, 1234, bytes([0x55] * 160), marker=True)
        packet = RtpPacket.parse(raw, arrival_time=1.0)

        self.assertEqual(packet.payload_type, 0)
        self.assertEqual(packet.sequence, 42)
        self.assertEqual(packet.timestamp, 160)
        self.assertEqual(packet.ssrc, 1234)
        self.assertTrue(packet.marker)
        self.assertEqual(packet.payload, bytes([0x55] * 160))


class RtpAnalyzerTests(unittest.TestCase):
    def test_sequence_gap_jitter_and_late_packet_metrics(self):
        analyzer = RtpAnalyzer()
        analyzer.observe(RtpPacket(0, 10, 160, 1, bytes([0x55] * 160), arrival_time=1.00))
        analyzer.observe(RtpPacket(0, 12, 480, 1, bytes([0x55] * 160), arrival_time=1.04))
        analyzer.observe(RtpPacket(0, 11, 320, 1, bytes([0x55] * 160), arrival_time=1.05))

        summary = analyzer.summary()
        self.assertEqual(summary["packet_loss"], 1)
        self.assertEqual(summary["sequence_gaps"], 1)
        self.assertEqual(summary["out_of_order"], 1)
        self.assertEqual(summary["late_packets"], 1)
        self.assertGreaterEqual(summary["jitter_ms"], 0)

    def test_silence_and_mos_summary(self):
        analyzer = RtpAnalyzer()
        analyzer.observe(RtpPacket(0, 1, 160, 1, bytes([0xFF] * 160), arrival_time=1.0))
        analyzer.observe(RtpPacket(0, 2, 320, 1, bytes([0x55] * 160), arrival_time=1.02))

        summary = analyzer.summary()
        self.assertEqual(summary["silence_percent"], 50.0)
        self.assertGreater(summary["mos"], 4.0)


if __name__ == "__main__":
    unittest.main()

