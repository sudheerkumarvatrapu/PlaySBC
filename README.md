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

## GitHub Security Scans

GitHub runs security checks on every pushed commit and pull request update:

- CodeQL for Python source.
- Dependency Review for pull requests.
- Trivy filesystem scan for vulnerabilities, secrets, Dockerfiles, Helm, Compose, YAML, and Kubernetes-style config.
- Checkov scan for Docker, Helm, Kubernetes, GitHub Actions, and secret patterns.

Results appear under GitHub Actions and Code Scanning alerts.

## Deployment Models

| Model | Best For | Needs Docker Desktop? | Needs Kubernetes? |
| --- | --- | --- | --- |
| Local regression suite | Development and validation on a laptop | Yes on macOS/Windows, Docker Engine is fine on Linux | No |
| Manual SIPp experiments | Host loopback tests, or hand-run SIPp inside Docker dual-realm agents | No for host loopback, yes for Docker dual realm | No |
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
- `sipp` only for manual SIPp experiments
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

## Model 2: Manual SIPp Experiments

Use this when you want to run your own SIPp command by hand instead of the automated Docker regression suite.

Important difference:

- Standard regression mode does not need host SIPp.
- Manual SIPp mode does need SIPp installed on your host machine.
- Manual mode is best for quick experiments. Use the regression suite for full dual-realm RTPengine, HA, PCAP, ladder, and report evidence.
- The host examples below use `127.0.0.1`. They do not exercise the real core/peer Docker networks.
- For real dual-realm manual SIPp, run SIPp inside the Docker topology agents. Do not bind host SIPp to `172.28.0.10` or `192.168.28.30` on macOS/Windows unless you created those host interfaces yourself.

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

Built-in SIPp scenarios such as `-sn uas` and `-sn uac` can be used for quick SIP parser/transport checks, but B2BUA routing still needs a reachable callee route. Use either REGISTER as shown above or a static route in PlaySBC config.

Example built-in UAS/UAC smoke shape:

```bash
sipp -sn uas -i 127.0.0.1 -p 5070
sipp 127.0.0.1:25062 -sn uac -s 1002 -i 127.0.0.1 -p 5062
```

For media PCAP replay, install SIPp with PCAP/play support and use the repo media scenarios and files under:

```text
sipp/scenarios/*media*.xml
sipp/scenarios/pcap/g711u_60s.pcap
sipp/scenarios/pcap/g711a_60s.pcap
```

The automated regression suite already runs these media paths inside Docker, so host PCAP permissions are not needed for normal testing.

### Docker Dual-Realm Manual SIPp

Use this when you want manual SIPp commands but still want the real PlaySBC topology:

```text
Core realm: SIPp A 172.28.0.10 -> PlaySBC 172.28.0.20 -> RTPengine 172.28.0.40
Peer realm: RTPengine 192.168.28.40 <- PlaySBC 192.168.28.20 <- SIPp B 192.168.28.30
```

The addresses above live inside Docker networks. The host Mac/Windows shell normally cannot bind to them directly.

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

This manual dual-realm example uses the static `peer-b` route from `configs/topology/helm-values.yaml`. Use the automated regression suite when you need the full evidence bundle, PCAP merge, RTCP helper, ladder, and HTML report.

Cleanup:

```bash
docker compose -f docker-compose.topology.yml down --remove-orphans
```

## Model 3: Local Kubernetes With Local Images

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

## Model 4: Kubernetes With Published Images

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

## Model 5: Kubernetes With External RTPengine

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

## Model 6: Maintainer Image Build And Publish

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
