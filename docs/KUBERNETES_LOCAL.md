# Local Kubernetes Lab

This page explains the local topology. Use the [Kubernetes and Helm runbook](KUBERNETES_HELM_RUNBOOK.md) for maintained commands.

## Default Topology

```text
kind (canonical) or minikube (compatibility)
├── PlaySBC-0 -> RTPengine-0
├── PlaySBC-1 -> RTPengine-1
├── shared HA lab state
├── Prometheus
├── Grafana
└── regression Job -> temporary SIPp core/peer pods
```

Expected active-active pods:

```text
playsbc-playsbc-0
playsbc-playsbc-1
playsbc-playsbc-rtpengine-0
playsbc-playsbc-rtpengine-1
```

Normal local deployment uses `configs/kubernetes/active-active-values.yaml`. RTPengine stays on pod networking because two replicas cannot bind the same host UDP range on a single kind node.

## Realm Model

```text
Core realm: 172.28.0.0/24
Peer realm: 192.168.28.0/24
```

In default kind/minikube these are logical realms represented in configuration, logs, reports, and metrics. Pods still receive normal CNI addresses such as `10.244.x.x`. Real secondary interfaces require Multus or another multi-network CNI.

## kind And minikube

Use kind for the primary development and regression lane. The maintained cluster is named `playsbc`, and its kubectl context is `kind-playsbc`. kind runs every Kubernetes node as a Docker container, so Docker Desktop must be running before the cluster or its workloads are available.

Use minikube as a compatibility lane. It creates a separate cluster and context, usually named `minikube`; it is not part of a `kind-playsbc` deployment. Minikube's runtime requirement depends on its driver: `--driver=docker` requires Docker Desktop, while a VM-based driver requires its corresponding hypervisor instead. The same Helm chart and regression behavior must remain valid, but kind is the canonical command path.

| Local cluster | kubectl context | Runtime dependency | PlaySBC role |
| --- | --- | --- | --- |
| kind `playsbc` | `kind-playsbc` | Docker Desktop | Primary development, HA, and full regression |
| minikube | `minikube` | Selected driver, commonly Docker Desktop | Compatibility validation |

Stopping Docker Desktop stops access to the kind API server and pauses every PlaySBC, RTPengine, Grafana, and Prometheus container. Their Kubernetes objects remain present. Restarting Docker Desktop normally resumes the existing cluster; pod restart counts can increase, which is expected after the node runtime restarts.

## Shared State

The current active-active lab uses stable StatefulSet identities and SQLite-backed shared registrar/dialog state. This is acceptable for a single-node experiment, not for production or robust multi-worker ownership.

The multi-node HA implementation will evaluate RWX storage and Redis/PostgreSQL so either worker can restore state safely.

## Upcoming Multi-Node HA Lab

```text
control-plane
├── worker-1: PlaySBC-0 + RTPengine-0
└── worker-2: PlaySBC-1 + RTPengine-1
```

The next implementation adds:

- worker separation and pod anti-affinity
- PodDisruptionBudgets and node drain
- PlaySBC/RTPengine pair failure during active calls
- shared registrar/dialog restoration
- HA-specific Grafana panels and combined packet evidence

See [EVOLUTION_PLAN.md](EVOLUTION_PLAN.md#next-implementation-local-multi-node-ha-lab).

## Local Real Devices

OBi1022 and Zoiper use the dedicated `playsbc-real-device-aa` kind cluster when both are on the same LAN. This is separate from the `playsbc` regression cluster and AKS. Two PlaySBC pods share registration/dialog state, while two host-network RTPengine pods use disjoint media and control ports. The cluster exposes these host ports through kind `extraPortMappings`:

- SIP `5062/UDP`, `5062/TCP`, and `5061/TCP`
- RTP/RTCP `30000-30049/UDP`

PlaySBC and RTPengine advertise the Mac LAN IP. SIP uses NodePort `32062`/`32061` behind the host's `5062`/`5061`; RTP remains one-to-one to the node. The chart rejects blank/mismatched LAN addresses, Azure exposure, incorrect replica counts, or a media range other than `30000-30049` in this profile.

Use the maintained [dedicated local real-device commands](KUBERNETES_HELM_RUNBOOK.md#dedicated-local-real-device-lab). This validates LAN device behavior, but not Azure LoadBalancer, public NAT, managed identity, or cloud firewall behavior.

## Boundaries

- `kubectl port-forward` is suitable for HTTP, Grafana, Prometheus, and TCP checks; it does not solve UDP SIP/RTP exposure.
- Multi-node kind still runs on one Mac and cannot prove physical host or availability-zone failure.
- AKS remains the milestone lane for Azure identity, ACR, public LoadBalancers, static IPs, and internet real-device calls.
- The local real-device capture command must include `--context kind-playsbc-real-device-aa`; the AKS capture must use its AKS context. Context isolation is part of the evidence contract.
