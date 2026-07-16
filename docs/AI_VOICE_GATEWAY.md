# PlaySBC AI Voice Gateway

PlaySBC has an AI Voice Gateway path for lab calls. In this path, PlaySBC can answer a SIP call as the callee, anchor RTP/RTCP through RTPengine, transcribe caller speech, ask Rasa for the bot response, synthesize the reply with Piper, and send the generated speech back as RTP evidence.

```text
SIPp A / caller
  -> PlaySBC SIP route policy: ai-gateway:rasa-support
     -> PlaySBC answers the SIP call as the AI callee
        -> RTPengine anchors RTP/RTCP media
           -> PlaySBC decodes inbound G.711 RTP to WAV
              -> Vosk STT converts speech to text
                 -> Rasa REST webhook returns bot text/action
                    -> Piper TTS generates WAV speech
                       -> PlaySBC sends G.711 RTP prompt evidence back through RTPengine
```

## Current Architecture

- **SIP termination:** PlaySBC terminates the inbound SIP call as an AI endpoint. In the speech profile, PlaySBC is the callee for SIPp A.
- **Routing:** `route_policies[].target` can use `ai-gateway:<bot-name>`.
- **Media:** AI calls can use internal RTP input or RTPengine anchoring. RTPengine is the media anchor/relay; it does not perform STT, TTS, or bot logic.
- **Rasa integration:** PlaySBC posts `sender`, `message`, and call metadata to the Rasa REST webhook. The default regression uses a deterministic mock; real-Rasa profiles start a real Rasa REST bot.
- **STT/TTS engines:** `ai-rasa-rtpengine-speech` uses Vosk STT and Piper TTS through wrapper commands. Mock profiles still use portable scripted/text modes.
- **Speech evidence:** The speech profile plays a real Piper-generated G.711 speech PCAP saying `I need support`, decodes RTP to PCM/WAV for Vosk, sends the transcript to Rasa, and generates a G.711 RTP prompt PCAP from Piper's bot-response WAV.
- **Long replies:** Rasa multi-message responses are preserved and shown as response chunks in the AI ladder.
- **Bot actions:** Rasa `custom` payloads can request `join`, `transfer`, or `release`. Today these are accepted and logged as control-plane actions; SIP REFER/re-INVITE/conference execution is the next deeper step.
- **Logging:** `log.ai` records AI call start/end, STT input, Rasa request/response, TTS output, and the AI call ladder.

The SIPp media PCAP used by the older tests is G.711 lab audio. The speech profile adds dedicated G.711u/G.711a speech fixtures generated from real Piper speech, sidecar transcripts, decoded WAV evidence, Vosk STT evidence, and generated TTS RTP prompt evidence.

## Contact-Center Bot Agent

`ai-rasa-contact-center-sales` models a contact-center style call:

```text
SIPp A caller
  -> PlaySBC
     -> virtual SIPp B Bot Agent
        -> RTPengine media anchor
        -> Vosk STT
        -> real Rasa sales workflow
        -> Piper TTS bot-agent speech
        -> RTP prompt back through RTPengine
```

SIPp itself cannot run Rasa workflows, so SIPp B is represented as a virtual bot-agent B side inside PlaySBC. The profile labels that B side as `SIPp B Bot Agent` in `log.ai`, `log.sip`, and the HTML ladder. The actual workflow brain is real Rasa behind PlaySBC.

The caller speech fixture says `Connect me to sales`. Vosk transcribes it, Rasa matches the sales workflow, and Piper generates the spoken bot-agent reply.

## End-To-End Speech Call

The `ai-rasa-rtpengine-speech` profile proves the full AI speech path.

Actors:

- **SIPp A:** the caller. It sends SIP and plays a real G.711 speech PCAP.
- **PlaySBC:** the SIP callee and AI voice gateway. It answers the call, controls RTPengine, decodes RTP, calls STT/Rasa/TTS, and sends the response audio.
- **RTPengine:** the RTP/RTCP media anchor. It relays and accounts for media; it is not the speech analyzer.
- **Vosk STT:** converts decoded caller WAV audio into text.
- **Rasa:** receives the transcript over REST and returns the bot response.
- **Piper TTS:** converts the Rasa text response into speech WAV output.

Call sequence:

1. SIPp A sends `INVITE` to PlaySBC for the AI route.
2. PlaySBC replies with `100 Trying`, `180 Ringing`, and `200 OK`.
3. PlaySBC sends RTPengine `OFFER` and `ANSWER` control commands to anchor RTP/RTCP.
4. SIPp A sends `ACK`; the SIP dialog is established.
5. SIPp A sends G.711 caller speech RTP through the RTPengine media path.
6. PlaySBC extracts/decodes the caller RTP into WAV.
7. PlaySBC sends the WAV to Vosk STT.
8. Vosk returns the transcript, for example `i need support`.
9. PlaySBC posts the transcript and call metadata to the real Rasa REST webhook.
10. Rasa returns the bot text response.
11. PlaySBC sends the bot text to Piper TTS.
12. Piper generates the bot response WAV.
13. PlaySBC converts the TTS output to G.711 RTP prompt evidence and sends it back through RTPengine toward SIPp A.
14. SIPp A clears the call with `BYE`; PlaySBC responds `200 OK`.

The report should therefore show one unified ladder with SIPp A, RTPengine, PlaySBC, Vosk STT, Real Rasa Pod, and Piper TTS. The same report also embeds the decoded caller WAV and generated Piper output WAV so the speech evidence can be played directly from the HTML page.

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
  speech_transcript: I need support
  stt_provider: vosk
  stt_command: python3 tools/vosk_stt_wrapper.py --audio {audio_path}
  tts_provider: piper
  tts_command: python3 tools/piper_tts_wrapper.py --text "{text}" --output {audio_path}
  tts_output_codec: PCMU
```

## Regression Coverage

| Test case | Short name | Purpose | Evidence |
| --- | --- | --- | --- |
| `ai-rasa-lab` | Mock Rasa REST, internal media | SIPp A calls `ai-bot`; PlaySBC terminates the AI call, consumes RTP input internally, and sends one deterministic mock Rasa REST turn | `log.ai`, `log.sip`, `log.media`, `capture.pcap`, HTML ladder with `Mock Rasa REST` |
| `ai-rasa-rtpengine` | Mock Rasa REST, RTPengine media | Same AI call, but RTP/RTCP is anchored by RTPengine; mock Rasa returns multiple response chunks plus a transfer action | `log.ai`, `log.media`, RTPengine query evidence, HTML ladder with `Mock Rasa + Action` |
| `ai-rasa-real-lab` | Real Rasa pod, RTPengine media | Kubernetes starts and trains a real Rasa REST pod, PlaySBC posts to that service, and RTP/RTCP remains anchored by RTPengine | `rasa.log`, `rasa-pod-evidence.log`, `log.ai`, `log.sip`, `log.media`, HTML ladder with `Real Rasa Pod` |
| `ai-rasa-rtpengine-speech` | Real Rasa pod, real speech STT/TTS | SIPp plays real G.711 speech, PlaySBC decodes RTP to WAV, Vosk transcribes `i need support`, Rasa returns the support response, and Piper generates G.711 RTP prompt evidence | speech PCAP/WAV/TTS artifacts, `log.ai`, `log.media`, RTPengine query evidence, HTML ladder with Vosk/Rasa/Piper nodes |
| `ai-rasa-contact-center-sales` | Real Rasa sales bot agent | SIPp A calls the virtual SIPp B bot agent, caller speech says `Connect me to sales`, Rasa runs the sales workflow, and Piper returns the bot-agent speech prompt through RTPengine | contact-center speech PCAP/WAV/TTS artifacts, `log.ai`, `log.media`, RTPengine query evidence, HTML ladder with `SIPp B Bot Agent` |
| Unit: Rasa REST client | JSON contract | Validates Rasa request/response JSON shape | `tests/test_ai_gateway.py` |
| Unit: AI route policy | SIP route target | Validates `ai-gateway:<bot>` routing | `tests/test_mini_call_server.py` |
| Unit: dual-realm profile | Harness wiring | Validates mock Rasa service and `log.ai` bundle wiring | `tests/test_sipp_harness.py` |

## Real Rasa Lab

The full `--all-b2bua-profiles` and Kubernetes `--all-profiles` suites include all Rasa/contact-center profiles. Use `--rasa-profiles` when you want to run only the AI/Rasa slice and keep its logs under `logs/RASA-Regression`.

For direct local speech runs, install the real engines once:

```bash
python3 -m pip install --user piper-tts vosk
python3 -m piper.download_voices en_US-lessac-low --download-dir .models/piper

python3 - <<'PY'
from pathlib import Path
import urllib.request
import zipfile

root = Path(".models/vosk")
root.mkdir(parents=True, exist_ok=True)
archive = root / "vosk-model-small-en-us-0.15.zip"
if not archive.exists():
    urllib.request.urlretrieve("https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip", archive)
model_dir = root / "vosk-model-small-en-us-0.15"
if not model_dir.exists():
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(root)
PY
```

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

### Kubernetes Rasa-Only Run

Use this when the Kubernetes cluster is already running and you want to run only the AI/Rasa profiles.

Step 1: select the cluster and namespace.

```bash
kubectl config use-context kind-playsbc
kubectl config set-context --current --namespace=playsbc
```

Step 2: deploy or upgrade PlaySBC with RTPengine and the real Rasa values file.

```bash
helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --create-namespace \
  -f configs/kubernetes/kind-values.yaml \
  -f configs/kubernetes/ai-rasa-real-values.yaml
```

Step 3: verify the pods.

```bash
kubectl get pods -n playsbc
kubectl -n playsbc rollout status deployment/playsbc-playsbc
kubectl -n playsbc rollout status deployment/playsbc-playsbc-rtpengine
kubectl -n playsbc rollout status deployment/playsbc-playsbc-rasa
```

Step 4: run only the Kubernetes AI/Rasa regression profiles.

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

This runs:

- `ai-rasa-lab`: mock Rasa, internal AI media path.
- `ai-rasa-rtpengine`: mock Rasa, RTP/RTCP anchored by RTPengine.
- `ai-rasa-real-lab`: real Rasa pod, RTPengine-backed AI call control.
- `ai-rasa-rtpengine-speech`: real G.711 speech input, Vosk STT, real Rasa, Piper TTS, RTPengine media evidence.
- `ai-rasa-contact-center-sales`: contact-center sales bot agent, virtual SIPp B side, Vosk STT, real Rasa sales workflow, Piper TTS, RTPengine media evidence.

Step 5: open the report.

```text
logs/RASA-Regression/<run-id>/RASA-reports/latest.html
```

The report contains the unified SIP/RTP/AI ladder, embedded caller speech WAV, embedded Piper output WAV, pass/fail status, and links to the per-profile log bundle.

Step 6: inspect the main evidence files inside the profile bundle.

```text
log.sip          SIP call flow and unified ladder
log.media        RTPengine media anchoring and RTP/RTCP evidence
log.ai           STT input/result, Rasa request/response, TTS output
capture.pcap     SIP/RTP/RTCP packet evidence
*.wav            decoded caller speech and generated Piper response
```

For kind clusters without DockerHub pull access, pre-pull and load the Rasa image:

```bash
docker pull rasa/rasa:3.6.20-full
kind load docker-image rasa/rasa:3.6.20-full --name playsbc
```

## Still To Build

- Add Whisper and Coqui alternatives beside the current Vosk/Piper path.
- Stream longer bot responses instead of one turn per call.
- Execute bot actions with SIP REFER/re-INVITE, conference join, and bot-driven release.
