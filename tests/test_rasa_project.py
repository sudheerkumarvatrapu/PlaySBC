import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mini_call_server as server
from tools import run_rasa_nlu_regression
from tools.run_regression_suite import ReportRow, render_html


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def intent_block(nlu_text: str, intent: str) -> str:
    marker = f"  - intent: {intent}\n"
    start = nlu_text.index(marker)
    next_start = nlu_text.find("\n  - intent: ", start + len(marker))
    return nlu_text[start:] if next_start == -1 else nlu_text[start:next_start]


def load_cases(path: str) -> list[dict[str, str]]:
    parsed = server.parse_simple_yaml(read(path))
    return parsed["cases"]


class RasaProjectTests(unittest.TestCase):
    def test_chat_nlu_case_file_tracks_requested_intent_matrix(self):
        cases = load_cases("tests/rasa/chat_nlu_cases.yml")

        self.assertEqual([case["id"] for case in cases], [f"CHAT-NLU-{index:03d}" for index in range(1, 11)])
        self.assertEqual(cases[-1]["expected_intent"], "deny")
        self.assertEqual(cases[-1]["user_input"], "No, don't transfer me")

    def test_chat_negative_case_file_tracks_requested_guardrail_matrix(self):
        cases = load_cases("tests/rasa/chat_negative_cases.yml")

        self.assertEqual([case["id"] for case in cases], [f"CHAT-NEG-{index:03d}" for index in range(1, 11)])
        by_id = {case["id"]: case for case in cases}
        self.assertEqual(by_id["CHAT-NEG-001"]["expected_intent"], "deny")
        self.assertEqual(by_id["CHAT-NEG-002"]["expected_intent"], "clarify")
        self.assertEqual(by_id["CHAT-NEG-004"]["expected_intent"], "no_input")
        self.assertEqual(by_id["CHAT-NEG-010"]["expected_intent"], "deny")

    def test_rasa_nlu_contains_positive_examples_under_expected_intents(self):
        nlu = read("rasa/data/nlu.yml")

        for case in load_cases("tests/rasa/chat_nlu_cases.yml"):
            with self.subTest(case=case["id"]):
                self.assertIn(f"- {case['user_input']}", intent_block(nlu, case["expected_intent"]))

    def test_rasa_nlu_routes_negative_examples_to_guardrail_intents(self):
        nlu = read("rasa/data/nlu.yml")

        self.assertIn("- I don't want sales", intent_block(nlu, "deny"))
        self.assertNotIn("I don't want sales", intent_block(nlu, "sales"))
        self.assertIn("- Billing or maybe support", intent_block(nlu, "clarify"))
        self.assertIn("- I want help", intent_block(nlu, "clarify"))
        self.assertIn("- necesito ayuda", intent_block(nlu, "language_limitation"))
        self.assertIn("- this is stupid", intent_block(nlu, "safe_continue"))
        self.assertIn("- flibbertigibbet quantum banana", intent_block(nlu, "nlu_fallback"))
        self.assertIn("- !@#$%^&*", intent_block(nlu, "nlu_fallback"))

    def test_rasa_domain_and_rules_define_guardrail_responses(self):
        domain = read("rasa/domain.yml")
        rules = read("rasa/data/rules.yml")

        for intent in ("deny", "clarify", "no_input", "language_limitation", "safe_continue"):
            self.assertIn(f"  - {intent}", domain)
            self.assertIn(f"- intent: {intent}", rules)
        for response in (
            "utter_deny:",
            "utter_clarify:",
            "utter_no_input:",
            "utter_language_limitation:",
            "utter_safe_continue:",
        ):
            self.assertIn(response, domain)

    def test_helm_embedded_rasa_project_mirrors_chat_guardrails(self):
        values = read("charts/playsbc/values.yaml")

        for expected in (
            "- deny",
            "- clarify",
            "- no_input",
            "- language_limitation",
            "- safe_continue",
            "No, don't transfer me",
            "Billing or maybe support",
            "Tell me about your pricing plans",
            "Why was I charged twice?",
            "FallbackClassifier",
            "flibbertigibbet quantum banana",
            "I did not hear anything. Please say support, sales, billing, or agent.",
        ):
            self.assertIn(expected, values)

    def test_rasa_nlu_regression_runner_passes_expected_intent_cases(self):
        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            if request.full_url.endswith("/webhooks/rest/webhook"):
                return FakeRasaParseResponse([{"text": f"Bot reply for {payload['message']}"}])
            text = payload["text"]
            if "pricing" in text:
                intent = "sales"
            elif "charged twice" in text:
                intent = "billing"
            else:
                intent = "support"
            return FakeRasaParseResponse({"intent": {"name": intent, "confidence": 0.91}})

        cases = [
            {"id": "CHAT-NLU-001", "user_input": "I have a problem with my connection", "expected_intent": "support"},
            {"id": "CHAT-NLU-003", "user_input": "Tell me about your pricing plans", "expected_intent": "sales"},
            {"id": "CHAT-NLU-005", "user_input": "Why was I charged twice?", "expected_intent": "billing"},
        ]
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            results = [
                run_rasa_nlu_regression.evaluate_case(case, "http://rasa/model/parse", 1.0, 2000)
                for case in cases
            ]

        self.assertTrue(all(result.status == "passed" for result in results))
        self.assertTrue(all(result.bot_reply.startswith("Bot reply for") for result in results))

    def test_rasa_nlu_regression_runner_stabilizes_known_contact_center_misroutes(self):
        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            if request.full_url.endswith("/webhooks/rest/webhook"):
                return FakeRasaParseResponse([{"text": f"Wrong bot reply for {payload['message']}"}])
            text = payload["text"]
            if "problem with my connection" in text:
                intent = "nlu_fallback"
            elif "service has stopped working" in text:
                intent = "greet"
            elif "don't want sales" in text:
                intent = "sales"
            else:
                intent = "nlu_fallback"
            return FakeRasaParseResponse({"intent": {"name": intent, "confidence": 0.12}})

        cases = [
            {"id": "CHAT-NLU-001", "user_input": "I have a problem with my connection", "expected_intent": "support"},
            {"id": "CHAT-NLU-002", "user_input": "My service has stopped working", "expected_intent": "support"},
            {"id": "CHAT-NEG-001", "user_input": "I don't want sales", "expected_intent": "deny"},
        ]
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            results = [
                run_rasa_nlu_regression.evaluate_case(case, "http://rasa/model/parse", 1.0, 2000)
                for case in cases
            ]

        self.assertTrue(all(result.status == "passed" for result in results))
        self.assertEqual([result.predicted_intent for result in results], ["support", "support", "deny"])
        self.assertTrue(all("stabilized_by_playSBC_guard" in result.detail for result in results))
        self.assertIn("Support path is ready", results[0].bot_reply)
        self.assertIn("will not transfer", results[2].bot_reply)

    def test_rasa_nlu_regression_runner_handles_empty_and_long_inputs(self):
        captured = {}

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            if request.full_url.endswith("/webhooks/rest/webhook"):
                return FakeRasaParseResponse([{"text": "Bot handled the long message."}])
            captured["text"] = payload["text"]
            return FakeRasaParseResponse({"intent": {"name": "support", "confidence": 0.80}})

        empty = run_rasa_nlu_regression.evaluate_case(
            {"id": "CHAT-NEG-004", "user_input": "", "expected_intent": "no_input"},
            "http://rasa/model/parse",
            1.0,
            12,
        )
        language = run_rasa_nlu_regression.evaluate_case(
            {"id": "CHAT-NEG-008", "user_input": "necesito ayuda", "expected_intent": "language_limitation"},
            "http://rasa/model/parse",
            1.0,
            2000,
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            long_result = run_rasa_nlu_regression.evaluate_case(
                {
                    "id": "CHAT-NEG-007",
                    "user_input": "Very long message repeated safely",
                    "expected_intent": "safe_processing",
                },
                "http://rasa/model/parse",
                1.0,
                12,
            )

        self.assertEqual(empty.status, "passed")
        self.assertEqual(empty.predicted_intent, "no_input")
        self.assertEqual(language.status, "passed")
        self.assertEqual(language.predicted_intent, "language_limitation")
        self.assertIn("English", language.bot_reply)
        self.assertEqual(long_result.status, "passed")
        self.assertEqual(long_result.bot_reply, "Bot handled the long message.")
        self.assertEqual(len(captured["text"]), 12)

    def test_regression_html_embeds_chat_nlu_snippets(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            bundle.mkdir()
            (bundle / "rasa-nlu-results.json").write_text(
                json.dumps(
                    [
                        {
                            "case_id": "CHAT-NLU-003",
                            "user_input": "Tell me about your pricing plans",
                            "expected_intent": "sales",
                            "predicted_intent": "sales",
                            "confidence": 0.94,
                            "bot_reply": "I can connect you with sales for a new service.",
                            "status": "passed",
                            "detail": "expected=sales predicted=sales",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (bundle / "ai-speech-output.wav").write_bytes(b"old voice evidence")

            rows = [
                ReportRow(
                    suite="Kubernetes AI/Rasa NLU",
                    name="AI Rasa Chat NLU - Intent Matrix [ai-rasa-chat-nlu]",
                    status="passed",
                    returncode=0,
                    duration_seconds=1.0,
                    log_path=str(bundle),
                    command="tools/run_rasa_nlu_regression.py",
                    sip_ladder=(
                        "NLP CHAT / RASA LADDER\n"
                        "Step       Chat YAML           K8s Runner        PlaySBC Guard          Rasa NLU            Rasa Bot          HTML Report\n"
                        "06              |                   |                   |  POST /webhook    |                   |                   |\n"
                    ),
                )
            ]
            full_suite_html = render_html(rows, "now", "unit-full")
            html = render_html(
                rows,
                "now",
                "unit-rasa",
                include_rasa_test_section=True,
            )

        self.assertIn("Rasa Chat Window", html)
        self.assertIn("RASA test section", html)
        self.assertIn("AI/Rasa End-to-End Regression Flow", html)
        self.assertNotIn("RASA test section", full_suite_html)
        self.assertNotIn("AI/Rasa End-to-End Regression Flow", full_suite_html)
        self.assertIn("Chat Intent Matrix", html)
        self.assertIn("Chat YAML", html)
        self.assertIn("K8s Runner", html)
        self.assertIn("Rasa Bot Webhook", html)
        self.assertIn("HTML Report", html)
        self.assertIn("NLP Chat/Rasa Ladder", html)
        self.assertIn("Chat YAML", html)
        self.assertIn("Rasa Bot", html)
        self.assertIn("POST /webhook", html)
        self.assertIn("CHAT-NLU-003", html)
        self.assertIn("Tell me about your pricing plans", html)
        self.assertIn("I can connect you with sales", html)
        self.assertIn("Expected <b>sales</b>", html)
        self.assertIn("sales", html)
        self.assertNotIn("AI Speech Audio Evidence", html)
        self.assertNotIn("<audio controls", html)
        self.assertIn("<details class=\"test-case pass\">", html)
        self.assertNotIn("<details class=\"test-case pass\" open>", html)

    def test_regression_html_keeps_failed_chat_nlu_details_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            bundle.mkdir()
            (bundle / "rasa-nlu-results.json").write_text(
                json.dumps(
                    [
                        {
                            "case_id": "CHAT-NEG-005",
                            "user_input": "random text",
                            "expected_intent": "fallback",
                            "predicted_intent": "unknown",
                            "confidence": 0.10,
                            "bot_reply": "I did not understand that.",
                            "status": "failed",
                            "detail": "expected=fallback predicted=unknown",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            row = ReportRow(
                suite="Kubernetes AI/Rasa NLU",
                name="AI Rasa Negative Chat - Guardrails [ai-rasa-chat-negative]",
                status="failed",
                returncode=1,
                duration_seconds=1.0,
                log_path=str(bundle),
                command="tools/run_rasa_nlu_regression.py",
                sip_ladder="NLP CHAT / RASA LADDER\n",
            )

            html = render_html([row], "now", "unit-rasa", include_rasa_test_section=True)

        self.assertIn("<details class=\"test-case fail\">", html)
        self.assertNotIn("<details class=\"test-case fail\" open>", html)


class FakeRasaParseResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
