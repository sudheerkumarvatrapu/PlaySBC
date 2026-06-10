# Mini Call Server Evolution Plan

The project is growing from a SIP/RTP echo demo into an educational SBC and real-time communications lab. The work should progress in layers so each phase stays testable.

## Current Baseline

Implemented:

- SIP UDP handling for `REGISTER`, `OPTIONS`, `INVITE`, `ACK`, and `BYE`
- Optional digest authentication for `REGISTER`
- RTP echo for PCMU/PCMA
- WAV recording and per-call logs
- RFC 2833 DTMF detection
- JSON config
- Unit tests and Python smoke clients
- SIPp regression harness
- SIP dialog state tracking and UDP transaction cache
- RTP analyzer metrics
- Basic inbound two-leg bridge room
- B2BUA outbound leg setup
- Registrar-backed endpoint lookup
- Route policies and legacy static-route fallback
- Unified B2BUA SIP ladder logs

## Phase 1: SIP State Machine Cleanup

Status: implemented baseline.

Explicit dialog state tracking:

```python
class CallState(Enum):
    INIT = 0
    RINGING = 1
    ANSWERED = 2
    TERMINATED = 3
```

Tracked:

- Call-ID
- Local and remote tags
- Branch IDs
- Local and remote CSeq
- Created, answered, and terminated timestamps

Outcome:

- Predictable dialog behavior
- Clear validation for invalid method ordering
- A stable foundation for bridging

## Phase 2: Transaction Layer

Status: implemented educational baseline.

Added:

- INVITE server transactions
- Non-INVITE server transactions
- Response caching
- UDP final INVITE response retransmission timers until ACK or expiry
- Transaction expiration

The current layer is intentionally compact. Continue using RFC 3261 as the primary baseline and study RFC 6026 for INVITE transaction corrections before treating it as production signaling code. Future hardening should add transport-aware timer tuning, CANCEL handling, error-response transaction coverage, and richer transaction metrics.

Regression coverage includes an async timer unit test, a Python UDP smoke client for byte-for-byte cached response replay, and a SIPp scenario for unknown-dialog `BYE` rejection.

## Phase 3: RTP Jitter Buffer And Metrics

Status: implemented baseline.

Added:

- RTP sequence tracking
- Sequence gap detection
- Out-of-order and duplicate packet detection
- Late packet tracking
- Basic jitter calculation

Export per-call metrics:

```text
packet_loss
jitter_ms
out_of_order
late_packets
```

## Phase 4: RTP Analyzer

Status: implemented baseline.

Added:

- RTP clock drift estimation
- Silence detection
- DTMF event summary
- Per-call media-session summary in call logs
- A documented MOS-estimation approximation

## Phase 5: Call Bridging

Status: implemented inbound bridge-room baseline and outbound B2BUA baseline.

Move from:

```text
UA -> server -> same UA echo
```

To:

```text
UA-A <-> server <-> UA-B
```

Add:

- Two inbound dialog legs
- RTP relay between endpoint legs
- PCMU/PCMA transcoding only where required

The meet-me bridge is still available: both endpoints call `sip:bridge@server`, then the media server pairs the legs and relays anchored RTP. The outbound B2BUA path now creates a separate outbound SIP leg and pairs inbound/outbound RTP sessions through the server.

## Phase 6: Routing Engine

Status: implemented educational baseline.

Added:

- In-memory registrar location service from `REGISTER` Contact headers
- Expiry and unregister handling for basic registrations
- `route_policies` config with glob-style dialed-user matching
- Registrar-backed policy target: `target="registration"`
- Static route-policy templates such as `sip:{user}@127.0.0.1:25082`
- Legacy `b2bua_routes` exact-match fallback
- B2BUA outbound INVITE, ACK, and BYE leg setup
- B2BUA response forwarding for provisional and final INVITE responses
- Dynamic SIPp smoke runner that registers any callee name before running the call
- Unified B2BUA call-flow log with an ASCII SIP ladder for basic one-call SIPp smoke
- SIPp B `100 Trying` is kept on the outbound leg to show the independent B2BUA-to-UAS INVITE transaction; it is not forwarded to SIPp A.

Current verified call path:

```text
SIPp A -> Mini Call Server B2BUA -> SIPp B
```

The 5 cps / 60 second hold smoke shape is supported by:

```bash
python3 tools/run_b2bua_sipp_smoke.py --callee load-user --calls 5 --rate 5 --hold-ms 60000
```

The load shape disables ladder logs by default to avoid creating one ladder file per call. Use `--ladder` when a sampled ladder is needed during a short run.

Remaining Phase 6 hardening:

- Multiple contacts per Address of Record
- Forking and hunt groups
- Failover retry on non-2xx or timeout
- Route metrics and policy counters
- Persistent registrar database
- CANCEL support and richer in-dialog request handling

## Phase 7: WebRTC Gateway

Treat this as a separate milestone:

- SIP over WebSocket
- ICE
- STUN
- DTLS-SRTP
- Browser demo client

## Phase 8: AI Voicebot Integration

Build a media pipeline:

```text
caller -> SIP server -> RTP -> STT -> LLM -> TTS -> RTP
```

Keep AI voicebot code behind a clean media adapter boundary so it does not complicate SIP transaction correctness.

## Cross-Cutting Work

### Replace `audioop`

`audioop` is unavailable in newer Python versions. Evaluate:

- `g711`
- `numpy`
- `soundfile`
- `PyAV`
- `ffmpeg` subprocess integration

### Structured Logging

Move toward call-scoped structured context:

```text
[call=abc123] [dialog=leg-a] RTP packet received seq=812
```

### Regression Discipline

Before each larger phase:

1. Run unit tests.
2. Run Python smoke clients.
3. Run SIPp regression scenarios.
4. Keep each run in a fresh artifacts folder.

## Recommended Next Implementation

Continue Phase 6 hardening with multi-contact registrar support, route failover, and hunt groups before moving to WebRTC gateway work.
