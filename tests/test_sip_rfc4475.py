import unittest

from sip.parser import SipParseError, parse_sip_bytes


class Rfc4475CorpusTests(unittest.TestCase):
    def test_accepts_escaped_display_name_and_ipv6_target(self):
        data = (
            b"OPTIONS sip:service@[2001:db8::1] SIP/2.0\r\n"
            b"Via: SIP/2.0/UDP [2001:db8::2];branch=z9hG4bK-corpus\r\n"
            b"From: \"A\\\"lice, Lab\" <sip:a@example.test>;tag=a\r\n"
            b"To: <sip:service@[2001:db8::1]>\r\nCall-ID: corpus\r\n"
            b"CSeq: 1 OPTIONS\r\nContent-Length: 0\r\n\r\n"
        )
        self.assertEqual(parse_sip_bytes(data).method, "OPTIONS")

    def test_rejects_conflicting_content_lengths(self):
        data = (
            b"OPTIONS sip:x@example.test SIP/2.0\r\nVia: SIP/2.0/UDP a;branch=z9hG4bK-x\r\n"
            b"From: <sip:a@x>;tag=a\r\nTo: <sip:x@x>\r\nCall-ID: x\r\nCSeq: 1 OPTIONS\r\n"
            b"Content-Length: 0\r\nl: 4\r\n\r\n"
        )
        with self.assertRaises(SipParseError): parse_sip_bytes(data)
