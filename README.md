<p align="center">
  <img src="docs/assets/playsbc-logo.svg?raw=1" alt="PlaySBC logo" width="620">
</p>

<h1 align="center">PlaySBC</h1>

<p align="center"><strong>Playful Session Border Controller: break SIP here, not in production.</strong></p>

<p align="center">
  <img alt="Python 3.x" src="https://img.shields.io/badge/-Python%203.x-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="SIPp Regression" src="https://img.shields.io/badge/-SIPp%20Regression-16A34A?style=flat-square">
  <img alt="B2BUA" src="https://img.shields.io/badge/-B2BUA-2563EB?style=flat-square">
  <img alt="G711 Transcoding" src="https://img.shields.io/badge/-G711u%20%7C%20G711a-9333EA?style=flat-square">
  <img alt="RTPengine" src="https://img.shields.io/badge/-RTPengine-0F766E?style=flat-square">
  <img alt="AI Rasa" src="https://img.shields.io/badge/-AI%20Rasa%20Gateway-BE185D?style=flat-square">
  <img alt="Version" src="https://img.shields.io/badge/-v1.3.0-111827?style=flat-square">
  <img alt="License MIT" src="https://img.shields.io/badge/-MIT%20License-F59E0B?style=flat-square">
</p>

Python SIP/RTP lab for B2BUA routing, G.711 media, transcoding, RTPengine, HA state experiments, AI voice gateway, and SIPp regression across real core and peer realms.

[Evolution plan](docs/EVOLUTION_PLAN.md) | [RTPengine runbook](docs/RTPENGINE_LOCAL.md) | [AI Voice Gateway](docs/AI_VOICE_GATEWAY.md) | [Observability](docs/OBSERVABILITY.md) | [Kubernetes lab](docs/KUBERNETES_LOCAL.md) | [Kubernetes Helm runbook](docs/KUBERNETES_HELM_RUNBOOK.md)

## Status

- Version: `1.3.0`
- License: MIT
- Release: <https://github.com/sudheerkumarvatrapu/PlaySBC/releases/tag/v1.3.0>
- Images: `ghcr.io/sudheerkumarvatrapu/playsbc:1.3.0`, `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.3.0`, `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.3.0`, `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.3.0`
- Security: CodeQL, Dependency Review, Trivy, and Checkov run in GitHub Actions.

The Helm package contains Kubernetes manifests and configuration. Kubernetes pulls the PlaySBC and RTPengine images at deploy time.

## Deployment Models

| Model | Best for | Main command |
| --- | --- | --- |
| Local Docker regression | Laptop validation with real dual-realm Docker networks | `tools/run_regression_suite.py --all-b2bua-profiles` |
| Manual SIPp | Fast parser/B2BUA experiments | `mini_call_server.py` plus repo SIPp XMLs |
| Local Kubernetes | kind/minikube lab with local images | `tools/run_k8s_regression_job.py --all-profiles` |
| Kubernetes release install | Shared/customer lab cluster | `helm upgrade --install ... playsbc-1.3.0.tgz` |
| AI/Rasa focused lab | Speech, chat/NLU, contact-center bot checks | `tools/run_k8s_regression_job.py --rasa-profiles` |
| Observability lab | Prometheus/Grafana for core/peer, RTPengine, AI metrics | `--set observability.enabled=true` |

Detailed commands live in [Kubernetes Helm runbook](docs/KUBERNETES_HELM_RUNBOOK.md), [Observability](docs/OBSERVABILITY.md), [RTPengine](docs/RTPENGINE_LOCAL.md), and [AI Voice Gateway](docs/AI_VOICE_GATEWAY.md).

## Quick Start

```bash
git clone https://github.com/sudheerkumarvatrapu/PlaySBC.git
cd PlaySBC
helm version --short
```

Install only the tools for your path: Docker for local regression/image builds, SIPp for host manual tests, and `kubectl` plus Helm for Kubernetes.

### Local Docker Regression

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py \
  --skip-sipp-smoke \
  --all-b2bua-profiles \
  --timeout 420
```

The suite starts PlaySBC, RTPengine, SIPp agents, RTCP helpers, packet capture, Helm-rendered configs, and HA-enabled dual-realm profiles.

```text
Core: SIPp A 172.28.0.10 -> PlaySBC 172.28.0.20 -> RTPengine 172.28.0.40
Peer: RTPengine 192.168.28.40 <- PlaySBC 192.168.28.20 <- SIPp B 192.168.28.30
```

Results:

```text
logs/reports/latest.html
logs/b2bua-Regression/<testcase>/
```

### Kubernetes Release Install

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.3.0/playsbc-1.3.0.tgz \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.3.0 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.3.0
```

Verify:

```bash
kubectl -n playsbc rollout status deployment/playsbc-playsbc
kubectl -n playsbc rollout status deployment/playsbc-playsbc-rtpengine
kubectl -n playsbc get pods,svc
kubectl -n playsbc port-forward service/playsbc-playsbc 8080:8080
curl http://127.0.0.1:8080/readyz
```

### Kubernetes Regression

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

The in-cluster Job runner creates temporary SIPp core/peer pods in the `playsbc` namespace, applies each profile through Helm, restores Helm values, and writes:

```text
logs/k8s-job/<run-id>/k8s-reports/latest.html
```

Use published release images instead of local builds:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --runner-image ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.3.0 \
  --sipp-image ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.3.0 \
  --playsbc-image ghcr.io/sudheerkumarvatrapu/playsbc:1.3.0 \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --no-load-sipp-image \
  --kind-cluster playsbc
```

### AI/Rasa Regression

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --rasa-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

Rasa-only mode writes:

```text
logs/RASA-Regression/<run-id>/RASA-reports/latest.html
```

### Observability

```bash
helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --create-namespace \
  --reuse-values \
  --set observability.enabled=true \
  --set observability.prometheus.retention=31d

kubectl -n playsbc port-forward svc/playsbc-playsbc-grafana 3000:3000
```

Open `http://127.0.0.1:3000`. Dashboard: `PlaySBC Core/Peer SBC Lab`.

### Manual SIPp

Host loopback manual SIPp uses `127.0.0.1`; real dual-realm manual SIPp should run inside the Docker topology agents because `172.28.x` and `192.168.28.x` live inside Docker networks.

```bash
python3 mini_call_server.py --config configs/config.b2bua.example.yaml
sipp -sf sipp/scenarios/b2bua_uas_b.xml -i 127.0.0.1 -p 5070 -m 1 -trace_msg -trace_err
sipp 127.0.0.1:25062 -sf sipp/scenarios/register_contact.xml -s 1002 -i 127.0.0.1 -p 5072 -key contact_port 5070 -m 1
sipp 127.0.0.1:25062 -sf sipp/scenarios/b2bua_uac_a.xml -s 1002 -key caller 1001 -i 127.0.0.1 -p 5062 -m 1 -r 1 -d 1000
```

For media PCAP replay, use repo assets under `sipp/scenarios/pcap/`. The automated regression suite already handles media permissions inside Docker.

## Uninstall

Remove PlaySBC from Kubernetes:

```bash
helm uninstall playsbc --namespace playsbc
kubectl delete namespace playsbc
```

## Contributor

[Sudheer Kumar Vatrapu](https://github.com/sudheerkumarvatrapu)
