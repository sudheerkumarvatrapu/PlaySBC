import asyncio
import unittest

from sip.transaction import TransactionManager, TransactionState


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.transactions = TransactionManager(
            lambda packet, destination: self.sent.append((packet, destination)),
            transaction_timeout=10,
            schedule_retransmissions=False,
        )
        self.source = ("127.0.0.1", 25060)
        self.via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-options"

    def test_non_invite_retransmission_replays_cached_response(self):
        transaction, duplicate = self.transactions.receive_request(
            "OPTIONS", self.via, "1 OPTIONS", "options-call", self.source
        )
        self.assertFalse(duplicate)
        self.transactions.cache_response(
            "OPTIONS", self.via, "1 OPTIONS", "options-call", b"SIP/2.0 200 OK", self.source, 200
        )

        replayed, duplicate = self.transactions.receive_request(
            "OPTIONS", self.via, "1 OPTIONS", "options-call", self.source
        )

        self.assertTrue(duplicate)
        self.assertIs(replayed, transaction)
        self.assertEqual(replayed.request_retransmissions, 1)
        self.assertEqual(self.sent, [(b"SIP/2.0 200 OK", self.source)])

    def test_invite_ack_confirms_transaction(self):
        via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-invite"
        transaction, _ = self.transactions.receive_request("INVITE", via, "7 INVITE", "call-007", self.source)
        self.transactions.cache_response("INVITE", via, "7 INVITE", "call-007", b"SIP/2.0 200 OK", self.source, 200)

        confirmed = self.transactions.acknowledge_invite("call-007", "7 ACK")

        self.assertIs(confirmed, transaction)
        self.assertEqual(transaction.state, TransactionState.CONFIRMED)

    def test_completed_transaction_expires(self):
        transaction, _ = self.transactions.receive_request(
            "OPTIONS", self.via, "1 OPTIONS", "options-call", self.source
        )
        self.transactions.cache_response(
            "OPTIONS", self.via, "1 OPTIONS", "options-call", b"SIP/2.0 200 OK", self.source, 200
        )

        self.transactions.cleanup_expired(now=transaction.expires_at)

        self.assertEqual(self.transactions.transactions, {})
        self.assertEqual(transaction.state, TransactionState.TERMINATED)

    def test_same_branch_with_new_cseq_is_a_distinct_transaction(self):
        first, first_duplicate = self.transactions.receive_request(
            "REGISTER", self.via, "1 REGISTER", "register-call", self.source
        )
        second, second_duplicate = self.transactions.receive_request(
            "REGISTER", self.via, "2 REGISTER", "register-call", self.source
        )

        self.assertFalse(first_duplicate)
        self.assertFalse(second_duplicate)
        self.assertIsNot(first, second)


class InviteTimerTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_invite_response_retransmits_until_ack(self):
        sent = []
        transactions = TransactionManager(
            lambda packet, destination: sent.append((packet, destination)),
            t1=0.01,
            t2=0.02,
            transaction_timeout=0.2,
        )
        source = ("127.0.0.1", 25060)
        via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-timer"
        transactions.receive_request("INVITE", via, "1 INVITE", "timer-call", source)
        transactions.cache_response("INVITE", via, "1 INVITE", "timer-call", b"SIP/2.0 200 OK", source, 200)

        await asyncio.sleep(0.035)
        self.assertGreaterEqual(len(sent), 2)

        transactions.acknowledge_invite("timer-call", "1 ACK")
        sent_after_ack = len(sent)
        await asyncio.sleep(0.04)
        self.assertEqual(len(sent), sent_after_ack)
        transactions.close()


if __name__ == "__main__":
    unittest.main()
