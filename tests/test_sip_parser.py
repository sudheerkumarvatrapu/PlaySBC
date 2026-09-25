import unittest

from sip.parser import SipParseError, SipParseLimits, parse_multipart_body, parse_parameters, parse_sip_bytes, parse_sip_uri, split_quoted


def request(*extra_headers: bytes, body: bytes = b"") -> bytes:
    headers = [
        b"OPTIONS sip:service@example.com SIP/2.0",
        b"Via: SIP/2.0/UDP client.example:5060;branch=z9hG4bK-parser",
        b"From: <sip:a@example.com>;tag=a",
        b"To: <sip:service@example.com>",
        b"Call-ID: parser-call",
        b"CSeq: 1 OPTIONS",
        *extra_headers,
    ]
    return b"\r\n".join(headers) + b"\r\n\r\n" + body


class SipParserTests(unittest.TestCase):
    def test_splits_comma_lists_and_parameters_around_quoted_delimiters(self):
        self.assertEqual(
            split_quoted('"Doe, Alice" <sip:alice@example.test>, <sip:bob@example.test>'),
            ('"Doe, Alice" <sip:alice@example.test>', '<sip:bob@example.test>'),
        )
        self.assertEqual(
            parse_parameters('application/sdp;charset="utf;8";boundary="a\\\"b"'),
            ('application/sdp', (("charset", "utf;8"), ("boundary", 'a"b'))),
        )

    def test_rejects_unterminated_quoted_header_value(self):
        with self.assertRaisesRegex(SipParseError, "Unterminated"):
            split_quoted('"Alice <sip:alice@example.test>')

    def test_max_forwards_and_content_type_policy(self):
        with self.assertRaises(SipParseError) as hops:
            parse_sip_bytes(request(b"Max-Forwards: 0", b"Content-Length: 0"))
        self.assertEqual(hops.exception.status, 483)
        body = b"hello"
        with self.assertRaises(SipParseError) as media:
            parse_sip_bytes(
                request(b"Content-Type: text/plain", b"Content-Length: 5", body=body),
                allowed_content_types=("application/sdp",),
            )
        self.assertEqual(media.exception.status, 415)

    def test_multipart_parser_preserves_binary_part_bytes(self):
        body = (
            b"--boundary42\r\nContent-Type: application/sdp\r\n\r\nv=0\r\n"
            b"--boundary42\r\nContent-Type: application/octet-stream\r\n\r\n\x00\xff\x01\r\n"
            b"--boundary42--\r\n"
        )
        parts = parse_multipart_body(body, 'multipart/mixed;boundary="boundary42"')
        self.assertEqual(parts[0].body, b"v=0")
        self.assertEqual(parts[1].body, b"\x00\xff\x01")

    def test_parses_sips_uri_with_ipv6_port_parameters_and_headers(self):
        uri = parse_sip_uri(
            "sips:alice:secret@[2001:db8::10]:5061;transport=tcp;lr?subject=project%20x&priority=urgent"
        )
        self.assertEqual(uri.scheme, "sips")
        self.assertEqual(uri.userinfo, "alice:secret")
        self.assertEqual(uri.host, "2001:db8::10")
        self.assertEqual(uri.port, 5061)
        self.assertEqual(uri.parameters, (("transport", "tcp"), ("lr", None)))
        self.assertEqual(uri.headers, (("subject", "project%20x"), ("priority", "urgent")))

    def test_accepts_hostname_ipv4_and_bracketed_ipv6_request_uris(self):
        for request_uri in (
            "sip:service@example.test",
            "sip:service@192.0.2.10:5060",
            "sip:service@[2001:db8::20]",
        ):
            with self.subTest(request_uri=request_uri):
                message = parse_sip_bytes(
                    request(b"Content-Length: 0").replace(
                        b"sip:service@example.com", request_uri.encode("ascii")
                    )
                )
                self.assertEqual(message.request_uri, request_uri)

    def test_rejects_unbracketed_or_malformed_ipv6_request_uri(self):
        for request_uri in ("sip:service@2001:db8::20", "sip:service@[2001:db8::zz]"):
            with self.subTest(request_uri=request_uri), self.assertRaisesRegex(
                SipParseError, "IPv6"
            ):
                parse_sip_bytes(
                    request(b"Content-Length: 0").replace(
                        b"sip:service@example.com", request_uri.encode("ascii")
                    )
                )

    def test_rejects_invalid_uri_ports_parameters_and_escapes(self):
        invalid = (
            "sip:service@example.test:0",
            "sip:service@example.test:65536",
            "sip:service@example.test;transport=udp;Transport=tcp",
            "sip:service%ZZ@example.test",
        )
        for request_uri in invalid:
            with self.subTest(request_uri=request_uri), self.assertRaises(SipParseError):
                parse_sip_uri(request_uri)

    def test_rejects_unsupported_scheme_with_416(self):
        with self.assertRaises(SipParseError) as raised:
            parse_sip_uri("tel:+15551212")
        self.assertEqual(raised.exception.status, 416)
        self.assertEqual(raised.exception.reason, "Unsupported URI Scheme")

    def test_allows_asterisk_request_uri_only_for_options(self):
        options = parse_sip_bytes(
            request(b"Content-Length: 0").replace(b"sip:service@example.com", b"*")
        )
        self.assertEqual(options.request_uri, "*")
        with self.assertRaisesRegex(SipParseError, "only valid for OPTIONS"):
            parse_sip_bytes(
                request(b"Content-Length: 0")
                .replace(b"OPTIONS sip:service@example.com", b"INVITE *")
                .replace(b"CSeq: 1 OPTIONS", b"CSeq: 1 INVITE")
            )

    def test_parses_compact_repeated_and_folded_headers(self):
        message = parse_sip_bytes(
            b"OPTIONS sip:service@example.com SIP/2.0\r\n"
            b"v: SIP/2.0/UDP first.example;branch=z9hG4bK-first\r\n"
            b"Via: SIP/2.0/UDP second.example;branch=z9hG4bK-second\r\n"
            b"f: <sip:a@example.com>;tag=a\r\n"
            b"t: <sip:service@example.com>\r\n"
            b"i: compact-call\r\n"
            b"CSeq: 1 OPTIONS\r\n"
            b"Supported: timer,\r\n"
            b"  path\r\n"
            b"l: 0\r\n\r\n"
        )

        self.assertEqual(message.method, "OPTIONS")
        self.assertEqual(len(message.header_values("via")), 2)
        self.assertEqual(message.header("supported"), "timer, path")
        self.assertEqual(message.header("call-id"), "compact-call")

    def test_preserves_unknown_extension_header(self):
        message = parse_sip_bytes(request(b"X-PlaySBC-Trace: opaque", b"Content-Length: 0"))
        self.assertEqual(message.header("x-playsbc-trace"), "opaque")

    def test_body_is_preserved_as_bytes(self):
        body = b"\x01\x02binary"
        message = parse_sip_bytes(
            request(b"Content-Type: application/octet-stream", f"Content-Length: {len(body)}".encode(), body=body)
        )
        self.assertEqual(message.body, body)

    def test_rejects_content_length_shorter_than_body(self):
        with self.assertRaisesRegex(SipParseError, "Content-Length mismatch"):
            parse_sip_bytes(request(b"Content-Length: 2", body=b"four"))

    def test_rejects_content_length_longer_than_body(self):
        with self.assertRaisesRegex(SipParseError, "Content-Length mismatch"):
            parse_sip_bytes(request(b"Content-Length: 20", body=b"short"))

    def test_rejects_duplicate_content_length_smuggling(self):
        with self.assertRaisesRegex(SipParseError, "Duplicate singleton"):
            parse_sip_bytes(request(b"Content-Length: 0", b"l: 0"))

    def test_rejects_duplicate_call_id(self):
        with self.assertRaisesRegex(SipParseError, "Duplicate singleton"):
            parse_sip_bytes(request(b"Call-ID: second", b"Content-Length: 0"))

    def test_rejects_missing_mandatory_header(self):
        data = request(b"Content-Length: 0").replace(b"To: <sip:service@example.com>\r\n", b"")
        with self.assertRaisesRegex(SipParseError, "Missing mandatory.*to"):
            parse_sip_bytes(data)

    def test_rejects_method_cseq_mismatch(self):
        data = request(b"Content-Length: 0").replace(b"CSeq: 1 OPTIONS", b"CSeq: 1 REGISTER")
        with self.assertRaisesRegex(SipParseError, "does not match"):
            parse_sip_bytes(data)

    def test_rejects_malformed_header(self):
        with self.assertRaisesRegex(SipParseError, "Malformed SIP header"):
            parse_sip_bytes(request(b"Broken header", b"Content-Length: 0"))

    def test_rejects_lone_lf_framing(self):
        with self.assertRaisesRegex(SipParseError, "no CRLF"):
            parse_sip_bytes(request(b"Content-Length: 0").replace(b"\r\n", b"\n"))

    def test_rejects_nul_byte(self):
        with self.assertRaisesRegex(SipParseError, "NUL"):
            parse_sip_bytes(request(b"X-Bad: one\x00two", b"Content-Length: 0"))

    def test_enforces_header_count_limit(self):
        with self.assertRaisesRegex(SipParseError, "header count"):
            parse_sip_bytes(
                request(b"X-One: 1", b"Content-Length: 0"),
                limits=SipParseLimits(max_header_count=6),
            )

    def test_enforces_message_size_limit_with_513_status(self):
        with self.assertRaises(SipParseError) as raised:
            parse_sip_bytes(
                request(b"Content-Length: 0"),
                limits=SipParseLimits(max_message_bytes=16),
            )
        self.assertEqual(raised.exception.status, 513)

    def test_parses_status_line(self):
        message = parse_sip_bytes(
            b"SIP/2.0 486 Busy Here\r\n"
            b"Via: SIP/2.0/UDP client.example;branch=z9hG4bK-response\r\n"
            b"From: <sip:a@example.com>;tag=a\r\n"
            b"To: <sip:b@example.com>;tag=b\r\n"
            b"Call-ID: response-call\r\n"
            b"CSeq: 1 INVITE\r\n"
            b"Content-Length: 0\r\n\r\n"
        )
        self.assertTrue(message.is_response)
        self.assertEqual(message.status_code, 486)
        self.assertEqual(message.reason_phrase, "Busy Here")
