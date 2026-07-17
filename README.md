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
  <img alt="Version" src="https://img.shields.io/badge/-v1.2.2-111827?style=flat-square">
  <img alt="License MIT" src="https://img.shields.io/badge/-MIT%20License-F59E0B?style=flat-square">
</p>

Python SIP/RTP lab for B2BUA routing, G.711 media, transcoding, RTPengine, HA state experiments, AI voice gateway, and SIPp regression across real core and peer realms.

[Evolution plan](docs/EVOLUTION_PLAN.md) | [RTPengine runbook](docs/RTPENGINE_LOCAL.md) | [AI Voice Gateway](docs/AI_VOICE_GATEWAY.md) | [Kubernetes lab](docs/KUBERNETES_LOCAL.md) | [Kubernetes Helm runbook](docs/KUBERNETES_HELM_RUNBOOK.md)

## Status

- Version: `1.2.2`
- License: MIT
- Release: <https://github.com/sudheerkumarvatrapu/PlaySBC/releases/tag/v1.2.2>
- Images: `ghcr.io/sudheerkumarvatrapu/playsbc:1.2.2`, `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.2.2`, `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2.2`, `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2.2`
- Security: CodeQL, Dependency Review, Trivy, and Checkov run in GitHub Actions.

The Helm package contains Kubernetes manifests and configuration. Kubernetes pulls the PlaySBC and RTPengine images at deploy time.

## Deployment Models

| Model | Best For | Needs Docker Desktop? | Needs Kubernetes? |
| --- | --- | --- | --- |
| Local regression suite | Development and validation on a laptop | Yes on macOS/Windows, Docker Engine is fine on Linux | No |
| Manual SIPp experiments | Host loopback tests, or hand-run SIPp inside Docker dual-realm agents | No for host loopback, yes for Docker dual realm | No |
| Local Kubernetes with local images | kind/minikube lab | Yes | Yes |
| Kubernetes with published images | Customer or shared lab cluster | No | Yes |
| Kubernetes with external RTPengine | Existing RTPengine lab | No | Yes |
| Maintainer image build/publish | Project release maintenance | Yes, or GitHub Actions | Optional |

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

Steps:

```bash
cd PlaySBC
docker info
helm version --short

env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py \
  --skip-sipp-smoke \
  --all-b2bua-profiles \
  --timeout 420
```

Host SIPp, host RTPengine, and `sudo` are not required. The suite starts its own PlaySBC, RTPengine, SIPp agents, RTCP helpers, and packet capture containers.

Topology used by the regression:

```text
Core: SIPp A 172.28.0.10 -> PlaySBC 172.28.0.20 -> RTPengine 172.28.0.40
Peer: RTPengine 192.168.28.40 <- PlaySBC 192.168.28.20 <- SIPp B 192.168.28.30
```

Results:

```text
logs/reports/latest.html
logs/b2bua-Regression/<testcase>/
```

Every profile is rendered through Helm with HA enabled. See the generated HTML report for the full pass/fail, ladder, PCAP, SIP, RTP, RTCP, and platform evidence.

</details>

<details>
<summary><strong>Model 2: Manual SIPp Experiments</strong></summary>

Manual mode is for quick experiments. Host loopback uses `127.0.0.1`; real dual-realm manual SIPp runs inside the Docker topology agents.

Install SIPp on the host only for manual mode:

```bash
# macOS
brew install sipp

# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y sipp
```

### Host Loopback Quick Test

Start PlaySBC with the local B2BUA example config:

```bash
python3 mini_call_server.py --config configs/config.b2bua.example.yaml
```

Run a basic manual B2BUA call with the repo SIPp XMLs.

Terminal 1: start SIPp B as UAS:

```bash
sipp -sf sipp/scenarios/b2bua_uas_b.xml \
  -i 127.0.0.1 \
  -p 5070 \
  -m 1 \
  -trace_msg \
  -trace_err
```

Terminal 2: register SIPp B as user `1002` through PlaySBC:

```bash
sipp 127.0.0.1:25062 \
  -sf sipp/scenarios/register_contact.xml \
  -s 1002 \
  -i 127.0.0.1 \
  -p 5072 \
  -key contact_port 5070 \
  -m 1 \
  -trace_msg \
  -trace_err
```

Terminal 3: place the call from SIPp A to registered user `1002` through PlaySBC:

```bash
sipp 127.0.0.1:25062 \
  -sf sipp/scenarios/b2bua_uac_a.xml \
  -s 1002 \
  -key caller 1001 \
  -i 127.0.0.1 \
  -p 5062 \
  -m 1 \
  -r 1 \
  -d 1000 \
  -trace_msg \
  -trace_err
```

Built-in SIPp scenarios such as `-sn uas` and `-sn uac` can be used for quick parser/transport checks. B2BUA routing still needs a REGISTERed callee or static route.

Example built-in UAS/UAC smoke shape:

```bash
sipp -sn uas -i 127.0.0.1 -p 5070
sipp 127.0.0.1:25062 -sn uac -s 1002 -i 127.0.0.1 -p 5062
```

For media PCAP replay, use SIPp with PCAP/play support and these repo assets:

```text
sipp/scenarios/*media*.xml
sipp/scenarios/pcap/g711u_60s.pcap
sipp/scenarios/pcap/g711a_60s.pcap
```

The automated regression suite runs media paths inside Docker, so host PCAP permissions are not needed for normal testing.

### Docker Dual-Realm Manual SIPp

Use this when you want manual SIPp commands but still want the real PlaySBC topology:

```text
Core realm: SIPp A 172.28.0.10 -> PlaySBC 172.28.0.20 -> RTPengine 172.28.0.40
Peer realm: RTPengine 192.168.28.40 <- PlaySBC 192.168.28.20 <- SIPp B 192.168.28.30
```

These addresses live inside Docker networks; the host shell normally cannot bind to them directly.

Bootstrap the topology:

```bash
export PLAYSBC_TOPOLOGY_OUTPUT=/tmp/playsbc-manual-dual-realm
rm -rf "$PLAYSBC_TOPOLOGY_OUTPUT"
mkdir -p "$PLAYSBC_TOPOLOGY_OUTPUT/work/sipp-a-uac" "$PLAYSBC_TOPOLOGY_OUTPUT/work/sipp-b-uas"

python3 - <<'PY'
from pathlib import Path
import os
import subprocess
from tools.run_b2bua_sipp_smoke import extract_helm_server_yaml

rendered = subprocess.run(
    [
        "helm",
        "template",
        "playsbc-topology",
        "charts/playsbc",
        "-f",
        "configs/topology/helm-values.yaml",
        "--show-only",
        "templates/configmap.yaml",
    ],
    text=True,
    capture_output=True,
    check=True,
)
output = Path(os.environ["PLAYSBC_TOPOLOGY_OUTPUT"])
(output / "server-config.yaml").write_text(extract_helm_server_yaml(rendered.stdout), encoding="utf-8")
PY

export PLAYSBC_TOPOLOGY_CONFIG="$PLAYSBC_TOPOLOGY_OUTPUT/server-config.yaml"
docker compose -f docker-compose.topology.yml build rtpengine playsbc sipp-a
docker compose -f docker-compose.topology.yml up -d rtpengine playsbc core-agent peer-agent
```

Terminal 1: start SIPp B in the peer realm:

```bash
docker compose -f docker-compose.topology.yml exec peer-agent sh -lc '
  cd /output/work/sipp-b-uas &&
  sipp -sf /scenarios/topology/uas_peer_pcma.xml \
    -s peer-b \
    -i 192.168.28.30 \
    -mi 192.168.28.30 \
    -p 5060 \
    -m 1 \
    -d 60000 \
    -trace_msg \
    -trace_err \
    -trace_stat \
    -nostdin \
    -timeout 180 \
    -timeout_error
'
```

Terminal 2: place the call from SIPp A in the core realm:

```bash
docker compose -f docker-compose.topology.yml exec core-agent sh -lc '
  cd /output/work/sipp-a-uac &&
  sipp 172.28.0.20:5060 \
    -sf /scenarios/topology/uac_core_pcmu.xml \
    -s peer-b \
    -key caller core-a \
    -i 172.28.0.10 \
    -mi 172.28.0.10 \
    -p 5060 \
    -m 1 \
    -r 1 \
    -d 60000 \
    -trace_msg \
    -trace_err \
    -trace_stat \
    -nostdin \
    -timeout 180 \
    -timeout_error
'
```

This example uses the static `peer-b` route from `configs/topology/helm-values.yaml`. Use automated regression for the full evidence bundle.

Cleanup:

```bash
docker compose -f docker-compose.topology.yml down --remove-orphans
```

</details>

<details>
<summary><strong>Model 3: Local Kubernetes With Local Images</strong></summary>

Requirements:

- Docker Desktop or Docker Engine
- Helm
- `kubectl`
- `kind` or `minikube`

kind steps:

```bash
cd PlaySBC
kind create cluster --name playsbc

docker build -f docker/playsbc.Dockerfile -t playsbc:1.2.2 .
docker build -f docker/rtpengine.Dockerfile -t playsbc-rtpengine:1.2.2 .
kind load docker-image playsbc:1.2.2 playsbc-rtpengine:1.2.2 --name playsbc

helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=playsbc \
  --set-string image.tag=1.2.2 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.2.2

kubectl -n playsbc rollout status deployment/playsbc-playsbc
kubectl -n playsbc get pods,services
```

minikube steps:

```bash
cd PlaySBC
minikube start
eval $(minikube docker-env)

docker build -f docker/playsbc.Dockerfile -t playsbc:1.2.2 .
docker build -f docker/rtpengine.Dockerfile -t playsbc-rtpengine:1.2.2 .

helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=playsbc \
  --set-string image.tag=1.2.2 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.2.2

kubectl -n playsbc rollout status deployment/playsbc-playsbc
minikube service -n playsbc playsbc-playsbc --url
```

Health and full Kubernetes regression:

```bash
kubectl -n playsbc port-forward service/playsbc-playsbc 8080:8080
curl http://127.0.0.1:8080/readyz
curl http://127.0.0.1:8080/metrics

PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images
```

The in-cluster Job runner creates temporary SIPp core/peer pods in the `playsbc` namespace, runs the full B2BUA/RTPengine/ESBC/HA/AI catalog, restores Helm values, and writes `logs/k8s-job/<run-id>/k8s-reports/latest.html`. Full Kubernetes commands are in [docs/KUBERNETES_HELM_RUNBOOK.md](docs/KUBERNETES_HELM_RUNBOOK.md).

Optional real Rasa lab:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --rasa-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images
```

The regular full suite uses the existing `logs/k8s-job` layout. Rasa-only mode deletes old `logs/RASA-Regression` output and writes `logs/RASA-Regression/<run-id>/RASA-reports/latest.html`. It runs mock AI, real Rasa, Vosk/Piper speech, Whisper STT, Coqui TTS, streaming response, contact-center bot, and real Rasa chat/NLU verifier profiles; see [docs/AI_VOICE_GATEWAY.md](docs/AI_VOICE_GATEWAY.md).

Cleanup:

```bash
helm uninstall playsbc --namespace playsbc
kind delete cluster --name playsbc
```

For minikube cleanup, use `minikube delete` if the cluster is only for this lab.

</details>

<details>
<summary><strong>Model 4: Kubernetes With Published Images</strong></summary>

Requirements:

- Kubernetes cluster access
- `kubectl`
- Helm
- Network access from the cluster to GitHub Container Registry

Docker Desktop is not required for this model.

Deploy directly from the GitHub release chart:

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.2.2/playsbc-1.2.2.tgz \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.2.2 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.2.2
```

Verify:

```bash
kubectl -n playsbc rollout status deployment/playsbc-playsbc
kubectl -n playsbc rollout status deployment/playsbc-playsbc-rtpengine
kubectl -n playsbc get pods,services
kubectl -n playsbc logs deployment/playsbc-playsbc --tail=100
```

Health check:

```bash
kubectl -n playsbc port-forward service/playsbc-playsbc 8080:8080
curl http://127.0.0.1:8080/readyz
curl http://127.0.0.1:8080/metrics
```

If GHCR packages are private, run this before the Helm install. Use a GitHub token with package read access:

```bash
kubectl create namespace playsbc --dry-run=client -o yaml | kubectl apply -f -
kubectl -n playsbc create secret docker-registry ghcr-pull-secret \
  --docker-server=ghcr.io \
  --docker-username=<github-user> \
  --docker-password=<github-token>
kubectl -n playsbc patch serviceaccount default \
  -p '{"imagePullSecrets":[{"name":"ghcr-pull-secret"}]}'
```

If the GHCR packages are public, no image pull secret is required.

</details>

<details>
<summary><strong>Model 5: Kubernetes With External RTPengine</strong></summary>

Requirements:

- Kubernetes cluster access
- Existing RTPengine reachable from PlaySBC
- RTPengine NG control URL, for example `udp://rtpengine.example.net:2223`

Deploy PlaySBC only and point it to the external RTPengine:

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.2.2/playsbc-1.2.2.tgz \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.2.2 \
  --set rtpengine.enabled=false \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://rtpengine.example.net:2223
```

Verify:

```bash
kubectl -n playsbc rollout status deployment/playsbc-playsbc
kubectl -n playsbc logs deployment/playsbc-playsbc --tail=100
```

</details>

<details>
<summary><strong>Model 6: Maintainer Image Build And Publish</strong></summary>

Local build:

```bash
docker build -f docker/playsbc.Dockerfile -t ghcr.io/sudheerkumarvatrapu/playsbc:1.2.2 .
docker build -f docker/rtpengine.Dockerfile -t ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.2.2 .
docker build -f docker/k8s-regression-runner.Dockerfile -t ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2.2 .
docker build -f docker/sipp.Dockerfile -t ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2.2 .
```

GitHub Actions publishes images automatically when `main` or a `v*` tag is pushed. The `v1.2.2` tag publishes the `1.2.2` and `1.2` image tags.

For chart values and Kubernetes operations, use [docs/KUBERNETES_HELM_RUNBOOK.md](docs/KUBERNETES_HELM_RUNBOOK.md).

</details>

## Uninstall

Remove PlaySBC from Kubernetes:

```bash
helm uninstall playsbc --namespace playsbc
kubectl delete namespace playsbc
```

## Contributor

[Sudheer Kumar Vatrapu](https://github.com/sudheerkumarvatrapu)
