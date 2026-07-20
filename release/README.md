# PlaySBC Release Artifacts

This folder keeps local release notes and Helm chart packages for PlaySBC.

Current release:

- Version: `1.4.1`
- Helm chart package: `helm/playsbc-1.4.1.tgz`
- Project license: MIT
- Chart version: `1.4.1`
- Application version: `1.4.1`

Rebuild the Helm package with:

```bash
helm package charts/playsbc --destination release/helm
shasum -a 256 release/helm/playsbc-1.4.1.tgz > release/helm/playsbc-1.4.1.tgz.sha256
```

## Container Image Deployment

The `.tgz` chart package contains Kubernetes manifests and config, not image layers. End users deploy the chart and point it at PlaySBC and RTPengine container images.

Published GHCR images for this release:

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.4.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.4.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.4.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.4.1`

Deploy the release chart:

```bash
helm upgrade --install playsbc helm/playsbc-1.4.1.tgz \
  --namespace playsbc \
  --create-namespace \
  -f configs/kubernetes/active-active-values.yaml \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.4.1 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.4.1 \
  --set rtpengine.hostNetwork=false
```

This is the normal Kubernetes shape for `v1.4.1` and later:

```text
PlaySBC StatefulSet replicas: 2
RTPengine StatefulSet replicas: 2
Prometheus Deployment replicas: 1
Grafana Deployment replicas: 1
```

If a deployment shows only one PlaySBC pod and one RTPengine pod, active-active values were not applied. Re-run Helm with `configs/kubernetes/active-active-values.yaml` or equivalent `--set topology.activeActive.enabled=true` values before running regression.

Kubernetes regression from published images:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --runner-image ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.4.1 \
  --sipp-image ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.4.1 \
  --playsbc-image ghcr.io/sudheerkumarvatrapu/playsbc:1.4.1 \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --no-load-sipp-image \
  --kind-cluster playsbc
```

Historical release notes are kept as `RELEASE_NOTES_<version>.md`.
