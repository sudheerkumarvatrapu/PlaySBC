import asyncio
import json
import unittest
from unittest import mock

from pathlib import Path
import tempfile

from ai_gateway import AiVoiceConfig, AiVoiceGateway, DtmfIntentMapper, RasaRestClient, RasaRestConfig, TextToSpeechAdapter
from ai_gateway.speech import decode_rtp_pcap_to_wav, iter_rtp_payloads
from tools import check_rasa


class FakeHttpResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class RasaRestClientTests(unittest.TestCase):
    def test_rasa_rest_client_posts_sender_message_and_metadata(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["timeout"] = timeout
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["content_type"] = request.headers["Content-type"]
            return FakeHttpResponse([{"text": "reply to hello"}])

        client = RasaRestClient(
            RasaRestConfig(
                webhook_url="http://rasa.example/webhooks/rest/webhook",
                timeout=2.0,
            )
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            responses = client.send_message("call-1", "hello", {"caller": "alice"})

        self.assertEqual(responses[0].text, "reply to hello")
        self.assertEqual(captured["timeout"], 2.0)
        self.assertEqual(captured["url"], "http://rasa.example/webhooks/rest/webhook")
        self.assertEqual(captured["body"]["sender"], "call-1")
        self.assertEqual(captured["body"]["message"], "hello")
        self.assertEqual(captured["body"]["metadata"]["caller"], "alice")
        self.assertEqual(captured["content_type"], "application/json")

    def test_check_rasa_posts_contract_message(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeHttpResponse([{"text": "rasa ok"}])

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            responses = check_rasa.post_rasa(
                "http://rasa.example/webhooks/rest/webhook",
                "sender-1",
                "support",
                2.5,
            )

        self.assertEqual(responses[0]["text"], "rasa ok")
        self.assertEqual(captured["timeout"], 2.5)
        self.assertEqual(captured["body"]["sender"], "sender-1")
        self.assertEqual(captured["body"]["message"], "support")
        self.assertEqual(captured["body"]["metadata"]["source"], "playsbc-check-rasa")

    def test_ai_voice_gateway_returns_rasa_turn_result(self):
        async def fake_send(_client, sender, message, metadata):
            self.assertEqual(sender, "call-2")
            self.assertEqual(message, "hello from voice")
            self.assertEqual(metadata["callee"], "ai-bot")
            return [FakeBotResponse("reply to hello from voice")]

        with mock.patch.object(RasaRestClient, "send_message_async", fake_send):
            gateway = AiVoiceGateway(
                AiVoiceConfig(
                    enabled=True,
                    rasa_webhook_url="http://rasa.example/webhooks/rest/webhook",
                    initial_message="hello from voice",
                )
            )

            result = asyncio.run(gateway.start_turn("call-2", {"callee": "ai-bot"}))

        self.assertFalse(result.fallback_used)
        self.assertEqual(result.user_text, "hello from voice")
        self.assertEqual(result.rendered_text, "reply to hello from voice")
        self.assertEqual(result.stt.provider, "lab-scripted")
        self.assertEqual(result.tts.provider, "text-only")

    def test_ai_voice_gateway_extracts_bot_actions_from_rasa_custom_payload(self):
        async def fake_send(_client, _sender, _message, _metadata):
            return [
                FakeBotResponse(
                    "I can transfer you",
                    custom={
                        "playsbc_action": "transfer",
                        "target": "sip:agent@peer.example",
                        "reason": "caller asked for agent",
                    },
                )
            ]

        with mock.patch.object(RasaRestClient, "send_message_async", fake_send):
            gateway = AiVoiceGateway(AiVoiceConfig(enabled=True, response_mode="streaming"))
            result = asyncio.run(gateway.start_turn("call-3", {}))

        self.assertEqual(result.response_mode, "streaming")
        self.assertEqual(len(result.bot_actions), 1)
        self.assertEqual(result.bot_actions[0].action, "transfer")
        self.assertEqual(result.bot_actions[0].target, "sip:agent@peer.example")

    def test_tts_adapter_reports_unconfigured_real_engine_without_failing_lab(self):
        result = asyncio.run(TextToSpeechAdapter("piper").synthesize("hello"))

        self.assertFalse(result.engine_ready)
        self.assertFalse(result.audio_generated)
        self.assertEqual(result.error, "tts_command_not_configured")

    def test_speech_pcap_decodes_to_wav_and_sidecar_transcript(self):
        root = Path(__file__).resolve().parents[1]
        pcap = root / "sipp" / "scenarios" / "pcap" / "ai_rasa_speech_g711u.pcap"
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "speech.wav"
            extraction = decode_rtp_pcap_to_wav(pcap, wav, codec="PCMU")

            self.assertTrue(wav.exists())
            self.assertGreater(extraction.packets, 0)
            self.assertEqual(extraction.transcript, "I need support")
            self.assertEqual(extraction.payload_type, 0)

    def test_tts_adapter_generates_lab_rtp_prompt_when_real_engine_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "tts.wav"
            pcap = Path(tmp) / "tts.pcap"
            result = asyncio.run(TextToSpeechAdapter("piper").synthesize("Support path is ready", str(wav), str(pcap)))

            self.assertFalse(result.engine_ready)
            self.assertTrue(result.audio_generated)
            self.assertTrue(result.rtp_prompt_generated)
            self.assertTrue(wav.exists())
            self.assertTrue(pcap.exists())
            self.assertGreater(len(list(iter_rtp_payloads(pcap, payload_type=0))), 0)

    def test_dtmf_mapper_translates_digits_to_text(self):
        mapper = DtmfIntentMapper({"1": "balance", "2": "support"})

        self.assertEqual(mapper.text_for_digits("12"), "balance support")
        self.assertEqual(mapper.text_for_digits("9"), "dtmf 9")


class FakeBotResponse:
    def __init__(self, text, custom=None):
        self.text = text
        self.custom = custom


if __name__ == "__main__":
    unittest.main()
