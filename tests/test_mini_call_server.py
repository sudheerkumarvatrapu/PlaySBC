import argparse
import tempfile
import unittest
from pathlib import Path

import mini_call_server as server


class SipParsingTests(unittest.TestCase):
    def test_parse_sip_message_and_compact_headers(self):
        raw = (
            "OPTIONS sip:echo@127.0.0.1 SIP/2.0\r\n"
            "v: SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK\r\n"
            "f: <sip:tester@127.0.0.1>;tag=abc\r\n"
            "t: <sip:echo@127.0.0.1>\r\n"
            "i: call-1\r\n"
            "CSeq: 1 OPTIONS\r\n"
            "l: 0\r\n"
            "\r\n"
        )
        message = server.parse_sip_message(raw, ("127.0.0.1", 25060))
        self.assertEqual(message.method, "OPTIONS")
        self.assertEqual(message.header("via"), "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK")
        self.assertEqual(message.header("call-id"), "call-1")
        self.assertEqual(message.header("content-length"), "0")

    def test_sdp_payload_and_dtmf_detection(self):
        sdp = (
            "c=IN IP4 127.0.0.1\r\n"
            "m=audio 26000 RTP/AVP 0 8 101\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:8 PCMA/8000\r\n"
            "a=rtpmap:101 telephone-event/8000\r\n"
        )
        self.assertEqual(server.parse_sdp_payloads(sdp), (0, 8, 101))
        self.assertEqual(server.parse_dtmf_payload_type(sdp), 101)
        self.assertEqual(server.parse_sdp_remote_addr(sdp), ("127.0.0.1", 26000))

    def test_parse_sip_uri_with_default_and_explicit_ports(self):
        explicit = server.parse_sip_uri("<sip:1002@127.0.0.1:25082>")
        self.assertEqual(explicit.user, "1002")
        self.assertEqual(explicit.address, ("127.0.0.1", 25082))

        default = server.parse_sip_uri("sip:1003@example.test")
        self.assertEqual(default.address, ("example.test", 5060))

    def test_make_sdp_can_include_multiple_codecs_and_dtmf(self):
        sdp = server.make_sdp("127.0.0.1", 30000, server.PCMU, dtmf_payload_type=101, payloads=(0, 8, 101))
        self.assertIn("m=audio 30000 RTP/AVP 0 8 101", sdp)
        self.assertIn("a=rtpmap:0 PCMU/8000", sdp)
        self.assertIn("a=rtpmap:8 PCMA/8000", sdp)
        self.assertIn("a=rtpmap:101 telephone-event/8000", sdp)


class CodecTests(unittest.TestCase):
    def test_choose_payload_prefers_default_when_remote_supports_it(self):
        self.assertEqual(server.choose_payload((0, 8), server.PCMA), server.PCMA)

    def test_choose_payload_falls_back_to_remote_supported_codec(self):
        self.assertEqual(server.choose_payload((0,), server.PCMA), server.PCMU)


class DigestAuthTests(unittest.TestCase):
    def test_parse_digest_header_and_response(self):
        header = (
            'Digest username="1001", realm="mini-call-server", nonce="abc", '
            'uri="sip:127.0.0.1:15062", response="placeholder", algorithm=MD5, '
            'qop=auth, nc=00000001, cnonce="client"'
        )
        parsed = server.parse_digest_header(header)
        self.assertEqual(parsed["username"], "1001")
        self.assertEqual(parsed["qop"], "auth")
        response = server.make_digest_response(
            username="1001",
            realm="mini-call-server",
            password="secret-password",
            method="REGISTER",
            uri="sip:127.0.0.1:15062",
            nonce="abc",
            nc="00000001",
            cnonce="client",
            qop="auth",
        )
        self.assertEqual(len(response), 32)


class DtmfTests(unittest.TestCase):
    def test_parse_dtmf_event_end_packet(self):
        event = server.parse_dtmf_event(bytes([5, 0x80 | 10, 1, 64]))
        self.assertEqual(event, (5, "5", True, 320))


class ConfigTests(unittest.TestCase):
    def test_load_config_and_cli_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                (
                    '{"sip_port": 5062, "default_codec": "PCMA", '
                    '"users": {"1001": "secret"}, '
                    '"b2bua_routes": {"1002": "sip:1002@127.0.0.1:25082"}}'
                ),
                encoding="utf-8",
            )
            config = server.load_config_file(str(config_path))
            args = argparse.Namespace(
                sip_ip=None,
                sip_port=15062,
                rtp_min=None,
                rtp_max=None,
                log_dir=None,
                recording_dir=None,
                artifact_root=None,
                run_id=None,
                default_codec=None,
                auth_realm=None,
                debug=None,
            )
            config = server.apply_cli_overrides(config, args)
            self.assertEqual(config.sip_port, 15062)
            self.assertEqual(config.default_payload, server.PCMA)
            self.assertEqual(config.users["1001"], "secret")
            self.assertEqual(config.b2bua_routes["1002"], "sip:1002@127.0.0.1:25082")

    def test_resolve_artifact_dirs_creates_unique_run_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = server.ServerConfig(artifact_root=tmp, run_id="sanity")
            log_dir, recording_dir, run_dir = server.resolve_artifact_dirs(config)
            self.assertEqual(run_dir, Path(tmp) / "sanity")
            self.assertEqual(log_dir, Path(tmp) / "sanity" / "logs")
            self.assertEqual(recording_dir, Path(tmp) / "sanity" / "recordings")


if __name__ == "__main__":
    unittest.main()
