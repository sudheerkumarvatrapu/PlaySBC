import unittest

from sip.parser import SipParseError, parse_sip_bytes, parse_sip_uri


def options_request(request_uri: str) -> bytes:
    return (
        f"OPTIONS {request_uri} SIP/2.0\r\n"
        "Via: SIP/2.0/UDP client.example:5060;branch=z9hG4bK-uri\r\n"
        "From: <sip:a@example.com>;tag=a\r\n"
        "To: <sip:service@example.com>\r\n"
        "Call-ID: uri-call\r\n"
        "CSeq: 1 OPTIONS\r\n"
        "Content-Length: 0\r\n\r\n"
    ).encode("ascii")


class SipUriRegressionTests(unittest.TestCase):
    def test_real_device_hostname_ipv4_and_ipv6_request_targets(self):
        expected = {
            "sip:1002@pbx.example.test": ("pbx.example.test", None),
            "sip:1002@192.0.2.20:5060": ("192.0.2.20", 5060),
            "sips:1002@[2001:db8::20]:5061;transport=tcp": ("2001:db8::20", 5061),
        }
        for value, host_port in expected.items():
            with self.subTest(value=value):
                parsed = parse_sip_uri(value)
                self.assertEqual((parsed.host, parsed.port), host_port)
                self.assertEqual(parse_sip_bytes(options_request(value)).request_uri, value)

    def test_ipv6_requires_brackets_and_valid_literal(self):
        for value in ("sip:1002@2001:db8::20", "sip:1002@[2001:db8::gg]"):
            with self.subTest(value=value), self.assertRaisesRegex(SipParseError, "IPv6"):
                parse_sip_uri(value)

    def test_scheme_port_escape_and_duplicate_parameter_rejections(self):
        cases = (
            ("tel:+15551212", 416),
            ("sip:1002@example.test:0", 400),
            ("sip:1002@example.test:65536", 400),
            ("sip:1002@999.999.999.999", 400),
            ("sip:1002%XX@example.test", 400),
            ("sip:1002@example.test;transport=", 400),
            ("sip:1002@example.test;transport=udp;Transport=tcp", 400),
        )
        for value, status in cases:
            with self.subTest(value=value), self.assertRaises(SipParseError) as raised:
                parse_sip_uri(value)
            self.assertEqual(raised.exception.status, status)

    def test_options_asterisk_is_valid_but_other_methods_are_rejected(self):
        self.assertEqual(parse_sip_bytes(options_request("*")).request_uri, "*")
        invite = options_request("*").replace(b"OPTIONS *", b"INVITE *").replace(
            b"CSeq: 1 OPTIONS", b"CSeq: 1 INVITE"
        )
        with self.assertRaisesRegex(SipParseError, "only valid for OPTIONS"):
            parse_sip_bytes(invite)


if __name__ == "__main__":
    unittest.main()
