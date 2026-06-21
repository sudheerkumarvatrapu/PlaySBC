import unittest

from rtp.rtpengine import RtpengineClient, bdecode, bencode, decode_bytes, parse_rtpengine_url


class RtpengineEncodingTests(unittest.TestCase):
    def test_bencode_round_trip_for_ng_command(self):
        payload = {
            "command": "offer",
            "call-id": "call-1",
            "from-tag": "from-a",
            "replace": ["origin", "session-connection"],
            "sdp": "v=0\r\n",
        }

        decoded, position = bdecode(bencode(payload))

        self.assertEqual(position, len(bencode(payload)))
        self.assertEqual(decode_bytes(decoded), payload)

    def test_client_builds_offer_packet_with_cookie(self):
        client = RtpengineClient("udp://127.0.0.1:2223")

        packet = client.build_packet(
            "offer",
            client._sdp_fields("call-1", "tag-a", "v=0\r\n"),
            cookie="cookie1",
        )

        self.assertTrue(packet.startswith(b"cookie1 "))
        self.assertIn(b"7:command5:offer", packet)
        self.assertIn(b"7:call-id6:call-1", packet)
        self.assertIn(b"5:flagsl13:trust addresse", packet)

    def test_client_builds_codec_policy_for_transcoding(self):
        client = RtpengineClient("udp://127.0.0.1:2223")

        packet = client.build_packet(
            "offer",
            client._sdp_fields(
                "call-1",
                "tag-a",
                "v=0\r\n",
                codec={"mask": ["PCMU"], "transcode": ["PCMA"]},
            ),
            cookie="cookie1",
        )

        self.assertIn(b"5:codec", packet)
        self.assertIn(b"4:maskl4:PCMUe", packet)
        self.assertIn(b"9:transcodel4:PCMAe", packet)

    def test_client_decodes_cookie_response(self):
        client = RtpengineClient("udp://127.0.0.1:2223")
        response = b"cookie1 " + bencode({"result": "ok", "sdp": "v=0\r\n"})

        decoded = client.decode_response(response, cookie="cookie1")

        self.assertEqual(decoded["result"], "ok")
        self.assertEqual(decoded["sdp"], "v=0\r\n")

    def test_client_builds_ping_packet(self):
        client = RtpengineClient("udp://127.0.0.1:2223")

        packet = client.build_packet("ping", {}, cookie="cookie1")

        self.assertTrue(packet.startswith(b"cookie1 "))
        self.assertIn(b"7:command4:ping", packet)

    def test_client_builds_query_packet(self):
        client = RtpengineClient("udp://127.0.0.1:2223")

        packet = client.build_packet(
            "query",
            {"call-id": "call-1", "from-tag": "from-a", "to-tag": "to-b"},
            cookie="cookie1",
        )

        self.assertTrue(packet.startswith(b"cookie1 "))
        self.assertIn(b"7:command5:query", packet)
        self.assertIn(b"8:from-tag6:from-a", packet)
        self.assertIn(b"6:to-tag4:to-b", packet)

    def test_parse_url_requires_udp_host_and_port(self):
        endpoint = parse_rtpengine_url("udp://127.0.0.1:2223")
        self.assertEqual(endpoint.host, "127.0.0.1")
        self.assertEqual(endpoint.port, 2223)

        with self.assertRaises(ValueError):
            parse_rtpengine_url("tcp://127.0.0.1:2223")

        with self.assertRaises(ValueError):
            parse_rtpengine_url("udp://127.0.0.1")


if __name__ == "__main__":
    unittest.main()
