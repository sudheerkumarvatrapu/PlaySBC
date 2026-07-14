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
  <img alt="Version" src="https://img.shields.io/badge/-v1.0.0-111827?style=flat-square">
  <img alt="License MIT" src="https://img.shields.io/badge/-MIT%20License-F59E0B?style=flat-square">
</p>

Python SIP/RTP lab for B2BUA routing, G.711 media, transcoding, RTPengine, HA state experiments, AI voice gateway, and SIPp regression across real core and peer realms.

[Evolution plan](docs/EVOLUTION_PLAN.md) | [RTPengine runbook](docs/RTPENGINE_LOCAL.md) | [AI Voice Gateway](docs/AI_VOICE_GATEWAY.md) | [Kubernetes lab](docs/KUBERNETES_LOCAL.md)

## Release

- Current version: `1.0.0`
- License: MIT
- GitHub release: <https://github.com/sudheerkumarvatrapu/PlaySBC/releases/tag/v1.0.0>
- Helm chart package: `release/helm/playsbc-1.0.0.tgz`
- Published images:
  - `ghcr.io/sudheerkumarvatrapu/playsbc:1.0.0`
  - `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.0.0`

The Helm package contains Kubernetes manifests and PlaySBC configuration. It does not contain Docker image layers. Kubernetes pulls PlaySBC and RTPengine images at deployment time.

## Deployment Models

| Model | Best For | Needs Docker Desktop? | Needs Kubernetes? |
| --- | --- | --- | --- |
| Local regression suite | Development and validation on a laptop | Yes on macOS/Windows, Docker Engine is fine on Linux | No |
| Local Kubernetes with local images | kind/minikube lab | Yes | Yes |
| Kubernetes with published images | Customer or shared lab cluster | No | Yes |
| Kubernetes with external RTPengine | Existing RTPengine lab | No | Yes |
| Maintainer image build/publish | Project release maintenance | Yes, or GitHub Actions | Optional |

## Common Tools

Install the tools for the model you want to run:

- `git`
- `python3`
- `helm`
- `kubectl` for Kubernetes deployments
- Docker Desktop on macOS/Windows, or Docker Engine on Linux, only when building images or running local regression
- `kind` or `minikube` only for local Kubernetes labs

Clone the repo when you want source code, regression tests, or local chart files:

```bash
git clone https://github.com/sudheerkumarvatrapu/PlaySBC.git
cd PlaySBC
helm version --short
```

## Model 1: Local Regression Suite

Use this when you want to run the full PlaySBC SIPp regression on your laptop.

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

Every profile is rendered through Helm with HA enabled. Coverage includes B2BUA signalling, registration, digest auth, DTMF, RTP/RTCP, transcoding, RTPengine, UDP/TCP/TLS, TLS/SRTP interworking, ESBC policies, HA lab checks, AI/Rasa lab paths, negative SIP cases, small load, soak, and 5 cps / 60 second CHT load.

## Model 2: Local Kubernetes With Local Images

Use this when you want to deploy PlaySBC into a local Kubernetes cluster and build images on your machine.

Requirements:

- Docker Desktop or Docker Engine
- Helm
- `kubectl`
- `kind` or `minikube`

kind steps:

```bash
cd PlaySBC
kind create cluster --name playsbc

docker build -f docker/playsbc.Dockerfile -t playsbc:1.0.0 .
docker build -f docker/rtpengine.Dockerfile -t playsbc-rtpengine:1.0.0 .
kind load docker-image playsbc:1.0.0 playsbc-rtpengine:1.0.0 --name playsbc

helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=playsbc \
  --set-string image.tag=1.0.0 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.0.0

kubectl -n playsbc rollout status deployment/playsbc-playsbc
kubectl -n playsbc get pods,services
```

minikube steps:

```bash
cd PlaySBC
minikube start
eval $(minikube docker-env)

docker build -f docker/playsbc.Dockerfile -t playsbc:1.0.0 .
docker build -f docker/rtpengine.Dockerfile -t playsbc-rtpengine:1.0.0 .

helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=playsbc \
  --set-string image.tag=1.0.0 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.0.0

kubectl -n playsbc rollout status deployment/playsbc-playsbc
minikube service -n playsbc playsbc-playsbc --url
```

Health check:

```bash
kubectl -n playsbc port-forward service/playsbc-playsbc 8080:8080
curl http://127.0.0.1:8080/readyz
curl http://127.0.0.1:8080/metrics
```

Cleanup:

```bash
helm uninstall playsbc --namespace playsbc
kind delete cluster --name playsbc
```

For minikube cleanup, use `minikube delete` if the cluster is only for this lab.

## Model 3: Kubernetes With Published Images

Use this when a customer or teammate has a Kubernetes cluster and does not want to build anything locally.

Requirements:

- Kubernetes cluster access
- `kubectl`
- Helm
- Network access from the cluster to GitHub Container Registry

Docker Desktop is not required for this model.

Deploy directly from the GitHub release chart:

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.0.0/playsbc-1.0.0.tgz \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.0.0 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.0.0
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

## Model 4: Kubernetes With External RTPengine

Use this when RTPengine already runs outside the chart, for example in an existing SIP lab.

Requirements:

- Kubernetes cluster access
- Existing RTPengine reachable from PlaySBC
- RTPengine NG control URL, for example `udp://rtpengine.example.net:2223`

Deploy PlaySBC only and point it to the external RTPengine:

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.0.0/playsbc-1.0.0.tgz \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.0.0 \
  --set rtpengine.enabled=false \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://rtpengine.example.net:2223
```

Verify:

```bash
kubectl -n playsbc rollout status deployment/playsbc-playsbc
kubectl -n playsbc logs deployment/playsbc-playsbc --tail=100
```

## Model 5: Maintainer Image Build And Publish

Use this only when maintaining the PlaySBC release images.

Local build:

```bash
docker build -f docker/playsbc.Dockerfile -t ghcr.io/sudheerkumarvatrapu/playsbc:1.0.0 .
docker build -f docker/rtpengine.Dockerfile -t ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.0.0 .
```

GitHub Actions publishes images automatically when `main` or a `v*` tag is pushed. The `v1.0.0` tag publishes the `1.0.0` and `1.0` image tags.

## Common Helm Values

Important values:

```yaml
image:
  repository: ghcr.io/sudheerkumarvatrapu/playsbc
  tag: "1.0.0"

rtpengine:
  enabled: true
  image:
    repository: ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine
    tag: "1.0.0"

playsbc:
  config:
    sip_transport: udp
    default_codec: PCMU
    media_backend: rtpengine
    rtpengine_url: udp://playsbc-playsbc-rtpengine:2223
```

For real credentials, use a private values file or a Kubernetes Secret. Do not commit production passwords.

## Uninstall

Remove PlaySBC from Kubernetes:

```bash
helm uninstall playsbc --namespace playsbc
kubectl delete namespace playsbc
```

## Contributor

[Sudheer Kumar Vatrapu](https://github.com/sudheerkumarvatrapu)
