# PlaySBC AI Voice Gateway

PlaySBC can act as an AI voice endpoint: it answers a SIP call, anchors RTP/RTCP through RTPengine when enabled, converts speech to text, sends the text to Rasa, generates a TTS reply, and records the full evidence in the regression report.

```text
SIPp caller -> PlaySBC AI route -> RTPengine -> STT -> Rasa -> TTS -> RTP prompt evidence
```

## Architecture

- **PlaySBC:** SIP callee, AI gateway, RTP decode/encode point, and Rasa client.
- **RTPengine:** media/RTCP anchor. It does not perform STT, TTS, or bot logic.
- **Vosk / Whisper STT:** convert decoded caller WAV audio into text through the same adapter boundary.
- **Rasa:** receives the text over REST and returns bot text/actions.
- **Piper / Coqui TTS:** generate spoken bot reply WAV/RTP evidence.
- **SIPp:** caller traffic generator for voice profiles; chat profiles use YAML text cases.

## RASA Test Section

Use `--rasa-profiles` for the focused AI/Rasa suite. It runs the AI/Rasa voice, speech, streaming, contact-center, and chat/NLU profiles and writes:

```text
logs/RASA-Regression/<run-id>/RASA-reports/latest.html
```

Common flow:

```text
K8s regression Job
  -> render profile config
  -> roll PlaySBC/RTPengine/Rasa when needed
  -> run SIPp voice traffic or Rasa chat YAML cases
  -> validate Rasa intent/response/action
  -> render logs, ladders, chat windows, audio evidence, and verdicts
```

| Profile | Purpose | E2E Flow | Evidence |
| --- | --- | --- | --- |
| `ai-rasa-lab` | Mock Rasa sanity check. | K8s Runner -> profile config -> SIPp A -> PlaySBC AI callee -> scripted STT/media -> Mock Rasa REST -> `log.ai` -> HTML Report. | `log.ai`, `log.sip`, `log.media`, `capture.pcap`, mock ladder. |
| `ai-rasa-rtpengine` | Mock Rasa with RTPengine media anchor. | K8s Runner -> profile config -> SIPp A -> PlaySBC -> RTPengine -> Mock Rasa REST/action -> RTPengine evidence -> HTML Report. | RTPengine query evidence, `log.ai`, `log.media`, AI ladder. |
| `ai-rasa-real-lab` | Real Rasa pod integration. | K8s Runner -> Helm/Rasa config -> Real Rasa Pod train/start -> SIPp A -> PlaySBC/RTPengine -> Rasa webhook -> HTML Report. | Rasa rollout logs, pod evidence, `log.ai`, `log.sip`, `log.media`. |
| `ai-rasa-rtpengine-speech` | Real speech STT/TTS path. | K8s Runner -> SIPp A speech PCAP -> RTPengine -> PlaySBC WAV decode -> Vosk STT -> Real Rasa -> Piper TTS -> RTP prompt/WAV evidence -> HTML Report. | Input/output WAV players, RTPengine evidence, Vosk/Rasa/Piper ladder. |
| `ai-rasa-rtpengine-speech-whisper` | Whisper STT speech variant. | K8s Runner -> SIPp A speech PCAP -> RTPengine -> PlaySBC WAV decode -> Whisper STT adapter -> Real Rasa -> Piper TTS -> RTP prompt/WAV evidence -> HTML Report. | `provider=whisper`, WAV/RTP prompt evidence, AI ladder. |
| `ai-rasa-long-response-streaming` | Long bot response streaming. | K8s Runner -> SIPp A speech PCAP -> RTPengine -> PlaySBC -> Real Rasa long response -> ordered Piper TTS chunks -> per-chunk RTP prompt evidence -> HTML Report. | `AI TTS STREAM` logs, chunked WAV/RTP prompt artifacts, AI ladder. |
| `ai-rasa-contact-center-sales` | Contact-center bot-agent call. | K8s Runner -> SIPp A -> PlaySBC virtual SIPp B Bot Agent -> RTPengine -> Vosk STT -> Real Rasa sales workflow -> Piper TTS -> HTML Report. | Contact-center ladder, speech WAVs, `log.ai`, `log.media`. |
| `ai-rasa-contact-center-sales-coqui` | Coqui TTS contact-center variant. | K8s Runner -> SIPp A -> PlaySBC virtual SIPp B Bot Agent -> RTPengine -> Vosk STT -> Real Rasa sales workflow -> Coqui TTS -> RTP prompt/WAV evidence -> HTML Report. | `renderer=coqui`, contact-center ladder, speech WAVs. |
| `ai-rasa-chat-nlu` | Positive chat intent matrix. | Chat YAML -> K8s Runner -> PlaySBC Guard -> Rasa NLU `/model/parse` -> Rasa Bot Webhook -> JSON verdict/chat window -> HTML Report. | Rasa chat window, `rasa-nlu-results.json`, `log.rasa-nlu`, NLP ladder. |
| `ai-rasa-chat-negative` | **Negative Chat / Guardrails.** | Negative Chat YAML -> K8s Runner -> PlaySBC no-input/language guards -> Rasa NLU/webhook when valid -> JSON verdict/guardrail chat window -> HTML Report. | Guardrail chat window, `rasa-nlu-results.json`, `log.rasa-nlu`, NLP ladder. |

The negative profile covers denial, ambiguity, empty input, fallback text, special characters, long input, unsupported language, offensive/frustrated input, and "transfer me - actually don't".

## Chat Coverage

Positive chat profile:

- `support`: connection/service problems.
- `sales`: pricing and new-connection requests.
- `billing`: invoice and duplicate-charge questions.
- `agent`: human-assistance requests.
- `repeat`, `confirm`, `deny`: simple control intents.

Negative chat profile:

- `deny`: "I don't want sales", "Transfer me - actually don't".
- `clarify`: ambiguous requests like "Billing or maybe support".
- `no_input`: empty message handled before Rasa.
- `nlu_fallback`: random text and special characters.
- `language_limitation`: unsupported-language text.
- `safe_continue`: frustrated/offensive input handled safely.
- `safe_processing`: long text is bounded and processed safely.

Case files:

```text
tests/rasa/chat_nlu_cases.yml
tests/rasa/chat_negative_cases.yml
```

## Run RASA-Only K8s Regression

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

Open:

```text
logs/RASA-Regression/<run-id>/RASA-reports/latest.html
```

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
  input_mode: speech
  stt_provider: vosk
  tts_provider: piper
  response_mode: rest
```

Whisper and Coqui are selectable by changing the adapter providers and commands:

```yaml
ai_voice_gateway:
  stt_provider: whisper
  stt_command: python3 tools/whisper_stt_wrapper.py --audio {audio_path} --fallback-transcript "{text}" --allow-lab-fallback
  tts_provider: coqui
  tts_command: python3 tools/coqui_tts_wrapper.py --text "{text}" --output {audio_path} --allow-lab-fallback
```

For longer bot replies, set:

```yaml
ai_voice_gateway:
  response_mode: streaming
  tts_chunk_chars: 120
```

## Still To Build

- Package heavyweight Whisper/Coqui image variants with real models preloaded.
- Execute bot actions with SIP REFER/re-INVITE, conference join, and bot-driven release.
