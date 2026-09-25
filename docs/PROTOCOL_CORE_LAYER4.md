# Protocol Core Layer 4

Layer 4 is the SIP dialog and B2BUA-leg gate. The full Kubernetes catalog has
125 profiles, including ten dedicated real Layer 4 calls. Run the documented
eight-hour [full-suite wrapper](KUBERNETES_HELM_RUNBOOK.md#build-current-source)
after building and upgrading the current private image.

## Implemented behavior

- Dialog identity is Call-ID plus local and remote tags. Forked dialog models
  keep distinct targets, route sets, CSeq counters, and offer/answer state.
- The B2BUA keeps separate inbound/outbound Call-IDs, tags, CSeq counters,
  Contacts, destinations, and route sets. Record-Route is reversed on the UAC
  leg. Loose and strict routing shape the Request-URI, Route header, and next
  hop for ACK, BYE, re-INVITE, PRACK, and UPDATE.
- Successful re-INVITE/UPDATE target refresh changes Contact only after the
  peer accepts it. Rejected refreshes do not mutate the target or route set.
- Reliable `183` passes RSeq/Require across the B2BUA, and a matching RAck is
  translated into outbound PRACK. A non-matching RAck is rejected with `481`.
  PRACK carrying a new SDP offer is explicitly rejected with `488`; use a
  separate re-INVITE/UPDATE offer for this product profile.
- Session-Expires/Min-SE are parsed with a 90-second floor. Too-small requests
  receive `422` and Min-SE. The B2BUA selects `refresher=uac`, refreshes the
  expiry on a successful re-INVITE/UPDATE with Session-Expires, and sends BYE
  on both legs when the caller does not refresh.
- In-dialog UPDATE supports a target-only request and an SDP offer/answer.
  A concurrent re-INVITE/UPDATE receives `491`; an outbound `491` triggers one
  delayed higher-CSeq retry. UDP duplicate requests remain transaction-cached.
- When multiple forked `2xx` responses arrive, only the first dialog wins.
  Losing dialogs receive ACK and BYE; retransmitted winning `2xx` gets ACK.
  A bounded tombstone handles late finals after ordinary call cleanup.
- Shared dialog and B2BUA state includes route, target, CSeq, and timer data.
  HA takeover increments a persisted ownership epoch; a stale owner cannot
  send dialog packets or run the destructive call finalizer. The existing
  `ha-shared-registrar-dialog-restore` profile remains the live HA gate.

## Real-call profiles

| Profile suffix (`protocol-core-layer4-live-…`) | Wire-level gate |
| --- | --- |
| `route-set` | Reversed loose Record-Route on ACK and BYE |
| `strict-route` | Strict Request-URI and trailing remote target in Route |
| `prack` | Reliable 183, PRACK/RAck, final INVITE ACK with correct CSeq |
| `session-timer` | Session-Expires negotiation |
| `session-expiry` | No refresh for 90 seconds; BYE to both legs |
| `min-se` | 422 with Min-SE 90 |
| `update-target` | Contact target refresh controls later BYE URI |
| `update-offer` | UPDATE SDP offer/answer and target refresh |
| `fork-cleanup` | Winner ACK/BYE and losing 2xx ACK/BYE |
| `update-glare` | 491 then delayed higher-CSeq retry |

All displayed regression SIP ladders are constructed only after scenario
execution and evidence collection. A PCAP with visible SIP is the preferred
source; otherwise the runner uses retained SIPp messages. It does not publish
an inferred successful-call sequence. For the 2026-09-20 Layer 3 negative
profiles, the retained PCAPs show 503/ACK, 487/ACK, and 480/ACK; the previous
three failures were evidence-selector and ladder-template defects, not those
wire outcomes.

The local unit suite and SIPp calls are pre-merge gates. Kubernetes behavior
still needs confirmation against the newly built image in the full regression
run; a green local gate is not a guarantee of a green cluster run.
