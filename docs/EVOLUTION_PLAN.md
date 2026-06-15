# PlaySBC Evolution Plan

This project is an educational SIP/RTP lab server. The goal is to grow it phase by phase into an enterprise-style SBC lab platform, not a production-certified SBC.

## Current Baseline

Implemented:

- SIP over UDP for `REGISTER`, `OPTIONS`, `INVITE`, `ACK`, and `BYE`
- Optional SIP digest authentication for `REGISTER`
- Initial PCMU/PCMA RTP echo, relay, and basic transcoding scaffolding
- RFC 2833 DTMF detection
- Dialog state tracking and UDP transaction cache
- RTP metrics: packet loss, jitter, out-of-order, late packets, silence, clock drift, MOS-style score
- Registrar-backed B2BUA routing
- Route policies and static fallback routes
- SIPp UAC/UAS scripts for B2BUA calls
- 5 cps / 60 second SIPp load shape
- 60 second G.711u/G.711a media replay profiles through the B2BUA path
- SBC-style category logs: `log.sip`, `log.media`, `log.transcoding`, `log.platform`, `log.networking`, `log.call`, `log.sipp`, and transport logs such as `log.udp`
- Single combined `capture.pcap` generated after non-load B2BUA calls from SIP traces, RTP media packets, and PlaySBC protocol logs
- Logical PCAP topology view for local B2BUA runs: SIPp A, PlaySBC, and SIPp B can appear as separate IPs while runtime remains on loopback
- Per-testcase B2BUA SIPp log bundles with no separate saved SIPp A/B leg folders
- Named B2BUA SIPp profiles for signalling, media, transcoding, RTPengine, registered inbound/outbound, and 5 cps / 60 second load
- SIPp XML regression coverage for the former Python smoke scenarios: digest registration, transaction replay, invalid BYE, media call, and two-leg bridge
- Optional RTPengine NG control backend scaffold for B2BUA SDP offer/answer/delete
- Persistent project logs only for B2BUA SIPp call runs by default
- Unit tests and SIPp regression harness

## Immediate Priority: RTPengine First

Before expanding more PlaySBC-native media and transcoding behavior, make RTPengine the first stable external media backend. This is important because PlaySBC is still early in media anchoring, codec handling, and transcoding. RTPengine gives the lab a proven media plane while PlaySBC focuses on SIP, B2BUA routing, policy, logs, and regression control.

Short-term target:

- Run the same open-source Sipwise `rtpengine` project used by Kamailio-style SIP deployments
- Add simple local start/check instructions for macOS via Docker or Linux VM
- Keep `tools/check_rtpengine.py` as the readiness gate
- Make RTPengine-backed SIPp profiles report `BLOCKED` when RTPengine is down
- Get one basic RTPengine B2BUA call green
- Then get RTPengine-backed G.711 media green
- Then add RTPengine-backed transcoding validation
- Only after that, continue deeper PlaySBC-native media/transcoding work

Target division:

```text
PlaySBC   = SIP, dialog/transaction state, B2BUA routing, policies, logs, regression reports
RTPengine = RTP anchoring, SDP rewrite, media relay, DTMF/media controls, transcoding experiments
```

## Next Focus

### Phase 1: Logging And Regression Reports

Make logs clean and review-friendly:

- One timestamped run folder per test run
- Persistent logs only for B2BUA SIPp basic calls, registration-to-callee setup, media, and load
- Clear category logs and SIPp trace logs
- One combined post-call PCAP per non-load B2BUA testcase with SIP, RTP, and diagnostic protocol events; skip load PCAPs to avoid noisy artifacts
- Default PCAP display topology maps SIPp A / PlaySBC / SIPp B to separate logical IPs for cleaner Wireshark review
- Pass/fail run result in `log.platform`
- No overwritten logs

### Phase 1B: Real Multi-IP Local Topology

Keep the logical PCAP view as the default safe local mode, then add a real multi-IP bind mode once the macOS/Linux setup is solid:

- SIPp A binds to a dedicated local alias, for example `127.0.0.10`
- PlaySBC binds to a dedicated local alias, for example `127.0.0.20`
- SIPp B binds to a dedicated local alias, for example `127.0.0.30`
- SIPp `-i`, `-mi`, Contact headers, registrar routing, and media ports are generated from that topology
- Regression preflight checks confirm the local aliases exist before running the profile

### Phase 2: SIPp Regression Expansion

Add more SIPp cases:

- `OPTIONS` keepalive
- `REGISTER` with auth success and failure
- Basic B2BUA call
- 60 second G.711 media call
- B2BUA transcoding call
- Registered caller origination
- RTPengine-backed B2BUA call
- 5 cps / 60 second CHT RTPengine/transcoding load profile
- Invalid `BYE`
- Unknown route
- Failed outbound leg
- `CANCEL`
- Retransmission behavior
- Small load and soak profiles

### Phase 3: RTPengine Media Backend

Status: active priority. Added optional media backend selection:

```json
{
  "media_backend": "internal",
  "rtpengine_url": "udp://127.0.0.1:2223"
}
```

Target:

```text
Python B2BUA = SIP, routing, policy, logs
RTPengine    = RTP/SRTP anchoring, SDP rewrite, DTMF/media controls
```

Keep the current internal RTP relay as a fallback. The immediate next step is local runtime validation with the open-source Sipwise RTPengine process, then a green basic RTPengine B2BUA call, then RTPengine-backed media and transcoding.

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
- No regression log overwrite
