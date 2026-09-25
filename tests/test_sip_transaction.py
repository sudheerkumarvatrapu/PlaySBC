import asyncio
import unittest

from sip.transaction import MergedRequestError, TransactionError, TransactionManager, TransactionState


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

    def test_non_2xx_invite_ack_confirms_transaction(self):
        via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-invite"
        transaction, _ = self.transactions.receive_request("INVITE", via, "7 INVITE", "call-007", self.source)
        self.transactions.cache_response("INVITE", via, "7 INVITE", "call-007", b"SIP/2.0 486 Busy Here", self.source, 486)

        confirmed = self.transactions.acknowledge_invite("call-007", "7 ACK")

        self.assertIs(confirmed, transaction)
        self.assertEqual(transaction.state, TransactionState.CONFIRMED)

    def test_2xx_invite_uses_accepted_state_and_ack_remains_end_to_end(self):
        via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-success"
        transaction, _ = self.transactions.receive_request("INVITE", via, "8 INVITE", "call-008", self.source)
        self.transactions.cache_response("INVITE", via, "8 INVITE", "call-008", b"SIP/2.0 200 OK", self.source, 200)

        matched = self.transactions.acknowledge_invite("call-008", "8 ACK", via)

        self.assertIsNone(matched)
        self.assertEqual(transaction.state, TransactionState.ACCEPTED)

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

    def test_same_branch_from_different_via_sent_by_is_a_distinct_transaction(self):
        branch = "z9hG4bK-7-1-0"
        peer_via = f"SIP/2.0/UDP 192.168.28.30:5070;branch={branch}"
        core_via = f"SIP/2.0/UDP 172.28.0.10:5070;branch={branch}"

        peer, peer_duplicate = self.transactions.receive_request(
            "REGISTER", peer_via, "1 REGISTER", "peer-register", ("192.168.28.30", 5070)
        )
        self.transactions.cache_response(
            "REGISTER",
            peer_via,
            "1 REGISTER",
            "peer-register",
            b"SIP/2.0 200 OK",
            ("192.168.28.30", 5070),
            200,
        )

        core, core_duplicate = self.transactions.receive_request(
            "REGISTER", core_via, "1 REGISTER", "core-register", ("172.28.0.10", 5070)
        )

        self.assertFalse(peer_duplicate)
        self.assertFalse(core_duplicate)
        self.assertIsNot(peer, core)
        self.assertEqual(self.sent, [])

    def test_request_method_must_match_cseq_method(self):
        with self.assertRaisesRegex(TransactionError, "does not match"):
            self.transactions.receive_request(
                "OPTIONS", self.via, "1 REGISTER", "invalid-cseq", self.source
            )

    def test_cseq_number_must_be_within_rfc_3261_range(self):
        with self.assertRaisesRegex(TransactionError, "outside the RFC 3261 range"):
            self.transactions.receive_request(
                "OPTIONS", self.via, "2147483648 OPTIONS", "invalid-cseq", self.source
            )

    def test_ack_with_via_matches_only_the_correct_invite_branch(self):
        first_via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-first"
        second_via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-second"
        first, _ = self.transactions.receive_request("INVITE", first_via, "7 INVITE", "forked", self.source)
        second, _ = self.transactions.receive_request("INVITE", second_via, "7 INVITE", "forked", self.source)
        self.transactions.cache_response("INVITE", first_via, "7 INVITE", "forked", b"SIP/2.0 486 Busy Here", self.source, 486)
        self.transactions.cache_response("INVITE", second_via, "7 INVITE", "forked", b"SIP/2.0 486 Busy Here", self.source, 486)

        confirmed = self.transactions.acknowledge_invite("forked", "7 ACK", second_via)

        self.assertIs(confirmed, second)
        self.assertEqual(first.state, TransactionState.COMPLETED)
        self.assertEqual(second.state, TransactionState.CONFIRMED)

    def test_non_ack_cannot_confirm_invite_transaction(self):
        with self.assertRaisesRegex(TransactionError, "Expected ACK"):
            self.transactions.acknowledge_invite("call", "1 BYE")

    def test_merged_request_is_distinct_from_retransmission(self):
        merge_id = "INVITE|sip:b@example|<sip:a@example>;tag=1|merged-call|1 INVITE"
        first_via = "SIP/2.0/UDP a.example:5060;branch=z9hG4bK-first"
        second_via = "SIP/2.0/UDP a.example:5060;branch=z9hG4bK-second"
        self.transactions.receive_request("INVITE", first_via, "1 INVITE", "merged-call", self.source, merge_id=merge_id)
        with self.assertRaises(MergedRequestError):
            self.transactions.receive_request("INVITE", second_via, "1 INVITE", "merged-call", self.source, merge_id=merge_id)

    def test_cancel_matches_only_pending_invite_branch(self):
        via = "SIP/2.0/UDP a.example:5060;branch=z9hG4bK-cancel"
        invite, _ = self.transactions.receive_request("INVITE", via, "7 INVITE", "cancel-call", self.source)
        self.assertIs(self.transactions.cancellable_invite("cancel-call", "7 CANCEL", via), invite)
        self.transactions.cache_response("INVITE", via, "7 INVITE", "cancel-call", b"SIP/2.0 200 OK", self.source, 200)
        self.assertIsNone(self.transactions.cancellable_invite("cancel-call", "7 CANCEL", via))


class InviteTimerTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_2xx_final_invite_response_retransmits_until_ack(self):
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
        transactions.cache_response("INVITE", via, "1 INVITE", "timer-call", b"SIP/2.0 486 Busy Here", source, 486)

        deadline = asyncio.get_running_loop().time() + 0.15
        while len(sent) < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.005)
        self.assertGreaterEqual(len(sent), 2)

        transactions.acknowledge_invite("timer-call", "1 ACK")
        sent_after_ack = len(sent)
        await asyncio.sleep(0.04)
        self.assertEqual(len(sent), sent_after_ack)
        transactions.close()


class NonInviteServerTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_provisional_response_enters_proceeding_without_timer_j(self):
        transactions = TransactionManager(lambda *_: None, t1=0.01, transaction_timeout=0.04)
        source = ("127.0.0.1", 25060)
        via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-options-provisional"
        transaction, _ = transactions.receive_request("OPTIONS", via, "1 OPTIONS", "options-provisional", source)

        self.assertEqual(transaction.state, TransactionState.TRYING)
        transactions.cache_response(
            "OPTIONS", via, "1 OPTIONS", "options-provisional", b"SIP/2.0 100 Trying", source, 100
        )
        await asyncio.sleep(0.06)

        self.assertEqual(transaction.state, TransactionState.PROCEEDING)
        self.assertIn(transaction.key, transactions.transactions)
        transactions.close()

    async def test_timer_j_terminates_completed_udp_transaction(self):
        transactions = TransactionManager(lambda *_: None, t1=0.01, transaction_timeout=0.04)
        source = ("127.0.0.1", 25060)
        via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-options-j"
        transaction, _ = transactions.receive_request("OPTIONS", via, "1 OPTIONS", "options-j", source)
        transactions.cache_response(
            "OPTIONS", via, "1 OPTIONS", "options-j", b"SIP/2.0 200 OK", source, 200
        )

        self.assertEqual(transaction.state, TransactionState.COMPLETED)
        await asyncio.sleep(0.06)

        self.assertEqual(transaction.state, TransactionState.TERMINATED)
        self.assertNotIn(transaction.key, transactions.transactions)

    async def test_timer_j_is_zero_for_reliable_transport(self):
        transactions = TransactionManager(lambda *_: None, t1=0.01, transaction_timeout=0.2)
        source = ("127.0.0.1", 25060)
        via = "SIP/2.0/TCP 127.0.0.1:25060;branch=z9hG4bK-options-tcp"
        transaction, _ = transactions.receive_request("OPTIONS", via, "1 OPTIONS", "options-tcp", source)
        transactions.cache_response(
            "OPTIONS", via, "1 OPTIONS", "options-tcp", b"SIP/2.0 200 OK", source, 200
        )

        self.assertTrue(transaction.reliable_transport)
        self.assertEqual(transaction.state, TransactionState.TERMINATED)
        self.assertNotIn(transaction.key, transactions.transactions)

    async def test_2xx_final_invite_response_does_not_start_timer_g(self):
        sent = []
        transactions = TransactionManager(
            lambda packet, destination: sent.append((packet, destination)),
            t1=0.01,
            t2=0.02,
            transaction_timeout=0.05,
        )
        source = ("127.0.0.1", 25060)
        via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-accepted"
        transaction, _ = transactions.receive_request("INVITE", via, "1 INVITE", "accepted-call", source)
        transactions.cache_response("INVITE", via, "1 INVITE", "accepted-call", b"SIP/2.0 200 OK", source, 200)

        await asyncio.sleep(0.04)

        self.assertEqual(transaction.state, TransactionState.ACCEPTED)
        self.assertEqual(sent, [])
        transactions.close()

    async def test_timer_h_terminates_unacknowledged_invite(self):
        transactions = TransactionManager(lambda *_: None, t1=0.01, transaction_timeout=0.04)
        source = ("127.0.0.1", 25060)
        via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-timer-h"
        transaction, _ = transactions.receive_request("INVITE", via, "1 INVITE", "timer-h-call", source)
        transactions.cache_response("INVITE", via, "1 INVITE", "timer-h-call", b"SIP/2.0 486 Busy Here", source, 486)

        await asyncio.sleep(0.06)

        self.assertEqual(transaction.state, TransactionState.TERMINATED)
        self.assertNotIn(transaction.key, transactions.transactions)

    async def test_timer_i_terminates_confirmed_invite(self):
        transactions = TransactionManager(
            lambda *_: None,
            t1=0.01,
            t4=0.03,
            transaction_timeout=0.2,
        )
        source = ("127.0.0.1", 25060)
        via = "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-timer-i"
        transaction, _ = transactions.receive_request("INVITE", via, "1 INVITE", "timer-i-call", source)
        transactions.cache_response("INVITE", via, "1 INVITE", "timer-i-call", b"SIP/2.0 486 Busy Here", source, 486)
        transactions.acknowledge_invite("timer-i-call", "1 ACK", via)

        self.assertEqual(transaction.state, TransactionState.CONFIRMED)
        await asyncio.sleep(0.05)

        self.assertEqual(transaction.state, TransactionState.TERMINATED)
        self.assertNotIn(transaction.key, transactions.transactions)

    async def test_reliable_transport_does_not_retransmit_final_response(self):
        sent = []
        transactions = TransactionManager(
            lambda packet, destination: sent.append((packet, destination)),
            t1=0.01,
            t2=0.02,
            transaction_timeout=0.05,
        )
        source = ("127.0.0.1", 25060)
        via = "SIP/2.0/TCP 127.0.0.1:25060;branch=z9hG4bK-reliable"
        transaction, _ = transactions.receive_request("INVITE", via, "1 INVITE", "tcp-call", source)
        transactions.cache_response("INVITE", via, "1 INVITE", "tcp-call", b"SIP/2.0 486 Busy Here", source, 486)

        await asyncio.sleep(0.04)

        self.assertTrue(transaction.reliable_transport)
        self.assertEqual(sent, [])
        transactions.close()


if __name__ == "__main__":
    unittest.main()
