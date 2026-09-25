# Protocol Core Layer 5

Layer 5 closes the registrar, routing, identity, and policy implementation
gate. The 125-profile catalog includes five focused real SIPp profiles plus the
existing authenticated-registration, routing, trunk-failover, and HA gates.

## Implemented behavior

- Digest registration supports policy-ordered SHA-256 and MD5, nonce lifetime,
  `stale=true`, monotonic nonce counts, replay rejection, constant-time response
  comparison, and credential isolation.
- The bounded location service accepts multiple Contacts, validates and orders
  q-values, expires or removes individual bindings, and requires wildcard
  de-registration to use `Expires: 0`.
- Path becomes the registered route set. Responses carry configured
  Service-Route. `+sip.instance` and `reg-id` establish explicit outbound flow
  state and `Require: outbound`.
- Received-source NAT routing, normalization, realm/trunk/hunt policy, health,
  failover, admission, Max-Forwards, merged-request rejection, and B2BUA
  topology hiding remain auditable policies outside transaction state.
- Asserted identity is accepted only from configured trusted addresses.
  Privacy policy removes asserted identity, History-Info, and Diversion before
  the peer leg. PlaySBC does not synthesize unverified STIR/SHAKEN identity.

## Real profiles

| Profile suffix (`protocol-core-layer5-live-…`) | Gate |
| --- | --- |
| `multi-contact` | Multiple Contacts and q-value ordering |
| `wildcard-expiry` | Binding followed by wildcard removal |
| `path-outbound` | Path, Service-Route, instance ID, reg-id, and outbound |
| `max-forwards` | Exhausted hop count receives 483 |
| `digest-replay` | Authenticated REGISTER through replay-controlled nonce state |

The existing `register-auth-*`, registered inbound/outbound, `esbc-*`, and
`ha-shared-registrar-dialog-restore` profiles remain cross-layer gates.
