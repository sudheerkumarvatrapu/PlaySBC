# PlaySBC AI Voice Gateway

PlaySBC can answer a SIP call as an AI endpoint, anchor its media through RTPengine, convert speech to text, send the transcript to Rasa, synthesize the response, and preserve the evidence in one report.

The commercial pre-v6 foundation exposes a provider-neutral, ordered
asynchronous response stream. Rasa is the first adapter; future bot providers
implement the same `ConversationProvider` contract without changing SIP or
media control.

```text
SIPp caller -> PlaySBC -> RTPengine -> STT -> Rasa -> TTS -> RTP response
```

## Component Roles

| Component | Responsibility |
| --- | --- |
| PlaySBC | SIP/B2BUA control, media conversion, AI orchestration, and evidence |
| RTPengine | RTP/RTCP anchoring and media transformation |
| Vosk or Whisper | STT through the shared adapter boundary |
| Rasa | Intent recognition, dialogue, and bot responses |
| Piper or Coqui | TTS through the shared adapter boundary |
| SIPp | Voice traffic and speech-PCAP playback |

## Rasa Regression Profiles

Run `--rasa-profiles` to execute only the AI/Rasa suite. The report is written to:

```text
logs/RASA-Regression/<run-id>/RASA-reports/latest.html
```

| Profile | Validates | Primary evidence |
| --- | --- | --- |
| `ai-rasa-lab` | PlaySBC AI route with mock Rasa | SIP/AI logs, ladder, merged PCAP |
| `ai-rasa-rtpengine` | Mock Rasa with anchored media | RTPengine query and media logs |
| `ai-rasa-real-lab` | Real Rasa train/start/webhook path | Rasa rollout, webhook, and SIP evidence |
| `ai-rasa-rtpengine-speech` | G.711 speech, Vosk, Rasa, and Piper | Input/output WAV, transcript, RTP prompt |
| `ai-rasa-rtpengine-speech-whisper` | Whisper STT alternative | Provider, transcript, WAV, and RTP evidence |
| `ai-rasa-long-response-streaming` | Ordered TTS chunks for long replies | Stream logs and per-chunk artifacts |
| `ai-rasa-contact-center-sales` | Vosk/Piper contact-center workflow | Sales ladder and speech evidence |
| `ai-rasa-contact-center-sales-coqui` | Coqui TTS alternative | Renderer and generated prompt evidence |
| `ai-rasa-chat-nlu` | Positive intent matrix | Chat window, JSON verdicts, NLU ladder |
| `ai-rasa-chat-negative` | Guardrails and negative inputs | Guardrail chat window and JSON verdicts |

The negative matrix covers denial, ambiguity, empty input, fallback text, special characters, long input, unsupported language, offensive input, and latest-instruction handling.

Case definitions:

```text
tests/rasa/chat_nlu_cases.yml
tests/rasa/chat_negative_cases.yml
```

## Run The Focused Suite

```bash
kubectl config use-context kind-playsbc
kubectl config set-context --current --namespace=playsbc

PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression_job.py \
  --rasa-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

Run the fast commercial foundation profiles before cluster regression:

```bash
python3 tools/run_commercial_foundation_regression.py
```

The command validates `ai-provider-streaming-contract` and
`rfc5359-consultation-hold`. Either profile can be selected independently with
`--profile`.

Use [KUBERNETES_HELM_RUNBOOK.md](KUBERNETES_HELM_RUNBOOK.md) for installation, image, observability, and cleanup commands.

## Configuration

```yaml
route_policies:
  - name: ai-rasa-gateway
    match: ai-bot
    target: ai-gateway:rasa-support

ai_voice_gateway:
  enabled: true
  provider: rasa
  rasa_webhook_url: http://rasa:5005/webhooks/rest/webhook
  input_mode: speech
  stt_provider: vosk
  tts_provider: piper
  response_mode: rest
```

Adapter alternatives:

```yaml
ai_voice_gateway:
  stt_provider: whisper
  stt_command: python3 tools/whisper_stt_wrapper.py --audio {audio_path} --fallback-transcript "{text}" --allow-lab-fallback
  tts_provider: coqui
  tts_command: python3 tools/coqui_tts_wrapper.py --text "{text}" --output {audio_path} --allow-lab-fallback
  response_mode: streaming
  tts_chunk_chars: 120
```

## Evidence Contract

Each voice profile should provide `sipmsg.log`, one merged `capture.pcap`, SIP/media/AI logs, an aligned ladder, and playable WAV evidence when speech is involved. Chat profiles provide an initially collapsed chat window, NLU verdict JSON, and an NLP ladder; old voice audio is not shown on chat-only reports.

## v3.0.0 Production Target

- Support multiple bot integrations through a stable provider adapter instead of coupling call control to one bot.
- Feed generated TTS RTP into live calls for every provider path and prove both media directions.
- Package production model images and explicit health/readiness contracts for STT and TTS providers.
- Add stateful multi-turn workflows, RFC 4733 DTMF, transfer, conference, fallback, and bot-driven release.
- Export per-provider STT, bot, TTS, streaming, fallback, and action latency/error metrics.
- Preserve canonical SIP/RTP/RTCP/AI evidence and all existing Docker, kind, AKS, and real-device gates.
