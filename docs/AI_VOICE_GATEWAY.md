# PlaySBC AI Voice Gateway

PlaySBC now has a first-phase AI Voice Gateway path for lab calls.

```text
SIPp A / caller
  -> PlaySBC SIP route policy: ai-gateway:rasa-support
     -> PlaySBC answers the SIP call and owns the RTP session
        -> STT/intent adapter stage
           -> Rasa REST webhook
              -> TTS adapter stage
                 -> voice/media response stage
```

## Current Architecture

- **SIP termination:** PlaySBC terminates the inbound SIP call as an AI endpoint.
- **Routing:** `route_policies[].target` can use `ai-gateway:<bot-name>`.
- **Media:** RTP is accepted by PlaySBC. Phase 1 logs the AI media pipeline and keeps the existing G.711 RTP evidence path.
- **Rasa integration:** PlaySBC posts `sender`, `message`, and call metadata to the Rasa REST webhook.
- **STT/TTS boundary:** STT and TTS are adapter stages today. They are intentionally isolated so Whisper/Vosk/Piper/Coqui can be added without disturbing SIP/B2BUA routing.
- **Logging:** `log.ai` records AI call start/end, STT input, Rasa request/response, TTS output, and the AI call ladder.

## Config Shape

```yaml
route_policies:
  - name: ai-rasa-gateway
    match: ai-bot
    target: ai-gateway:rasa-support
    priority: 5

ai_voice_gateway:
  enabled: true
  provider: rasa
  bot_name: rasa-support
  rasa_webhook_url: http://rasa:5005/webhooks/rest/webhook
  rasa_timeout: 3.0
  input_mode: scripted
  initial_message: hello from playsbc voice
  fallback_text: Rasa lab bot is unavailable
```

## Regression Coverage

| Test case | Purpose | Evidence |
| --- | --- | --- |
| `ai-rasa-lab` | SIPp A calls `ai-bot`; PlaySBC answers and sends one Rasa REST turn | `log.ai`, `log.sip`, `log.media`, `capture.pcap`, HTML report |
| Unit: Rasa REST client | Validates Rasa request/response JSON shape | `tests/test_ai_gateway.py` |
| Unit: AI route policy | Validates `ai-gateway:<bot>` routing | `tests/test_mini_call_server.py` |
| Unit: dual-realm profile | Validates mock Rasa service and `log.ai` bundle wiring | `tests/test_sipp_harness.py` |

## Next Test Cases

- DTMF-driven bot menu: RFC 4733 digit -> intent text -> Rasa REST.
- Real STT lab: RTP audio -> STT transcript -> Rasa REST.
- Real TTS lab: Rasa text -> G.711 prompt RTP.
- Bot-assisted B2BUA: AI joins a normal A/B call for transfer, release, or announcement.
