import unittest
import tempfile

import mini_call_server as server

from sip.overload import OverloadController
from sip.registrar import (
    ContactBinding,
    DigestNonceStore,
    LocationService,
    digest_response,
    parse_contact_bindings,
    privacy_filter,
)


class RegistrarPolicyTests(unittest.TestCase):
    def test_sha256_digest_vector_and_nonce_replay(self):
        response = digest_response(
            "alice", "playsbc", "secret", "REGISTER", "sip:playsbc",
            "nonce", algorithm="SHA-256", nc="00000001", cnonce="abc", qop="auth",
        )
        self.assertEqual(len(response), 64)
        store = DigestNonceStore(10)
        nonce = store.issue(now=100)
        self.assertEqual(store.validate(nonce, "alice", "00000001", now=101), "ok")
        self.assertEqual(store.validate(nonce, "alice", "00000001", now=102), "replay")
        self.assertEqual(store.validate(nonce, "alice", "00000002", now=102), "ok")
        self.assertEqual(store.validate(nonce, "alice", "00000003", now=111), "stale")

    def test_multiple_contacts_q_expiry_and_wildcard(self):
        parsed = parse_contact_bindings('<sip:a@192.0.2.1>;q=0.2, <sip:a@192.0.2.2>;q=0.9')
        self.assertEqual(len(parsed), 2)
        location = LocationService(2)
        location.upsert(ContactBinding("a", parsed[0][0], ("192.0.2.1", 5060), 120, 0.2))
        location.upsert(ContactBinding("a", parsed[1][0], ("192.0.2.2", 5060), 110, 0.9))
        self.assertEqual(location.best("a", 100).contact_uri, "sip:a@192.0.2.2")
        self.assertEqual(location.best("a", 115).contact_uri, "sip:a@192.0.2.1")
        location.remove("a")
        self.assertIsNone(location.best("a", 115))

    def test_path_outbound_parameters_and_privacy(self):
        parsed = parse_contact_bindings('<sip:a@edge>;q=1;+sip.instance="urn:uuid:a";reg-id=1')
        self.assertEqual(parsed[0][1]["reg-id"], "1")
        headers = {"p-asserted-identity": "<sip:a@example.test>", "privacy": "id", "history-info": "<sip:b>"}
        self.assertEqual(privacy_filter(headers, True), {"privacy": "id"})
        self.assertNotIn("p-asserted-identity", privacy_filter({"p-asserted-identity": "x"}, False))

    def test_multiple_bindings_survive_shared_state_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = server.SharedStateStore(f"{tmp}/ha.sqlite3", "node-a", server.SbcLogger(None))
            first = ContactBinding("a", "sip:a@192.0.2.1", ("192.0.2.1", 5060), 200, 0.2, ("<sip:edge;lr>",))
            second = ContactBinding("a", "sip:a@192.0.2.2", ("192.0.2.2", 5060), 200, 0.9, (), "urn:uuid:a", "1", "flow")
            store.save_registration_binding(first)
            store.save_registration_binding(second)
            loaded = store.load_registration_bindings(now=100)
            self.assertEqual({item.contact_uri for item in loaded}, {first.contact_uri, second.contact_uri})
            self.assertEqual(next(item for item in loaded if item.reg_id).flow_token, "flow")
            store.delete_registration("a")
            self.assertEqual(store.load_registration_bindings(now=100), ())
            store.close()


class OverloadPolicyTests(unittest.TestCase):
    def test_per_source_method_hysteresis_and_priority(self):
        controller = OverloadController({
            "enabled": True, "per_source_rps": 2, "invite_cps": 1,
            "retry_after": 3, "recovery_seconds": 2,
        })
        self.assertTrue(controller.decide("192.0.2.1", "INVITE", now=10).allowed)
        rejected = controller.decide("192.0.2.1", "INVITE", now=10.1)
        self.assertFalse(rejected.allowed)
        self.assertEqual((rejected.dimension, rejected.retry_after), ("invite", 3))
        self.assertEqual(controller.decide("192.0.2.1", "OPTIONS", now=11).dimension, "hysteresis")
        self.assertTrue(controller.decide("192.0.2.1", "INVITE", now=11, priority=True).allowed)
        self.assertTrue(controller.decide("192.0.2.1", "INVITE", now=13).allowed)

    def test_registration_and_method_dimensions_are_independent(self):
        controller = OverloadController({"enabled": True, "registration_rps": 1, "method_rps": {"OPTIONS": 1}, "recovery_seconds": 0})
        self.assertTrue(controller.decide("a", "REGISTER", now=1).allowed)
        self.assertEqual(controller.decide("a", "REGISTER", now=1.1).dimension, "registration")
        self.assertTrue(controller.decide("b", "OPTIONS", now=2).allowed)
        self.assertEqual(controller.decide("b", "OPTIONS", now=2.1).dimension, "method:OPTIONS")


if __name__ == "__main__":
    unittest.main()
