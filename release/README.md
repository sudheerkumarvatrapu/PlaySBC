# PlaySBC 1.2.1 Release Artifacts

This folder contains generated release artifacts for PlaySBC.

- Helm chart package: `helm/playsbc-1.2.1.tgz`
- Project license: MIT
- Chart version: `1.2.1`
- Application version: `1.2.1`

Rebuild the Helm package with:

```bash
helm package charts/playsbc --destination release/helm
```

## Container Image Deployment

The `.tgz` chart package contains Kubernetes manifests and config, not image layers. End users deploy the chart and point it at PlaySBC and RTPengine container images.

Published GHCR images:

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.2.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.2.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2.1`

Kubernetes deployment example:

```bash
helm upgrade --install playsbc helm/playsbc-1.2.1.tgz \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set image.tag=1.2.1 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set rtpengine.image.tag=1.2.1
```

Local lab image example:

```bash
docker build -f docker/playsbc.Dockerfile -t playsbc:1.2.1 .
docker build -f docker/rtpengine.Dockerfile -t playsbc-rtpengine:1.2.1 .
helm upgrade --install playsbc helm/playsbc-1.2.1.tgz \
  --set image.repository=playsbc \
  --set image.tag=1.2.1 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=playsbc-rtpengine \
  --set rtpengine.image.tag=1.2.1
```

The chart can also deploy RTPengine by setting `rtpengine.enabled=true`. If disabled, PlaySBC can still point to an external RTPengine using `playsbc.config.rtpengine_url`.

Kubernetes regression from published images:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --runner-image ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.2.1 \
  --sipp-image ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.2.1 \
  --playsbc-image ghcr.io/sudheerkumarvatrapu/playsbc:1.2.1 \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --no-load-sipp-image
```
