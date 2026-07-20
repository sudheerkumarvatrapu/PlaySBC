# PlaySBC v1.4.2

PlaySBC `v1.4.2` is a Kubernetes HA regression-runner hotfix release.

## Fixed

- Kubernetes regression profile Helm upgrades now recover from the StatefulSet immutable `serviceName` migration introduced by the active-active headless service model.
- The runner detects Helm errors such as `spec: Forbidden: updates to statefulset spec`, deletes the PlaySBC StatefulSet with `--cascade=orphan`, and retries the same Helm upgrade once.
- Final Helm restore uses the same recovery path, so a failed profile no longer leaves the full HA suite marked failed purely because Helm could not patch an immutable StatefulSet field.

## Why This Matters

`v1.4.1` correctly introduced direct per-pod Prometheus scraping through a PlaySBC headless service, but existing clusters could still have an older StatefulSet spec in place. Kubernetes does not allow changing `spec.serviceName` in-place, so HA-only or full K8s regression runs could fail during the first per-profile Helm upgrade before SIP, RTP, HA, or observability validation actually started.

This release makes the regression runner handle that migration automatically.

## Published Images

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.4.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.4.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.4.2`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.4.2`

## Upgrade

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.4.2/playsbc-1.4.2.tgz \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.4.2 \
  --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.4.2 \
  --set rtpengine.image.pullPolicy=Always
```

## Verification

- Targeted HA regression harness unit tests passed.
- `tools/run_k8s_regression.py` compiles with `PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache`.
- Helm chart packaging completed for `playsbc-1.4.2.tgz`.
