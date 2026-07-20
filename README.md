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
  <img alt="Version" src="https://img.shields.io/badge/-v1.4.1-111827?style=flat-square">
  <img alt="License MIT" src="https://img.shields.io/badge/-MIT%20License-F59E0B?style=flat-square">
</p>

Python SIP/RTP lab for B2BUA routing, G.711 media, transcoding, RTPengine, HA state experiments, AI voice gateway, observability, and SIPp regression across core and peer realms.

Kubernetes regression now defaults to an active-active PlaySBC/RTPengine lab topology with logical core and peer realms; Multus wiring is available when a cluster has multi-network CNI installed.

[Evolution plan](docs/EVOLUTION_PLAN.md) | [RTPengine runbook](docs/RTPENGINE_LOCAL.md) | [AI Voice Gateway](docs/AI_VOICE_GATEWAY.md) | [Observability](docs/OBSERVABILITY.md) | [Kubernetes lab](docs/KUBERNETES_LOCAL.md) | [Kubernetes Helm runbook](docs/KUBERNETES_HELM_RUNBOOK.md)

## Status

- Version: `1.4.1`
- License: MIT
- Release: <https://github.com/sudheerkumarvatrapu/PlaySBC/releases/tag/v1.4.1>
- Images: `ghcr.io/sudheerkumarvatrapu/playsbc:1.4.1`, `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.4.1`, `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.4.1`, `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.4.1`
- Security: CodeQL, Dependency Review, Trivy, and Checkov run in GitHub Actions.

The Helm package contains Kubernetes manifests and configuration. Kubernetes pulls the PlaySBC, RTPengine, SIPp, and regression-runner images at deploy/test time.

## Standard Kubernetes Architecture

Going forward, the normal Kubernetes lab and regression path is always:

```text
PlaySBC active-active StatefulSet: 2 pods
RTPengine active-active StatefulSet: 2 pods
Prometheus: 1 pod
Grafana: 1 pod
```

Always deploy with `configs/kubernetes/active-active-values.yaml` or equivalent `--set topology.activeActive.enabled=true` overrides. If `kubectl get pods` shows only one PlaySBC pod and one RTPengine pod, the chart is running in single-replica mode and should be upgraded again with active-active enabled.

## Deployment Models

| Model | Best For | Needs Docker Desktop? | Needs Kubernetes? |
| --- | --- | --- | --- |
| Local regression suite | Development and validation on a laptop | Yes on macOS/Windows, Docker Engine is fine on Linux | No |
| Manual SIPp experiments | Host loopback tests and quick SIP parser checks | No for host loopback | No |
| Local Kubernetes with local images | kind/minikube lab before publishing images | Yes | Yes |
| Kubernetes with published images | Customer/shared lab cluster | No | Yes |
| Observability lab | Prometheus/Grafana for PlaySBC, RTPengine, and AI gateway evidence | No, if images are published | Yes |
| Maintainer release flow | Build/publish images and Helm releases | Yes locally, or GitHub Actions | Optional |

## Quick Start

```bash
git clone https://github.com/sudheerkumarvatrapu/PlaySBC.git
cd PlaySBC
helm version --short
```

Install only the tools required by your model: Docker for local regression/image builds, SIPp for host manual tests, and `kubectl` plus Helm for Kubernetes.

<details>
<summary><strong>Model 1: Local Regression Suite</strong></summary>

Requirements:

- Docker Desktop on macOS/Windows, or Docker Engine with Compose on Linux
- Python 3
- Helm

Run:

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py \
  --skip-sipp-smoke \
  --all-b2bua-profiles \
  --timeout 420
```

Host SIPp, host RTPengine, and `sudo` are not required. The suite starts its own PlaySBC, RTPengine, SIPp agents, RTCP helpers, and packet capture containers.

Topology used by local Docker regression:

```text
Core: SIPp A 172.28.0.10 -> PlaySBC 172.28.0.20 -> RTPengine 172.28.0.40
Peer: RTPengine 192.168.28.40 <- PlaySBC 192.168.28.20 <- SIPp B 192.168.28.30
```

Results:

```text
logs/reports/latest.html
logs/b2bua-Regression/<testcase>/
```

</details>

<details>
<summary><strong>Model 2: Manual SIPp Experiments</strong></summary>

Manual mode is for quick experiments. Host loopback uses `127.0.0.1`; real dual-realm manual SIPp should run inside the Docker topology agents because `172.28.x` and `192.168.28.x` live inside Docker networks.

Install SIPp on the host only for manual mode:

```bash
# macOS
brew install sipp

# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y sipp
```

Start PlaySBC:

```bash
python3 mini_call_server.py --config configs/config.b2bua.example.yaml
```

Terminal 1: start SIPp B as UAS:

```bash
sipp -sf sipp/scenarios/b2bua_uas_b.xml -i 127.0.0.1 -p 5070 -m 1 -trace_msg -trace_err
```

Terminal 2: register SIPp B as user `1002`:

```bash
sipp 127.0.0.1:25062 -sf sipp/scenarios/register_contact.xml -s 1002 -i 127.0.0.1 -p 5072 -key contact_port 5070 -m 1
```

Terminal 3: place the call from SIPp A:

```bash
sipp 127.0.0.1:25062 -sf sipp/scenarios/b2bua_uac_a.xml -s 1002 -key caller 1001 -i 127.0.0.1 -p 5062 -m 1 -r 1 -d 1000
```

Built-in SIPp scenarios such as `-sn uas` and `-sn uac` can be used for parser/transport smoke checks. B2BUA routing still needs a REGISTERed callee or static route.

Media assets live under:

```text
sipp/scenarios/pcap/
```

</details>

<details>
<summary><strong>Model 3: Local Kubernetes With Local Images</strong></summary>

Use this when you are changing source locally and want kind/minikube to run your local images.

Requirements:

- Docker Desktop or Docker Engine
- Helm
- `kubectl`
- `kind` or `minikube`

Main command shape:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

Full cluster creation, Helm install, rollout, and debug steps are in [docs/KUBERNETES_HELM_RUNBOOK.md](docs/KUBERNETES_HELM_RUNBOOK.md).

</details>

<details>
<summary><strong>Model 4: Kubernetes With Published Images</strong></summary>

Use this for the normal release path. Docker Desktop is not required if the cluster can pull from GitHub Container Registry.

The current release chart:

```text
https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.4.1/playsbc-1.4.1.tgz
```

Published images:

```text
ghcr.io/sudheerkumarvatrapu/playsbc:1.4.1
ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.4.1
ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.4.1
ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.4.1
```

The standard process is:

1. Upgrade PlaySBC and RTPengine to `v1.4.1`.
2. Enable observability.
3. Wait for PlaySBC, RTPengine, Prometheus, and Grafana rollouts.
4. Run the full Kubernetes regression catalog with release images.

The exact copy/paste command is in [docs/KUBERNETES_HELM_RUNBOOK.md](docs/KUBERNETES_HELM_RUNBOOK.md#standard-full-regression-flow).

</details>

<details>
<summary><strong>Model 5: Observability Lab</strong></summary>

PlaySBC can deploy Prometheus and Grafana in the same namespace.

Dashboard:

```text
PlaySBC Core/Peer SBC Lab
```

The dashboard tracks:

- current active calls
- calls and completed calls in selected range
- SIP requests and responses
- RTPengine sessions and control failures
- codec negotiation and transcoding
- AI/Rasa STT/TTS turns

Open Grafana:

```bash
kubectl -n playsbc port-forward svc/playsbc-playsbc-grafana 3000:3000
```

Then open:

```text
http://127.0.0.1:3000
```

Full observability notes are in [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).

</details>

<details>
<summary><strong>Model 6: Maintainer Release Flow</strong></summary>

GitHub Actions publishes images automatically when `main` or a `v*` tag is pushed.

The `v1.4.1` tag publishes:

```text
1.4.1
1.3
latest
```

Images:

```text
ghcr.io/sudheerkumarvatrapu/playsbc
ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine
ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression
ghcr.io/sudheerkumarvatrapu/playsbc-sipp
```

Security scans run through GitHub Actions. Release notes and chart assets are published on the GitHub release page.

</details>

## Uninstall

Remove PlaySBC from Kubernetes:

```bash
helm uninstall playsbc --namespace playsbc
kubectl delete namespace playsbc
```

## Contributor

[Sudheer Kumar Vatrapu](https://github.com/sudheerkumarvatrapu)
