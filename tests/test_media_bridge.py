import unittest

import mini_call_server as server


class MediaBridgeTests(unittest.TestCase):
    def test_join_bridge_pairs_two_waiting_sessions(self):
        logger = server.SbcLogger(None)
        media = server.MediaServer("127.0.0.1", 12000, 12010, None, logger)
        first = server.RtpSession("call-a", "127.0.0.1", 12000, media_mode="bridge", bridge_id="bridge")
        second = server.RtpSession("call-b", "127.0.0.1", 12002, media_mode="bridge", bridge_id="bridge")

        media.join_bridge(first)
        self.assertIs(media.bridge_waiting["bridge"], first)

        media.join_bridge(second)
        self.assertIs(first.peer_session, second)
        self.assertIs(second.peer_session, first)
        self.assertNotIn("bridge", media.bridge_waiting)

    def test_dtmf_relay_preserves_telephone_event_payload(self):
        class FakeTransport:
            def __init__(self):
                self.sent = []

            def sendto(self, payload, destination):
                self.sent.append((payload, destination))

        source = server.RtpSession(
            "call-a",
            "127.0.0.1",
            12000,
            preferred_payload=server.PCMU,
            dtmf_payload_type=101,
            media_mode="b2bua",
        )
        peer = server.RtpSession(
            "call-b",
            "127.0.0.1",
            12002,
            preferred_payload=server.PCMA,
            dtmf_payload_type=101,
            media_mode="b2bua",
        )
        transport = FakeTransport()
        peer.transport = transport
        peer.remote_addr = ("127.0.0.1", 27000)
        source.set_peer(peer)
        start_payload = bytes([5, 0x0A, 0x00, 0xA0])
        end_payload = bytes([5, 0x8A, 0x06, 0x40])
        start_packet = server.RtpPacket(101, 42, 8000, 0x12345678, start_payload, marker=True)
        end_packet = server.RtpPacket(101, 43, 8000, 0x12345678, end_payload)

        protocol = server.RtpProtocol(source, server.G711Transcoder())
        initial_peer_timestamp = peer.timestamp
        protocol._relay_packet(start_packet)
        protocol._relay_packet(end_packet)

        self.assertEqual(len(transport.sent), 2)
        relayed_start = server.RtpPacket.parse(transport.sent[0][0])
        relayed_end = server.RtpPacket.parse(transport.sent[1][0])
        self.assertEqual(relayed_start.payload_type, 101)
        self.assertEqual(relayed_start.timestamp, (initial_peer_timestamp + 160) & 0xFFFFFFFF)
        self.assertEqual(relayed_end.timestamp, relayed_start.timestamp)
        self.assertEqual(relayed_end.payload, end_payload)
        self.assertTrue(relayed_start.marker)
        self.assertEqual(peer.timestamp, (relayed_start.timestamp + 1600) & 0xFFFFFFFF)


if __name__ == "__main__":
    unittest.main()
