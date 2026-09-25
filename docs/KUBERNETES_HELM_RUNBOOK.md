# PlaySBC Kubernetes And Helm Runbook

This is the canonical local Kubernetes command guide. The three principal
workflows below are intentionally self-contained: public v4.0.0 release,
current public source. Pick one workflow and run
its complete code block in one terminal; do not combine variables between
tracks. Use [KUBERNETES_LOCAL.md](KUBERNETES_LOCAL.md) for topology and
networking concepts.

## Standard Lab

```text
PlaySBC-0 + RTPengine-0
PlaySBC-1 + RTPengine-1
Prometheus + Grafana
Regression Job + temporary SIPp core/peer pods
```

| Service | Ports |
| --- | --- |
| PlaySBC | `5062/UDP`, `5062/TCP`, `5061/TCP`, `8080/TCP` |
| RTPengine control | `2223/UDP` |
| Grafana | `3000/TCP` |
| Prometheus | `9090/TCP` |

## Prerequisites

```bash
open -a Docker

until docker info >/dev/null 2>&1; do
  echo "Waiting for Docker Desktop..."
  sleep 5
done

docker info
kubectl version --client
helm version --short
kind version
```

kind uses Docker containers as Kubernetes nodes. Docker Desktop must remain running while the local PlaySBC lab or regression is running. Minikube is a separate compatibility lane and is not required for the canonical `kind-playsbc` workflow.

Create the default cluster once:

```bash
kind create cluster --name playsbc
kubectl config use-context kind-playsbc
kubectl config set-context --current --namespace=playsbc
```

## Resume The Local Lab

After a Mac reboot or Docker Desktop shutdown, start Docker and reuse the existing kind cluster:

```bash
open -a Docker

until docker info >/dev/null 2>&1; do
  echo "Waiting for Docker Desktop..."
  sleep 5
done

kind get clusters
docker ps -a --filter label=io.x-k8s.kind.cluster

kubectl config use-context kind-playsbc
kubectl config set-context --current --namespace=playsbc
kubectl get nodes
kubectl get pods -n playsbc
```

If `playsbc-control-plane` exists but is stopped, resume it and verify the API:

```bash
docker start playsbc-control-plane
kubectl cluster-info --context kind-playsbc
kubectl get pods -n playsbc
```

An error such as `127.0.0.1:<port>: connect: connection refused` means the kubeconfig context exists but the local kind API container is unavailable. Start Docker Desktop first. Recreate the cluster only when `kind get clusters` does not list `playsbc`:

```bash
kind create cluster --name playsbc --wait 180s
kubectl config use-context kind-playsbc
kubectl create namespace playsbc --dry-run=client -o yaml | kubectl apply -f -
kubectl config set-context --current --namespace=playsbc
```

Do not run `kind delete cluster` as a restart step; deletion removes the local Kubernetes workloads and requires a fresh Helm deployment.

## Dedicated Local Real-Device Lab

This lane is separate from both `kind-playsbc` regression and AKS:

| Purpose | Cluster | Context | Topology |
| --- | --- | --- | --- |
| Full local regression | `playsbc` | `kind-playsbc` | Active-active |
| LAN OBi/Zoiper calls | `playsbc-real-device` | `kind-playsbc-real-device` | One PlaySBC + one RTPengine |
| Azure validation | `playsbc-aks` | AKS context | Azure values and LoadBalancers |

Start Docker, discover the Mac LAN address, and create the dedicated cluster once. The port mappings are fixed when kind creates the node, so an older cluster with the same name must be recreated.

```bash
cd /Users/sudheerkumar/Documents/Codex/2026-05-18/Mini-Call-Server

export PLAYSBC_VERSION=4.0.0
export REAL_DEVICE_CLUSTER=playsbc-real-device
export REAL_DEVICE_CONTEXT=kind-playsbc-real-device
export LAN_IF=$(route -n get default | awk '/interface:/{print $2; exit}')
export LAN_IP=$(ipconfig getifaddr "$LAN_IF")
: "${LAN_IP:?Could not determine the Mac LAN IPv4 address}"

open -a Docker
until docker info >/dev/null 2>&1; do sleep 5; done

if ! kind get clusters | grep -qx "$REAL_DEVICE_CLUSTER"; then
  kind create cluster \
    --name "$REAL_DEVICE_CLUSTER" \
    --config configs/kubernetes/kind-real-device-cluster.yaml \
    --wait 180s
fi

kubectl --context "$REAL_DEVICE_CONTEXT" create namespace playsbc \
  --dry-run=client -o yaml | kubectl --context "$REAL_DEVICE_CONTEXT" apply -f -
```

Create a short-lived lab TLS secret. UDP and TCP calls do not require the phone to trust this certificate; a hardphone TLS test must import or trust the generated CA/certificate.

```bash
TLS_DIR=$(mktemp -d)
cat >"$TLS_DIR/openssl.cnf" <<EOF
[req]
distinguished_name=dn
x509_extensions=ext
prompt=no
[dn]
CN=$LAN_IP
[ext]
subjectAltName=IP:$LAN_IP,DNS:playsbc.local
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EOF

openssl req -x509 -newkey rsa:2048 -nodes -days 30 -sha256 \
  -config "$TLS_DIR/openssl.cnf" \
  -keyout "$TLS_DIR/tls.key" \
  -out "$TLS_DIR/tls.crt"

kubectl --context "$REAL_DEVICE_CONTEXT" -n playsbc create secret tls playsbc-real-device-tls \
  --cert "$TLS_DIR/tls.crt" \
  --key "$TLS_DIR/tls.key" \
  --dry-run=client -o yaml \
  | kubectl --context "$REAL_DEVICE_CONTEXT" apply -f -
```

The local lab disables peer certificate verification, so this standard TLS Secret only needs
`tls.crt` and `tls.key`. A `ca.crt` entry is required only when
`playsbc.config.tls_verify_peer=true`.

Install the isolated values profile and advertise the Mac LAN IP on both signalling and media:

The local profile uses a `Recreate` rollout because both revisions cannot own the same fixed host
ports on one kind node. A short signalling interruption during an upgrade is expected; Helm waits
for the replacement pod and avoids a host-port scheduling deadlock.

```bash
helm upgrade --install playsbc \
  "https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v${PLAYSBC_VERSION}/playsbc-${PLAYSBC_VERSION}.tgz" \
  --kube-context "$REAL_DEVICE_CONTEXT" \
  --namespace playsbc \
  --create-namespace \
  --atomic --wait --timeout 10m \
  -f configs/kubernetes/kind-real-device-values.yaml \
  --set-string localRealDevice.lanIPv4="$LAN_IP" \
  --set-string playsbc.config.sip_advertised_ip="$LAN_IP" \
  --set-string playsbc.config.b2bua_advertised_ip="$LAN_IP" \
  --set-string rtpengine.advertisedIP="$LAN_IP" \
  --set-string tls.existingSecret=playsbc-real-device-tls \
  --set-string image.tag="$PLAYSBC_VERSION" \
  --set-string rtpengine.image.tag="$PLAYSBC_VERSION"

kubectl --context "$REAL_DEVICE_CONTEXT" -n playsbc rollout status \
  deployment/playsbc-playsbc --timeout=240s
kubectl --context "$REAL_DEVICE_CONTEXT" -n playsbc rollout status \
  deployment/playsbc-playsbc-rtpengine --timeout=240s

python3 tools/check_kind_real_device_lab.py \
  --context "$REAL_DEVICE_CONTEXT" \
  --cluster "$REAL_DEVICE_CLUSTER" \
  --lan-ip "$LAN_IP" \
  --expected-version "$PLAYSBC_VERSION"
```

Configure both devices with `$LAN_IP`, SIP port `5062`, users `1001` and `1002`, and password `secret-password`. Monitor and capture without changing the current kube context:

```bash
kubectl --context "$REAL_DEVICE_CONTEXT" -n playsbc logs -f \
  -l app.kubernetes.io/instance=playsbc \
  --all-containers=true --prefix --max-log-requests=10 --since=10m \
  | grep -aE 'REGISTER|SIP (INVITE|ACK|BYE|CANCEL)|SIP TX response|SIP response|SDP SUMMARY|RTPENGINE|RTP packet|RTCP|1001|1002'

PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_real_device_capture.py \
  --context "$REAL_DEVICE_CONTEXT" \
  --namespace playsbc \
  --duration 120 \
  --capture-image nicolaka/netshoot:latest
```

The capture produces one combined `capture.pcap`, one `sipmsg.log`, an HTML report, and one `.tgz`. Stop this lane without touching `kind-playsbc` or AKS:

```bash
helm --kube-context "$REAL_DEVICE_CONTEXT" -n playsbc uninstall playsbc
kind delete cluster --name "$REAL_DEVICE_CLUSTER"
```

## Public Source Build, Package, Upgrade, And Full Regression

Use this before a public release is published. It builds the current public
SBC and RTPengine sources, packages the chart, upgrades every product pod, and
starts all 78 full-regression profiles. It does not publish images or a tag.

```bash
set -euo pipefail
cd /Users/sudheerkumar/Documents/Codex/2026-05-18/Mini-Call-Server

git switch main
git pull --ff-only origin main
test -z "$(git status --porcelain)"

export KUBE_CONTEXT=kind-playsbc
export KIND_CLUSTER=playsbc
export SOURCE_TAG=$(git rev-parse --short=12 HEAD)
export PLAYSBC_REPOSITORY=playsbc-public-dev
export RTPENGINE_REPOSITORY=playsbc-rtpengine-public-dev
export PLAYSBC_IMAGE="${PLAYSBC_REPOSITORY}:${SOURCE_TAG}"
export RTPENGINE_IMAGE="${RTPENGINE_REPOSITORY}:${SOURCE_TAG}"
export PACKAGE_DIR=$(mktemp -d /private/tmp/playsbc-public-package.XXXXXX)
export CHART_VERSION=$(awk '/^version:/ {print $2; exit}' charts/playsbc/Chart.yaml)
export PLAYSBC_CHART="${PACKAGE_DIR}/playsbc-${CHART_VERSION}.tgz"

open -a Docker
until docker info >/dev/null 2>&1; do echo "Waiting for Docker Desktop..."; sleep 5; done
if ! kind get clusters | grep -qx "$KIND_CLUSTER"; then
  kind create cluster --name "$KIND_CLUSTER" --wait 180s
fi
kubectl --context "$KUBE_CONTEXT" get --raw=/readyz
kubectl --context "$KUBE_CONTEXT" create namespace playsbc --dry-run=client -o yaml \
  | kubectl --context "$KUBE_CONTEXT" apply -f -

docker build -f docker/playsbc.Dockerfile -t "$PLAYSBC_IMAGE" .
docker build -f docker/rtpengine.Dockerfile -t "$RTPENGINE_IMAGE" .
kind load docker-image "$PLAYSBC_IMAGE" "$RTPENGINE_IMAGE" --name "$KIND_CLUSTER"

helm lint charts/playsbc
helm package charts/playsbc --destination "$PACKAGE_DIR"
test -s "$PLAYSBC_CHART"

helm upgrade --install playsbc "$PLAYSBC_CHART" \
  --kube-context "$KUBE_CONTEXT" --namespace playsbc --create-namespace \
  --reuse-values --atomic --wait --timeout 10m \
  -f configs/kubernetes/active-active-values.yaml \
  --set image.repository="$PLAYSBC_REPOSITORY" \
  --set-string image.tag="$SOURCE_TAG" --set image.pullPolicy=IfNotPresent \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository="$RTPENGINE_REPOSITORY" \
  --set-string rtpengine.image.tag="$SOURCE_TAG" \
  --set rtpengine.image.pullPolicy=IfNotPresent \
  --set rtpengine.hostNetwork=false \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://playsbc-playsbc-rtpengine:2223 \
  --set observability.enabled=true \
  --set observability.prometheus.retention=31d \
  --set observability.prometheus.persistence.size=5Gi \
  --set observability.grafana.persistence.size=2Gi

kubectl --context "$KUBE_CONTEXT" -n playsbc rollout restart \
  statefulset/playsbc-playsbc statefulset/playsbc-playsbc-rtpengine \
  deployment/playsbc-playsbc-prometheus deployment/playsbc-playsbc-grafana
for WORKLOAD in \
  statefulset/playsbc-playsbc \
  statefulset/playsbc-playsbc-rtpengine \
  deployment/playsbc-playsbc-prometheus \
  deployment/playsbc-playsbc-grafana; do
  kubectl --context "$KUBE_CONTEXT" -n playsbc rollout status "$WORKLOAD" --timeout=240s
done
kubectl --context "$KUBE_CONTEXT" -n playsbc get pods -o wide

PYTHONPYCACHEPREFIX=/private/tmp/playsbc-public-pycache \
python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --playsbc-image "$PLAYSBC_IMAGE" --rtpengine-image "$RTPENGINE_IMAGE" \
  --set-playsbc-image --set-rtpengine-image \
  --no-load-playsbc-image --no-load-rtpengine-image \
  --build-runner-image --build-sipp-image --kind-load-images \
  --kind-cluster "$KIND_CLUSTER"
```

The final command launches the 78-profile suite. The three smoke profiles are
a separate build gate and are not included in that count.

## Public v4.0.0 Release Upgrade And Full Regression

Run this complete block from the public repository on the Mac after the
v4.0.0 chart and all four images have been published. It upgrades PlaySBC,
RTPengine, Prometheus, and Grafana, verifies them, and launches all 78 profiles.

```bash
cd /Users/sudheerkumar/Documents/Codex/2026-05-18/Mini-Call-Server

export PLAYSBC_VERSION=4.0.0

kubectl config use-context kind-playsbc
kubectl config set-context --current --namespace=playsbc

helm upgrade --install playsbc \
  "https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v${PLAYSBC_VERSION}/playsbc-${PLAYSBC_VERSION}.tgz" \
  --namespace playsbc \
  --create-namespace \
  --atomic \
  --wait \
  --timeout 10m \
  -f configs/kubernetes/active-active-values.yaml \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag="$PLAYSBC_VERSION" \
  --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag="$PLAYSBC_VERSION" \
  --set rtpengine.image.pullPolicy=Always \
  --set rtpengine.hostNetwork=false \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://playsbc-playsbc-rtpengine:2223 \
  --set observability.enabled=true \
  --set observability.prometheus.retention=31d \
  --set observability.prometheus.persistence.size=5Gi \
  --set observability.grafana.persistence.size=2Gi

kubectl -n playsbc rollout status statefulset/playsbc-playsbc --timeout=240s
kubectl -n playsbc rollout status statefulset/playsbc-playsbc-rtpengine --timeout=240s
kubectl -n playsbc rollout status deployment/playsbc-playsbc-prometheus --timeout=240s
kubectl -n playsbc rollout status deployment/playsbc-playsbc-grafana --timeout=240s
kubectl -n playsbc get pods -o wide

PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --runner-image "ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:${PLAYSBC_VERSION}" \
  --sipp-image "ghcr.io/sudheerkumarvatrapu/playsbc-sipp:${PLAYSBC_VERSION}" \
  --playsbc-image "ghcr.io/sudheerkumarvatrapu/playsbc:${PLAYSBC_VERSION}" \
  --rtpengine-image "ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:${PLAYSBC_VERSION}" \
  --set-playsbc-image \
  --set-rtpengine-image \
  --no-load-playsbc-image \
  --no-load-rtpengine-image \
  --no-load-sipp-image \
  --kind-cluster playsbc
```

Outputs:

```text
logs/k8s-job/<run-id>/runner.log
logs/k8s-job/<run-id>/k8s-reports/latest.html
```

## Build Current Source

Use this before publishing a release. It builds all images from the working tree, loads them into kind, and runs the full catalog.

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --build-rtpengine-image \
  --kind-load-images \
  --set-playsbc-image \
  --set-rtpengine-image \
  --kind-cluster playsbc
```

This is the golden compatibility gate for local source changes.

## Focused Runs

Rasa only:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression_job.py \
  --rasa-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

One or more named profiles:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression_job.py \
  --profile ha-playsbc-midcall-failover \
  --profile ha-rtpengine-midcall-recovery \
  --kind-cluster playsbc
```

The next local HA milestone will add a dedicated shortcut for the complete multi-node HA catalog.

## Observe A Run

```bash
kubectl -n playsbc get job,pod -o wide
kubectl -n playsbc get events --sort-by=.lastTimestamp | tail -40
```

Follow the newest runner:

```bash
POD=$(kubectl -n playsbc get pods \
  -l app.kubernetes.io/name=playsbc-k8s-regression-runner \
  --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{.items[-1:].metadata.name}')

kubectl -n playsbc logs "$POD" -c regression-runner -f
```

Stop a run without deleting PlaySBC/RTPengine:

```bash
kubectl -n playsbc delete job \
  -l app.kubernetes.io/name=playsbc-k8s-regression-runner \
  --ignore-not-found

kubectl -n playsbc delete pod \
  -l app.kubernetes.io/name=playsbc-k8s-regression-runner \
  --ignore-not-found
```

## Grafana And Prometheus

```bash
kubectl -n playsbc port-forward svc/playsbc-playsbc-grafana 3000:3000
kubectl -n playsbc port-forward svc/playsbc-playsbc-prometheus 9090:9090
```

- Grafana: `http://127.0.0.1:3000`
- Prometheus: `http://127.0.0.1:9090`

See [OBSERVABILITY.md](OBSERVABILITY.md) for queries and dashboard interpretation.

## Debug

```bash
helm -n playsbc status playsbc
helm -n playsbc get values playsbc
helm -n playsbc history playsbc

kubectl -n playsbc get pods,svc,statefulset,deployment -o wide
kubectl -n playsbc get events --sort-by=.lastTimestamp | tail -60
kubectl -n playsbc logs statefulset/playsbc-playsbc --tail=120
kubectl -n playsbc logs statefulset/playsbc-playsbc-rtpengine --tail=120
```

Render the chart without changing the cluster:

```bash
helm lint charts/playsbc
helm template playsbc charts/playsbc \
  --namespace playsbc \
  -f configs/kubernetes/active-active-values.yaml \
  --set observability.enabled=true >/tmp/playsbc-rendered.yaml
```

## Cleanup

```bash
helm -n playsbc uninstall playsbc
kubectl delete namespace playsbc --ignore-not-found
kind delete cluster --name playsbc
```

## Rules

- Normal local regression uses active-active values and `rtpengine.hostNetwork=false`.
- Load profiles may skip PCAP; single-call profiles keep one combined PCAP and one `sipmsg.log`.
- Real secondary core/peer interfaces require Multus; default kind realms are logical.
- Do not run manual real-device calls while regression is mutating Helm profile values.
