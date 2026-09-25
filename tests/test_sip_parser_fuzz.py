import random
import unittest

from sip.parser import SipParseError, SipParseLimits, parse_sip_bytes


class SipParserFuzzTests(unittest.TestCase):
    def test_deterministic_mutations_never_escape_parser_contract(self):
        seed = b"OPTIONS sip:x@example.test SIP/2.0\r\nVia: SIP/2.0/UDP a;branch=z9hG4bK-x\r\nFrom: <sip:a@x>;tag=a\r\nTo: <sip:x@x>\r\nCall-ID: x\r\nCSeq: 1 OPTIONS\r\nContent-Length: 0\r\n\r\n"
        randomizer = random.Random(3261)
        for _ in range(500):
            value = bytearray(seed)
            for _ in range(randomizer.randint(1, 5)):
                value[randomizer.randrange(len(value))] = randomizer.randrange(256)
            try:
                parse_sip_bytes(bytes(value), limits=SipParseLimits(max_message_bytes=1024))
            except SipParseError:
                pass
