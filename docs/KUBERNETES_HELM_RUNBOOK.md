# PlaySBC Kubernetes And Helm Runbook

This is the short operator runbook for deploying PlaySBC, RTPengine, Rasa, SIPp regression pods, and observability in a local Kubernetes lab.

## Topology

```text
kubectl / helm
  -> namespace playsbc
     -> PlaySBC active-active StatefulSet and service
     -> paired RTPengine StatefulSet, service, and headless service
     -> shared HA state PVC for registrar/dialog lab state
     -> optional Rasa deployment and service
     -> optional Prometheus and Grafana
     -> regression Job plus temporary SIPp core/peer pods
```

Default service ports:

| Component | Ports |
| --- | --- |
| PlaySBC | `5062/UDP`, `5062/TCP`, `5061/TCP`, `8080/TCP` |
| RTPengine | `2223/UDP` |
| Grafana | `3000/TCP` |
| Prometheus | `9090/TCP` |

The default Kubernetes regression model is active-active:

```text
Core realm 172.28.0.0/24 (logical in kind unless Multus is enabled)
  -> PlaySBC-0 / PlaySBC-1
     -> paired RTPengine-0 / RTPengine-1
        -> Peer realm 192.168.28.0/24 (logical in kind unless Multus is enabled)
```

In a normal kind lab the pod IPs still come from the CNI, such as `10.244.x.x`. The `172.x` and `192.x` realm model is rendered in PlaySBC config, logs, reports, metrics, and regression evidence. Real secondary interfaces require Multus or another multi-network CNI.

## Tool Check

```bash
docker info
kubectl version --client
kubectl config current-context
helm version --short
kind version
```

If local Codex tools were used:

```bash
export PATH="/Users/sudheerkumar/Documents/Codex/.local/bin:$PATH"
```

## Create A Local Cluster

```bash
kind create cluster --name playsbc
kubectl config use-context kind-playsbc
kubectl config set-context --current --namespace=playsbc
kubectl get ns
kubectl get pods -A
```

## Standard Full Regression Flow

Use this as the normal repeatable process: upgrade PlaySBC/RTPengine to the release, enable active-active topology, enable observability, wait for all workloads, then run every Kubernetes regression profile with the release images.

This is the standard PlaySBC Kubernetes architecture from `v1.4.2` onward:

```text
playsbc-playsbc-0
playsbc-playsbc-1
playsbc-playsbc-rtpengine-0
playsbc-playsbc-rtpengine-1
playsbc-playsbc-prometheus-...
playsbc-playsbc-grafana-...
```

If you see only one PlaySBC pod and one RTPengine pod, active-active was not enabled for that Helm upgrade. Re-run the upgrade with `configs/kubernetes/active-active-values.yaml` or the equivalent `--set topology.activeActive.enabled=true` flags.

```bash
cd /Users/sudheerkumar/Documents/Codex/2026-05-18/Mini-Call-Server

kubectl config use-context kind-playsbc
kubectl config set-context --current --namespace=playsbc

helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.4.2/playsbc-1.4.2.tgz \
  --namespace playsbc \
  --create-namespace \
  -f configs/kubernetes/active-active-values.yaml \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.4.2 \
  --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.4.2 \
  --set rtpengine.image.pullPolicy=Always \
  --set rtpengine.hostNetwork=false \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://playsbc-playsbc-rtpengine:2223 \
  --set observability.enabled=true \
  --set observability.prometheus.retention=31d \
  --set observability.prometheus.persistence.size=5Gi \
  --set observability.grafana.persistence.size=2Gi

kubectl -n playsbc rollout status statefulset/playsbc-playsbc --timeout=180s
kubectl -n playsbc rollout status statefulset/playsbc-playsbc-rtpengine --timeout=180s
kubectl -n playsbc rollout status deployment/playsbc-playsbc-prometheus --timeout=180s
kubectl -n playsbc rollout status deployment/playsbc-playsbc-grafana --timeout=180s
kubectl -n playsbc get pods -o wide
kubectl -n playsbc get statefulsets

PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --runner-image ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.4.2 \
  --sipp-image ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.4.2 \
  --playsbc-image ghcr.io/sudheerkumarvatrapu/playsbc:1.4.2 \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --no-load-sipp-image \
  --kind-cluster playsbc
```

The runner deletes old full-suite `logs/k8s-job` output by default, then writes:

```text
logs/k8s-job/<run-id>/runner.log
logs/k8s-job/<run-id>/k8s-reports/latest.html
```

Optional Grafana port-forward:

```bash
kubectl -n playsbc port-forward svc/playsbc-playsbc-grafana 3000:3000
```

## Deploy The Release

Use active-active values for every normal Kubernetes deployment:

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.4.2/playsbc-1.4.2.tgz \
  --namespace playsbc \
  --create-namespace \
  -f configs/kubernetes/active-active-values.yaml \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.4.2 \
  --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.4.2 \
  --set rtpengine.image.pullPolicy=Always \
  --set rtpengine.hostNetwork=false \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://playsbc-playsbc-rtpengine:2223
```

For a single-node kind cluster, keep `rtpengine.hostNetwork=false`. Two RTPengine replicas cannot safely bind the same host UDP port range on one node with host networking enabled.

Verify:

```bash
kubectl -n playsbc get pods,svc
kubectl -n playsbc get statefulsets
kubectl -n playsbc rollout status statefulset/playsbc-playsbc
kubectl -n playsbc rollout status statefulset/playsbc-playsbc-rtpengine
kubectl -n playsbc logs statefulset/playsbc-playsbc --tail=80
kubectl -n playsbc logs statefulset/playsbc-playsbc-rtpengine --tail=80
```

Health and metrics:

```bash
kubectl -n playsbc port-forward service/playsbc-playsbc 8080:8080
curl http://127.0.0.1:8080/readyz
curl http://127.0.0.1:8080/metrics
```

## Enable Observability

```bash
helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --create-namespace \
  --reuse-values \
  --set observability.enabled=true \
  --set observability.prometheus.retention=31d \
  --set observability.prometheus.persistence.size=5Gi \
  --set observability.grafana.persistence.size=2Gi

kubectl -n playsbc rollout status deployment/playsbc-playsbc-prometheus --timeout=180s
kubectl -n playsbc rollout status deployment/playsbc-playsbc-grafana --timeout=180s
kubectl -n playsbc port-forward svc/playsbc-playsbc-grafana 3000:3000
```

Open `http://127.0.0.1:3000` and select `PlaySBC Core/Peer SBC Lab`.

The dashboard shows current active calls separately from range totals, SIP requests and responses, RTPengine sessions, codec negotiation, transcoding, AI events, and load evidence.

## Active-Active And Multus

All Kubernetes regression profiles now default to active-active topology:

- PlaySBC runs as `statefulset/playsbc-playsbc` with stable pods `playsbc-playsbc-0` and `playsbc-playsbc-1`.
- RTPengine runs as `statefulset/playsbc-playsbc-rtpengine` with stable pods `playsbc-playsbc-rtpengine-0` and `playsbc-playsbc-rtpengine-1`.
- PlaySBC uses `$POD_NAME` as the HA node ID and pairs each node to the matching RTPengine headless-service endpoint.
- Shared registrar/dialog state is mounted at `/var/lib/playsbc/ha-state.sqlite3`.
- Grafana shows active calls by node, shared state, drain status, RTPengine failures, requests, responses, codecs, and transcoding.

The default shared-state PVC uses `ReadWriteOnce`, which is fine for a single-node kind lab. For true multi-node Kubernetes, use RWX storage or move HA state to Redis/PostgreSQL in a later hardening phase.

Optional Multus annotations:

```bash
helm upgrade --install playsbc charts/playsbc \
  --namespace playsbc \
  --reuse-values \
  -f configs/kubernetes/active-active-values.yaml \
  --set topology.multus.enabled=true
```

Use `--set topology.multus.createNetworkAttachmentDefinitions=true` only after installing the Multus CRDs. Without Multus, kind uses logical core/peer realms over normal pod networking.

## Run Full Kubernetes Regression

Build local images and load them into kind:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
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

This is also the v1.5.0 development safety gate. If you upgrade the local chart/app version to v1.5.0 before publishing images, run this local-image command; it uses the source tree and does not require `ghcr.io/...:1.5.0` to exist yet.

Use published release images:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --runner-image ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.4.2 \
  --sipp-image ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.4.2 \
  --playsbc-image ghcr.io/sudheerkumarvatrapu/playsbc:1.4.2 \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --no-load-sipp-image \
  --kind-cluster playsbc
```

Outputs:

```text
logs/k8s-job/<run-id>/runner.log
logs/k8s-job/<run-id>/k8s-Regression/
logs/k8s-job/<run-id>/k8s-reports/latest.html
```

## Run AI/Rasa Regression Only

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --rasa-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

Outputs:

```text
logs/RASA-Regression/<run-id>/runner.log
logs/RASA-Regression/<run-id>/RASA-reports/latest.html
```

Rasa coverage includes mock Rasa, real Rasa pod, RTPengine AI media, Vosk/Piper speech, Whisper STT, Coqui TTS, long-response streaming, contact-center bot flow, and chat/NLU positive and negative guardrail cases.

## Watch A Run

```bash
kubectl -n playsbc get job,pod
kubectl -n playsbc get pods -o wide
kubectl -n playsbc logs job/$(kubectl -n playsbc get jobs --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}') -c regression-runner -f
```

The runner creates temporary SIPp core and peer pods per profile, applies Helm config, runs the call, collects logs, ladders, PCAP evidence, and restores Helm values.

## Useful Helm Commands

```bash
helm -n playsbc list
helm -n playsbc status playsbc
helm -n playsbc get values playsbc
helm -n playsbc get manifest playsbc
helm -n playsbc history playsbc
helm -n playsbc rollback playsbc <revision>
helm -n playsbc uninstall playsbc
```

Render or lint locally:

```bash
helm lint charts/playsbc
helm template playsbc charts/playsbc --namespace playsbc --set observability.enabled=true
```

## Debug

```bash
kubectl -n playsbc describe pod -l app.kubernetes.io/name=playsbc
kubectl -n playsbc get events --sort-by=.lastTimestamp
kubectl -n playsbc logs deployment/playsbc-playsbc --tail=120
kubectl -n playsbc logs deployment/playsbc-playsbc --previous --tail=120
kubectl -n playsbc logs deployment/playsbc-playsbc-rtpengine --tail=120
kubectl -n playsbc logs deployment/playsbc-playsbc-prometheus --tail=120
kubectl -n playsbc logs deployment/playsbc-playsbc-grafana --tail=120
```

DNS check:

```bash
kubectl -n playsbc run dns-check --rm -it --image=busybox:1.36 --restart=Never -- nslookup playsbc-playsbc
kubectl -n playsbc run dns-check --rm -it --image=busybox:1.36 --restart=Never -- nslookup playsbc-playsbc-rtpengine
```

## Notes

- Kubernetes regression runs in the `playsbc` namespace.
- Load profiles skip PCAP by design; single-call profiles keep SIP/RTP/RTCP evidence.
- Kubernetes currently uses logical core/peer pods on one pod network. True multi-interface core and peer realms are future Multus work.
- Production-grade active-active still needs external load balancing, shared registrar/dialog state, and controlled RTPengine pairing.
- For UDP SIP/RTP, test from inside the cluster or expose with NodePort/LoadBalancer. `kubectl port-forward` is mainly useful for TCP health, Grafana, and Prometheus.

## Cleanup

```bash
helm -n playsbc uninstall playsbc
kubectl delete namespace playsbc
kind delete cluster --name playsbc
```
