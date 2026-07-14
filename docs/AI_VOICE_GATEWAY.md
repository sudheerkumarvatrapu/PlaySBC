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
- **Rasa integration:** PlaySBC posts `sender`, `message`, and call metadata to the Rasa REST webhook. The default regression uses a deterministic mock; `ai-rasa-real-lab` starts a real Rasa REST bot.
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
| `ai-rasa-real-lab` | Optional profile: same RTPengine AI call, but PlaySBC talks to a real Rasa REST service trained from `rasa/` | `log.ai`, `rasa.log`, `log.sip`, `log.media`, RTPengine evidence |
| Unit: Rasa REST client | Validates Rasa request/response JSON shape | `tests/test_ai_gateway.py` |
| Unit: AI route policy | Validates `ai-gateway:<bot>` routing | `tests/test_mini_call_server.py` |
| Unit: dual-realm profile | Validates mock Rasa service and `log.ai` bundle wiring | `tests/test_sipp_harness.py` |

## Real Rasa Lab

The real Rasa profile is optional by design. The normal `--all-b2bua-profiles` suite stays fast and deterministic with the mock service. Use `ai-rasa-real-lab` when you want to prove the actual Rasa REST channel.

### Direct Local

Terminal 1:

```bash
cd rasa
rasa train --config config.yml --domain domain.yml --data data --out /tmp/playsbc-rasa-models
rasa run --enable-api --cors "*" --host 0.0.0.0 --port 5005 \
  --model /tmp/playsbc-rasa-models --credentials credentials.yml --endpoints endpoints.yml
```

Terminal 2:

```bash
python3 tools/check_rasa.py --url http://127.0.0.1:5005/webhooks/rest/webhook
python3 mini_call_server.py --config configs/config.ai-rasa-real.example.yaml
```

### Docker Dual Realm

The profile starts the official Rasa image inside the core realm at `172.28.0.61`.

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_regression_suite.py \
  --skip-sipp-smoke \
  --b2bua-profile ai-rasa-real-lab \
  --timeout 420
```

### Kubernetes

Deploy or upgrade with the real Rasa values file:

```bash
helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --create-namespace \
  -f configs/kubernetes/kind-values.yaml \
  -f configs/kubernetes/ai-rasa-real-values.yaml
```

Run only the real Rasa Kubernetes profile:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression_job.py \
  --profile ai-rasa-real-lab \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

For kind clusters without DockerHub pull access, pre-pull and load the Rasa image:

```bash
docker pull rasa/rasa:3.6.20-full
kind load docker-image rasa/rasa:3.6.20-full --name playsbc
```

## Still To Build

- Decode RTP audio into engine-ready files for Whisper/Vosk.
- Generate playable G.711 prompt RTP from Piper/Coqui output.
- Execute bot actions with SIP REFER/re-INVITE, conference join, and bot-driven release.
