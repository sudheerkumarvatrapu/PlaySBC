# PlaySBC Evolution Plan

PlaySBC is an enterprise-style SIP/RTP and AI voice lab. It is not yet a production-certified SBC. Historical delivery detail belongs in the [release notes](../release/README.md); this page tracks only the current baseline and forward work.

## Execution Order At A Glance

This document is ordered by dependency, not by feature visibility:

1. Preserve the imported public `v2.6.0` regression and evidence baseline.
2. Apply the private `v6.0.0` release boundary and measure current pre-v6 state.
3. Complete the production SBC protocol core; every higher-level service depends on it.
4. Complete all network-side RFC 5359 business calling services with three-endpoint, RTPengine, AKS, and real-device evidence.
5. Complete the production AI Voice Gateway on the proven signaling/media core.
6. Close cloud, HA, scale, security, upgrade, rollback, and operational gates.

Workstreams may progress in parallel, but a later stage cannot declare
production readiness while an earlier dependency remains open.

## 1. Imported Public Base Gate: v2.6.0

- SIP UDP/TCP/TLS registration and B2BUA calls through local kind and Azure AKS
- RTPengine anchoring, G.711 transcoding, RTP/RTCP, SRTP interworking, NAT learning, and media evidence
- Active-active and failure-injection regression foundations
- OBi1022 and Zoiper registration with verified two-way audio
- One combined PCAP, canonical SIP ladder, `sipmsg.log`, media verdicts, and HTML/archive evidence
- Role-aware `pcap-legs.json` certification for expected capture sources and both plain-SIP B2BUA INVITE legs
- Isolated `kind-playsbc-real-device` lane with guarded LAN advertisement, optional TLS CA verification, and deadlock-free local upgrades
- Rasa, STT, TTS, DTMF, and scripted AI Voice Gateway regression foundations
- RFC 5359 call hold/resume propagation across both B2BUA legs over UDP, TCP, TLS, and RTPengine

The baseline includes selected transaction, retransmission, CANCEL, dialog,
TCP reuse, and transport-failure tests. It is not a complete RFC 3261
transaction-layer implementation or a SIP conformance claim.

### v2.6.0 Regression And Evidence Gate

- The inherited public catalog plus the current public business-service slice contains 78 Kubernetes-selectable profiles, and the launcher reports live `X/78` progress for a full private-source run.
- `evidence-b2bua-two-leg-pcap` requires core and peer packet sources plus two distinct B2BUA INVITE Call-IDs.
- Long local macOS runs use a scoped `caffeinate` process to prevent host sleep from creating false SIPp timeouts.
- Missing or empty expected capture roles fail evidence collection before split captures are removed.
- High-volume profiles remain intentionally compact and explicitly state when packet capture is omitted.
- Mock Rasa profiles require a reachable in-job webhook; fallback is not accepted as successful bot evidence.
- Pre-fault PlaySBC logs are retained before fault injection so media and dialog evidence is not lost with a pod.

The public `v2.6.0` tag (`3c8e8072...`) and public maintenance head
(`f2a7355d...`) form this imported MIT gate. These are regression-evidence
results, not blanket protocol certification or a measured production capacity
claim.

## 2. Private Commercial Target: v6.0.0

The first private commercial release is `v6.0.0`. Until that gate, private
`main` receives reviewed code, tests, and internal documentation only. No
commercial tags, GitHub releases, images, charts, models, evidence bundles, or
customer documentation are published.

Pre-v6 development is identified by commit SHA. Work proceeds in testable
slices while product contents, third-party licensing, notices, SBOMs,
provenance, security controls, and customer terms are segregated and reviewed.

## 3. Current Private Pre-v6 Foundations

Private `main` now contains the first source foundations for the two active
commercial tracks. These changes build on the public `v2.6.0` base gate and
are identified by commit SHA; they are not a new public release or a claim of
production readiness.

| Track | Implemented now | Regression evidence | Still gated |
| --- | --- | --- | --- |
| Production AI Voice Gateway | Provider-neutral `ConversationProvider` contract; immutable request/chunk types; ordered asynchronous response streaming; Rasa adapter; rejection of missing, out-of-order, or post-final chunks; overall provider deadline; deterministic timeout/error fallback; provider-stage call-finalization interruption with no fallback/TTS from the interrupted response; timeout/failure/interruption counters | Unit coverage in `tests/test_ai_gateway.py`; fast profiles `ai-provider-streaming-contract` and `ai-provider-interruption-fallback`; Kubernetes profile `ai-rasa-long-response-streaming` | Additional live providers, retry/idempotency policy, latency histograms, TTS-stage cancellation, barge-in from new caller media, and complete kind/AKS/real-device acceptance |
| RFC 5359 business calling services | Existing live hold/resume; deterministic consultation and music-on-hold state contracts; REFER/NOTIFY relay for unattended transfer; RFC 3891 Replaces translation for attended transfer; ordered unconditional, busy, and no-answer forwarding policy with loop/hop protection; Find-Me and incoming/outgoing screening policy foundations; business-service metrics | Unit coverage; eight RFC-focused fast contract profiles; live local SIPp passes for unattended transfer and three forwarding modes; selectable RTPengine variants for all four; existing live hold/resume over UDP, TCP, TLS, and RTPengine | Wire Find-Me and screening into live routing; execute and retain RTPengine/kind/AKS profiles; live consultation and controlled music media; multi-dialog attended-transfer SIPp; physical-device acceptance |

Run all ten fast foundation profiles from the repository root. These are
contract checks, not substitutes for live Kubernetes SIPp regression:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-public-pycache \
python3 tools/run_public_foundation_regression.py \
  --profile ai-provider-streaming-contract \
  --profile ai-provider-interruption-fallback \
  --profile rfc5359-consultation-hold \
  --profile rfc5359-consultation-failure-recovery \
  --profile rfc5359-music-on-hold \
  --profile rfc5359-unattended-transfer \
  --profile rfc5359-attended-transfer \
  --profile rfc5359-call-forwarding \
  --profile rfc5359-find-me-policy \
  --profile rfc5359-call-screening-policy
```

For the focused Kubernetes profiles and the current-source SBC deployment,
use the [commercial build, package, upgrade, and regression workflow](KUBERNETES_HELM_RUNBOOK.md#commercial-source-build-package-and-sbc-upgrade).
See the [AI Voice Gateway guide](AI_VOICE_GATEWAY.md) and the
[RFC 5359 business-calling guide](BUSINESS_CALLING_SERVICES.md)
for the detailed scope and acceptance gates.

## 4. Production SBC Protocol Core — Release Blocking

The AI Voice Gateway must sit on a deterministic SIP core. Successful
REGISTER and call flows are not enough for a production SBC: parsing,
transport, transaction, dialog, registrar, routing, authentication, overload,
and recovery behavior must be independently testable and must not depend on
bot or media-provider code.

The target layering is:

```text
UDP/TCP/TLS transport and framing
              |
              v
SIP parser, validation, normalization
              |
              v
INVITE and non-INVITE transaction state machines
              |
              v
Dialog, registration, and SIP usage state
              |
              v
B2BUA policy, routing, topology, and admission control
              |
              v
RTPengine/media and AI Voice Gateway services
```

No B2BUA, AI, or feature handler may bypass transaction matching, timer,
retransmission, or transport-error processing.

### Standards Baseline

| Standard | Production scope |
| --- | --- |
| [RFC 3261](https://www.rfc-editor.org/info/rfc3261/) | SIP message grammar, UAC/UAS behavior, registrations, dialogs, proxy/B2BUA routing, transactions, transports, authentication, and response handling |
| [RFC 6026](https://www.rfc-editor.org/info/rfc6026/) | Corrections to the RFC 3261 INVITE transaction state machines |
| [RFC 4320](https://www.rfc-editor.org/info/rfc4320/) | Corrections to non-INVITE transaction behavior |
| [RFC 3263](https://www.rfc-editor.org/info/rfc3263/) | DNS NAPTR/SRV/A/AAAA server location, transport selection, priority, weight, and failover |
| [RFC 3581](https://www.rfc-editor.org/info/rfc3581/) | Symmetric response routing using Via `rport` and `received` for NAT traversal |
| [RFC 5923](https://www.rfc-editor.org/info/rfc5923/) | SIP TCP/TLS connection reuse and connection ownership |
| [RFC 3262](https://www.rfc-editor.org/info/rfc3262/) | Reliable provisional responses, `100rel`, RSeq, RAck, and PRACK |
| [RFC 4028](https://www.rfc-editor.org/info/rfc4028/) | Session refresh negotiation, refresher ownership, expiry, and teardown |
| [RFC 8760](https://www.rfc-editor.org/info/rfc8760/) | Modern SIP digest authentication algorithms and RFC 3261 digest updates |
| [RFC 3327](https://www.rfc-editor.org/info/rfc3327/) and [RFC 3608](https://www.rfc-editor.org/info/rfc3608/) | Registrar Path and Service-Route behavior |
| [RFC 5626](https://www.rfc-editor.org/info/rfc5626/) | SIP Outbound flows, keepalives, and edge availability for NATed clients |
| [RFC 5922](https://www.rfc-editor.org/info/rfc5922/) and [RFC 8996](https://www.rfc-editor.org/info/rfc8996/) | SIP domain certificates and current TLS version policy |
| [RFC 3264](https://www.rfc-editor.org/info/rfc3264/) and [RFC 8866](https://www.rfc-editor.org/info/rfc8866/) | Offer/answer state and current SDP grammar used by B2BUA media negotiation |
| [RFC 4475](https://www.rfc-editor.org/info/rfc4475/) | Parser torture messages and robustness expectations |
| [RFC 7339](https://www.rfc-editor.org/info/rfc7339/) | SIP overload-control behavior, used with local admission and rate controls |

This list is the initial protocol-core target, not a claim that every optional
SIP extension is required for `v6.0.0`. Each supported item needs an explicit
implementation matrix, normative requirements review, tests, and retained
evidence.

### Layer 1: Message Parser And Validation

- Parse request/status lines, compact and repeated headers, comma-separated
  values, quoted strings, escaped URIs, IPv6 literals, MIME bodies, and exact
  `Content-Length` framing without ad hoc string assumptions.
- Enforce mandatory headers, singleton rules, method/CSeq consistency,
  Max-Forwards, URI validity, body length, supported schemes, message-size
  limits, header-count limits, and deterministic error responses.
- Preserve unknown extension headers end to end when policy permits; reject
  malformed, ambiguous, smuggled, truncated, or oversized messages safely.
- Build a maintained RFC 4475 corpus with accepted/rejected verdicts, resource
  limits, fuzzing, and proof that malformed traffic cannot crash or poison a
  later transaction.

### Layer 2: Transport And Server Location

- Implement SIP UDP datagrams and TCP/TLS stream framing, partial reads,
  multiple messages per read, pipelining, backpressure, idle timeout,
  half-close, connection failure, and bounded connection pools.
- Keep transaction retransmission behavior transport-aware: timers retransmit
  on unreliable transports and do not create duplicate application actions on
  reliable transports.
- Implement RFC 3581 `rport`/`received`, RFC 5923 connection reuse, TLS identity
  and policy, certificate rotation, SNI where applicable, and safe NAT
  keepalive handling.
- Add RFC 3263 NAPTR/SRV/A/AAAA resolution with priority/weight selection,
  cache/TTL behavior, address-family handling, transport fallback, and bounded
  failover across resolved targets.

### Layer 3: RFC 3261 Transaction State Machines

- Implement separate INVITE client, INVITE server, non-INVITE client, and
  non-INVITE server transaction state machines with explicit states and
  deterministic clocks.
- Cover timers A, B, D, E, F, G, H, I, J, K and proxy Timer C where applicable;
  make T1, T2, and T4 configurable within policy and observable at runtime.
- Match requests and responses using Via branch/sent-by, method, CSeq, and the
  RFC 3261 fallback rules; detect merged requests separately from
  retransmissions.
- Cache and replay responses to duplicate requests without repeating routing,
  admission, media, billing, AI, or teardown side effects.
- Distinguish ACK for non-2xx final responses, which belongs to the INVITE
  transaction, from end-to-end ACK for 2xx responses, which belongs to the
  dialog usage.
- Complete CANCEL matching and races correctly: `200` to valid CANCEL, `487`
  to the pending INVITE, no cancellation of a completed transaction, and no
  leaked B-leg or RTPengine session.
- Apply RFC 6026 and RFC 4320 corrections, transport-error propagation, timeout
  mapping, final-response retransmission, and bounded state cleanup.

### Layer 4: Dialog And B2BUA Leg State

- Key dialogs by Call-ID plus local/remote tags and maintain independent
  transaction, dialog, and usage state for each B2BUA leg.
- Preserve local/remote CSeq rules, route sets, Record-Route/Route ordering,
  remote targets, Contact refresh, strict/loose routing, and target-refresh
  requests.
- Handle early dialogs, multiple provisional responses, forked responses,
  winning/losing branches, late `2xx`, ACK, BYE, and cleanup without crossing
  dialog state between calls.
- Implement reliable provisional responses and PRACK, session timers,
  re-INVITE/UPDATE collision handling, `491` retry policy, and idempotent
  in-dialog retransmission behavior.
- Persist only the state required for HA restoration, with ownership epochs,
  expiry, fencing, and evidence that a stale node cannot act on a restored
  dialog.

### Layer 5: Registrar, Routing, Identity, And Policy

- Implement digest REGISTER with nonce lifetime, stale handling, replay
  resistance, algorithm policy, credential isolation, binding expiry,
  wildcard de-registration, multiple Contacts, q-values, and deterministic
  NAT contact treatment.
- Add Path, Service-Route, SIP Outbound/flow identity where selected by the
  product profile, and define explicit behavior for unsupported extensions.
- Separate routing policy from transaction machinery: realm classification,
  normalized identities, loop/spiral detection, Max-Forwards, topology hiding,
  source trust, number manipulation, trunk health, failover, and response-code
  mapping must be auditable policy decisions.
- Protect privacy-sensitive headers and define the later identity roadmap for
  P-Asserted-Identity, Privacy, Diversion/History-Info, and STIR/SHAKEN rather
  than forwarding them accidentally.

### Layer 6: Overload, Security, And Operations

- Enforce per-source, realm, subscriber, trunk, method, registration, CPS,
  concurrent-call, transaction, connection, message-size, and RTP-port limits.
- Add deterministic `503`/Retry-After and policy-defined rejection behavior,
  priority treatment, load shedding, queue bounds, circuit breakers, and
  recovery hysteresis.
- Add malformed-message, parser-fuzz, slow TCP/TLS, connection-exhaustion,
  authentication-flood, registration-storm, OPTIONS-storm, INVITE-burst,
  CANCEL-race, and retransmission-amplification gates.
- Export transaction counts and durations by method/state/result, timer expiry,
  retransmissions, duplicate suppression, dialog leaks, DNS outcomes,
  transport failures, overload decisions, and cleanup latency.

### Required Protocol Regression Families

| Family | Minimum focused profiles before v6.0.0 |
| --- | --- |
| Parser | valid RFC 3261 variants, compact/repeated headers, stream framing, malformed/oversized input, maintained RFC 4475 corpus |
| INVITE transactions | client/server success, provisional, non-2xx ACK, 2xx retransmission until ACK, Timer B/H timeout, transport failure |
| Non-INVITE transactions | REGISTER/OPTIONS/BYE success, duplicate suppression, Timer E/F/J/K behavior, delayed/lost response |
| CANCEL and races | cancel before provisional, cancel after provisional, final-response race, duplicate CANCEL, late response cleanup |
| Dialogs | route set, target refresh, CSeq rejection, forked early dialogs, late 2xx, re-INVITE glare, BYE from either leg |
| Extensions | PRACK/100rel, session refresh and expiry, digest algorithm negotiation, NAT `rport`, TCP/TLS reuse |
| Routing | RFC 3263 priority/weight/failover, IPv4/IPv6, loop detection, Max-Forwards, trunk failover and recovery |
| Resilience | restart/HA fencing, transaction cleanup, registration recovery, overload and admission recovery |

Every profile must use deterministic clocks where practical and retain a
canonical ladder, `sipmsg.log`, transaction-state trace, timer/retransmission
verdict, clean process logs, and combined PCAP when networking is exercised.
High-volume profiles may aggregate ladders but may not omit transaction and
cleanup verdicts.

### v6.0.0 Production SBC Acceptance

- The supported RFC matrix names every implemented mandatory behavior,
  extension, limitation, and intentionally unsupported feature.
- All four transaction state machines pass deterministic timer, loss,
  duplicate, race, transport-failure, and cleanup tests over applicable
  transports.
- Parser robustness and fuzz gates have no crash, hang, unbounded allocation,
  request smuggling, cross-message contamination, or unsafe exception.
- Dialog, registrar, routing, authentication, overload, HA, and observability
  gates pass together under sustained and failure-injection load.
- Docker, kind, AKS, real-device, AI, media, HA, upgrade, rollback, and security
  lanes remain green under the repository Golden Rule.
- Performance and interoperability claims are limited to measured evidence;
  the product does not use a blanket “RFC compliant” label.

## 5. RFC 5359 Business Calling Services

[RFC 5359](https://www.rfc-editor.org/info/rfc5359/) is a Best Current Practice containing SIP service examples. It does not make a device or SBC compliant by itself. Most examples are user-agent features; some require proxy assistance, and PlaySBC can implement the network side as a B2BUA.

PlaySBC propagates an in-dialog re-INVITE to the opposite B2BUA leg,
preserves dialog routing and CSeq ordering, and updates an existing RTPengine
session. The public release also relays REFER/NOTIFY between independent B2BUA
legs, translates attended-transfer Replaces identifiers, and applies bounded
call-forwarding policy. Internal and RTPengine variants are cataloged for the
live unattended-transfer and forwarding flows. Real-device acceptance remains
required before any service is described as production-ready.

### v2.6.0 Public Baseline: Hold And Resume

- Accept an in-dialog re-INVITE from either call leg and originate the corresponding request on the opposite B2BUA leg
- Validate dialog identifiers, route set, Contact, monotonically increasing CSeq, SDP origin version, retransmissions, and glare handling
- Support `a=sendonly`, `a=recvonly`, and `a=inactive`; tolerate legacy `c=IN IP4 0.0.0.0` while preferring direction attributes
- Update RTPengine offer/answer state without destroying the media session
- Stop and restore the correct RTP direction while keeping RTCP and dialog state coherent
- Complete re-INVITE, `200 OK`, and ACK transactions before changing the service verdict
- Handle BYE, CANCEL-equivalent race conditions, timeout, failed re-INVITE, and repeated hold/resume safely
- Validate both OBi1022-to-Zoiper and Zoiper-to-OBi1022 over UDP first, then TCP/TLS where the device supports it

Synthetic regression profiles:

- `rfc5359-call-hold-resume`
- `rfc5359-call-hold-resume-rtpengine`
- `rfc5359-call-hold-resume-tcp`
- `rfc5359-call-hold-resume-tls`

Planned real-device acceptance profile: `rfc5359-call-hold-resume-real-device`.

Evidence must prove the initial two-way media period, hold SDP and media suppression, resume SDP and restored bidirectional RTP/RTCP, clean teardown, and no leaked RTPengine session.

The v2.6.0 public synthetic gate sends bounded PCMU bursts from both call legs before hold and after resume. Its combined PCAP must contain both RTP directions and a measurable media-free hold interval; SIP-only success is rejected.

### Remaining HA Evidence Caveat

The current mid-call PlaySBC pod-delete profiles use Kubernetes' normal termination grace period. They prove call continuity during replacement and now preserve pre-fault logs, but they do not yet prove an abrupt process crash followed by shared-dialog restoration on a surviving node. A future HA gate must force termination, identify the serving node before the fault, prove restoration on a different node, and verify uninterrupted or bounded-loss RTP.

### Pre-v6 Service Sequence

| RFC 5359 service | Current status | PlaySBC responsibility |
| --- | --- | --- |
| 2.1 Call Hold | Live synthetic baseline | Re-INVITE/SDP propagation and RTPengine direction update |
| 2.2 Consultation Hold | Active next implementation chunk | Three-endpoint multi-dialog signaling and media while preserving the original dialog |
| 2.3 Music on Hold | State contract only | Controlled media source, SDP direction, and RTPengine anchoring |
| 2.4 Unattended Transfer | Live signaling slice | REFER/NOTIFY relay, failure recovery, and teardown across B2BUA legs |
| 2.5 Attended Transfer | Live translation plus unit evidence | Consultation dialog plus REFER with mapped Replaces identifiers |
| 2.6 Instant Messaging Transfer | Deferred | Optional MESSAGE-session work; not a real-device voice priority |
| 2.7 Unconditional Forwarding | Live signaling slice | Policy/registrar target selection and loop prevention |
| 2.8 Forwarding on Busy | Live 302 redirect slice | Busy response handling and policy-selected redirect target |
| 2.9 Forwarding on No Answer | Live 302 redirect slice | Ring timeout, cancellation, and policy-selected redirect target |
| 2.10 3-Way Conference: Third Party Is Added | Planned | Network conference focus, three dialogs, and three media legs |
| 2.11 3-Way Conference: Third Party Joins | Planned | Dial-in conference focus, authorization, and participant lifecycle |
| 2.12 Find-Me | Policy foundation implemented | Ordered sequential/parallel target selection; live forking remains |
| 2.13 Incoming Call Screening | Policy foundation implemented | Directional caller/callee allow/reject enforcement before routing |
| 2.14 Outgoing Call Screening | Policy foundation implemented | Directional caller/callee allow/reject enforcement before routing |
| 2.15 Call Park | Planned | Park-slot ownership, retrieval, timeout, and recovery |
| 2.16 Call Pickup | Planned | Pickup groups, ringing-dialog replacement, and race handling |
| 2.17 Automatic Redial | Planned | Bounded retries, cancellation, answer race, and overload control |
| 2.18 Click to Dial | Planned | Authenticated controller and consent-aware two-leg call setup |

Every service needs unit tests, local SIPp profiles, kind and AKS regression
coverage, combined evidence, and real-device validation when the endpoint
exposes the feature. Multi-party scenarios must use three independent SIPp
pods (A/B/C), never multiple logical roles in one pod. SIPp success alone does
not close a service: standard-SIP physical-device signaling and media must pass
in both supported device directions, including the RTPengine path.

The current public release adds deterministic consultation and
music-on-hold contracts, recovery of the original dialog after failed
consultation or transfer, live unattended REFER/NOTIFY relay, attended Replaces
translation, and forwarding policy. The new live flows have paired internal
and RTPengine profiles. See
[RFC 5359 business calling services](BUSINESS_CALLING_SERVICES.md) for the
exact implementation matrix; consultation media, controlled music, complete
attended-transfer call flow, kind/AKS evidence, and real-device acceptance
remain open gates.

## 6. Production AI Voice Gateway

The AI Voice Gateway is a commercial product focus, but its production gate
depends on the SBC protocol core and the business-service interactions above.

### Pre-v6 Foundation

- Return generated TTS as live RTP in the established call, not report-only audio
- Define one streaming adapter contract for Rasa and future bot providers (foundation contract implemented; live provider integrations remain gated)
- Add deterministic STT partial/final results and retry/idempotency behavior; provider-stream timeout, call-finalization cancellation, fallback, and ordered TTS chunking are implemented foundations
- Preserve call/media state when a bot or model becomes slow or unavailable
- Add provider health plus STT, bot, TTS, first-audio, and end-to-end latency metrics
- Produce synchronized SIP, RTP/RTCP, transcript, model, action, and audio evidence
- Keep Docker, kind, AKS, real-device, HA, and non-AI regression gates green

### Pre-v6 Expansion

- Add multiple bot backends behind the provider interface
- Add multi-turn dialog state, DTMF hybrid IVR, transfer, conference, and human-agent fallback
- Add real Whisper/Vosk and Piper/Coqui images with model warmup and readiness gates
- Add streaming load, interruption, bot-failure, model-failure, and long-call profiles
- Add AI dashboards for provider health, latency percentiles, active sessions, fallback, and errors

### v6.0.0 Acceptance

- Live bidirectional speech loop passes on local kind, AKS, SIPp, and real devices
- Multiple bot integrations run without SIP/media implementation changes
- Call state and fallback behavior are deterministic under provider failures
- Every AI call has complete signalling, media, transcript, model, action, and latency evidence
- Security, privacy, retention, secret rotation, overload, and operational runbooks are validated

## 7. Cloud, HA, And Scale

Azure remains the first reference cloud; AWS follows.

- Replace lab-only shared state with a replicated registrar/dialog/CDR backend
- Validate production SIP load balancing, affinity, draining, certificate rotation, firewalling, and SRTP policy
- Add malformed SIP, registration storm, OPTIONS storm, INVITE burst, RTP exhaustion, and overload protection
- Add multi-zone failure, backup/restore, rollback, and days-long soak tests
- Progress through 10k, 50k, 100k, then 300k registrations
- Progress through 250, 500, 1000, then 2500 concurrent calls
- Publish measured CPU, memory, CPS, registrations/s, packet rate, media sessions, quality, and recovery time

## 8. Golden Rule

No AI, protocol-core, RFC 5359, HA, cloud, or real-device change may silently regress another lane. Each change needs focused tests, existing-suite compatibility, explicit kube context and image preflight, clean logs, one combined evidence bundle, and an actionable pass/fail verdict.
