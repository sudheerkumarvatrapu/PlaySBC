import unittest

from sip.dialog import (
    CallState, DialogError, DialogManager, ensure_header_tag, extract_branch,
    extract_tag, in_dialog_route, split_header_values,
    parse_session_expires,
)


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

    def test_route_set_preserves_order_and_strict_loose_routing(self):
        routes = split_header_values('"Edge, One" <sip:edge.example;lr>,<sip:proxy.example;lr>')
        self.assertEqual(len(routes), 2)
        self.dialog.set_remote_route(",".join(routes), "<sip:bob@target.example>", reverse=True)
        self.assertEqual(self.dialog.route_set[0], "<sip:proxy.example;lr>")
        self.assertEqual(
            in_dialog_route(self.dialog.remote_target, self.dialog.route_set),
            ("sip:bob@target.example", self.dialog.route_set, "sip:proxy.example;lr"),
        )
        self.assertEqual(
            in_dialog_route("sip:bob@target.example", ("<sip:strict.example>", "<sip:next.example;lr>")),
            ("sip:strict.example", ("<sip:next.example;lr>", "<sip:bob@target.example>"), "sip:strict.example"),
        )

    def test_forked_dialogs_have_independent_targets_and_cseq(self):
        first = self.dialogs.establish_fork("call-001", "fork-a", contact="<sip:a@example.test>")
        second = self.dialogs.establish_fork("call-001", "fork-b", contact="<sip:b@example.test>")
        self.assertIs(self.dialogs.get_by_id("call-001", self.dialog.local_tag, "fork-a"), first)
        self.assertNotEqual(first.dialog_id, second.dialog_id)
        first.refresh_remote_target("<sip:a2@example.test>")
        self.assertEqual(first.remote_target, "sip:a2@example.test")
        self.assertEqual(second.remote_target, "sip:b@example.test")
        self.assertEqual(first.next_local_cseq(), 1)
        self.assertEqual(second.local_cseq, 0)

    def test_offer_cseq_session_and_prack_state(self):
        self.dialog.begin_offer()
        with self.assertRaisesRegex(DialogError, "pending"):
            self.dialog.begin_offer()
        self.dialog.complete_offer()
        self.assertTrue(self.dialog.accept_remote_request("UPDATE", 2, "z9hG4bK-update"))
        self.assertFalse(self.dialog.accept_remote_request("UPDATE", 2, "z9hG4bK-update"))
        with self.assertRaisesRegex(DialogError, "not newer"):
            self.dialog.accept_remote_request("BYE", 1)
        self.dialog.set_session_timer(90, "uac", now=100.0)
        self.assertEqual(self.dialog.session_expires_at, 190.0)
        with self.assertRaisesRegex(DialogError, "at least 90"):
            self.dialog.set_session_timer(89, "uac")
        self.dialog.remember_reliable_provisional(12, 1, "INVITE")
        with self.assertRaisesRegex(DialogError, "does not match"):
            self.dialog.acknowledge_reliable_provisional("13 1 INVITE")
        self.dialog.acknowledge_reliable_provisional("12 1 INVITE")
        self.assertEqual(self.dialog.reliable_provisionals, {})

    def test_session_expires_parser_enforces_min_se_and_refresher(self):
        self.assertEqual(parse_session_expires("180;refresher=uas"), (180, "uas"))
        self.assertEqual(parse_session_expires("90"), (90, "uac"))
        with self.assertRaisesRegex(DialogError, "too small"):
            parse_session_expires("89;refresher=uac")
        with self.assertRaisesRegex(DialogError, "too small"):
            parse_session_expires("120", min_se=180)
        with self.assertRaisesRegex(DialogError, "Invalid session refresher"):
            parse_session_expires("180;refresher=proxy")


if __name__ == "__main__":
    unittest.main()
