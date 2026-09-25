# PlaySBC v4.0.0

PlaySBC v4.0.0 expands the public MIT SBC baseline with a substantially
stronger SIP protocol core, B2BUA behavior, registrar and location services,
overload protection, RFC 3263 server discovery, and expanded evidence-driven
regression coverage.

## SIP Protocol Core

- Added deterministic RFC 3261 client transaction handling with transaction
  states, retransmission timers, timeout handling, response matching, and
  transport-error handling.
- Added stricter SIP message parsing and framing with bounded message, header,
  and line limits.
- Added SIP URI parsing, compact-header handling, mandatory-header validation,
  and deterministic syntax rejection.
- Expanded dialog and transaction handling used by the PlaySBC call path.
- Added documented Protocol Core Layers 2 through 6 with corresponding
  regression coverage.

## Registration And Routing

- Added registrar and location-service primitives with bounded multi-contact
  bindings and deterministic q-value ordering.
- Added digest authentication primitives supporting SHA-256 and MD5,
  nonce lifetime handling, nonce-count validation, and replay protection.
- Added registration scenarios covering multi-contact, wildcard,
  Path/Outbound, and related routing behavior.
- Added RFC 3263-oriented SIP server discovery with NAPTR, SRV, A/AAAA
  resolution, bounded caching, priority/weight ordering, and failover targets.

## B2BUA And Call Control

- Expanded B2BUA regression scenarios for PRACK and reliable provisional
  response handling.
- Added Session-Timer, Min-SE, session-expiry, and UPDATE coverage.
- Added route-set, strict-routing, fork, UPDATE offer, and glare scenarios.
- Extended unattended-transfer regression behavior and supporting dialog and
  transaction validation.

## Resilience And Overload

- Added deterministic overload and abuse-control policy.
- Added per-source, method, registration, INVITE, realm, subscriber, and trunk
  rate controls.
- Added maximum in-flight transaction protection, Retry-After behavior, and
  recovery hysteresis.
- Added recovery, priority, REGISTER-storm, OPTIONS-storm, and Max-Forwards
  regression scenarios.

## Regression And Validation

- Expanded Kubernetes and SIPp regression tooling for the new protocol-core
  and B2BUA scenarios.
- Added focused B2BUA SIPp smoke tooling.
- Added parser, URI, stream, RFC 4475, client-transaction, server-location,
  protocol-core Layer 5/6, dialog, transaction, and call-server tests.
- The v4.0.0 Python validation gate passes all 466 discovered tests.

## Deployment And Documentation

- Chart, application version, and public PlaySBC image metadata are aligned on
  `4.0.0`.
- Added the PlayFabric / PlayConverse architecture guide for AI voice
  interconnect integration.
- Standardized Azure deployment guidance on `docs/AZURE_AKS.md`.
- Added Protocol Core Layer 2 through Layer 6 implementation and regression
  documentation.
- Product Guide HTML and PDF artifacts are generated for v4.0.0.

## Scope

PlaySBC v4.0.0 is a public MIT lab, development, interoperability, and
regression baseline. Release validation does not by itself constitute
production certification for every network, endpoint, carrier, or deployment
environment.
