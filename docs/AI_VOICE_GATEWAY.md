# PlaySBC AI Voice Gateway

PlaySBC now has an AI Voice Gateway path for lab calls.

```text
SIPp A / caller
  -> PlaySBC SIP route policy: ai-gateway:rasa-support
     -> PlaySBC answers the SIP call and owns the RTP session
        -> STT/intent adapter stage: lab-scripted, Whisper, or Vosk boundary
           -> Rasa REST webhook
              -> TTS adapter stage: text-only, Piper, or Coqui boundary
                 -> voice/media response stage
```

## Current Architecture

- **SIP termination:** PlaySBC terminates the inbound SIP call as an AI endpoint.
- **Routing:** `route_policies[].target` can use `ai-gateway:<bot-name>`.
- **Media:** AI calls can use internal RTP input or RTPengine anchoring. The RTPengine profile keeps RTP/RTCP on RTPengine and leaves PlaySBC as SIP/control plus AI orchestration.
- **Rasa integration:** PlaySBC posts `sender`, `message`, and call metadata to the Rasa REST webhook.
- **STT/TTS boundary:** Real engine adapters are now named in config. `lab-scripted` and `text-only` keep regression portable; `whisper`/`vosk` and `piper`/`coqui` can be enabled with local command wrappers.
- **Long replies:** Rasa multi-message responses are preserved and shown as response chunks in the AI ladder.
- **Bot actions:** Rasa `custom` payloads can request `join`, `transfer`, or `release`. Today these are accepted and logged as control-plane actions; SIP REFER/re-INVITE/conference execution is the next deeper step.
- **Logging:** `log.ai` records AI call start/end, STT input, Rasa request/response, TTS output, and the AI call ladder.

The SIPp media PCAP used by the test is only G.711 lab audio. Earlier builds echoed that tone back, which sounded like a continuous beep. The AI gateway now records/analyzes input RTP without echoing fake TTS audio.

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
  stt_provider: lab-scripted
  tts_provider: text-only
  response_mode: rest
  bot_actions_enabled: true
  initial_message: hello from playsbc voice
  fallback_text: Rasa lab bot is unavailable
```

## Regression Coverage

| Test case | Purpose | Evidence |
| --- | --- | --- |
| `ai-rasa-lab` | SIPp A calls `ai-bot`; PlaySBC answers, records RTP input, and sends one Rasa REST turn | `log.ai`, `log.sip`, `log.media`, `capture.pcap`, HTML report |
| `ai-rasa-rtpengine` | SIPp A calls `ai-bot`; RTP/RTCP is anchored by RTPengine; Rasa returns a multi-part response plus a transfer action | `log.ai`, `log.media`, RTPengine query evidence, HTML ladder |
| Unit: Rasa REST client | Validates Rasa request/response JSON shape | `tests/test_ai_gateway.py` |
| Unit: AI route policy | Validates `ai-gateway:<bot>` routing | `tests/test_mini_call_server.py` |
| Unit: dual-realm profile | Validates mock Rasa service and `log.ai` bundle wiring | `tests/test_sipp_harness.py` |

## Still To Build

- Decode RTP audio into engine-ready files for Whisper/Vosk.
- Generate playable G.711 prompt RTP from Piper/Coqui output.
- Execute bot actions with SIP REFER/re-INVITE, conference join, and bot-driven release.
