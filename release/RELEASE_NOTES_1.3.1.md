# PlaySBC 1.3.1 Release Notes

`v1.3.1` is a hotfix release for the `v1.3.x` observability line.

## Highlights

- Restores the main README to the expandable deployment-model style while keeping current `v1.3.1` release metadata.
- Updates the Grafana dashboard color model:
  - request stats and request time series use blue or Grafana's multi-color palette
  - response stats use purple
  - response time series use multiple colors
  - 4xx/5xx response series are red
  - AI/Rasa and RTPengine failure series are red
  - active/healthy normal-state stat panels remain green
  - peak-active historical range panel uses orange
- Updates chart/version metadata so the Kubernetes regression runner image contains the new dashboard template and does not overwrite Grafana back to the old all-green dashboard during profile runs.

## Validation

- `helm template playsbc charts/playsbc --namespace playsbc --set observability.enabled=true --set observability.grafana.enabled=true --set observability.prometheus.enabled=true`
- `helm lint charts/playsbc`
- `PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 -m unittest tests.test_sipp_harness.SippScenarioTests.test_helm_chart_includes_observability_stack`

## Deployment

Use the `v1.3.1` chart and images for Kubernetes regression:

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.3.1/playsbc-1.3.1.tgz \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.3.1 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.3.1
```

Then run regression with:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --runner-image ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.3.1 \
  --sipp-image ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.3.1 \
  --playsbc-image ghcr.io/sudheerkumarvatrapu/playsbc:1.3.1 \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --no-load-sipp-image \
  --kind-cluster playsbc
```
