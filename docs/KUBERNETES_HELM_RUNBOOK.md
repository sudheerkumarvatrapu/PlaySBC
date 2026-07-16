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
  --set-string image.tag=1.2.2 \
  --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.2.2 \
  --set rtpengine.image.pullPolicy=Always \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://playsbc-playsbc-rtpengine:2223
```

Deploy from the GitHub release Helm package:

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.2.2/playsbc-1.2.2.tgz \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.2.2 \
  --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.2.2 \
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

## Optional Real Rasa Lab

The complete Kubernetes regression catalog includes mock Rasa, real Rasa, and real Vosk/Piper speech profiles. This values file enables the real Rasa REST bot inside Kubernetes:

```bash
helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --create-namespace \
  -f configs/kubernetes/kind-values.yaml \
  -f configs/kubernetes/ai-rasa-real-values.yaml
```

Verify the extra pod and service:

```bash
kubectl -n playsbc rollout status deployment/playsbc-playsbc-rasa
kubectl -n playsbc get pods,svc
kubectl -n playsbc logs deployment/playsbc-playsbc-rasa --tail=100
```

Run only the Kubernetes AI/Rasa profiles:

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

This mode runs `ai-rasa-lab`, `ai-rasa-rtpengine`, `ai-rasa-real-lab`, `ai-rasa-rtpengine-speech`, `ai-rasa-contact-center-sales`, `ai-rasa-chat-nlu`, and `ai-rasa-chat-negative`. It deletes old local `logs/RASA-Regression` output before each run unless `--keep-old-logs` is used.

If the kind node cannot pull DockerHub images, load Rasa first:

```bash
docker pull rasa/rasa:3.6.20-full
kind load docker-image rasa/rasa:3.6.20-full --name playsbc
```

## Run Kubernetes Regression

Use this after PlaySBC and RTPengine are deployed by Helm. The preferred Kubernetes path is the in-cluster Job runner: one controller pod starts inside the `playsbc` namespace, applies each profile through Helm, creates temporary SIPp core/peer pods, runs the canonical B2BUA catalog, copies reports back to the repo, and restores the Helm release afterward.

Execution model:

- Runner Job pod: `playsbc` namespace.
- SIPp A/core and SIPp B/peer: temporary pods created per profile.
- PlaySBC config: rendered by Helm before each profile.
- RTPengine profiles: use the in-cluster RTPengine Service.
- Reports: robot-style HTML with setup, config, execution, teardown, evidence, and ladders.

Kubernetes regression does not alter the local Docker regression command. In `kind` or `minikube`, Kubernetes profiles use logical core/peer pods inside one pod network. True separate core and peer pod interfaces, for example `172.x` and `192.x`, need a future Multus or multi-network CNI enhancement.

List the Kubernetes profile catalog:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression.py --list-profiles
```

Run the full 52-profile in-cluster Kubernetes suite:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

Run one Kubernetes profile:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression_job.py \
  --profile tls-srtp-to-udp-rtp \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

If all required images are already available in the cluster, skip local builds:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --runner-image ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2.2 \
  --sipp-image ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2.2 \
  --playsbc-image ghcr.io/sudheerkumarvatrapu/playsbc:1.2.2 \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --no-load-sipp-image
```

Job-mode outputs copied back to the repo:

```text
logs/k8s-job/<run-id>/runner.log
logs/k8s-job/<run-id>/k8s-Regression/
logs/k8s-job/<run-id>/k8s-reports/latest.html
```

Rasa-only Job-mode outputs:

```text
logs/RASA-Regression/<run-id>/runner.log
logs/RASA-Regression/<run-id>/RASA-Regression/
logs/RASA-Regression/<run-id>/RASA-reports/latest.html
```

Useful live checks while the Job runs:

```bash
kubectl -n playsbc get job,pod
kubectl -n playsbc get pods -o wide
kubectl -n playsbc logs job/<job-name> -c regression-runner --tail=100
kubectl -n playsbc describe job/<job-name>
```

Job defaults:

- Deletes old local `logs/k8s-job` output before a full/default run; Rasa-only mode deletes old `logs/RASA-Regression` output. Use `--keep-old-logs` to retain history.
- Builds `playsbc:k8s-regression`, `playsbc-k8s-regression:local`, and `playsbc-sipp:local` when the build flags are used.
- Loads local images into `kind` when `--kind-load-images` is set.
- Creates a lab-only `playsbc-regression-tls` Secret when TLS profiles are selected.
- Uses PlaySBC pod IP for RTPengine-facing SDP while SIPp still targets the stable PlaySBC Service.
- Uses PlaySBC pod IP in SDP for all K8s profiles so SIPp media targets a numeric, routable pod address instead of the Service DNS name.
- Captures `capture-core.pcap`, `capture-peer.pcap`, and merged `capture.pcap` for non-load profiles. Load profiles skip PCAP by design.
- Sends RTCP helper reports for eligible single-call media profiles and records `K8S RTCP OBSERVATION` in `log.media`.
- Collects deployment logs, pod descriptions, and current/previous pod logs for CrashLoopBackOff evidence.
- Restores the Helm release to pre-run values unless `--no-restore-helm-values` is used.

Coverage:

- B2BUA signalling, media, transcoding, and RTPengine anchoring.
- UDP, TCP, TLS/SRTP interworking, RTCP, and DTMF profiles.
- REGISTER, digest auth success/failure, registered inbound/outbound calls.
- ESBC route policy, trunk, failover, normalization, admission, health, and metrics profiles.
- AI/Rasa lab profiles. The default catalog includes mock Rasa, real Rasa, speech STT/TTS, the contact-center sales bot profile, and real Rasa chat/NLU verifier profiles. Run `--profile ai-rasa-real-lab`, `--profile ai-rasa-rtpengine-speech`, `--profile ai-rasa-contact-center-sales`, `--profile ai-rasa-chat-nlu`, or `--profile ai-rasa-chat-negative` for targeted real Rasa checks.
- Negative SIP cases such as invalid BYE, unknown route, failed outbound leg, CANCEL, and retransmission.
- Small load, soak, and 5 cps / 60 second CHT load profiles.

The three smoke aliases are still available for quick Kubernetes checks: `options`, `register-contact`, and `b2bua-signalling`.

Advanced shell-driven runner, useful only when you do not want an in-cluster controller Job:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_k8s_regression.py \
  --all-profiles \
  --build-sipp-image \
  --kind-load-image \
  --kind-cluster playsbc
```

Keep Kubernetes evidence for debugging:

```bash
python3 tools/run_k8s_regression_job.py --profile basic-signalling --keep-job --keep-old-logs
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
  --set-string image.tag=1.2.2 \
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
