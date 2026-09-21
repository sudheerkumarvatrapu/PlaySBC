# PlaySBC Kubernetes And Helm Runbook

This is the canonical local Kubernetes command guide. Use [KUBERNETES_LOCAL.md](KUBERNETES_LOCAL.md) for topology and networking concepts.

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

export PLAYSBC_VERSION=3.0.0
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

## Commercial Source Build, Package, And SBC Upgrade

Use this workflow to deploy the current public `main` branch to the existing
`kind-playsbc` lab. It creates only local development artifacts: it does not
publish a container image, chart, tag, or commercial release. The commit SHA
identifies the PlaySBC image until the `v6.0.0` release gate is complete.

Run every block in the same terminal. Start from a clean, current checkout and
verify the required tools and cluster before building anything:

```bash
set -euo pipefail

cd /Users/sudheerkumar/Documents/Codex/2026-05-18/PlaySBC-Commercial
git switch main
git pull --ff-only origin main

if [ -n "$(git status --porcelain)" ]; then
  git status --short
  echo "The working tree must be clean before creating a commit-tagged image." >&2
  exit 1
fi

export KUBE_CONTEXT=kind-playsbc
export KIND_CLUSTER=playsbc
export SOURCE_TAG=$(git rev-parse --short=12 HEAD)
export PLAYSBC_REPOSITORY=playsbc-commercial-dev
export PLAYSBC_IMAGE="${PLAYSBC_REPOSITORY}:${SOURCE_TAG}"

for REQUIRED_BINARY in docker kind kubectl helm; do
  command -v "$REQUIRED_BINARY" >/dev/null || {
    echo "$REQUIRED_BINARY is required" >&2
    exit 1
  }
done

test -f docker/playsbc.Dockerfile
test -f charts/playsbc/Chart.yaml
docker info >/dev/null
kind get clusters | grep -qx "$KIND_CLUSTER"
kubectl --context "$KUBE_CONTEXT" get nodes
```

### Build The PlaySBC Image

The repository has no root-level `Dockerfile`. Always select
`docker/playsbc.Dockerfile` explicitly:

```bash
docker build \
  -f docker/playsbc.Dockerfile \
  -t "$PLAYSBC_IMAGE" \
  .

docker image inspect "$PLAYSBC_IMAGE" \
  --format 'image={{index .RepoTags 0}} id={{.Id}}'
```

### Validate And Package The Helm Chart

Package the chart in a temporary directory so the validation artifact cannot
be confused with a published commercial release:

```bash
export PACKAGE_DIR=$(mktemp -d /private/tmp/playsbc-commercial-package.XXXXXX)
export CHART_VERSION=$(awk '/^version:/ {print $2; exit}' charts/playsbc/Chart.yaml)
export PLAYSBC_CHART="${PACKAGE_DIR}/playsbc-${CHART_VERSION}.tgz"

: "${CHART_VERSION:?Chart version is empty}"
helm lint charts/playsbc
helm package charts/playsbc --destination "$PACKAGE_DIR"
test -s "$PLAYSBC_CHART"
helm show chart "$PLAYSBC_CHART"
shasum -a 256 "$PLAYSBC_CHART"
```

The package continues to carry the imported baseline chart version until the
commercial release gate changes it. The image's `$SOURCE_TAG`, not the chart
filename, identifies the private source revision being tested.

### Load And Upgrade Only PlaySBC

Load the image into kind before the Helm upgrade. The command explicitly
enables RTPengine and observability instead of trusting previously reused
values. After Helm succeeds, restart every product controller so PlaySBC,
RTPengine, Prometheus, and Grafana all begin from the same upgrade boundary.

```bash
kind load docker-image "$PLAYSBC_IMAGE" --name "$KIND_CLUSTER"

helm upgrade --install playsbc "$PLAYSBC_CHART" \
  --kube-context "$KUBE_CONTEXT" \
  --namespace playsbc \
  --create-namespace \
  --reuse-values \
  --atomic \
  --wait \
  --timeout 10m \
  --set image.repository="$PLAYSBC_REPOSITORY" \
  --set-string image.tag="$SOURCE_TAG" \
  --set image.pullPolicy=IfNotPresent \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=3.0.0 \
  --set rtpengine.image.pullPolicy=IfNotPresent \
  --set rtpengine.hostNetwork=false \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://playsbc-playsbc-rtpengine:2223 \
  --set observability.enabled=true

kubectl --context "$KUBE_CONTEXT" -n playsbc rollout restart \
  statefulset/playsbc-playsbc \
  statefulset/playsbc-playsbc-rtpengine \
  deployment/playsbc-playsbc-prometheus \
  deployment/playsbc-playsbc-grafana
```

The installed local Helm version supports `--atomic`; do not replace it with
`--rollback-on-failure` unless `helm upgrade --help` confirms that newer flag
is available.

Verify rollout health and prove that the StatefulSet uses the exact image that
was built and loaded:

```bash
kubectl --context "$KUBE_CONTEXT" -n playsbc rollout status \
  statefulset/playsbc-playsbc --timeout=240s
kubectl --context "$KUBE_CONTEXT" -n playsbc rollout status \
  statefulset/playsbc-playsbc-rtpengine --timeout=240s
kubectl --context "$KUBE_CONTEXT" -n playsbc rollout status \
  deployment/playsbc-playsbc-prometheus --timeout=240s
kubectl --context "$KUBE_CONTEXT" -n playsbc rollout status \
  deployment/playsbc-playsbc-grafana --timeout=240s

kubectl --context "$KUBE_CONTEXT" -n playsbc get pods -o wide

kubectl --context "$KUBE_CONTEXT" -n playsbc get statefulset,deployment \
  -l app.kubernetes.io/instance=playsbc \
  -o custom-columns='KIND:.kind,NAME:.metadata.name,IMAGES:.spec.template.spec.containers[*].image'

ACTUAL_PLAYSBC_IMAGE=$(kubectl --context "$KUBE_CONTEXT" -n playsbc get \
  statefulset/playsbc-playsbc \
  -o jsonpath='{.spec.template.spec.containers[0].image}')

test "$ACTUAL_PLAYSBC_IMAGE" = "$PLAYSBC_IMAGE"
test "$(kubectl --context "$KUBE_CONTEXT" -n playsbc get statefulset \
  playsbc-playsbc-rtpengine -o jsonpath='{.status.readyReplicas}')" -ge 1
echo "Verified PlaySBC image: $ACTUAL_PLAYSBC_IMAGE"
```

### Run The Fast Pre-v6 Foundation Gate

Before the in-cluster profiles, validate provider streaming, provider
timeout/interruption fallback, consultation hold, and consultation failure/race
recovery directly from the checked-out source:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-public-pycache \
python3 tools/run_public_foundation_regression.py
```

### Run Only The RFC 5359 Kubernetes Profiles

The PlaySBC image is already loaded by the upgrade workflow. Build and load
only the regression runner and SIPp helper images, then run the live business
calling profiles through both the internal and RTPengine service paths:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-public-pycache \
python3 tools/run_k8s_regression_job.py \
  --profile rfc5359-call-hold-resume \
  --profile rfc5359-call-hold-resume-rtpengine \
  --profile rfc5359-call-hold-resume-tcp \
  --profile rfc5359-call-hold-resume-tls \
  --profile rfc5359-unattended-transfer \
  --profile rfc5359-unconditional-forwarding \
  --profile rfc5359-forwarding-on-busy \
  --profile rfc5359-forwarding-on-no-answer \
  --profile rfc5359-unattended-transfer-rtpengine \
  --profile rfc5359-unconditional-forwarding-rtpengine \
  --profile rfc5359-forwarding-on-busy-rtpengine \
  --profile rfc5359-forwarding-on-no-answer-rtpengine \
  --playsbc-image "$PLAYSBC_IMAGE" \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster "$KIND_CLUSTER"
```

See [RFC 5359 business calling services](BUSINESS_CALLING_SERVICES.md) for
configuration, profile scope, interoperability requirements, and the
physical-device acceptance checklist.

The command prints the exact result directory. Open the newest report without
starting a separate report server:

```bash
LATEST_REPORT=$(find logs/k8s-job \
  -path '*/k8s-reports/latest.html' \
  -type f -print | sort | tail -1)

: "${LATEST_REPORT:?No Kubernetes regression report was found}"
open "$LATEST_REPORT"
```

Keep `latest.html` together with its generated `evidence/` directory. Text
evidence opens in a browser viewer; packet captures open a metadata page with a
raw-file download link.

### Troubleshooting This Upgrade

- `failed to read dockerfile: open Dockerfile`: rerun the documented build with
  `-f docker/playsbc.Dockerfile`; there is intentionally no root Dockerfile.
- `unknown flag: --rollback-on-failure`: use the documented `--atomic` command
  with the installed local Helm version.
- `ImagePullBackOff` for `playsbc-commercial-dev`: confirm
  `kind load docker-image "$PLAYSBC_IMAGE" --name "$KIND_CLUSTER"` completed and
  that `image.pullPolicy=IfNotPresent` is set.
- The old PlaySBC image remains: compare `ACTUAL_PLAYSBC_IMAGE` with
  `$PLAYSBC_IMAGE`, then inspect `helm -n playsbc get values playsbc`.
- Helm waits or rolls back: inspect `helm -n playsbc status playsbc`,
  `helm -n playsbc history playsbc`, and
  `kubectl -n playsbc get events --sort-by=.lastTimestamp`.
- A report link fails after moving files: move or copy the complete
  `k8s-reports` directory, including `evidence/`, rather than `latest.html`
  alone.

## Release Upgrade And Full Regression

Run from the repository on the Mac. This is the single maintained release-image workflow.

```bash
cd /Users/sudheerkumar/Documents/Codex/2026-05-18/Mini-Call-Server

export PLAYSBC_VERSION=3.0.0

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
