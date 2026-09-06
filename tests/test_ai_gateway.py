import asyncio
import json
import unittest
from unittest import mock

from pathlib import Path
import tempfile

from ai_gateway import AiVoiceConfig, AiVoiceGateway, ConversationChunk, DtmfIntentMapper, RasaRestClient, RasaRestConfig, TextToSpeechAdapter
from ai_gateway.rasa import RasaBotResponse
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
    def test_ai_voice_gateway_accepts_provider_neutral_ordered_stream(self):
        class StreamingProvider:
            async def stream(self, request):
                self.assert_request(request)
                yield ConversationChunk(RasaBotResponse(text="first"), 1, False)
                yield ConversationChunk(RasaBotResponse(text="second"), 2, True)

            @staticmethod
            def assert_request(request):
                if request.sender != "provider-call" or request.metadata["tenant"] != "commercial":
                    raise AssertionError("provider request contract was not preserved")

        gateway = AiVoiceGateway(
            AiVoiceConfig(enabled=True, initial_message="hello", response_mode="streaming"),
            conversation_provider=StreamingProvider(),
        )
        result = asyncio.run(gateway.start_turn("provider-call", {"tenant": "commercial"}))

        self.assertEqual(result.rendered_text, "first second")
        self.assertEqual(result.tts_chunk_count, 2)

    def test_ai_voice_gateway_times_out_provider_and_synthesizes_fallback(self):
        class StalledProvider:
            async def stream(self, _request):
                await asyncio.Event().wait()
                yield ConversationChunk(RasaBotResponse(text="unreachable"), 1, True)

        gateway = AiVoiceGateway(
            AiVoiceConfig(
                enabled=True,
                initial_message="support",
                provider_timeout=0.01,
                fallback_text="Please try again later.",
            ),
            conversation_provider=StalledProvider(),
        )
        result = asyncio.run(gateway.start_turn("provider-timeout", {}))

        self.assertTrue(result.fallback_used)
        self.assertFalse(result.interrupted)
        self.assertEqual(result.error_code, "provider_timeout")
        self.assertEqual(result.rendered_text, "Please try again later.")
        self.assertEqual(result.tts_chunk_count, 1)

    def test_ai_voice_gateway_interrupts_inflight_provider_without_fallback_or_tts(self):
        async def scenario():
            started = asyncio.Event()
            provider_cancelled = asyncio.Event()

            class StalledProvider:
                async def stream(self, _request):
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        provider_cancelled.set()
                    yield ConversationChunk(RasaBotResponse(text="unreachable"), 1, True)

            cancellation = asyncio.Event()
            gateway = AiVoiceGateway(
                AiVoiceConfig(enabled=True, initial_message="support", provider_timeout=1.0),
                conversation_provider=StalledProvider(),
            )
            turn = asyncio.create_task(
                gateway.start_turn("provider-interrupted", {}, cancellation_event=cancellation)
            )
            await asyncio.wait_for(started.wait(), timeout=0.25)
            cancellation.set()
            result = await asyncio.wait_for(turn, timeout=0.25)
            await asyncio.wait_for(provider_cancelled.wait(), timeout=0.25)
            return result

        result = asyncio.run(scenario())

        self.assertTrue(result.interrupted)
        self.assertFalse(result.fallback_used)
        self.assertEqual(result.error_code, "provider_cancelled")
        self.assertEqual(result.bot_responses, [])
        self.assertEqual(result.tts_chunk_count, 0)

    def test_ai_voice_gateway_converts_unexpected_provider_failure_to_fallback(self):
        class FailedProvider:
            async def stream(self, _request):
                raise RuntimeError("provider connection lost")
                yield ConversationChunk(RasaBotResponse(text="unreachable"), 1, True)

        gateway = AiVoiceGateway(
            AiVoiceConfig(enabled=True, initial_message="support", fallback_text="Fallback response."),
            conversation_provider=FailedProvider(),
        )
        result = asyncio.run(gateway.start_turn("provider-failure", {}))

        self.assertTrue(result.fallback_used)
        self.assertEqual(result.error_code, "provider_error")
        self.assertIn("provider connection lost", result.error)
        self.assertEqual(result.rendered_text, "Fallback response.")

    def test_provider_timeout_defaults_to_the_rasa_transport_timeout(self):
        config = AiVoiceConfig.from_dict({"rasa_timeout": 4.5})

        self.assertEqual(config.provider_timeout, 4.5)
        self.assertEqual(config.to_dict()["provider_timeout"], 4.5)

    def test_ai_voice_gateway_rejects_nonpositive_provider_timeout(self):
        with self.assertRaisesRegex(ValueError, "provider timeout must be greater than zero"):
            AiVoiceGateway(AiVoiceConfig(enabled=True, provider_timeout=0))

    def test_call_interruption_wins_race_with_provider_final_chunk(self):
        async def scenario():
            cancellation = asyncio.Event()

            class RacingProvider:
                async def stream(self, _request):
                    cancellation.set()
                    yield ConversationChunk(RasaBotResponse(text="late response"), 1, True)

            gateway = AiVoiceGateway(
                AiVoiceConfig(enabled=True, initial_message="support"),
                conversation_provider=RacingProvider(),
            )
            return await gateway.start_turn(
                "provider-race",
                {},
                cancellation_event=cancellation,
            )

        result = asyncio.run(scenario())

        self.assertTrue(result.interrupted)
        self.assertEqual(result.error_code, "provider_cancelled")
        self.assertEqual(result.tts_chunk_count, 0)

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

    def test_ai_voice_gateway_reprompts_empty_input_without_calling_rasa(self):
        async def fake_send(_client, _sender, _message, _metadata):
            raise AssertionError("blank input should be handled before the Rasa REST call")

        with mock.patch.object(RasaRestClient, "send_message_async", fake_send):
            gateway = AiVoiceGateway(
                AiVoiceConfig(
                    enabled=True,
                    initial_message="   ",
                    no_input_text="Please say support, sales, billing, or agent.",
                )
            )
            result = asyncio.run(gateway.start_turn("call-empty", {"callee": "ai-bot"}))

        self.assertFalse(result.fallback_used)
        self.assertEqual(result.error, "no_input")
        self.assertEqual(result.user_text, "")
        self.assertEqual(result.rendered_text, "Please say support, sales, billing, or agent.")

    def test_ai_voice_gateway_caps_very_long_input_before_rasa(self):
        captured = {}

        async def fake_send(_client, _sender, message, _metadata):
            captured["message"] = message
            return [FakeBotResponse("processed safely")]

        with mock.patch.object(RasaRestClient, "send_message_async", fake_send):
            gateway = AiVoiceGateway(
                AiVoiceConfig(
                    enabled=True,
                    initial_message="x" * 120,
                    max_user_text_chars=32,
                )
            )
            result = asyncio.run(gateway.start_turn("call-long", {}))

        self.assertEqual(captured["message"], "x" * 32)
        self.assertEqual(result.rendered_text, "processed safely")

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

    def test_ai_voice_gateway_streams_long_responses_as_ordered_tts_chunks(self):
        async def fake_send(_client, _sender, _message, _metadata):
            return [
                FakeBotResponse("First answer sentence."),
                FakeBotResponse("Second answer sentence that should stay ordered."),
                FakeBotResponse("Third answer sentence closes the response."),
            ]

        with mock.patch.object(RasaRestClient, "send_message_async", fake_send):
            gateway = AiVoiceGateway(
                AiVoiceConfig(
                    enabled=True,
                    response_mode="streaming",
                    tts_chunk_chars=80,
                )
            )
            result = asyncio.run(gateway.start_turn("call-stream", {}))

        self.assertEqual(result.response_mode, "streaming")
        self.assertEqual(result.tts_chunk_count, 3)
        self.assertEqual([chunk.chunk_index for chunk in result.tts_chunks], [1, 2, 3])
        self.assertEqual([chunk.chunk_count for chunk in result.tts_chunks], [3, 3, 3])
        self.assertEqual(result.tts_chunks[0].text, "First answer sentence.")
        self.assertEqual(result.tts.provider, "text-only")

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
