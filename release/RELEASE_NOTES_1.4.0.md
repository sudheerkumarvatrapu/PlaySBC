# PlaySBC v1.4.0

PlaySBC `v1.4.0` is the HA failover lab release.

## Highlights

- Adds priority-0 HA Kubernetes regression profiles:
  - `ha-playsbc-precall-failover`
  - `ha-playsbc-midcall-failover`
  - `ha-playsbc-postcall-failover`
  - `ha-rtpengine-precall-failover`
  - `ha-rtpengine-midcall-recovery`
  - `ha-node-drain-active-calls`
  - `ha-active-active-load-distribution`
  - `ha-shared-registrar-dialog-restore`
- Adds shared B2BUA leg-state persistence so a sibling PlaySBC pod can restore enough outbound-leg context to handle ACK/BYE after pod failover.
- Adds runtime lab drain controls used by regression to prove active calls can finish while new INVITEs are rejected with `503 Node Draining`.
- Fixes active-active HA node identity by normalizing legacy `playsbc-a` / `playsbc-b` profile aliases to real StatefulSet pod IDs.
- Fixes RTPengine interface-failure regression by preserving deliberate `missing-core` / `missing-peer` directions through Helm rendering.
- Adds per-profile metrics settle time in Kubernetes regression so Prometheus has a final scrape window before the next Helm rollout.

## RTPengine HA Scope

- Pre-call RTPengine pod failure is covered as a clean recovery/routing case.
- Mid-call RTPengine failure is covered as best-effort recovery and cleanup evidence.
- True lossless RTPengine media-session migration depends on RTPengine-side replication/session ownership support and remains a v1.4.x follow-up if Sipwise limits prevent fully lossless migration in this lab.

## Regression And Evidence

- Kubernetes full regression now includes the HA failover profiles in `--all-profiles`.
- Single-call HA profiles include unified ladders with the K8s HA fault-injection step.
- HA restore evidence appears in `log.platform`, `log.call`, `log.media`, the combined PCAP, and the HTML report bundle.

## Images

Publish these images with the release:

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.4.0`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.4.0`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.4.0`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.4.0`

## Upgrade

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.4.0/playsbc-1.4.0.tgz \
  --namespace playsbc \
  --create-namespace \
  -f configs/kubernetes/active-active-values.yaml \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.4.0 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.4.0 \
  --set observability.enabled=true
```
