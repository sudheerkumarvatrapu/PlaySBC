import asyncio
import unittest

from sip.client_transaction import ClientTransactionManager, ClientTransactionState


class ClientTransactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sent = []
        self.source = ("127.0.0.1", 5060)

    async def test_invite_timer_a_retransmits_until_provisional_response(self):
        manager = ClientTransactionManager(
            lambda packet, destination: self.sent.append((packet, destination)),
            t1=0.01,
            timer_b=0.2,
        )
        via = "SIP/2.0/UDP sbc.example:5060;branch=z9hG4bK-client-a"
        transaction = manager.start_request(
            "INVITE", via, "1 INVITE", "client-a", b"INVITE", self.source
        )
        await asyncio.sleep(0.025)
        self.assertGreaterEqual(transaction.retransmissions, 1)

        manager.receive_response(180, via, "1 INVITE", "client-a")
        count = len(self.sent)
        await asyncio.sleep(0.03)

        self.assertEqual(transaction.state, ClientTransactionState.PROCEEDING)
        self.assertEqual(len(self.sent), count)
        manager.close()

    async def test_invite_timer_b_reports_timeout(self):
        timed_out = []
        manager = ClientTransactionManager(
            lambda *_: None,
            t1=0.01,
            timer_b=0.03,
            on_timeout=timed_out.append,
        )
        via = "SIP/2.0/UDP sbc.example:5060;branch=z9hG4bK-client-b"
        transaction = manager.start_request(
            "INVITE", via, "1 INVITE", "client-b", b"INVITE", self.source
        )
        await asyncio.sleep(0.05)

        self.assertEqual(transaction.state, ClientTransactionState.TERMINATED)
        self.assertEqual(timed_out, [transaction])
        self.assertNotIn(transaction.key, manager.transactions)

    async def test_invite_non_2xx_final_uses_timer_d(self):
        manager = ClientTransactionManager(
            lambda *_: None,
            t1=0.01,
            timer_b=0.2,
            timer_d=0.03,
        )
        via = "SIP/2.0/UDP sbc.example:5060;branch=z9hG4bK-client-d"
        transaction = manager.start_request(
            "INVITE", via, "1 INVITE", "client-d", b"INVITE", self.source
        )
        manager.receive_response(486, via, "1 INVITE", "client-d")
        self.assertEqual(transaction.state, ClientTransactionState.COMPLETED)
        cleanup_task = transaction.cleanup_task
        manager.receive_response(486, via, "1 INVITE", "client-d")
        self.assertIs(transaction.cleanup_task, cleanup_task)
        await asyncio.sleep(0.05)
        self.assertEqual(transaction.state, ClientTransactionState.TERMINATED)

    async def test_invite_2xx_enters_accepted_state(self):
        manager = ClientTransactionManager(lambda *_: None, t1=0.001, timer_b=0.2)
        via = "SIP/2.0/UDP sbc.example:5060;branch=z9hG4bK-client-m"
        transaction = manager.start_request(
            "INVITE", via, "1 INVITE", "client-m", b"INVITE", self.source
        )
        manager.receive_response(200, via, "1 INVITE", "client-m")

        self.assertEqual(transaction.state, ClientTransactionState.ACCEPTED)
        await asyncio.sleep(0.08)
        self.assertEqual(transaction.state, ClientTransactionState.TERMINATED)

    async def test_non_invite_timer_e_continues_at_t2_after_provisional(self):
        manager = ClientTransactionManager(
            lambda packet, destination: self.sent.append((packet, destination)),
            t1=0.01,
            t2=0.02,
            timer_f=0.2,
        )
        via = "SIP/2.0/UDP sbc.example:5060;branch=z9hG4bK-client-e"
        transaction = manager.start_request(
            "OPTIONS", via, "1 OPTIONS", "client-e", b"OPTIONS", self.source
        )
        manager.receive_response(100, via, "1 OPTIONS", "client-e")
        await asyncio.sleep(0.05)

        self.assertEqual(transaction.state, ClientTransactionState.PROCEEDING)
        self.assertGreaterEqual(transaction.retransmissions, 2)
        manager.close()

    async def test_non_invite_timer_f_reports_timeout(self):
        timed_out = []
        manager = ClientTransactionManager(
            lambda *_: None,
            t1=0.01,
            timer_f=0.03,
            on_timeout=timed_out.append,
        )
        via = "SIP/2.0/UDP sbc.example:5060;branch=z9hG4bK-client-f"
        transaction = manager.start_request(
            "OPTIONS", via, "1 OPTIONS", "client-f", b"OPTIONS", self.source
        )
        await asyncio.sleep(0.05)

        self.assertEqual(transaction.state, ClientTransactionState.TERMINATED)
        self.assertEqual(timed_out, [transaction])

    async def test_non_invite_final_uses_timer_k(self):
        manager = ClientTransactionManager(
            lambda *_: None,
            t1=0.01,
            t4=0.03,
            timer_f=0.2,
        )
        via = "SIP/2.0/UDP sbc.example:5060;branch=z9hG4bK-client-k"
        transaction = manager.start_request(
            "BYE", via, "2 BYE", "client-k", b"BYE", self.source
        )
        manager.receive_response(200, via, "2 BYE", "client-k")
        self.assertEqual(transaction.state, ClientTransactionState.COMPLETED)
        await asyncio.sleep(0.05)
        self.assertEqual(transaction.state, ClientTransactionState.TERMINATED)

    async def test_reliable_transport_has_no_retransmission_and_zero_cleanup_timer(self):
        manager = ClientTransactionManager(lambda *_: None, t1=0.01, timer_f=0.2)
        via = "SIP/2.0/TCP sbc.example:5060;branch=z9hG4bK-client-tcp"
        transaction = manager.start_request(
            "OPTIONS",
            via,
            "1 OPTIONS",
            "client-tcp",
            b"OPTIONS",
            self.source,
        )
        await asyncio.sleep(0.025)
        self.assertTrue(transaction.reliable_transport)
        self.assertEqual(transaction.retransmissions, 0)

        manager.receive_response(200, via, "1 OPTIONS", "client-tcp")
        self.assertEqual(transaction.state, ClientTransactionState.TERMINATED)
        self.assertNotIn(transaction.key, manager.transactions)

    async def test_unmatched_response_is_ignored(self):
        manager = ClientTransactionManager(lambda *_: None)
        via = "SIP/2.0/UDP sbc.example:5060;branch=z9hG4bK-unknown"
        self.assertIsNone(manager.receive_response(200, via, "1 OPTIONS", "unknown"))

    async def test_timer_c_replaces_timer_b_after_provisional_response(self):
        timed_out = []
        manager = ClientTransactionManager(
            lambda *_: None, t1=0.01, timer_b=0.02, timer_c=0.06, on_timeout=timed_out.append
        )
        via = "SIP/2.0/UDP sbc.example:5060;branch=z9hG4bK-client-c"
        transaction = manager.start_request("INVITE", via, "1 INVITE", "client-c", b"INVITE", self.source)
        await asyncio.sleep(0.01)
        manager.receive_response(180, via, "1 INVITE", "client-c")
        await asyncio.sleep(0.025)
        self.assertEqual(transaction.state, ClientTransactionState.PROCEEDING)
        self.assertEqual(timed_out, [])
        await asyncio.sleep(0.05)
        self.assertEqual(timed_out, [transaction])

    async def test_transport_error_terminates_matching_transactions(self):
        errors = []
        manager = ClientTransactionManager(lambda *_: None, on_transport_error=lambda tx, exc: errors.append((tx, exc)))
        via = "SIP/2.0/TCP sbc.example:5060;branch=z9hG4bK-transport"
        transaction = manager.start_request("BYE", via, "2 BYE", "transport-call", b"BYE", self.source)
        failure = ConnectionError("peer reset")
        self.assertEqual(manager.transport_error(self.source, failure), [transaction])
        self.assertEqual(transaction.state, ClientTransactionState.TERMINATED)
        self.assertEqual(errors, [(transaction, failure)])
