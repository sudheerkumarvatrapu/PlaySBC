# Kubernetes Lab

The Helm chart deploys PlaySBC with HTTP liveness/readiness probes, optional Secret-backed SIP users, ClientIP dialog affinity, active-active StatefulSet lab mode, shared HA state, and paired RTPengine pods. The standard Kubernetes regression path now uses active-active PlaySBC plus active-active RTPengine by default, with logical core/peer realms; real secondary interfaces need Multus.

Expected standard lab shape:

```text
playsbc-playsbc-0
playsbc-playsbc-1
playsbc-playsbc-rtpengine-0
playsbc-playsbc-rtpengine-1
```

Always include `configs/kubernetes/active-active-values.yaml` for normal lab and regression runs. In single-node kind, this file keeps RTPengine on pod networking with `rtpengine.hostNetwork=false`, avoiding host-port collisions between RTPengine replicas.

## kind

```bash
kind create cluster --name playsbc
docker build -f docker/playsbc.Dockerfile -t playsbc:local .
docker build -f docker/rtpengine.Dockerfile -t playsbc/rtpengine:local .
kind load docker-image playsbc:local playsbc/rtpengine:local --name playsbc
helm upgrade --install playsbc charts/playsbc \
  -f configs/kubernetes/kind-values.yaml \
  -f configs/kubernetes/active-active-values.yaml
kubectl rollout status statefulset/playsbc-playsbc
kubectl rollout status statefulset/playsbc-playsbc-rtpengine
kubectl get pods,services
kubectl port-forward service/playsbc-playsbc 8080:8080 5060:5062
```

Check `http://127.0.0.1:8080/readyz` and `http://127.0.0.1:8080/metrics`. UDP SIP and RTP are easiest to test inside the kind network; TCP SIP can use the port-forward above.

## minikube

```bash
minikube start
eval $(minikube docker-env)
docker build -f docker/playsbc.Dockerfile -t playsbc:local .
docker build -f docker/rtpengine.Dockerfile -t playsbc/rtpengine:local .
helm upgrade --install playsbc charts/playsbc \
  -f configs/kubernetes/minikube-values.yaml \
  -f configs/kubernetes/active-active-values.yaml
kubectl rollout status statefulset/playsbc-playsbc
kubectl rollout status statefulset/playsbc-playsbc-rtpengine
minikube service playsbc-playsbc --url
```

Keep real credentials in a private values file or pre-create a Secret and set `authSecret.existingSecret`. Do not commit production passwords.

## Dialog Affinity Experiment

```bash
helm upgrade playsbc charts/playsbc \
  -f configs/kubernetes/kind-values.yaml \
  -f configs/kubernetes/dialog-affinity-values.yaml
kubectl scale deployment/playsbc-playsbc --replicas=2
kubectl get pods -l app.kubernetes.io/name=playsbc -o wide
```

This is a simple Deployment-only experiment. The normal HA lab mode uses `configs/kubernetes/active-active-values.yaml`, stable StatefulSet pod identities, SQLite-backed shared registrar/dialog state, node-to-RTPengine pairing, external-LB policy metadata, and per-node draining. It is still a lab store; PostgreSQL or Redis is a later hardening phase.

## Media Model

SIP reaches the PlaySBC Service. PlaySBC sends RTPengine NG control to the chart-managed RTPengine Service or to a stable paired RTPengine headless-service endpoint. In active-active regression, RTPengine uses pod networking to avoid host-port collisions in kind. Multus is the future path for real core and peer media interfaces inside Kubernetes.
