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
        self.assertEqual(explicit.transport, "udp")

        default = server.parse_sip_uri("sip:1003@example.test")
        self.assertEqual(default.address, ("example.test", 5060))

    def test_parse_sip_uri_preserves_transport_parameter(self):
        uri = server.parse_sip_uri("<sip:1002@127.0.0.1:25082;transport=tcp>")

        self.assertEqual(uri.address, ("127.0.0.1", 25082))
        self.assertEqual(uri.transport, "tcp")
        self.assertEqual(uri.uri, "sip:1002@127.0.0.1:25082;transport=tcp")
        self.assertEqual(
            server.extract_sip_uri("<sip:1002@127.0.0.1:25082;transport=tcp>;expires=300"),
            "sip:1002@127.0.0.1:25082;transport=tcp",
        )

    def test_b2bua_outbound_transport_inherits_tcp_when_contact_omits_transport(self):
        protocol = server.SipServerProtocol(
            "127.0.0.1",
            25062,
            media=None,
            logger=server.SbcLogger(None),
            default_payload=server.PCMU,
            auth_realm="playsbc",
            users={},
            bridge_rooms=(),
            b2bua_routes={},
            route_policies=(),
            b2bua_ladder_logs=False,
        )
        route = server.RouteResult(
            target=server.SipUri("callee", "127.0.0.1", 25082, "tcp"),
            policy_name="registered",
            source="registrar",
        )
        flow = server.B2BUAFlowLog(None, "inbound-call", "callee", route, enabled=False)
        call = server.B2BUACall(
            inbound_call_id="inbound-call",
            outbound_call_id="outbound-call",
            outbound_target=route.target,
            outbound_from_header="<sip:b2bua@127.0.0.1>",
            target_user="callee",
            route_policy="registered",
            route_source="registrar",
            flow_log=flow,
        )

        call.outbound_contact_uri = "sip:sipp-b@127.0.0.1:25082"
        self.assertEqual(protocol.outbound_transport(call), "tcp")

        call.outbound_contact_uri = "sip:sipp-b@127.0.0.1:25082;transport=udp"
        self.assertEqual(protocol.outbound_transport(call), "udp")

    def test_register_expires_parsing_prefers_contact_parameter(self):
        self.assertEqual(server.parse_register_expires("300", "<sip:bob@127.0.0.1>;expires=60"), 60)
        self.assertEqual(server.parse_register_expires("120", "<sip:bob@127.0.0.1>"), 120)
        self.assertEqual(server.parse_register_expires("", "<sip:bob@127.0.0.1>"), 300)

    def test_make_sdp_can_include_multiple_codecs_and_dtmf(self):
        sdp = server.make_sdp("127.0.0.1", 30000, server.PCMU, dtmf_payload_type=101, payloads=(0, 8, 101))
        self.assertIn("m=audio 30000 RTP/AVP 0 8 101", sdp)
        self.assertIn("a=rtpmap:0 PCMU/8000", sdp)
        self.assertIn("a=rtpmap:8 PCMA/8000", sdp)
        self.assertIn("a=rtpmap:101 telephone-event/8000", sdp)

    def test_make_sdp_moves_preferred_codec_first(self):
        sdp = server.make_sdp("127.0.0.1", 30000, server.PCMA, dtmf_payload_type=101, payloads=(0, 8, 101))
        self.assertIn("m=audio 30000 RTP/AVP 8 0 101", sdp)


class CodecTests(unittest.TestCase):
    def test_choose_payload_prefers_default_when_remote_supports_it(self):
        self.assertEqual(server.choose_payload((0, 8), server.PCMA), server.PCMA)

    def test_choose_payload_falls_back_to_remote_supported_codec(self):
        self.assertEqual(server.choose_payload((0,), server.PCMA), server.PCMU)

    def test_internal_b2bua_transcoding_offer_uses_b_leg_codec_only(self):
        payloads = server.b2bua_outbound_offer_payloads((server.PCMU, 101), server.PCMU, server.PCMA)
        sdp = server.make_sdp("127.0.0.1", 25102, server.PCMA, dtmf_payload_type=101, payloads=payloads)

        self.assertEqual(payloads, (server.PCMA,))
        self.assertIn("m=audio 25102 RTP/AVP 8 101", sdp)
        self.assertIn("a=rtpmap:8 PCMA/8000", sdp)
        self.assertNotIn("a=rtpmap:0 PCMU/8000", sdp)

    def test_internal_b2bua_same_codec_offer_keeps_original_payloads(self):
        payloads = server.b2bua_outbound_offer_payloads((server.PCMU, server.PCMA, 101), server.PCMU, server.PCMU)

        self.assertEqual(payloads, (server.PCMU, server.PCMA, 101))


class ResponseTests(unittest.TestCase):
    def test_send_response_can_preserve_untagged_to_header_for_trying(self):
        class DummyTransport:
            def __init__(self):
                self.sent = []

            def sendto(self, packet, destination):
                self.sent.append((packet, destination))

        logger = server.SbcLogger(None)
        media = server.MediaServer("127.0.0.1", 12000, 12010, None, logger)
        protocol = server.SipServerProtocol(
            "127.0.0.1",
            25062,
            media,
            logger,
            server.PCMU,
            "playsbc",
            {},
            (),
            {},
            (),
            False,
        )
        transport = DummyTransport()
        protocol.transport = transport
        message = server.parse_sip_message(
            (
                "INVITE sip:bob@127.0.0.1:25062 SIP/2.0\r\n"
                "Via: SIP/2.0/UDP 127.0.0.1:25081;branch=z9hG4bK-test\r\n"
                "From: <sip:alice@127.0.0.1:25081>;tag=1\r\n"
                "To: <sip:bob@127.0.0.1:25062>\r\n"
                "Call-ID: response-test\r\n"
                "CSeq: 1 INVITE\r\n"
                "Content-Length: 0\r\n"
                "\r\n"
            ),
            ("127.0.0.1", 25081),
        )

        protocol.send_response(message, 100, "Trying", to_header=message.header("to"))

        packet = transport.sent[0][0].decode("utf-8")
        self.assertIn("To: <sip:bob@127.0.0.1:25062>\r\n", packet)
        self.assertNotIn("To: <sip:bob@127.0.0.1:25062>;tag=", packet)

    def test_tcp_via_and_contact_include_transport_parameter(self):
        logger = server.SbcLogger(None)
        media = server.MediaServer("127.0.0.1", 12000, 12010, None, logger)
        protocol = server.SipServerProtocol(
            "127.0.0.1",
            25062,
            media,
            logger,
            server.PCMU,
            "playsbc",
            {},
            (),
            {},
            (),
            False,
            sip_transport="tcp",
        )

        self.assertTrue(protocol.make_via_header("tcp").startswith("SIP/2.0/TCP 127.0.0.1:25062;branch="))
        self.assertEqual(protocol.local_contact_uri("tcp"), "sip:b2bua@127.0.0.1:25062;transport=tcp")


class DigestAuthTests(unittest.TestCase):
    def test_parse_digest_header_and_response(self):
        header = (
            'Digest username="1001", realm="playsbc", nonce="abc", '
            'uri="sip:127.0.0.1:15062", response="placeholder", algorithm=MD5, '
            'qop=auth, nc=00000001, cnonce="client"'
        )
        parsed = server.parse_digest_header(header)
        self.assertEqual(parsed["username"], "1001")
        self.assertEqual(parsed["qop"], "auth")
        response = server.make_digest_response(
            username="1001",
            realm="playsbc",
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
                    '"b2bua_routes": {"1002": "sip:1002@127.0.0.1:25082"}, '
                    '"route_policies": [{"name": "registered", "match": "*", "target": "registration"}], '
                    '"b2bua_ladder_logs": false, '
                    '"media_backend": "rtpengine", '
                    '"rtpengine_url": "udp://127.0.0.1:2223", '
                    '"rtpengine_timeout": 1.5}'
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
                default_codec=None,
                auth_realm=None,
                debug=None,
            )
            config = server.apply_cli_overrides(config, args)
            self.assertEqual(config.sip_port, 15062)
            self.assertEqual(config.default_payload, server.PCMA)
            self.assertEqual(config.users["1001"], "secret")
            self.assertEqual(config.b2bua_routes["1002"], "sip:1002@127.0.0.1:25082")
            self.assertEqual(config.route_policies[0]["name"], "registered")
            self.assertFalse(config.b2bua_ladder_logs)
            self.assertEqual(config.media_backend, "rtpengine")
            self.assertEqual(config.rtpengine_url, "udp://127.0.0.1:2223")
            self.assertEqual(config.rtpengine_timeout, 1.5)

    def test_invalid_rtpengine_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text('{"media_backend": "rtpengine", "rtpengine_url": "tcp://127.0.0.1:2223"}', encoding="utf-8")

            with self.assertRaises(ValueError):
                server.load_config_file(str(config_path))

    def test_rtpengine_codec_policy_masks_source_and_transcodes_target(self):
        policy = server.rtpengine_codec_policy((server.PCMU,), server.PCMA)

        self.assertEqual(policy, {"mask": ["PCMU"], "transcode": ["PCMA"]})

    def test_rtpengine_codec_policy_is_empty_when_target_is_already_offered(self):
        policy = server.rtpengine_codec_policy((server.PCMU, server.PCMA), server.PCMA)

        self.assertEqual(policy, {})

    def test_resolve_log_dir_uses_configured_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = server.ServerConfig(log_dir=str(Path(tmp) / "server-logs"))
            self.assertEqual(server.resolve_log_dir(config), Path(tmp) / "server-logs")

    def test_resolve_log_dir_is_disabled_by_default(self):
        self.assertIsNone(server.resolve_log_dir(server.ServerConfig()))


class RoutingEngineTests(unittest.TestCase):
    def test_registrar_policy_resolves_registered_endpoint(self):
        engine = server.RoutingEngine(
            ({"name": "registered", "match": "*", "target": "registration"},),
            {},
        )
        registrations = {
            "sales": server.Registration(
                user="sales",
                contact_uri="sip:sales@127.0.0.1:25082",
                source=("127.0.0.1", 25082),
                expires_at=9999999999,
            )
        }

        route = engine.resolve("sales", registrations)

        self.assertIsNotNone(route)
        self.assertEqual(route.target.address, ("127.0.0.1", 25082))
        self.assertEqual(route.source, "registrar")

    def test_route_policy_can_template_static_target(self):
        engine = server.RoutingEngine(
            ({"name": "lab", "match": "lab-*", "target": "sip:{user}@127.0.0.1:26000"},),
            {},
        )

        route = engine.resolve("lab-123", {})

        self.assertIsNotNone(route)
        self.assertEqual(route.target.uri, "sip:lab-123@127.0.0.1:26000")

    def test_legacy_b2bua_routes_are_static_fallback(self):
        engine = server.RoutingEngine((), {"support": "sip:support@127.0.0.1:27000"})

        route = engine.resolve("support", {})

        self.assertIsNotNone(route)
        self.assertEqual(route.source, "static")
        self.assertEqual(route.target.address, ("127.0.0.1", 27000))


class B2BUAFlowLogTests(unittest.TestCase):
    def test_sbc_logger_creates_category_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = server.SbcLogger(Path(tmp))
            logger.sip("SIP TEST", "method=OPTIONS", call_id="call-1")
            logger.media("MEDIA TEST", "rtp_packets=1", call_id="call-1")
            logger.transcoding("TRANSCODING TEST", "src=PCMU dst=PCMA")
            logger.udp("UDP TEST", "protocol=sip")

            self.assertTrue((Path(tmp) / "log.sip").exists())
            self.assertTrue((Path(tmp) / "log.media").exists())
            self.assertTrue((Path(tmp) / "log.transcoding").exists())
            self.assertTrue((Path(tmp) / "log.platform").exists())
            self.assertTrue((Path(tmp) / "log.networking").exists())
            self.assertTrue((Path(tmp) / "log.udp").exists())
            self.assertTrue((Path(tmp) / "log.tcp").exists())
            self.assertTrue((Path(tmp) / "log.tls").exists())
            self.assertTrue((Path(tmp) / "log.call").exists())
            self.assertTrue((Path(tmp) / "log.sipp").exists())
            self.assertIn("SIP TEST", (Path(tmp) / "log.sip").read_text(encoding="utf-8"))
            self.assertIn("MEDIA TEST", (Path(tmp) / "log.media").read_text(encoding="utf-8"))
            self.assertIn("TRANSCODING TEST", (Path(tmp) / "log.transcoding").read_text(encoding="utf-8"))

    def test_sbc_logger_is_noop_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = server.SbcLogger(None)
            logger.sip("SIP TEST", "method=OPTIONS")
            logger.media("MEDIA TEST", "rtp_packets=1")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_ladder_renderer_uses_clear_three_column_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            route = server.RouteResult(
                target=server.SipUri("callee", "127.0.0.1", 25082),
                policy_name="registered",
                source="registrar",
            )
            logger = server.SbcLogger(Path(tmp))
            flow = server.B2BUAFlowLog(Path(tmp), "call-123", "callee", route, logger=logger)
            flow.sip("SIPp A", "B2BUA", "INVITE")
            flow.sip("B2BUA", "SIPp B", "INVITE")
            flow.sip("SIPp B", "B2BUA", "200 OK")
            flow.sip("B2BUA", "SIPp A", "200 OK")
            flow.render_ladder()

            self.assertEqual(flow.path, Path(tmp) / "log.call")
            text = flow.path.read_text(encoding="utf-8")
            self.assertIn("SIP LADDER", text)
            self.assertIn("SIPp A", text)
            self.assertIn("B2BUA", text)
            self.assertIn("SIPp B", text)
            self.assertIn("Step", text)
            self.assertIn("01                  |          INVITE           |", text)
            self.assertIn("                    |-------------------------->|", text)
            self.assertIn("03                  |                           |          200 OK", text)
            self.assertIn("                    |                           |<--------------------------|", text)

            sip_log = (Path(tmp) / "log.sip").read_text(encoding="utf-8")
            self.assertIn("B2BUA SIP FLOW", sip_log)
            self.assertIn("B2BUA SIP LADDER", sip_log)
            self.assertIn("SIP LADDER", sip_log)

    def test_disabled_ladder_still_logs_rtpengine_media_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            route = server.RouteResult(
                target=server.SipUri("callee", "127.0.0.1", 25082),
                policy_name="registered",
                source="registrar",
            )
            logger = server.SbcLogger(Path(tmp))
            flow = server.B2BUAFlowLog(Path(tmp), "call-rtpengine", "callee", route, enabled=False, logger=logger)

            flow.sip("SIPp A", "B2BUA", "INVITE")
            flow.write("RTPENGINE QUERY", "result=ok rtp_packets_total=6000")

            self.assertIsNone(flow.path)
            sip_log = (Path(tmp) / "log.sip").read_text(encoding="utf-8")
            media_log = (Path(tmp) / "log.media").read_text(encoding="utf-8")
            self.assertNotIn("B2BUA SIP FLOW", sip_log)
            self.assertIn("B2BUA RTPENGINE QUERY", media_log)
            self.assertIn("rtp_packets_total=6000", media_log)


if __name__ == "__main__":
    unittest.main()
