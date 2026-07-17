# PlaySBC Evolution Plan

PlaySBC is an enterprise-style SIP/RTP experimentation lab, not a production-certified SBC.

## Implemented

### Signalling And Routing

- SIP over UDP/TCP/TLS; REGISTER, OPTIONS, INVITE, ACK, CANCEL, and BYE
- Digest registration, dialog/transaction state, registrar-backed routing
- Trunk groups, primary/secondary selection, hunt groups, route policies, E.164/header normalization, CAC, health state, and counters
- Active SIP OPTIONS trunk probing with failure thresholds and timed health recovery
- HA shared registrar/dialog state using a SQLite lab store, plus node-to-RTPengine pairing for active-active experiments
- HA node model with external-LB policy, per-node weights, drain state, and `503 Node Draining` rejection for new calls

### Media

- G.711u/G.711a RTP, internal transcoding, and RFC 4733 DTMF
- RTCP sender/receiver evidence and quality analytics for single calls; load profiles omit RTCP validation
- RTPengine anchoring, SDP rewrite, interface selection, transcoding, bidirectional SDES-SRTP/RTP interworking, and fault profiles
- RTCP receiver-report loss/jitter analytics for single calls

### AI Voice Gateway

```text
SIP caller -> PlaySBC AI route -> RTP/RTPengine media input -> STT/intent adapter -> Rasa REST -> TTS adapter
```

- Route policies can target `ai-gateway:<bot-name>`.
- STT/TTS provider boundaries exist for lab-scripted, Whisper, Vosk, text-only, Piper, and Coqui modes.
- Rasa REST supports multi-message responses; custom bot actions can request join, transfer, or release and are logged as control-plane actions.
- Regression includes `ai-rasa-lab`: SIPp A calls `ai-bot`, PlaySBC answers, sends a Rasa REST turn, logs `log.ai`, and captures SIP/RTP/HTTP evidence.
- Regression includes `ai-rasa-rtpengine`: RTP/RTCP is anchored by RTPengine while PlaySBC handles SIP/control and the Rasa turn.
- Optional real Rasa lab is wired for local config, Docker dual-realm, Helm, and Kubernetes via `ai-rasa-real-lab`.
- Version `1.2.0` adds `ai-rasa-rtpengine-speech`: SIPp plays real G.711 speech, PlaySBC decodes RTP to WAV, Vosk transcribes `i need support`, PlaySBC posts the transcript to real Rasa, Piper generates the bot-response WAV/RTP prompt, and RTP/RTCP stay anchored by RTPengine.
- Whisper STT is selectable through `ai-rasa-rtpengine-speech-whisper`, using the same RTPengine/WAV/Rasa/Piper path with the Whisper adapter boundary.
- Contact-center sales bot profile is wired as `ai-rasa-contact-center-sales`: SIPp A calls a virtual SIPp B bot agent, Vosk transcribes `connect me to sales`, real Rasa runs the sales workflow, Piper generates the bot-agent prompt, and RTP/RTCP stay anchored by RTPengine.
- Coqui TTS is selectable through `ai-rasa-contact-center-sales-coqui`, using the same contact-center sales bot flow with Coqui-generated prompt evidence.
- Long Rasa replies are covered by `ai-rasa-long-response-streaming`, where real Rasa long-response text is split into ordered TTS chunks with per-chunk WAV/RTP prompt evidence.
- Real Rasa project assets live under `rasa/`, with `tools/check_rasa.py` as the readiness gate.

### Lab Platform

- Dual-realm Docker topology: core `172.28.0.0/24`, peer `192.168.28.0/24`
- Dual-homed PlaySBC and RTPengine with Docker-based SIPp agents
- Helm-rendered configuration for every regression profile
- Every dual-realm regression profile runs with HA enabled by default
- SBC category logs, combined live PCAP, and Robot-style HTML report with unified ladders and AI speech WAV playback evidence
- Basic Prometheus-style metrics endpoint at `/metrics` for call, trunk, stream, admission, and HA counters
- Signalling, media, auth, routing, negative, soak, and 5 cps / 60-second CHT profiles
- Kubernetes Helm lab with health probes, Secret-backed SIP users, RTPengine pairing, kind/minikube values, and a dialog-affinity experiment
- HA regression profiles: `ha-shared-state-rtpengine`, `ha-options-health-recovery`, and `ha-node-draining`

## Next

### Observability Lab

- Prometheus integration for PlaySBC `/metrics`, with Helm scrape annotations and optional `ServiceMonitor` support.
- Grafana dashboards for SBC overview, SIP signalling, trunk health, RTPengine media, AI/Rasa gateway, HA state, and regression verdicts.
- Prometheus metric metadata and labels: add `# HELP`, `# TYPE`, and labels such as `node`, `realm`, `trunk`, `transport`, `codec`, and `profile`.
- Alert rules for trunk down, high admission rejection rate, RTPengine unavailable, HA node draining, Rasa unavailable, and regression failures.
- Kubernetes regression profiles: `observability-prometheus-scrape`, `observability-grafana-dashboard`, and `observability-alert-rules`.
- Report evidence that Prometheus scraped PlaySBC after a B2BUA call and that Grafana dashboard JSON loads cleanly.

### HA And Networking

- Full B2BUA mid-call failover: checkpoint outbound leg state, restore ACK/BYE/CANCEL/re-INVITE handling on a sibling node, and prove it by killing `playsbc-a` during an active dialog.
- RTPengine media-session migration or continuity design: either shared RTPengine pair ownership, session re-homing, or deterministic media teardown/re-establish after a PlaySBC/RTPengine pair loss.
- Multi-node chaos/failover regression that kills one PlaySBC/RTPengine pair during active SIP/RTP traffic.
- Kubernetes real dual-realm networking: replace the current logical core/peer pod model with Multus or another multi-network CNI so SIPp, PlaySBC, and RTPengine can use separate Kubernetes media/signalling interfaces such as core `172.x` and peer `192.x`.
- Full active-active Kubernetes HA topology for all regression profiles: run every profile through multiple PlaySBC pods and paired RTPengine nodes behind the lab load-balancing/drain model, not only the dedicated HA profiles.
- Optional StatefulSet lab mode for scaled pods: provide stable identities such as `playsbc-0`/`playsbc-1` and `rtpengine-0`/`rtpengine-1` for deterministic PlaySBC-to-RTPengine pairing, ordered rollout tests, and fixed-node HA experiments.
- External shared state backend option such as Redis/PostgreSQL after the SQLite lab store proves the behavior

### AI Voice Gateway

- Package heavyweight Whisper and Coqui image variants with real models preloaded, beside the portable lab-fallback wrappers.
- Add streamed callback/channel support for bot responses that arrive over time rather than in one REST result.

## Later

### WebRTC Gateway

- SIP WebSocket, ICE/STUN, DTLS-SRTP, and browser calling

### AI Voice Gateway

- Executed bot-assisted B2BUA actions: REFER/re-INVITE transfer, conference join, and release

## Delivery Rule

Every new feature needs focused unit tests, a dual-realm SIPp profile when applicable, clear SBC logs, combined packet evidence, and a report verdict.
