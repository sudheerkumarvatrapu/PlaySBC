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

Status: implemented inbound bridge-room baseline.

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

The current bridge is a meet-me room: both endpoints call `sip:bridge@server`, then the media server pairs the legs and relays anchored RTP. Full outbound B2BUA setup, registrar-backed lookup, and route policies remain Phase 6 work.

## Phase 6: Routing Engine

Add:

- Registrar location service
- Route policies
- Routing-table config
- Failover destinations
- Hunt groups

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

Begin Phase 6 routing engine work: registrar-backed endpoint lookup, route policies, and outbound leg setup for a fuller B2BUA path.
