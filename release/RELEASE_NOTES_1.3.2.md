# PlaySBC 1.3.2 Release Notes

`v1.3.2` is a hotfix release for the `v1.3.x` Kubernetes observability and AI/RTPengine evidence line.

## Highlights

- Fixes Grafana range counters for Kubernetes regression rollovers.
  - Calls started, completed calls, SIP requests, SIP responses, media negotiations, and transcoding sessions now use `increase(...)`.
  - Current active calls remains an instant gauge.
  - Peak active calls remains a range high-water gauge.
- Fixes AI Voice Gateway RTPengine post-call evidence.
  - PlaySBC records a live RTPengine query snapshot immediately after AI RTPengine `ANSWER`.
  - If long AI/TTS processing means RTPengine has already aged out the call-id by final teardown, PlaySBC logs cached post-answer evidence instead of a false `Unknown call-id` failure.
- Keeps the v1.3.x K8s regression model unchanged: PlaySBC, RTPengine, Prometheus, Grafana, and the regression runner remain separately deployable.

## Validation

- `PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 -m unittest tests.test_mini_call_server.RtpengineRetryTests`
- `PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 -m unittest tests.test_sipp_harness.SippScenarioTests.test_helm_chart_includes_observability_stack`
- `PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 -m py_compile mini_call_server.py`
- `helm lint charts/playsbc`
- `helm template playsbc charts/playsbc --namespace playsbc --set observability.enabled=true --set observability.grafana.enabled=true --set observability.prometheus.enabled=true`

## Deployment

Use the `v1.3.2` chart and images for Kubernetes regression:

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.3.2/playsbc-1.3.2.tgz \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.3.2 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.3.2 \
  --set observability.enabled=true
```

Then run full Kubernetes regression with:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --runner-image ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.3.2 \
  --sipp-image ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.3.2 \
  --playsbc-image ghcr.io/sudheerkumarvatrapu/playsbc:1.3.2 \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --no-load-sipp-image \
  --kind-cluster playsbc
```
