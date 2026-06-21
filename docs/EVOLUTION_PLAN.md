# PlaySBC Evolution Plan

This project is an educational SIP/RTP lab server. The goal is to grow it phase by phase into an enterprise-style SBC lab platform, not a production-certified SBC.

## Current Baseline

Current validated baseline: the B2BUA SIPp regression is green for signalling, G.711 media, internal transcoding, registered inbound/outbound, RTPengine signalling, RTPengine G.711 media, RTPengine PCMU-to-PCMA transcoding, and 5 cps / 60 second CHT load profiles.

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
- 5 cps SIPp load shape with 300 total calls and 60 second CHT
- 60 second G.711u/G.711a media replay profiles through the B2BUA path
- SBC-style category logs: `log.sip`, `log.media`, `log.transcoding`, `log.platform`, `log.networking`, `log.call`, `log.sipp`, and transport logs such as `log.udp`
- Single combined `capture.pcap` generated after non-load B2BUA calls from SIP traces, RTP media packets, and PlaySBC protocol logs
- Logical PCAP topology view for local B2BUA runs: SIPp A, PlaySBC, and SIPp B can appear as separate IPs while runtime remains on loopback
- Per-testcase B2BUA SIPp log bundles with no separate saved SIPp A/B leg folders
- Named B2BUA SIPp profiles for signalling, media, transcoding, RTPengine signalling, RTPengine G.711 media, RTPengine transcoding, registered inbound/outbound, negative call handling, small load, soak, and 5 cps / 60 second CHT load
- SIPp XML regression coverage for `OPTIONS`, digest registration success/failure, basic B2BUA calls, media calls, transcoding calls, registered inbound/outbound, invalid `BYE`, unknown route, failed outbound leg, `CANCEL`, and INVITE retransmission behavior
- SIPp PCAP replay sudo keepalive for long macOS regression runs, so one initial `sudo -v` can cover late media/load profiles
- RTPengine NG control backend for B2BUA SDP offer/answer/query/delete, with preflight blocking when RTPengine is down
- Local Sipwise RTPengine Docker runbook with a load-sized RTP port range
- RTPengine media observations from query totals, including RTP packet, byte, and error counters
- RTPengine-backed load validation: 300 total calls at 5 cps / 60 second CHT with RTPengine media anchored and `0` RTP errors
- Persistent project logs only for B2BUA SIPp call runs by default
- Unit tests and SIPp regression harness

## RTPengine Status And Future Work

RTPengine is now the first stable external media backend. PlaySBC remains the SIP/B2BUA control plane, while RTPengine is used as the RTP anchor and transcoding backend for RTPengine profiles.

Done:

- Run the same open-source Sipwise `rtpengine` project used by Kamailio-style SIP deployments
- Start RTPengine locally with Docker on macOS using `docker/rtpengine.Dockerfile`
- Document local Docker and Linux VM startup in `docs/RTPENGINE_LOCAL.md`
- Keep `tools/check_rtpengine.py` as the readiness gate
- Make RTPengine-backed SIPp profiles report `BLOCKED` when RTPengine is down
- Pass one basic RTPengine B2BUA signalling call
- Pass RTPengine-backed G.711 media
- Pass RTPengine-backed PCMU-to-PCMA transcoding validation
- Pass RTPengine-backed 5 cps / 60 second CHT load with 300 total calls
- Confirm PlaySBC internal RTP counters remain zero for RTPengine media, proving media is external to PlaySBC

Target division:

```text
PlaySBC   = SIP, dialog/transaction state, B2BUA routing, policies, logs, regression reports
RTPengine = RTP anchoring, SDP rewrite, media relay, DTMF/media controls, transcoding experiments
```

Future RTPengine work:

- Add a separate logical RTPengine IP in generated PCAPs, so signalling can show PlaySBC as `10.10.10.20` and media can show RTPengine as a separate media anchor
- Add real multi-IP local topology mode for SIPp A, PlaySBC, RTPengine, and SIPp B
- Add Docker Compose for one-command local RTPengine startup
- Add RTPengine port-pool and active-session health checks before load runs
- Add stricter SDP validation around RTPengine offer/answer rewrites
- Add RTPengine failure scenarios: down before call, timeout during offer, delete failure, and mid-call media loss
- Add RTCP and RTP quality reporting from RTPengine query data
- Add RTPengine DTMF/media-control experiments
- Add SRTP/TLS groundwork for enterprise SBC lab profiles
- Add longer soak profiles after 5 cps / 60 second CHT remains stable
- Add optional real packet capture mode for RTPengine runs when local permissions allow it

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

Status: done for the current lab baseline.

Implemented SIPp regression coverage:

- `OPTIONS` keepalive
- `REGISTER` with digest authentication success and failure
- Basic B2BUA signalling call
- 60 second G.711 media call using SIPp PCAP replay
- B2BUA PCMU-to-PCMA transcoding call
- Registered inbound call to a registered callee
- Registered outbound call from a registered caller
- RTPengine-backed B2BUA signalling call
- RTPengine-backed G.711 media call
- RTPengine-backed PCMU-to-PCMA transcoding call
- 5 cps / 60 second CHT RTPengine/transcoding load profile with 300 total calls
- Invalid `BYE`
- Unknown route
- Failed outbound leg
- `CANCEL`
- INVITE retransmission behavior
- Small load and soak profiles

Implemented supporting harness work:

- SIPp XMLs for both A-side UAC behavior and B-side UAS behavior where the scenario needs two legs
- Per-profile B2BUA log bundles with `log.sip`, `log.media`, `log.transcoding`, `log.platform`, `log.networking`, `log.call`, and `log.sipp`
- Ladder diagrams for single-call SIP and registration flows; load profiles intentionally skip ladders
- Combined `capture.pcap` for non-load B2BUA profiles with SIP, RTP, and diagnostic protocol events
- Latest-report retention for local runs so old HTML reports and associated report files are pruned automatically
- Suite-level sudo keepalive for SIPp `play_pcap_audio` runs on macOS after one initial `sudo -v`

### Phase 3: RTPengine Media Backend

Status: baseline green. RTPengine can be selected as the media backend:

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

Keep the current internal RTP relay as a fallback. The next RTPengine phase is hardening: clearer topology separation, stronger SDP validation, health checks, failure coverage, and deeper media-quality reporting.

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
