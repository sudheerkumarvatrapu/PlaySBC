import asyncio
import unittest

from mini_call_server import SipServerProtocol, SipTcpConnectionProtocol
from sip.parser import SipParseLimits


class FakeLogger:
    def __init__(self): self.events = []
    def write(self, *args): self.events.append(args)
    def networking(self, *args): self.events.append(args)


class FakeTransport:
    def __init__(self): self.closed = False; self.write_limits = None
    def get_extra_info(self, _name): return ("192.0.2.10", 5060)
    def close(self): self.closed = True
    def set_write_buffer_limits(self, **limits): self.write_limits = limits


class FakeServer:
    sip_parse_limits = SipParseLimits(max_message_bytes=128)
    stream_idle_timeout = 120.0
    stream_max_connections = 1024
    def __init__(self):
        self.received = []
        self.logger = FakeLogger()
        self.connections = set()
        self.stream_rejections = 0
        self.stream_idle_timeouts = 0
    def register_stream_connection(self, _transport, _peer, connection):
        if len(self.connections) >= self.stream_max_connections:
            self.stream_rejections += 1
            return False
        self.connections.add(connection)
        return True
    def unregister_stream_connection(self, _transport, _peer, connection):
        self.connections.discard(connection)
    def receive_sip_data(self, data, *_args, **_kwargs): self.received.append(data)


class SipStreamTests(unittest.TestCase):
    def test_oversized_partial_frame_closes_connection_and_clears_buffer(self):
        protocol = SipTcpConnectionProtocol(FakeServer())
        transport = FakeTransport()
        protocol.connection_made(transport)
        protocol.data_received(b"A" * 129)
        self.assertTrue(transport.closed)
        self.assertEqual(protocol.buffer, b"")

    def test_partial_and_pipelined_frames_remain_isolated(self):
        server = FakeServer()
        server.sip_parse_limits = SipParseLimits(max_message_bytes=2048)
        protocol = SipTcpConnectionProtocol(server)
        protocol.connection_made(FakeTransport())
        first = b"OPTIONS sip:a@x SIP/2.0\r\nContent-Length: 3\r\n\r\none"
        second = b"OPTIONS sip:b@x SIP/2.0\r\nContent-Length: 3\r\n\r\ntwo"
        protocol.data_received(first[:25])
        self.assertEqual(server.received, [])
        protocol.data_received(first[25:] + second)
        self.assertEqual(server.received, [first, second])

    def test_pipelined_read_may_exceed_single_message_limit(self):
        server = FakeServer()
        server.sip_parse_limits = SipParseLimits(max_message_bytes=64, max_header_bytes=64)
        protocol = SipTcpConnectionProtocol(server)
        transport = FakeTransport()
        protocol.connection_made(transport)
        message = b"OPTIONS * SIP/2.0\r\nContent-Length: 0\r\n\r\n"
        protocol.data_received(message + message)
        self.assertEqual(server.received, [message, message])
        self.assertFalse(transport.closed)

    def test_malformed_or_duplicate_content_length_closes_connection(self):
        for content_length in (b"wat", b"0\r\nl: 0"):
            with self.subTest(content_length=content_length):
                server = FakeServer()
                protocol = SipTcpConnectionProtocol(server)
                transport = FakeTransport()
                protocol.connection_made(transport)
                protocol.data_received(b"OPTIONS * SIP/2.0\r\nContent-Length: " + content_length + b"\r\n\r\n")
                self.assertTrue(transport.closed)
                self.assertEqual(server.received, [])

    def test_incomplete_oversized_header_closes_connection(self):
        server = FakeServer()
        server.sip_parse_limits = SipParseLimits(max_message_bytes=128, max_header_bytes=32)
        protocol = SipTcpConnectionProtocol(server)
        transport = FakeTransport()
        protocol.connection_made(transport)
        protocol.data_received(b"OPTIONS sip:a@example.test SIP/2.0\r\nVia: unfinished")
        self.assertTrue(transport.closed)
        self.assertEqual(protocol.buffer, b"")

    def test_partial_frame_at_eof_is_discarded_and_logged(self):
        server = FakeServer()
        protocol = SipTcpConnectionProtocol(server)
        protocol.connection_made(FakeTransport())
        protocol.data_received(b"OPTIONS sip:a@example.test SIP/2.0\r\n")
        self.assertFalse(protocol.eof_received())
        self.assertEqual(protocol.buffer, b"")
        self.assertTrue(any(event[0] == "SIP STREAM INCOMPLETE EOF" for event in server.logger.events))

    def test_write_backpressure_is_observable(self):
        server = FakeServer()
        protocol = SipTcpConnectionProtocol(server)
        transport = FakeTransport()
        protocol.connection_made(transport)
        self.assertEqual(transport.write_limits, {"high": 128, "low": 64})
        protocol.pause_writing()
        self.assertTrue(protocol.write_paused)
        protocol.resume_writing()
        self.assertFalse(protocol.write_paused)

    def test_connection_pool_rejects_connections_over_limit(self):
        server = FakeServer()
        server.stream_max_connections = 1
        first = SipTcpConnectionProtocol(server)
        first.connection_made(FakeTransport())
        second_transport = FakeTransport()
        second = SipTcpConnectionProtocol(server)
        second.connection_made(second_transport)
        self.assertFalse(first.closed)
        self.assertTrue(second.closed)
        self.assertTrue(second_transport.closed)
        self.assertEqual(server.stream_rejections, 1)

    def test_server_connection_registry_enforces_bound_and_releases_slot(self):
        server = SipServerProtocol(
            "127.0.0.1", 5060, None, FakeLogger(), 0, "playsbc", {}, (), {}, (), False,
            sip_stream={"idle_timeout": 10, "max_connections": 1},
        )
        first = SipTcpConnectionProtocol(server)
        second = SipTcpConnectionProtocol(server)
        self.assertTrue(server.register_stream_connection("tcp", ("192.0.2.1", 5060), first))
        self.assertFalse(server.register_stream_connection("tls", ("192.0.2.2", 5061), second))
        self.assertEqual(server.stream_rejections, 1)
        server.unregister_stream_connection("tcp", ("192.0.2.1", 5060), first)
        self.assertTrue(server.register_stream_connection("tls", ("192.0.2.2", 5061), second))

    def test_inbound_connection_requires_rfc5923_alias_for_reverse_reuse(self):
        server = FakeServer()
        server.sip_parse_limits = SipParseLimits(max_message_bytes=2048)
        protocol = SipTcpConnectionProtocol(server)
        protocol.connection_made(FakeTransport())
        self.assertFalse(protocol.reuse_authorized)
        message = (
            b"OPTIONS sip:x@example SIP/2.0\r\n"
            b"Via: SIP/2.0/TCP 192.0.2.10:5060;branch=z9;alias\r\n"
            b"From: <sip:a@example>;tag=a\r\nTo: <sip:x@example>\r\n"
            b"Call-ID: alias-test\r\nCSeq: 1 OPTIONS\r\nContent-Length: 0\r\n\r\n"
        )
        protocol.data_received(message)
        self.assertTrue(protocol.reuse_authorized)

    def test_idle_connection_is_closed_and_counted(self):
        async def exercise():
            server = FakeServer()
            server.stream_idle_timeout = 0.01
            transport = FakeTransport()
            protocol = SipTcpConnectionProtocol(server)
            protocol.connection_made(transport)
            await asyncio.sleep(0.03)
            self.assertTrue(transport.closed)
            self.assertTrue(protocol.closed)
            self.assertNotIn(protocol, server.connections)
            self.assertEqual(server.stream_idle_timeouts, 1)
            self.assertTrue(any(event[0] == "SIP STREAM IDLE TIMEOUT" for event in server.logger.events))

        asyncio.run(exercise())
