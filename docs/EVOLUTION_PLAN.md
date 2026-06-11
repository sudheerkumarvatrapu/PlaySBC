# Mini Call Server Evolution Plan

This project is an educational SIP/RTP lab server. The goal is to grow it phase by phase into an enterprise-style SBC lab platform, not a production-certified SBC.

## Current Baseline

Implemented:

- SIP over UDP for `REGISTER`, `OPTIONS`, `INVITE`, `ACK`, and `BYE`
- Optional SIP digest authentication for `REGISTER`
- PCMU/PCMA RTP echo, relay, recording, and basic transcoding
- RFC 2833 DTMF detection
- Dialog state tracking and UDP transaction cache
- RTP metrics: packet loss, jitter, out-of-order, late packets, silence, clock drift, MOS-style score
- Registrar-backed B2BUA routing
- Route policies and static fallback routes
- SIPp UAC/UAS scripts for B2BUA calls
- 5 cps / 60 second SIPp load shape
- 60 second G.711u/G.711a media replay through the B2BUA path
- Unified B2BUA ladder logs for basic calls
- Unit tests and SIPp regression harness

## Next Focus

### Phase 1: Logging And Regression Reports

Make logs clean and review-friendly:

- One timestamped run folder per test run
- Separate folders for unit tests, SIPp basic calls, registration auth, media, and load
- Clear server, SIP, RTP/media, ladder, and SIPp trace logs
- Single regression result document
- Green/pass and red/fail status summary
- No overwritten logs

### Phase 2: SIPp Regression Expansion

Add more SIPp cases:

- `OPTIONS` keepalive
- `REGISTER` with auth success and failure
- Basic B2BUA call
- 60 second G.711 media call
- Invalid `BYE`
- Unknown route
- Failed outbound leg
- `CANCEL`
- Retransmission behavior
- Small load and soak profiles

### Phase 3: RTPengine Media Backend

Add optional media backend selection:

```json
{
  "media_backend": "internal",
  "rtpengine_url": "udp://127.0.0.1:2223"
}
```

Target:

```text
Python B2BUA = SIP, routing, policy, logs
RTPengine    = RTP/SRTP anchoring, SDP rewrite, recording, DTMF/media controls
```

Keep the current internal RTP relay as a fallback.

### Phase 4: Enterprise SBC Lab Features

Build lab-grade SBC features:

- Trunk profiles
- SIP header normalization
- E.164 number normalization
- Route failover
- Hunt groups
- Call admission control
- OPTIONS monitoring
- TLS profile groundwork
- Teams Direct Routing style lab profile

Example profile:

```json
{
  "trunk_profiles": {
    "teams-lab": {
      "transport": "tls",
      "media": "srtp",
      "options_ping": true,
      "require_e164": true,
      "normalize_headers": true
    }
  }
}
```

This is for lab experimentation only, not Microsoft-certified production Direct Routing.

### Phase 5: WebRTC Gateway

Add browser-call capability:

- SIP over WebSocket
- ICE/STUN
- DTLS-SRTP
- Browser demo client
- RTPengine-assisted WebRTC media path

### Phase 6: AI Voice Gateway

Keep AI integration as a later media pipeline:

```text
Caller -> SIP/B2BUA -> RTP media -> STT -> LLM -> TTS -> RTP back
```

Possible features:

- AI IVR
- Voicebot
- Call transcription
- Telecom troubleshooting assistant
- Agent-assist media fork

Keep AI code behind a clean media adapter so SIP/B2BUA logic remains stable.

## Guiding Rule

Every phase should include:

- Focused unit tests
- SIPp regression coverage
- Clear logs
- A pass/fail result report
- No regression artifact overwrite
