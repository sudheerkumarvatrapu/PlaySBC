# Protocol Core Layer 6

Layer 6 closes the overload, security, and operations implementation gate.
Limits are explicit configuration, disabled by default, and deterministic when
enabled.

## Implemented behavior

- Sliding-window controls cover source, realm, subscriber, trunk, method,
  REGISTER rate, INVITE CPS, and in-flight transactions. Existing controls
  bound call/trunk concurrency, stream connections, parser resources,
  registrar bindings, and RTPengine sessions/ports.
- Rejection is `503 Service Unavailable` with `Retry-After`. Recovery
  hysteresis prevents oscillation, while Resource-Priority traffic can bypass
  shedding only when its source is explicitly trusted by policy.
- Decisions are logged by source/method/dimension and exported through
  `playsbc_overload_decisions_total{outcome,dimension}` alongside SIP,
  transaction, transport, trunk, HA, media, and RTPengine telemetry.
- Existing malformed/fuzz/resource, slow-stream/pool, transaction race and
  amplification, authentication, load/soak, HA, and RTPengine failure profiles
  are mandatory Layer 6 fault-injection gates.
- In-flight capacity is released by task completion callbacks on both success
  and exception paths; parser, stream, transaction, registrar, call, and media
  structures retain bounded policies.

## Real profiles

| Profile suffix (`protocol-core-layer6-live-…`) | Gate |
| --- | --- |
| `options-storm` | Method limit, 503, and Retry-After |
| `register-storm` | Registration-specific rejection |
| `source-limit` | Per-source shedding |
| `priority-bypass` | Priority admission during hysteresis |
| `recovery` | Quiet interval and restored admission |
