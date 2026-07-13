# Kubernetes Lab

The Helm chart deploys PlaySBC with HTTP liveness/readiness probes, optional Secret-backed SIP users, ClientIP dialog affinity, and a paired RTPengine control Service. RTPengine uses the Kubernetes node IP for its media interface and host networking for the lab RTP range.

## kind

```bash
kind create cluster --name playsbc
docker build -f docker/playsbc.Dockerfile -t playsbc:local .
docker build -f docker/rtpengine.Dockerfile -t playsbc/rtpengine:local .
kind load docker-image playsbc:local playsbc/rtpengine:local --name playsbc
helm upgrade --install playsbc charts/playsbc \
  -f configs/kubernetes/kind-values.yaml
kubectl rollout status deployment/playsbc-playsbc
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
  -f configs/kubernetes/minikube-values.yaml
kubectl rollout status deployment/playsbc-playsbc
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

`ClientIP` affinity keeps one SIP source on one PlaySBC pod and one RTPengine control endpoint. The HA lab mode can also use SQLite-backed shared registrar/dialog state plus node-to-RTPengine pairing. This is useful for active-active experiments, but it is still a lab store; a production-style backend such as PostgreSQL or Redis is a later hardening phase.

## Media Model

SIP reaches the PlaySBC Service. PlaySBC sends RTPengine NG control to the chart-managed RTPengine Service. RTPengine advertises the node IP and binds the configured host RTP range. For active-active lab runs, configure one RTPengine pair per PlaySBC node and keep the shared state path on storage visible to both nodes.
