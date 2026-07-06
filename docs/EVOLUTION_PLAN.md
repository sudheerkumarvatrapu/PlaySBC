# PlaySBC Evolution Plan

PlaySBC is an enterprise-style SIP/RTP experimentation lab, not a production-certified SBC.

## Implemented

### Signalling And Routing

- SIP over UDP/TCP/TLS; REGISTER, OPTIONS, INVITE, ACK, CANCEL, and BYE
- Digest registration, dialog/transaction state, registrar-backed routing
- Trunk groups, primary/secondary selection, hunt groups, route policies, E.164/header normalization, CAC, health state, and counters

### Media

- G.711u/G.711a RTP, internal transcoding, and RFC 4733 DTMF
- RTCP sender/receiver evidence and quality analytics for single calls; load profiles omit RTCP validation
- RTPengine anchoring, SDP rewrite, interface selection, transcoding, bidirectional SDES-SRTP/RTP interworking, and fault profiles
- RTCP receiver-report loss/jitter analytics for single calls

### Lab Platform

- Dual-realm Docker topology: core `172.28.0.0/24`, peer `192.168.28.0/24`
- Dual-homed PlaySBC and RTPengine with Docker-based SIPp agents
- Helm-rendered configuration for every regression profile
- SBC category logs, combined live PCAP, and Robot-style HTML report with single-call ladders
- Signalling, media, auth, routing, negative, soak, and 5 cps / 60-second CHT profiles
- Kubernetes Helm lab with health probes, Secret-backed SIP users, RTPengine pairing, kind/minikube values, and a dialog-affinity experiment

## Next

- Multi-node RTPengine/PlaySBC node pairing and shared registrar/dialog state
- Active SIP OPTIONS trunk probing and timed health recovery

## Later

### WebRTC Gateway

- SIP WebSocket, ICE/STUN, DTLS-SRTP, and browser calling

### AI Voice Gateway

```text
Caller -> SIP/B2BUA -> RTP -> STT -> LLM -> TTS -> RTP
```

Keep AI behind a media adapter so the SIP/B2BUA core remains stable.

## Delivery Rule

Every new feature needs focused unit tests, a dual-realm SIPp profile when applicable, clear SBC logs, combined packet evidence, and a report verdict.
