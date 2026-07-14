# PlaySBC Kubernetes And Helm Runbook

This runbook captures the local Kubernetes deployment flow tested with `kind`, Helm, PlaySBC, and RTPengine.

## Topology

```text
macOS Terminal
  -> kubectl / helm
    -> kind cluster: kind-playsbc
      -> namespace: playsbc
        -> service/playsbc-playsbc
          -> deployment/playsbc-playsbc
        -> service/playsbc-playsbc-rtpengine
          -> deployment/playsbc-playsbc-rtpengine
```

Default chart service ports:

```text
PlaySBC:    5062/UDP, 5062/TCP, 5061/TCP, 8080/TCP
RTPengine: 2223/UDP
```

## Tool Checks

```bash
docker info
kubectl version --client
kubectl config current-context
helm version --short
kind version
```

If `helm` or `kind` were installed under the local Codex tools folder, add it to your shell:

```bash
export PATH="/Users/sudheerkumar/Documents/Codex/.local/bin:$PATH"
```

If `kind` is missing on macOS ARM64, install it locally:

```bash
mkdir -p /Users/sudheerkumar/Documents/Codex/.local/bin
curl -L -o /Users/sudheerkumar/Documents/Codex/.local/bin/kind https://kind.sigs.k8s.io/dl/v0.32.0/kind-darwin-arm64
chmod +x /Users/sudheerkumar/Documents/Codex/.local/bin/kind
export PATH="/Users/sudheerkumar/Documents/Codex/.local/bin:$PATH"
kind version
```

## Create Local kind Cluster

```bash
kind create cluster --name playsbc
kubectl config use-context kind-playsbc
kubectl get ns
kubectl get pods -A
```

Expected namespaces after a fresh cluster:

```text
default
kube-node-lease
kube-public
kube-system
local-path-storage
```

## Deploy PlaySBC With RTPengine

Run from the repo root:

```bash
cd /Users/sudheerkumar/Documents/Codex/2026-05-18/Mini-Call-Server
```

Deploy from the local chart and public GHCR images:

```bash
helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.0.0 \
  --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.0.0 \
  --set rtpengine.image.pullPolicy=Always \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://playsbc-playsbc-rtpengine:2223
```

Deploy from the GitHub release Helm package:

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.0.0/playsbc-1.0.0.tgz \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.0.0 \
  --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.0.0 \
  --set rtpengine.image.pullPolicy=Always \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://playsbc-playsbc-rtpengine:2223
```

## Verify Deployment

```bash
kubectl get ns
kubectl -n playsbc get pods
kubectl -n playsbc get svc
kubectl -n playsbc get deploy
kubectl -n playsbc rollout status deployment/playsbc-playsbc
kubectl -n playsbc rollout status deployment/playsbc-playsbc-rtpengine
```

Expected pods:

```text
playsbc-playsbc-...              1/1 Running
playsbc-playsbc-rtpengine-...    1/1 Running
```

Expected services:

```text
playsbc-playsbc             ClusterIP   5062/UDP,5062/TCP,5061/TCP,8080/TCP
playsbc-playsbc-rtpengine   ClusterIP   2223/UDP
```

## Health And Metrics

Port-forward the HTTP health service:

```bash
kubectl -n playsbc port-forward service/playsbc-playsbc 8080:8080
```

In another terminal:

```bash
curl http://127.0.0.1:8080/readyz
curl http://127.0.0.1:8080/metrics
```

Expected health response:

```text
ready
```

Useful metrics:

```text
playsbc_active_calls
playsbc_admission_rejections_total
playsbc_ha_enabled
playsbc_stream_connects_total
playsbc_stream_failures_total
playsbc_stream_reuses_total
```

## Logs

```bash
kubectl -n playsbc logs deployment/playsbc-playsbc --tail=100
kubectl -n playsbc logs deployment/playsbc-playsbc-rtpengine --tail=100
kubectl -n playsbc logs -f deployment/playsbc-playsbc
```

Expected PlaySBC lines:

```text
Using RTPengine media backend at udp://playsbc-playsbc-rtpengine:2223
SIP listening on udp:0.0.0.0:5062
```

Expected RTPengine line:

```text
Startup complete
```

## Run Kubernetes Regression

Use this after PlaySBC and RTPengine are already deployed by Helm. The runner creates temporary SIPp pods inside the `playsbc` namespace, mounts the repo SIPp XMLs with a ConfigMap, executes the profiles against the Kubernetes Service, collects pod/deployment evidence, and writes the same robot-style HTML report format as the local B2BUA suite.

One-time SIPp image prep for `kind`:

```bash
docker build -f docker/sipp.Dockerfile -t playsbc-sipp:local .
kind load docker-image playsbc-sipp:local --name playsbc
```

If you are validating current local source changes instead of a published PlaySBC image, rebuild and roll the PlaySBC pod too:

```bash
docker build -f docker/playsbc.Dockerfile -t playsbc:k8s-regression .
kind load docker-image playsbc:k8s-regression --name playsbc
helm upgrade playsbc charts/playsbc \
  --namespace playsbc \
  --reuse-values \
  --set image.repository=playsbc \
  --set-string image.tag=k8s-regression \
  --set image.pullPolicy=IfNotPresent
kubectl -n playsbc rollout status deployment/playsbc-playsbc
```

Run all Kubernetes profiles:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression.py \
  --all-profiles \
  --namespace playsbc \
  --service playsbc-playsbc \
  --sipp-image playsbc-sipp:local
```

Or let the runner build and load the SIPp image before the first profile:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression.py \
  --all-profiles \
  --build-sipp-image \
  --kind-load-image \
  --kind-cluster playsbc
```

Current Kubernetes profiles:

| Profile | What It Checks |
| --- | --- |
| `options` | SIPp pod sends OPTIONS to the PlaySBC Kubernetes Service and expects `200 OK`. |
| `register-contact` | SIPp pod registers a contact through PlaySBC and expects registrar `200 OK`. |
| `b2bua-signalling` | SIPp B registers in-cluster, SIPp A calls through PlaySBC B2BUA, and SIPp B answers the call. |

Outputs:

```text
logs/k8s-Regression/<run-id>-<profile>/
logs/k8s-reports/<run-id>.html
logs/k8s-reports/latest.html
```

Useful single-profile commands:

```bash
python3 tools/run_k8s_regression.py --profile options
python3 tools/run_k8s_regression.py --profile register-contact
python3 tools/run_k8s_regression.py --profile b2bua-signalling
```

## Inspect Helm Release

```bash
helm -n playsbc list
helm -n playsbc status playsbc
helm -n playsbc get values playsbc
helm -n playsbc get manifest playsbc
helm -n playsbc history playsbc
```

Render locally without deploying:

```bash
helm lint charts/playsbc
helm template playsbc charts/playsbc \
  --namespace playsbc \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.0.0 \
  --set rtpengine.enabled=true
```

## Upgrade

Upgrade image tag:

```bash
helm upgrade playsbc charts/playsbc \
  --namespace playsbc \
  --reuse-values \
  --set-string image.tag=1.0.1 \
  --set-string rtpengine.image.tag=1.0.1
```

Upgrade with a values file:

```bash
helm upgrade playsbc charts/playsbc \
  --namespace playsbc \
  -f configs/kubernetes/kind-values.yaml
```

Restart pods without changing chart values:

```bash
kubectl -n playsbc rollout restart deployment/playsbc-playsbc
kubectl -n playsbc rollout status deployment/playsbc-playsbc
```

## Rollback

```bash
helm -n playsbc history playsbc
helm -n playsbc rollback playsbc <revision>
kubectl -n playsbc rollout status deployment/playsbc-playsbc
```

Example:

```bash
helm -n playsbc rollback playsbc 1
```

## Scale

Scale PlaySBC pods:

```bash
kubectl -n playsbc scale deployment/playsbc-playsbc --replicas=2
kubectl -n playsbc get pods -o wide
```

Note: scaling is useful for lab experiments, but production-grade active-active needs external load balancing, shared registrar/dialog state, and careful RTPengine pairing.

## Configure SIP Users

For a quick lab, pass users through Helm values:

```bash
helm upgrade playsbc charts/playsbc \
  --namespace playsbc \
  --reuse-values \
  --set playsbc.config.users.1001=secret-password
```

Preferred Kubernetes style is Secret-backed users:

```bash
kubectl -n playsbc create secret generic playsbc-users \
  --from-literal=users.yaml='users:
  "1001": "secret-password"
  "1002": "secret-password"'

helm upgrade playsbc charts/playsbc \
  --namespace playsbc \
  --reuse-values \
  --set authSecret.enabled=true \
  --set authSecret.existingSecret=playsbc-users
```

## Describe And Debug

```bash
kubectl -n playsbc describe pod -l app.kubernetes.io/name=playsbc
kubectl -n playsbc describe svc playsbc-playsbc
kubectl -n playsbc describe svc playsbc-playsbc-rtpengine
kubectl -n playsbc get events --sort-by=.lastTimestamp
kubectl -n playsbc get configmap
kubectl -n playsbc get secret
```

Check DNS from a temporary pod:

```bash
kubectl -n playsbc run dns-check --rm -it --image=busybox:1.36 --restart=Never -- nslookup playsbc-playsbc
kubectl -n playsbc run dns-check --rm -it --image=busybox:1.36 --restart=Never -- nslookup playsbc-playsbc-rtpengine
```

## Exposing SIP And RTP

The default service type is `ClusterIP`, so PlaySBC and RTPengine are reachable inside the cluster.

HTTP health can use `kubectl port-forward` because it is TCP:

```bash
kubectl -n playsbc port-forward service/playsbc-playsbc 8080:8080
```

SIP UDP and RTP media should be tested from inside the cluster, exposed through NodePort/LoadBalancer, or handled with a kind extra-port-mapping cluster. Kubernetes `port-forward` is TCP-oriented and is not a good general solution for UDP SIP/RTP testing.

For SIP over TCP only, a temporary port-forward can help parser or signalling checks:

```bash
kubectl -n playsbc port-forward service/playsbc-playsbc 5062:5062
```

## Delete PlaySBC

Uninstall the Helm release:

```bash
helm -n playsbc uninstall playsbc
```

Delete the namespace:

```bash
kubectl delete namespace playsbc
```

Delete only pods and let Kubernetes recreate them:

```bash
kubectl -n playsbc delete pod -l app.kubernetes.io/name=playsbc
```

## Delete Local Cluster

```bash
kind delete cluster --name playsbc
```

After deletion:

```bash
kubectl config get-contexts
kubectl config current-context
```

## Quick Command Summary

```bash
# Create cluster
kind create cluster --name playsbc

# Deploy
# Use the full GHCR image command in the deploy section for a fresh kind cluster.
helm upgrade --install playsbc charts/playsbc --namespace playsbc --create-namespace

# Verify
kubectl -n playsbc get pods,svc
kubectl -n playsbc rollout status deployment/playsbc-playsbc

# Logs
kubectl -n playsbc logs deployment/playsbc-playsbc --tail=100

# Health
kubectl -n playsbc port-forward service/playsbc-playsbc 8080:8080
curl http://127.0.0.1:8080/readyz

# Upgrade
helm upgrade playsbc charts/playsbc --namespace playsbc --reuse-values

# Rollback
helm -n playsbc rollback playsbc <revision>

# Uninstall app
helm -n playsbc uninstall playsbc

# Delete cluster
kind delete cluster --name playsbc
```
