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

## Phase 1: SIP State Machine Cleanup

Add explicit dialog state tracking:

```python
class CallState(Enum):
    INIT = 0
    RINGING = 1
    ANSWERED = 2
    TERMINATED = 3
```

Track:

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

Add:

- INVITE server transactions
- Non-INVITE server transactions
- Response caching
- UDP retransmission timers
- Transaction expiration

Use RFC 3261 as the primary baseline and study RFC 6026 for INVITE transaction corrections.

## Phase 3: RTP Jitter Buffer And Metrics

Add:

- RTP sequence tracking
- Sequence gap detection
- Packet reordering
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

Add:

- RTP clock drift estimation
- Silence detection
- DTMF event summary
- A lightweight media-session inspection command
- A documented MOS-estimation approximation

## Phase 5: Call Bridging

Move from:

```text
UA -> server -> same UA echo
```

To:

```text
UA-A <-> server <-> UA-B
```

Add:

- Registrar-backed endpoint lookup
- Two dialog legs
- RTP relay between endpoint legs
- PCMU/PCMA transcoding only where required

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

Implement Phase 1: explicit SIP dialog state tracking, then extend SIPp scenarios to cover invalid and retransmitted requests.
