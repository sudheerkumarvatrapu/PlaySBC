import unittest

from sip.business_services import BusinessServiceError, ConsultationHold, ConsultationState


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


if __name__ == "__main__":
    unittest.main()
