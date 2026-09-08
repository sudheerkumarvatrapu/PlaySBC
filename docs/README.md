# PlaySBC Documentation

Use one canonical page for each task. Supporting pages explain design and evidence; they do not repeat full deployment commands.

## Choose A Workflow

| Task | Canonical Page | Environment |
| --- | --- | --- |
| Local release deployment and full regression | [Kubernetes and Helm runbook](KUBERNETES_HELM_RUNBOOK.md) | kind/minikube |
| Local source-image compatibility gate | [Kubernetes and Helm runbook](KUBERNETES_HELM_RUNBOOK.md#build-current-source) | kind |
| Local topology and networking | [Local Kubernetes lab](KUBERNETES_LOCAL.md) | kind/minikube |
| OBi1022/Zoiper local LAN calls | [Kubernetes and Helm runbook](KUBERNETES_HELM_RUNBOOK.md#dedicated-local-real-device-lab) | dedicated kind |
| Azure creation, deployment, regression, and cleanup | [Azure AKS runbook](AKS.md) | AKS/Cloud Shell |
| OBi1022 and Zoiper calls | [Real-device lab](REAL_DEVICE_LAB.md) | AKS or dedicated kind |
| RTPengine design and focused checks | [RTPengine](RTPENGINE_LOCAL.md) | local/Kubernetes |
| Rasa voice and chat regression | [AI Voice Gateway](AI_VOICE_GATEWAY.md) | Docker/Kubernetes |
| Hold, transfer, and call forwarding | [RFC 5359 business calling services](BUSINESS_CALLING_SERVICES.md) | source/Docker/Kubernetes/devices |
| Grafana, Prometheus, and metrics | [Observability](OBSERVABILITY.md) | Kubernetes |
| Roadmap and production gates | [Evolution plan](EVOLUTION_PLAN.md) | all |
| Release assets and historical notes | [Release index](../release/README.md) | GitHub |

## Default Test Strategy

| Lane | Purpose | Frequency |
| --- | --- | --- |
| Docker dual-realm | Fast signalling/media regression | Every feature |
| Local kind | Full suite, active-active, HA, failover, observability | Every feature/release |
| AKS | Azure LoadBalancer, ACR, public SIP/RTP, real-device smoke | Release milestones |
| Real device | OBi1022/Zoiper signalling and two-way media | Media/release milestones |

## Documentation Rules

1. The current version appears in the README, canonical runbooks, chart metadata, and release index.
2. A command has one canonical home. Other pages link to it.
3. Release notes are historical and are not rewritten during documentation cleanup.
4. Commands must fail before changing workloads when variables or images are invalid.
5. Local kind regression, AKS regression, Docker regression, and real-device behavior must remain isolated.

Current release: `v3.0.0`.
