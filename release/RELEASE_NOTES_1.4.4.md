# PlaySBC v1.4.4

PlaySBC `v1.4.4` is the Azure AKS readiness release.

## Scope

This release does not replace the stable local lab baseline. Keep `v1.4.2` for kind, minikube, and local regression repeatability. Use `v1.4.4` for the Azure-first cloud track.

## Added

- `--aks-profiles` for the Kubernetes regression Job runner.
- Dedicated AKS output folder: `logs/AKS-Regression`.
- Azure LoadBalancer evidence in every AKS bundle:
  - `aks-services.json`
  - `aks-services-wide.log`
  - `aks-services-describe.log`
  - `aks-validation.json`
- Validation for Azure public SIP LoadBalancer service shape:
  - `LoadBalancer` type
  - `externalTrafficPolicy: Local`
  - SIP UDP/TCP and SIP TLS ports
  - static public IP annotation
- Optional strict validation for assigned external SIP ingress with `--aks-require-public-sip-ingress`.
- Per-exposure source CIDR controls for Azure public SIP, private SIP, and lab RTP services.
- Updated `docs/AZURE_AKS.md` with AKS deployment and readiness regression commands.

## AKS Readiness Profile Set

`--aks-profiles` runs:

- `esbc-options-keepalive`
- `register-auth-success`
- `registered-inbound`
- `rtpengine-media`
- `rtpengine-transcoding`
- `tcp-rtpengine-transcoding`
- `tls-transport-policy`
- `tls-srtp-to-udp-rtp`
- `udp-rtp-to-tls-srtp`
- `rtcp-receiver-quality`

## Images

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.4.4`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.4.4`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.4.4`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.4.4`

## Known Boundary

Full RTP/SRTP media range exposure on AKS is still targeted for `v1.5.0`. `v1.4.4` validates single-call RTPengine media/transcoding paths and keeps lab RTP LoadBalancer ports explicit.
