# PlaySBC v1.4.1

PlaySBC `v1.4.1` is a focused HA observability and ladder hotfix release.

## Highlights

- Fixes Grafana transcoding visibility by counting `playsbc_media_negotiations_total{transcoding="true"}` in the selected time range.
- Adds a Grafana `Transcoding By PlaySBC Node` panel so PCMU-to-PCMA evidence is visible per active-active replica.
- Changes Prometheus scraping in active-active mode from sticky Service scraping to direct StatefulSet pod scraping:
  - `playsbc-playsbc-0`
  - `playsbc-playsbc-1`
- Adds a PlaySBC headless Service for stable StatefulSet pod metrics targets.
- Updates HA ladders to show explicit nodes:
  - `PlaySBC-1`
  - `PlaySBC-2`
  - `RTPengine-1`
  - `RTPengine-2`
- Makes HA shared-state and active-active load-distribution profiles generate PCMU-to-PCMA transcoding evidence.

## Why This Matters

In `v1.4.0`, Grafana could miss transcoding sessions during HA runs because Prometheus scraped the normal PlaySBC Service, and `ClientIP` affinity could keep scrapes on one backend pod. `v1.4.1` scrapes each PlaySBC StatefulSet pod directly, so counters survive active-active routing and pod rollover better.

## Images

Publish these images with the release:

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.4.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.4.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.4.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.4.1`

## Upgrade

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.4.1/playsbc-1.4.1.tgz \
  --namespace playsbc \
  --create-namespace \
  -f configs/kubernetes/active-active-values.yaml \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.4.1 \
  --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.4.1 \
  --set rtpengine.image.pullPolicy=Always \
  --set rtpengine.hostNetwork=false \
  --set observability.enabled=true
```

## Validation

- Focused unit checks passed for Helm active-active topology, observability dashboard, HA ladder labels, and HA transcoding profile metadata.
- Helm active-active chart render passed with observability enabled.
