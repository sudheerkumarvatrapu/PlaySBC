import unittest

from sip.business_services import (
    BusinessServiceError,
    CallForwarding,
    CallTransfer,
    ConsultationHold,
    ConsultationState,
    ForwardCondition,
    ForwardingRule,
    HoldState,
    MusicOnHold,
    TransferKind,
    TransferState,
    parse_refer_to,
)


class ConsultationHoldTests(unittest.TestCase):
    def test_consultation_preserves_original_dialog_until_resume(self):
        service = ConsultationHold("original-call")
        service.hold_original()
        service.start_consultation("consult-call", "sip:expert@example.com")

        self.assertTrue(service.original_dialog_preserved)
        self.assertEqual(service.state, ConsultationState.CONSULTING)
        self.assertEqual(service.original_call_id, "original-call")

        service.complete_consultation()
        service.resume_original()
        self.assertEqual(service.state, ConsultationState.COMPLETED)

    def test_consultation_rejects_reusing_original_call_id(self):
        service = ConsultationHold("same-call")
        service.hold_original()
        with self.assertRaises(BusinessServiceError):
            service.start_consultation("same-call", "sip:expert@example.com")

    def test_failed_consultation_retains_reason_and_original_dialog(self):
        service = ConsultationHold("original-call")
        service.hold_original()
        service.start_consultation("consult-call", "sip:expert@example.com")
        service.fail("486 Busy Here")

        self.assertEqual(service.state, ConsultationState.FAILED)
        self.assertEqual(service.failure_reason, "486 Busy Here")
        self.assertTrue(service.original_dialog_preserved)

    def test_failed_consultation_can_recover_the_original_dialog(self):
        service = ConsultationHold("original-call")
        service.hold_original()
        service.start_consultation("consult-call", "sip:expert@example.com")

        service.fail_consultation("486 Busy Here")
        self.assertEqual(service.state, ConsultationState.RECOVERING)
        self.assertTrue(service.original_dialog_preserved)
        self.assertFalse(service.consultation_dialog_active)

        service.resume_original()
        self.assertEqual(service.state, ConsultationState.RECOVERED)
        self.assertEqual(service.failure_reason, "486 Busy Here")
        self.assertTrue(service.original_dialog_preserved)

    def test_original_dialog_termination_wins_consultation_completion_race(self):
        service = ConsultationHold("original-call")
        service.hold_original()
        service.start_consultation("consult-call", "sip:expert@example.com")

        service.terminate_original("BYE received")
        service.terminate_original("duplicate BYE")

        self.assertEqual(service.state, ConsultationState.TERMINATED)
        self.assertEqual(service.termination_reason, "BYE received")
        self.assertFalse(service.original_dialog_preserved)
        self.assertFalse(service.consultation_dialog_active)
        with self.assertRaises(BusinessServiceError):
            service.complete_consultation()


class MusicOnHoldTests(unittest.TestCase):
    def test_music_lifetime_is_bounded_by_hold_and_resume(self):
        service = MusicOnHold("call-1")
        service.hold("sendonly")
        service.start_music("sip:moh@example.com")

        self.assertEqual(service.state, HoldState.MUSIC_ACTIVE)
        self.assertEqual(service.direction, "sendonly")
        self.assertEqual(service.source, "sip:moh@example.com")

        service.stop_music()
        service.resume()
        self.assertEqual(service.state, HoldState.RESUMED)

    def test_music_cannot_start_before_hold(self):
        with self.assertRaises(BusinessServiceError):
            MusicOnHold("call-1").start_music("sip:moh@example.com")

    def test_hold_rejects_unknown_direction(self):
        with self.assertRaises(BusinessServiceError):
            MusicOnHold("call-1").hold("sideways")


class CallTransferTests(unittest.TestCase):
    def test_unattended_transfer_completes_from_notify(self):
        service = CallTransfer("original-call")
        target = service.request("<sip:2002@example.com>")
        self.assertEqual(target.kind, TransferKind.UNATTENDED)
        service.accept()
        service.notify(100, "Trying")
        service.notify(200, "OK")

        self.assertEqual(service.state, TransferState.COMPLETED)
        self.assertEqual(service.notify_statuses, [100, 200])

    def test_attended_transfer_parses_percent_encoded_replaces(self):
        refer_to = (
            "<sip:2002@example.com?Replaces=consult-call%3Bto-tag%3Dto-tag"
            "%3Bfrom-tag%3Dfrom-tag>"
        )
        target = parse_refer_to(refer_to)

        self.assertEqual(target.kind, TransferKind.ATTENDED)
        self.assertEqual(target.replaces_call_id, "consult-call")
        self.assertEqual(target.replaces_to_tag, "to-tag")
        self.assertEqual(target.replaces_from_tag, "from-tag")

    def test_failed_transfer_recovers_original_dialog(self):
        service = CallTransfer("original-call")
        service.request("sip:busy@example.com")
        service.accept()
        service.notify(486, "Busy Here")
        self.assertEqual(service.state, TransferState.FAILED)

        service.recover_original()
        self.assertEqual(service.state, TransferState.RECOVERED)
        self.assertEqual(service.failure_reason, "Busy Here")

    def test_attended_transfer_rejects_self_replacement(self):
        service = CallTransfer("original-call")
        with self.assertRaises(BusinessServiceError):
            service.request(
                "sip:2002@example.com?Replaces=original-call%3Bto-tag%3Da%3Bfrom-tag%3Db"
            )


class CallForwardingTests(unittest.TestCase):
    def setUp(self):
        self.forwarding = CallForwarding(
            (
                ForwardingRule("always", "1001", "sip:2001@example.com"),
                ForwardingRule("busy", "1002", "2002", ForwardCondition.BUSY),
                ForwardingRule("no-answer", "1003", "2003", ForwardCondition.NO_ANSWER),
            )
        )

    def test_selects_each_forwarding_condition(self):
        unconditional = self.forwarding.select("1001")
        busy = self.forwarding.select("1002", status=486)
        no_answer = self.forwarding.select("1003", timed_out=True)

        self.assertEqual(unconditional.target, "sip:2001@example.com")
        self.assertEqual(unconditional.condition, ForwardCondition.UNCONDITIONAL)
        self.assertEqual(busy.target, "2002")
        self.assertEqual(busy.condition, ForwardCondition.BUSY)
        self.assertEqual(no_answer.target, "2003")
        self.assertEqual(no_answer.condition, ForwardCondition.NO_ANSWER)

    def test_busy_rule_does_not_apply_to_unrelated_failure(self):
        self.assertIsNone(self.forwarding.select("1002", status=404))

    def test_detects_forwarding_loop(self):
        with self.assertRaises(BusinessServiceError):
            self.forwarding.select("1001", history=("2001",))

    def test_enforces_forwarding_hop_limit(self):
        forwarding = CallForwarding(
            (ForwardingRule("always", "1001", "2001"),),
            max_hops=2,
        )
        with self.assertRaises(BusinessServiceError):
            forwarding.select("1001", history=("3001", "3002"))


if __name__ == "__main__":
    unittest.main()
