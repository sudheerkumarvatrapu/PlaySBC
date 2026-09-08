# RFC 5359 Business Calling Services

This guide describes the public PlaySBC v3.0.0 implementation of the
network-side business calling services illustrated by RFC 5359. RFC 5359 is a
Best Current Practice with example SIP call flows; it is not a protocol
conformance certificate. A real deployment also depends on each phone or
softphone supporting the relevant SIP method, SDP behavior, and transfer
subscription flow.

## Current Implementation Matrix

| Service | PlaySBC behavior | Automated evidence | Remaining acceptance |
| --- | --- | --- | --- |
| 2.1 Call hold/resume | Relays in-dialog re-INVITE/SDP between independent B2BUA legs; preserves CSeq/dialog state; updates the existing internal or RTPengine media session | SIPp over UDP, TCP, TLS, and RTPengine | OBi1022/Zoiper bidirectional physical-device capture |
| 2.2 Consultation hold | Maintains distinct original and consultation Call-IDs with deterministic complete, failed-consultation recovery, and original-BYE race states | Unit and fast state profiles | **Active next chunk:** dedicated three-endpoint multi-dialog SIPp flow, media evidence, kind/AKS, and devices |
| 2.3 Music on hold | Defines the hold/music/resume state contract and validates its lifecycle | Unit and fast state profile | Controlled RTP source, anchoring, live SIPp, kind/AKS, and devices |
| 2.4 Unattended transfer | Accepts in-dialog REFER, relays it across the B2BUA legs, relays `message/sipfrag` NOTIFY progress, and preserves the original call after rejection | Protocol unit test; passing three-endpoint kind evidence for internal and RTPengine profiles | AKS and devices that support REFER subscriptions |
| 2.5 Attended transfer | Parses RFC 3891 Replaces and maps the referenced Call-ID and tags from one B2BUA leg to the other | Protocol unit test and fast state profile | Multi-dialog SIPp, kind/AKS, and physical-device evidence |
| 2.6 Instant-messaging transfer | Deferred pending product decision; retained explicitly so RFC coverage is not hidden | None | Decide scope, then require three independent SIP endpoints if implemented |
| 2.7 Unconditional forwarding | Applies ordered policy before normal routing, supports registered users or explicit SIP URIs, and rejects loops/hop overflow | Unit; internal and RTPengine SIPp profiles | Execute/retain kind and AKS evidence; devices |
| 2.8 Forwarding on busy | Maps configured busy responses to a policy-controlled `302 Moved Temporarily` Contact | Unit; passing three-endpoint kind evidence for internal and RTPengine profiles | AKS and physical-device redirect behavior |
| 2.9 Forwarding on no answer | Applies a bounded INVITE timeout, cancels the original B-leg, and returns a policy-controlled 302 Contact | Unit; passing three-endpoint kind evidence for internal and RTPengine profiles | AKS and physical-device redirect behavior |
| 2.10 3-Way Conference: third party added | Planned network conference focus/bridge with three distinct dialogs and media legs | None | Three-node SIPp internal/RTPengine, AKS, and two real-device combinations |
| 2.11 3-Way Conference: third party joins | Planned dial-in conference focus with authorization and participant lifecycle | None | Three-node SIPp internal/RTPengine, AKS, and two real-device combinations |
| 2.12 Find-Me | Ordered sequential/parallel target policy with bounds, duplicate detection, and loop protection; live forking is not wired yet | Unit policy tests | Three independent SIPp nodes, RTPengine, AKS, and registered real devices |
| 2.13 Incoming call screening | Directional caller/callee allow/reject policy foundation; live INVITE enforcement is not wired yet | Unit policy tests | SIP response and audit evidence from three SIPp nodes, AKS, and devices |
| 2.14 Outgoing call screening | Directional caller/callee allow/reject policy foundation; live INVITE enforcement is not wired yet | Unit policy tests | SIP response and audit evidence from three SIPp nodes, AKS, and devices |
| 2.15 Call park | Planned network-owned park slot, retrieval authorization, timeout, and original-party recovery | None | Three-node park/retrieve and timeout profiles with RTPengine, AKS, and devices |
| 2.16 Call pickup | Planned pickup-group policy and deterministic competing-pickup handling | None | Three-node ringing/pickup/race profiles with RTPengine, AKS, and devices |
| 2.17 Automatic redial | Planned bounded retry policy with cancellation, answer race, and overload protection | None | Three-node busy/retry/answer profiles with RTPengine, AKS, and devices |
| 2.18 Click to dial | Planned authenticated controller-triggered two-leg call with consent and cleanup | None | Controller plus three-node SIP evidence, RTPengine, AKS, and real devices |

## Complete RFC 5359 Delivery Plan

The plan covers every service example in RFC 5359 sections 2.1 through 2.18.
Work proceeds in four gates: (1) finish live consultation hold, music on hold,
and attended transfer; (2) wire Find-Me and incoming/outgoing screening into
normal INVITE routing; (3) add both three-way conference variants, call park,
and call pickup; (4) add automatic redial and click-to-dial, and resolve the
explicit product decision for instant-messaging transfer.

Every live multi-party profile must use three independent SIPp pods A, B, and
C. Reusing one pod for multiple roles is a failure. SIPp is only synthetic
evidence: implementation must use standard SIP methods, dialog identifiers,
SDP, RTP/RTCP, and configurable timers so normal phones and softphones work.
Each service closes only after internal-media and RTPengine kind runs, AKS
evidence, and physical-device validation in both supported device directions.
The exact paired profile names, A/B/C roles, and RTPengine assertions are in
[RFC 5359 Regression Profile Plan](RFC5359_REGRESSION_PLAN.md).

## Active Next Chunk: Live Consultation Hold

The next implementation gate uses caller A, original party B, and consultation
party C. It must prove that A places B on hold, establishes an independent
dialog with C, preserves both dialog identifiers and media ownership, then
either resumes B after consultation failure or completes the selected transfer
path. A synthetic state-only result is insufficient: the kind report must carry
the three endpoint captures, both Call-IDs, SDP direction changes, RTPengine
session evidence, and deterministic cleanup.
The implementation is suitable for interoperability testing now. Do not call
the whole RFC 5359 track production-ready until the remaining cells above have
retained evidence.

## Configuration

Transfer is enabled by default. Forwarding has no rules until an operator adds
them under `playsbc.config.business_services` in Helm values or
`business_services` in a standalone server configuration.

```yaml
business_services:
  transfer:
    enabled: true
  forwarding:
    max_hops: 5
    rules:
      - name: support-always
        match: "4100"
        target: "4200"
        condition: unconditional
        priority: 10
      - name: sales-busy
        match: "4300"
        target: "sip:4399@pbx.example.com"
        condition: busy
        priority: 20
      - name: helpdesk-no-answer
        match: "4400"
        target: "4499"
        condition: no-answer
        priority: 30
  screening:
    rules:
      - name: reject-premium-outbound
        direction: outgoing
        caller: "4*"
        callee: "1900*"
        action: reject
        status: 603
        reason: Decline
  find_me:
    max_targets: 8
    rules:
      - name: support-find-me
        match: "4100"
        targets: ["4101", "sip:4102@branch.example.com"]
        mode: sequential
        no_answer_timeout: 15
```

Rules are evaluated by ascending `priority`, then name. `match` uses shell-style
patterns such as `44*`. A numeric or named `target` is routed through the
normal registrar/routing engine. A `sip:` or `sips:` target is used directly.
Busy forwarding currently recognizes SIP 486, 600, and 603. No-answer uses
`b2bua_invite_timeout`, which defaults to 10 seconds.

For busy and no-answer, PlaySBC returns a 302 Contact to the originating user
agent. Confirm that the deployed phone or upstream PBX follows SIP redirects;
otherwise use that PBX's forwarding policy until server-originated alternate
B-leg routing is added.

## RFC 5359-Only Kubernetes Regression

Build and upgrade the public release or current public source first with the
self-contained workflows in
[Kubernetes and Helm runbook](KUBERNETES_HELM_RUNBOOK.md).
Then, in the same terminal, run only the live RFC 5359 profiles:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-public-pycache \
python3 tools/run_k8s_regression_job.py \
  --profile rfc5359-call-hold-resume \
  --profile rfc5359-call-hold-resume-rtpengine \
  --profile rfc5359-call-hold-resume-tcp \
  --profile rfc5359-call-hold-resume-tls \
  --profile rfc5359-unattended-transfer \
  --profile rfc5359-unconditional-forwarding \
  --profile rfc5359-forwarding-on-busy \
  --profile rfc5359-forwarding-on-no-answer \
  --profile rfc5359-unattended-transfer-rtpengine \
  --profile rfc5359-unconditional-forwarding-rtpengine \
  --profile rfc5359-forwarding-on-busy-rtpengine \
  --profile rfc5359-forwarding-on-no-answer-rtpengine \
  --playsbc-image "$PLAYSBC_IMAGE" \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster "$KIND_CLUSTER"
```

These twelve profiles are live signaling/media scenarios. The four new
`-rtpengine` variants require a ready RTPengine service and retain its
offer/answer or offer/cleanup control evidence. The fast foundation
runner separately validates consultation, music-on-hold, attended-transfer,
and recovery state contracts that do not yet have complete live call flows.

## Physical-Device Acceptance

Use the isolated real-device lab described in
[REAL_DEVICE_LAB.md](REAL_DEVICE_LAB.md). Test both directions when the device
exposes the feature: OBi1022 to Zoiper and Zoiper to OBi1022.

For each service, retain:

- the phone configuration and firmware/application version;
- the initial INVITE, final response, ACK, and clean BYE sequence;
- every re-INVITE/SDP direction change for hold/resume;
- REFER, 202, NOTIFY sipfrag progress, Replaces, and subscription termination
  for transfer;
- the selected target, 302 Contact, original B-leg CANCEL, and final routed
  destination for forwarding;
- bidirectional RTP/RTCP before and after the feature action, including the
  media-free hold interval where applicable;
- the combined PCAP, `sipmsg.log`, PlaySBC logs, media verdict, and HTML report.

A service passes the real-device gate only when signaling and media both pass,
the call tears down cleanly, and no PlaySBC or RTPengine session leaks.
For a service involving three parties, the synthetic prerequisite is three
separate SIPp pods and the device gate must exercise a real phone or softphone
in every active party role; no SIPp-only extension may be required by the SBC.

## Metrics And Troubleshooting

Query business-service transitions with:

```promql
sum by (service,outcome) (
  increase(playsbc_business_service_events_total[15m])
)
```

- REFER returns 403: set `business_services.transfer.enabled: true`.
- REFER returns 400: verify that Refer-To is a SIP URI; attended transfer also
  requires a percent-encoded Replaces Call-ID, `to-tag`, and `from-tag`.
- REFER returns 481 or Replaces is rejected: the referenced dialog is not
  active on the source B2BUA leg, or the NOTIFY arrived on the wrong leg.
- A transfer remains active: confirm the peer sends `Event: refer`, a valid
  `message/sipfrag` status line, and a terminating Subscription-State.
- Forwarding does not select a rule: compare the dialed user, rule pattern,
  condition, and priority; inspect `CALL FORWARDING` in `log.sip`.
- Busy/no-answer returns 302 but the call stops: the originating endpoint did
  not follow the Contact redirect. Confirm that behavior in its SIP settings.
- No-answer takes too long: reduce `b2bua_invite_timeout` carefully and verify
  that normal answer times in the target environment still fit the bound.
- A loop is rejected: inspect every rule in the chain; PlaySBC deliberately
  stops repeated targets and chains beyond `max_hops`.
