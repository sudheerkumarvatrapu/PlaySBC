# PlaySBC Evolution Plan

PlaySBC is an educational SIP/RTP lab server. The goal is an enterprise-style SBC lab platform for experimentation, not a production-certified SBC.

## Current Baseline

Implemented and covered by unit tests plus SIPp regression:

- SIP over UDP and TCP
- REGISTER, OPTIONS, INVITE, ACK, CANCEL, BYE
- SIP digest REGISTER support
- Dialog state and UDP transaction cache
- Registrar-backed B2BUA routing
- Route policies and static route fallback
- G.711u/G.711a RTP media replay
- Internal PCMU/PCMA transcoding
- RTPengine media backend for anchoring and transcoding experiments
- SIPp B2BUA profiles for signalling, media, transcoding, registration, negative flows, small load, soak, RTPengine, TCP RTPengine transcoding, and 5 cps / 60 second CHT load
- One log bundle per B2BUA testcase
- Latest HTML regression report

## Current Regression Focus

Keep these profiles green:

- `basic-signalling`
- `basic-media`
- `transcoding`
- `registered-inbound`
- `registered-outbound`
- `rtpengine`
- `rtpengine-media`
- `rtpengine-transcoding`
- `tcp-rtpengine-transcoding`
- `load-5cps-60s`
- `load-5cps-60s-rtpengine-transcoding`
- negative profiles: invalid BYE, unknown route, failed outbound leg, CANCEL, retransmission

## Logging Rule

Keep logging simple:

- One bundle per B2BUA testcase under `logs/b2bua-Regression/`
- Main files: `log.sip`, `log.media`, `log.transcoding`, `log.platform`, `log.sipp`
- Single-call profiles may include SIP ladders and `capture.pcap`
- Load profiles should avoid ladders and PCAP clutter
- Regression report should show one row per testcase/profile

## RTPengine Direction

PlaySBC owns SIP, dialog state, routing, policies, logs, and reports.

RTPengine owns RTP anchoring, SDP rewrite, media relay, and transcoding experiments.

Next RTPengine improvements:

- Docker Compose startup
- Real multi-IP local topology
- RTPengine port-pool health checks
- Stronger SDP validation
- RTPengine failure scenarios
- RTCP and media quality reporting

## Next Phases

### Phase 1: SIP Transport Hardening

- Expand SIP over TCP regression
- Add SIP over TLS later
- Add transport-specific route policies
- Improve TCP connection reuse and failure logging

### Phase 2: SBC Lab Features

- Trunk profiles
- SIP header normalization
- E.164 number normalization
- Route failover
- Hunt groups
- Call admission control
- OPTIONS monitoring

### Phase 3: WebRTC Gateway

- SIP over WebSocket
- ICE/STUN
- DTLS-SRTP
- Browser demo client
- RTPengine-assisted WebRTC media path

### Phase 4: AI Voice Gateway

Later media pipeline:

```text
Caller -> SIP/B2BUA -> RTP -> STT -> LLM -> TTS -> RTP
```

Keep AI behind a media adapter so SIP/B2BUA code stays stable.

## Guiding Rule

Every feature should include:

- Focused unit tests
- SIPp regression coverage when applicable
- Clear logs
- Pass/fail report output
