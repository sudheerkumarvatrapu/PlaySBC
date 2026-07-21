# PlaySBC v1.4.3

PlaySBC `v1.4.3` starts the Azure-first Production Cloud SBC track.

## Added

- AKS reference values in `configs/kubernetes/aks-values.yaml`.
- Azure public SIP LoadBalancer template with static Public IP annotations.
- Optional Azure internal SIP LoadBalancer template for private/core-side reachability.
- Optional RTPengine public UDP media Service for explicit lab media ports.
- Azure AKS operator runbook in `docs/AZURE_AKS.md`.
- Evolution plan split for `v1.4.3`, `v1.4.4`, and `v1.5.0`.

## AKS Scope

`v1.4.3` is the deployment foundation:

- AKS active-active PlaySBC StatefulSet.
- Paired RTPengine StatefulSet.
- Azure Standard Load Balancer service wiring.
- Static Public IP name/resource group annotations.
- SIP UDP/TCP/TLS ingress ports.
- Private SIP service option.
- Prometheus/Grafana observability defaults.

## Important Caveat

Full RTP/SRTP media range exposure on AKS is not marked production-complete in this release. Kubernetes Services do not provide a clean compact `30000-32000/UDP` range primitive, so `v1.4.3` only provides explicit lab media-port Service wiring. The production media dataplane is tracked for the next Azure hardening releases.

## Next Azure Milestones

- `v1.4.4`: AKS validation profiles, Azure LB SIP UDP/TCP/TLS evidence, TLS lifecycle notes, and media dataplane checks.
- `v1.5.0`: production-style AKS reference architecture with full media range model, dedicated node pools, NSG/Azure Firewall rules, external shared state, multi-zone failure tests, and operational runbooks.

## Published Images

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.4.3`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.4.3`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.4.3`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.4.3`

## Verification

- Helm lint passed.
- Helm template passed with `configs/kubernetes/aks-values.yaml`.
- Targeted chart unit tests passed.
