# PlaySBC v2.6.0 Product Guide

Deployment, build, upgrade, regression, observability, and troubleshooting.

# Document Control

| Track | Repository | Artifact identity |
| --- | --- | --- |
| Public release | `PlaySBC` | Published `2.6.0` images and chart |
| Public source | `PlaySBC` | Local commit SHA |
| Private commercial | `PlaySBC-Commercial` | `playsbc-commercial-dev:<commit>` |

This curated guide does not embed the standalone Markdown runbooks. They remain
maintained independently under `docs/`. The browser edition provides COPY
buttons; PDF viewers keep command text selectable but cannot access the system
clipboard.

# Common Local Preparation

```bash
set -euo pipefail
export KUBE_CONTEXT=kind-playsbc
export KIND_CLUSTER=playsbc
open -a Docker
until docker info >/dev/null 2>&1; do echo "Waiting for Docker Desktop..."; sleep 5; done
if ! kind get clusters | grep -qx "$KIND_CLUSTER"; then
  kind create cluster --name "$KIND_CLUSTER" --wait 180s
fi
kubectl --context "$KUBE_CONTEXT" get --raw=/readyz
kubectl --context "$KUBE_CONTEXT" create namespace playsbc --dry-run=client -o yaml \
  | kubectl --context "$KUBE_CONTEXT" apply -f -
```

# Public v2.6.0 Release Upgrade

```bash
cd /Users/sudheerkumar/Documents/Codex/2026-05-18/Mini-Call-Server
export PLAYSBC_VERSION=2.6.0
export KUBE_CONTEXT=kind-playsbc
helm upgrade --install playsbc \
  "https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v${PLAYSBC_VERSION}/playsbc-${PLAYSBC_VERSION}.tgz" \
  --kube-context "$KUBE_CONTEXT" --namespace playsbc --create-namespace \
  --atomic --wait --timeout 10m \
  -f configs/kubernetes/active-active-values.yaml \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag="$PLAYSBC_VERSION" --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag="$PLAYSBC_VERSION" \
  --set rtpengine.image.pullPolicy=Always --set observability.enabled=true
kubectl --context "$KUBE_CONTEXT" -n playsbc get pods -o wide
```

# Public Source Build And Upgrade

```bash
cd /Users/sudheerkumar/Documents/Codex/2026-05-18/Mini-Call-Server
git switch main && git pull --ff-only origin main
export SOURCE_TAG=$(git rev-parse --short=12 HEAD)
export PLAYSBC_REPOSITORY=playsbc-public-dev
export PLAYSBC_IMAGE="${PLAYSBC_REPOSITORY}:${SOURCE_TAG}"
docker build -f docker/playsbc.Dockerfile -t "$PLAYSBC_IMAGE" .
kind load docker-image "$PLAYSBC_IMAGE" --name playsbc
helm upgrade --install playsbc charts/playsbc --kube-context kind-playsbc \
  --namespace playsbc --create-namespace --reuse-values --atomic --wait --timeout 10m \
  --set image.repository="$PLAYSBC_REPOSITORY" --set-string image.tag="$SOURCE_TAG" \
  --set image.pullPolicy=IfNotPresent
```

Run the full public source catalog:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-public-pycache \
python3 tools/run_k8s_regression_job.py --all-profiles \
  --build-playsbc-image --build-runner-image --build-sipp-image \
  --build-rtpengine-image --kind-load-images --set-playsbc-image \
  --set-rtpengine-image --kind-cluster playsbc
```

# Private Commercial Build And Upgrade

This track creates local development artifacts only; it does not publish a
commercial release.

```bash
cd /Users/sudheerkumar/Documents/Codex/2026-05-18/PlaySBC-Commercial
git switch main && git pull --ff-only origin main
test -z "$(git status --porcelain)"
export SOURCE_TAG=$(git rev-parse --short=12 HEAD)
export PLAYSBC_REPOSITORY=playsbc-commercial-dev
export PLAYSBC_IMAGE="${PLAYSBC_REPOSITORY}:${SOURCE_TAG}"
docker build -f docker/playsbc.Dockerfile -t "$PLAYSBC_IMAGE" .
kind load docker-image "$PLAYSBC_IMAGE" --name playsbc
export PACKAGE_DIR=$(mktemp -d /private/tmp/playsbc-commercial-package.XXXXXX)
export CHART_VERSION=$(awk '/^version:/ {print $2; exit}' charts/playsbc/Chart.yaml)
export PLAYSBC_CHART="${PACKAGE_DIR}/playsbc-${CHART_VERSION}.tgz"
helm lint charts/playsbc
helm package charts/playsbc --destination "$PACKAGE_DIR"
helm upgrade --install playsbc "$PLAYSBC_CHART" --kube-context kind-playsbc \
  --namespace playsbc --create-namespace --reuse-values --atomic --wait --timeout 10m \
  --set image.repository="$PLAYSBC_REPOSITORY" --set-string image.tag="$SOURCE_TAG" \
  --set image.pullPolicy=IfNotPresent
kubectl --context kind-playsbc -n playsbc rollout status \
  statefulset/playsbc-playsbc --timeout=240s
```

# Private Commercial RFC 5359 Regression

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-commercial-pycache \
python3 tools/run_commercial_foundation_regression.py

PYTHONPYCACHEPREFIX=/private/tmp/playsbc-commercial-pycache \
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
  --playsbc-image "$PLAYSBC_IMAGE" --set-playsbc-image --no-load-playsbc-image \
  --build-runner-image --build-sipp-image --kind-load-images --kind-cluster playsbc
```

# Evidence, Prometheus, And Grafana

```bash
LATEST_REPORT=$(find logs/k8s-job -path '*/k8s-reports/latest.html' -type f | sort | tail -1)
: "${LATEST_REPORT:?No report found}"
open "$LATEST_REPORT"
```

Keep `latest.html` with its `evidence/` directory. If local links are blocked:

```bash
python3 tools/serve_regression_report.py "$LATEST_REPORT"
```

```bash
kubectl --context kind-playsbc -n playsbc port-forward \
  svc/playsbc-playsbc-prometheus 9090:9090
```

```bash
kubectl --context kind-playsbc -n playsbc port-forward \
  svc/playsbc-playsbc-grafana 3000:3000
```

Open Prometheus at `http://127.0.0.1:9090`. Open Grafana at
`http://127.0.0.1:3000` with `admin` / `playsbc-lab`.

# Troubleshooting

- Root `Dockerfile` missing: always use `-f docker/playsbc.Dockerfile`.
- `--rollback-on-failure` unknown: use `--atomic` with the installed Helm.
- kind API refused: start Docker and `docker start playsbc-control-plane`.
- `ImagePullBackOff`: reload the exact image and retain `IfNotPresent`.
- Helm failure: inspect status, history, pod logs, and timestamp-sorted events.
- Broken report links: preserve the entire report directory or use its server.

```bash
helm --kube-context kind-playsbc -n playsbc status playsbc
helm --kube-context kind-playsbc -n playsbc history playsbc
kubectl --context kind-playsbc -n playsbc get events --sort-by=.lastTimestamp
kubectl --context kind-playsbc -n playsbc get pods -o wide
```

# Detailed Runbooks

These remain standalone and are not embedded in either generated guide:
`AZURE_AKS.md`, `KUBERNETES_HELM_RUNBOOK.md`, `KUBERNETES_LOCAL.md`,
`REAL_DEVICE_LAB.md`, `RTPENGINE_LOCAL.md`, `OBSERVABILITY.md`,
`AI_VOICE_GATEWAY.md`, `BUSINESS_CALLING_SERVICES.md`, and `EVOLUTION_PLAN.md`.
