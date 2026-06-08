import unittest

from sip.dialog import CallState, DialogError, DialogManager, ensure_header_tag, extract_branch, extract_tag


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.dialogs = DialogManager()
        self.dialog = self.dialogs.create_invite(
            call_id="call-001",
            from_header="<sip:alice@example.test>;tag=alice-tag",
            via_header="SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-invite",
            cseq_header="1 INVITE",
        )

    def test_answered_dialog_tracks_tags_branches_cseq_and_timestamps(self):
        self.dialog.mark_ringing()
        self.dialog.mark_answered()
        self.dialogs.acknowledge("call-001", "1 ACK")
        self.dialogs.terminate(
            "call-001",
            "<sip:alice@example.test>;tag=alice-tag",
            f"<sip:echo@example.test>;tag={self.dialog.local_tag}",
            "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-bye",
            "2 BYE",
        )

        self.assertEqual(self.dialog.state, CallState.TERMINATED)
        self.assertEqual(self.dialog.remote_tag, "alice-tag")
        self.assertEqual(self.dialog.remote_cseq, 2)
        self.assertEqual(self.dialog.branch_ids, {"z9hG4bK-invite", "z9hG4bK-bye"})
        self.assertIsNotNone(self.dialog.ringing_at)
        self.assertIsNotNone(self.dialog.answered_at)
        self.assertIsNotNone(self.dialog.acknowledged_at)
        self.assertIsNotNone(self.dialog.terminated_at)

    def test_bye_before_answer_is_rejected(self):
        with self.assertRaisesRegex(DialogError, "expected ANSWERED"):
            self.dialogs.terminate(
                "call-001",
                "<sip:alice@example.test>;tag=alice-tag",
                f"<sip:echo@example.test>;tag={self.dialog.local_tag}",
                "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-bye",
                "2 BYE",
            )

    def test_bye_with_old_cseq_is_rejected(self):
        self.dialog.mark_ringing()
        self.dialog.mark_answered()
        with self.assertRaisesRegex(DialogError, "must be greater"):
            self.dialogs.terminate(
                "call-001",
                "<sip:alice@example.test>;tag=alice-tag",
                f"<sip:echo@example.test>;tag={self.dialog.local_tag}",
                "SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-bye",
                "1 BYE",
            )

    def test_header_helpers_preserve_stable_tag_and_extract_branch(self):
        tagged = ensure_header_tag("<sip:echo@example.test>", "server-tag")
        self.assertEqual(tagged, "<sip:echo@example.test>;tag=server-tag")
        self.assertEqual(ensure_header_tag(tagged, "other-tag"), tagged)
        self.assertEqual(extract_tag(tagged), "server-tag")
        self.assertEqual(
            extract_branch("SIP/2.0/UDP 127.0.0.1:25060;branch=z9hG4bK-001;rport"),
            "z9hG4bK-001",
        )


if __name__ == "__main__":
    unittest.main()

