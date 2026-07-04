# PlaySBC Evolution Plan

PlaySBC is an enterprise-style SIP/RTP experimentation lab, not a production-certified SBC.

## Implemented

### Signalling And Routing

- SIP over UDP/TCP; REGISTER, OPTIONS, INVITE, ACK, CANCEL, and BYE
- Digest registration, dialog/transaction state, registrar-backed routing
- Static trunks, route policies, E.164 matching, and failure propagation

### Media

- G.711u/G.711a RTP, internal transcoding, and RFC 4733 DTMF
- RTCP evidence for single calls and a load canary
- RTPengine anchoring, SDP rewrite, interface selection, and transcoding

### Lab Platform

- Dual-realm Docker topology: core `172.28.0.0/24`, peer `192.168.28.0/24`
- Dual-homed PlaySBC and RTPengine with Docker-based SIPp agents
- Helm-rendered configuration for every regression profile
- SBC category logs, combined live PCAP, and Robot-style HTML report
- Signalling, media, auth, routing, negative, soak, and 5 cps / 60-second CHT profiles

## Next

### SBC Lab Features

- Trunk groups and primary/secondary failover
- Header and E.164 normalization policies
- Hunt groups, call admission control, and trunk health state
- Per-trunk metrics and failure counters

### Transport And Media

- SIP over TLS and transport-specific policies
- TCP reuse/failure coverage
- RTPengine failure, port exhaustion, and interface-failure profiles
- Receiver-report and media-quality analytics

### Kubernetes Lab

- Health probes and secret-backed SIP users
- RTPengine deployment pairing and media-network model
- `kind`/`minikube` runbook and dialog-affinity experiment

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
