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
- **Rasa integration:** PlaySBC posts `sender`, `message`, and call metadata to the Rasa REST webhook. The default regression uses a deterministic mock; real-Rasa profiles start a real Rasa REST bot.
- **STT/TTS boundary:** Real engine adapters are named in config. `lab-scripted` and `text-only` keep regression portable; `whisper`/`vosk` and `piper`/`coqui` can be enabled with local command wrappers. When those commands are not installed, the speech regression uses deterministic lab fallback artifacts and logs `engine_ready=false`.
- **Speech evidence:** The speech profile decodes G.711 RTP PCAP to PCM/WAV before STT and generates a G.711 RTP prompt PCAP after TTS.
- **Long replies:** Rasa multi-message responses are preserved and shown as response chunks in the AI ladder.
- **Bot actions:** Rasa `custom` payloads can request `join`, `transfer`, or `release`. Today these are accepted and logged as control-plane actions; SIP REFER/re-INVITE/conference execution is the next deeper step.
- **Logging:** `log.ai` records AI call start/end, STT input, Rasa request/response, TTS output, and the AI call ladder.

The SIPp media PCAP used by the older tests is G.711 lab audio. The speech profile adds dedicated G.711u/G.711a speech fixtures with sidecar transcripts, decoded WAV evidence, and generated TTS RTP prompt evidence.

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

Speech profile additions:

```yaml
ai_voice_gateway:
  input_mode: speech
  speech_input_pcap: sipp/scenarios/pcap/ai_rasa_speech_g711u.pcap
  speech_input_codec: PCMU
  speech_transcript: support
  stt_provider: whisper
  tts_provider: piper
  tts_output_codec: PCMU
```

## Regression Coverage

| Test case | Short name | Purpose | Evidence |
| --- | --- | --- | --- |
| `ai-rasa-lab` | Mock Rasa REST, internal media | SIPp A calls `ai-bot`; PlaySBC terminates the AI call, consumes RTP input internally, and sends one deterministic mock Rasa REST turn | `log.ai`, `log.sip`, `log.media`, `capture.pcap`, HTML ladder with `Mock Rasa REST` |
| `ai-rasa-rtpengine` | Mock Rasa REST, RTPengine media | Same AI call, but RTP/RTCP is anchored by RTPengine; mock Rasa returns multiple response chunks plus a transfer action | `log.ai`, `log.media`, RTPengine query evidence, HTML ladder with `Mock Rasa + Action` |
| `ai-rasa-real-lab` | Real Rasa pod, RTPengine media | Kubernetes starts and trains a real Rasa REST pod, PlaySBC posts to that service, and RTP/RTCP remains anchored by RTPengine | `rasa.log`, `rasa-pod-evidence.log`, `log.ai`, `log.sip`, `log.media`, HTML ladder with `Real Rasa Pod` |
| `ai-rasa-rtpengine-speech` | Real Rasa pod, speech STT/TTS boundary | SIPp plays G.711 speech, PlaySBC decodes the RTP PCAP to WAV, feeds the transcript through the Whisper/Vosk boundary, posts to Rasa, and generates Piper/Coqui RTP prompt evidence | speech PCAP/WAV/TTS artifacts, `log.ai`, `log.media`, RTPengine query evidence, HTML ladder with STT/Rasa/TTS nodes |
| Unit: Rasa REST client | JSON contract | Validates Rasa request/response JSON shape | `tests/test_ai_gateway.py` |
| Unit: AI route policy | SIP route target | Validates `ai-gateway:<bot>` routing | `tests/test_mini_call_server.py` |
| Unit: dual-realm profile | Harness wiring | Validates mock Rasa service and `log.ai` bundle wiring | `tests/test_sipp_harness.py` |

## Real Rasa Lab

The real Rasa profiles are optional by design. The normal `--all-b2bua-profiles` suite stays fast and deterministic with the mock service. Use `ai-rasa-real-lab` or `ai-rasa-rtpengine-speech` when you want to prove the actual Rasa REST channel.

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

Run all Kubernetes AI/Rasa profiles:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression_job.py \
  --rasa-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

Rasa-only Kubernetes runs delete old `logs/RASA-Regression` output by default and write the latest report under:

```text
logs/RASA-Regression/<run-id>/RASA-reports/latest.html
```

For kind clusters without DockerHub pull access, pre-pull and load the Rasa image:

```bash
docker pull rasa/rasa:3.6.20-full
kind load docker-image rasa/rasa:3.6.20-full --name playsbc
```

## Still To Build

- Package actual Whisper/Vosk/Piper/Coqui binaries and models into optional lab images.
- Stream longer bot responses instead of one turn per call.
- Execute bot actions with SIP REFER/re-INVITE, conference join, and bot-driven release.
