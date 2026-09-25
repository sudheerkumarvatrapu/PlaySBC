# Protocol Core Layer 2

Layer 2 is the PlaySBC transport and server-location gate. It covers real UDP,
TCP, and TLS traffic, not only isolated parser tests. The full Kubernetes
catalog contains 125 profiles, including ten dedicated Layer 2 live profiles.

## Implemented Behavior

- TCP/TLS framing accepts partial and pipelined messages while enforcing the
  parser's message, header, line, and buffer limits.
- Stream connections have configurable idle expiry and a bounded global pool.
  EOF with an incomplete frame, half-close, rejection, backpressure, and cleanup
  are logged and counted.
- RFC 3581 response routing honors `rport`, adds `received` only when required,
  and otherwise uses the Via sent-by port for UDP responses.
- RFC 5923 reverse TCP/TLS reuse requires connection ownership: locally opened
  connections are reusable and accepted connections require the Via `alias`
  parameter.
- RFC 3263 server location supports IP-literal bypass, NAPTR service discovery,
  SRV priority/weight ordering, A/AAAA expansion, bounded candidates, positive
  and negative caching, and sequential connection failover.
- TLS enforces TLS 1.2 or newer, supports peer verification, accepted SNI names,
  optional mandatory SNI, and in-place certificate/key reload for new
  handshakes.

## Configuration

```yaml
sip_stream:
  idle_timeout: 60.0
  max_connections: 1024
server_location:
  enabled: true
  cache_ttl: 60.0
  negative_ttl: 5.0
  max_targets: 16
tls_server_names: []
tls_require_sni: false
tls_reload_interval: 30.0
```

`server_location.enabled: false` keeps direct address resolution. TLS identity
allowlists are case-insensitive and ignore a trailing DNS dot. Certificate
rotation affects new handshakes; established TLS sessions continue normally.

## Live Regression Profiles

| Profile | Real validation |
| --- | --- |
| `protocol-core-layer2-live-tcp-call` | TCP call, framing, lifecycle, and owned connection reuse |
| `protocol-core-layer2-live-tls-call` | TLS 1.2+ call, framing, lifecycle, and reuse |
| `protocol-core-layer2-live-dns-srv-call` | Kubernetes SRV and A/AAAA discovery used by a real B2BUA call |
| `protocol-core-layer2-live-dns-udp-call` | Kubernetes hostname A/AAAA discovery used by a real UDP B2BUA call |
| `protocol-core-layer2-live-rport-call` | Mismatched Via sent-by receives a response with `received` and populated `rport` |
| `protocol-core-layer2-live-idle-timeout` | A real call survives normal dialog pauses while a separate idle socket expires and is removed |
| `protocol-core-layer2-live-half-close` | Partial-frame half-close is discarded and cleaned up |
| `protocol-core-layer2-live-pool-limit` | Connection above the configured pool bound is rejected |
| `protocol-core-layer2-live-tls-sni` | Valid SNI succeeds and an unapproved identity fails |
| `protocol-core-layer2-live-tls-rotation` | Secret replacement changes the certificate on a new handshake without a pod restart |

Each profile retains SIPp traces, PlaySBC category logs, one combined packet
capture where applicable, commands, and an HTML verdict. DNS selection emits
`SIP DNS CANDIDATES`, `SIP DNS TARGET ATTEMPT`, and
`SIP DNS TARGET SELECTED`. Stream and TLS probes have corresponding `LAYER2`
markers, and rotation requires `TLS CERTIFICATE RELOADED`.

Run only the Layer 2 profiles:

```bash
python3 tools/run_k8s_regression_job.py \
  --profiles \
  protocol-core-layer2-live-tcp-call,protocol-core-layer2-live-tls-call,protocol-core-layer2-live-dns-srv-call,protocol-core-layer2-live-dns-udp-call,protocol-core-layer2-live-rport-call,protocol-core-layer2-live-idle-timeout,protocol-core-layer2-live-half-close,protocol-core-layer2-live-pool-limit,protocol-core-layer2-live-tls-sni,protocol-core-layer2-live-tls-rotation
```

For the current source build, Helm upgrade, and full 125-profile overnight run,
use the [Kubernetes and Helm runbook](KUBERNETES_HELM_RUNBOOK.md#build-current-source).
