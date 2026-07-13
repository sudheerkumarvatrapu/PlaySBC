# PlaySBC 1.0.0 Release Artifacts

This folder contains generated release artifacts for PlaySBC.

- Helm chart package: `helm/playsbc-1.0.0.tgz`
- Project license: MIT
- Chart version: `1.0.0`
- Application version: `1.0.0`

Rebuild the Helm package with:

```bash
helm package charts/playsbc --destination release/helm
```

## Container Image Deployment

The `.tgz` chart package contains Kubernetes manifests and config, not image layers. End users deploy the chart and point it at PlaySBC and RTPengine container images.

Published GHCR image example:

```bash
helm upgrade --install playsbc helm/playsbc-1.0.0.tgz \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set image.tag=1.0.0 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set rtpengine.image.tag=1.0.0
```

Local lab image example:

```bash
docker build -f docker/playsbc.Dockerfile -t playsbc:1.0.0 .
docker build -f docker/rtpengine.Dockerfile -t playsbc-rtpengine:1.0.0 .
helm upgrade --install playsbc helm/playsbc-1.0.0.tgz \
  --set image.repository=playsbc \
  --set image.tag=1.0.0 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=playsbc-rtpengine \
  --set rtpengine.image.tag=1.0.0
```

The chart can also deploy RTPengine by setting `rtpengine.enabled=true`. If disabled, PlaySBC can still point to an external RTPengine using `playsbc.config.rtpengine_url`.
