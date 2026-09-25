# Protocol Core Layer 3

Layer 3 is the SIP transaction-state gate. The 125-profile Kubernetes catalog
includes five dedicated real Layer 3 profiles in addition to deterministic
server/client transaction modules.

## Implemented Behavior

- Separate INVITE and non-INVITE client/server state machines implement Timers
  A, B, C, D, E, F, G, H, I, J, K, and M behavior with transport awareness.
- T1, T2, T4, and Timer C are configurable under `sip_transactions`.
- Every outbound B2BUA non-ACK request—INVITE, CANCEL, BYE, OPTIONS, REFER, and
  NOTIFY—starts a client transaction. Responses match the top Via branch and
  sent-by, method, CSeq, and Call-ID context.
- Duplicate UDP server requests replay the cached response without repeating
  application actions. Logical merged UDP requests with a different branch are
  rejected separately with `482 Loop Detected`.
- Non-2xx INVITE finals generate the matching transaction ACK; retransmitted
  finals are ACKed again. Successful `2xx` ACK remains end-to-end dialog work.
- CANCEL must match a pending INVITE branch/CSeq. A completed INVITE cannot
  be cancelled, preventing final-response/CANCEL races from tearing down a call.
- TCP/TLS connection failures terminate transactions for the affected target
  and emit transport-error evidence. Transaction starts, responses, timeouts,
  non-2xx finals, and errors are logged and exported as metrics.

## Configuration

```yaml
sip_transactions:
  t1: 0.5
  t2: 4.0
  t4: 5.0
  timer_c: 180.0
```

## Live Profiles

| Profile | Validation |
| --- | --- |
| `protocol-core-layer3-live-client-transactions` | Successful INVITE/BYE client transactions and matched responses |
| `protocol-core-layer3-live-non2xx-ack` | Rejected INVITE and matching transaction ACK |
| `protocol-core-layer3-live-cancel` | Pending INVITE, CANCEL/200, INVITE 487, ACK, and cleanup |
| `protocol-core-layer3-live-retransmission` | Duplicate INVITE response replay with one outbound application action |
| `protocol-core-layer3-live-transport-error` | Unreachable TCP peer and transaction transport-error propagation |

The 2026-09-20 report marked non-2xx ACK, CANCEL, and transport-error profiles
failed despite successful SIPp steps. Retained PCAPs show 503/ACK, 487/ACK, and
480/ACK respectively. The harness had checked packet start lines in `log.sip`
(transaction events) and generated a normal successful-call ladder for these
negative calls. Packet assertions now use `sipmsg.log`; the displayed ladder
for every profile is built after execution and evidence collection from PCAP
or SIPp traces, never from a projected success sequence.

The outer full-suite wrapper timeout is eight hours (`28800` seconds), and
the Kubernetes Job active deadline is `30000` seconds so it cannot kill a
healthy run before the wrapper timeout. Individual profiles retain their own
bounded timeouts.
