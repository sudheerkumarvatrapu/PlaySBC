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
- Real Rasa can replace the mock by changing `ai_voice_gateway.rasa_webhook_url`.

### Lab Platform

- Dual-realm Docker topology: core `172.28.0.0/24`, peer `192.168.28.0/24`
- Dual-homed PlaySBC and RTPengine with Docker-based SIPp agents
- Helm-rendered configuration for every regression profile
- Every dual-realm regression profile runs with HA enabled by default
- SBC category logs, combined live PCAP, and Robot-style HTML report with single-call ladders
- Signalling, media, auth, routing, negative, soak, and 5 cps / 60-second CHT profiles
- Kubernetes Helm lab with health probes, Secret-backed SIP users, RTPengine pairing, kind/minikube values, and a dialog-affinity experiment
- HA regression profiles: `ha-shared-state-rtpengine`, `ha-options-health-recovery`, and `ha-node-draining`

## Next

- Full B2BUA mid-call failover: checkpoint outbound leg state, restore ACK/BYE/CANCEL/re-INVITE handling on a sibling node, and prove it by killing `playsbc-a` during an active dialog.
- RTPengine media-session migration or continuity design: either shared RTPengine pair ownership, session re-homing, or deterministic media teardown/re-establish after a PlaySBC/RTPengine pair loss.
- Multi-node chaos/failover regression that kills one PlaySBC/RTPengine pair during active SIP/RTP traffic.
- Kubernetes real dual-realm networking: replace the current logical core/peer pod model with Multus or another multi-network CNI so SIPp, PlaySBC, and RTPengine can use separate Kubernetes media/signalling interfaces such as core `172.x` and peer `192.x`.
- Full active-active Kubernetes HA topology for all regression profiles: run every profile through multiple PlaySBC pods and paired RTPengine nodes behind the lab load-balancing/drain model, not only the dedicated HA profiles.
- Optional StatefulSet lab mode for scaled pods: provide stable identities such as `playsbc-0`/`playsbc-1` and `rtpengine-0`/`rtpengine-1` for deterministic PlaySBC-to-RTPengine pairing, ordered rollout tests, and fixed-node HA experiments.
- External shared state backend option such as Redis/PostgreSQL after the SQLite lab store proves the behavior

### AI Real Speech Pipeline

Track this in a separate branch/PR, recommended branch:

```text
codex/ai-voice-real-speech-stt-tts
```

Target PR scope:

- Speech PCAP playback: add real G.711u/G.711a speech PCAP assets and an `ai-rasa-rtpengine-speech` regression profile.
- RTP audio extraction: assemble inbound RTP, decode G.711 to PCM/WAV, and feed Whisper/Vosk through the current STT adapter boundary.
- TTS back to RTP: generate Piper/Coqui WAV, convert to G.711 RTP, and send the prompt back through RTPengine.
- E2E validation: prove speech input, STT transcript, Rasa response, TTS RTP output, RTPengine query evidence, PCAP evidence, and AI ladder/report output.

Estimated lab-quality timeline: 4 to 8 working days.

## Later

### WebRTC Gateway

- SIP WebSocket, ICE/STUN, DTLS-SRTP, and browser calling

### AI Voice Gateway

- Executed bot-assisted B2BUA actions: REFER/re-INVITE transfer, conference join, and release

## Delivery Rule

Every new feature needs focused unit tests, a dual-realm SIPp profile when applicable, clear SBC logs, combined packet evidence, and a report verdict.
