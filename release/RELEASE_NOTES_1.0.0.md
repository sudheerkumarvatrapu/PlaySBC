# PlaySBC 1.0.0

Release date: 2026-07-13

PlaySBC 1.0.0 is the first packaged lab release of the project: a Python-based SIP, RTP, B2BUA, transcoding, RTPengine, HA, and AI voice gateway playground built for SIPp regression and SBC-style experimentation.

## What Is Included

- SIP B2BUA call handling with inbound and outbound legs.
- Dynamic routing, registrar-backed endpoint lookup, route policies, trunk groups, and failure handling.
- Dual-realm core/peer regression topology for cleaner SBC-style call-flow evidence.
- G.711 PCMU and PCMA media handling with lab transcoding validation.
- SIPp regression coverage with combined SBC log bundles, SIP ladders, PCAP output, and HTML reports.
- RTPengine media backend integration using the open-source Sipwise RTPengine style of control-plane flow.
- SIP over UDP, TCP, TLS lab coverage, plus TLS/SRTP-to-RTP interworking scenarios.
- Digest authentication success and failure coverage.
- RFC 4733 DTMF event coverage.
- ESBC-style lab features: OPTIONS probing, trunk failover, E.164 normalization, header normalization, hunt groups, CAC, trunk metrics, and trunk failure cases.
- HA lab foundation: shared registrar/dialog state, active-active node identity, external load-balancer policy, node draining, RTPengine pairing, and OPTIONS health recovery.
- AI voice gateway lab path with Rasa REST adapter support, scripted STT/TTS adapter boundaries, bot action hooks, and RTPengine-backed AI media profiles.
- Kubernetes-ready Helm chart with health probes, secret-backed users, TLS secret hooks, and optional RTPengine deployment.
- MIT open-source license.

## Helm And Container Deployment

The Helm chart package contains Kubernetes manifests and default PlaySBC configuration. It does not contain Docker image layers. Kubernetes pulls PlaySBC and RTPengine images at deploy time.

The release publishes these container image names through GitHub Actions:

- `ghcr.io/sudheerkumarvatrapu/playsbc`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine`

After the `v1.0.0` release workflow completes, deploy both PlaySBC and RTPengine with:

```bash
helm upgrade --install playsbc release/helm/playsbc-1.0.0.tgz \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set image.tag=1.0.0 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set rtpengine.image.tag=1.0.0
```

For a local kind/minikube-style lab, build images locally and point Helm at them:

```bash
docker build -f docker/playsbc.Dockerfile -t playsbc:1.0.0 .
docker build -f docker/rtpengine.Dockerfile -t playsbc-rtpengine:1.0.0 .

helm upgrade --install playsbc release/helm/playsbc-1.0.0.tgz \
  --set image.repository=playsbc \
  --set image.tag=1.0.0 \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=playsbc-rtpengine \
  --set rtpengine.image.tag=1.0.0
```

If `rtpengine.enabled=false`, PlaySBC can still use an external RTPengine by setting:

```bash
--set playsbc.config.media_backend=rtpengine \
--set playsbc.config.rtpengine_url=udp://<rtpengine-host>:2223
```

## Release Assets

- `playsbc-1.0.0.tgz`: Helm chart package.
- `playsbc-1.0.0.tgz.sha256`: checksum for the Helm chart package.
- GitHub source archive: generated automatically by GitHub for tag `v1.0.0`.

## Validation

Release packaging validation:

- `helm lint charts/playsbc`
- `helm template playsbc release/helm/playsbc-1.0.0.tgz`
- `shasum -a 256 -c release/helm/playsbc-1.0.0.tgz.sha256`

Regression coverage in this release includes:

- Basic B2BUA signalling.
- G.711 media calls.
- PCMU-to-PCMA transcoding.
- RTPengine signalling, media, and transcoding profiles.
- TCP RTPengine transcoding.
- Real dual-realm RTPengine transcoding.
- Registered inbound and outbound calls.
- REGISTER auth success and failure.
- RFC 4733 DTMF.
- Invalid BYE, unknown route, failed outbound leg, CANCEL, and retransmission scenarios.
- ESBC OPTIONS, static trunk, E.164 policy, trunk failure, failover, normalization, hunt group, CAC, and metrics profiles.
- RTPengine control failure, port exhaustion, and interface-failure profiles.
- RTCP receiver quality for single-call media profiles.
- TLS/SRTP interworking lab profiles.
- AI Rasa lab and AI RTPengine lab profiles.
- Small load, soak, 5 cps / 60 second CHT, and RTPengine transcoding load profiles.

## Known Lab Boundaries

- HA support is a lab active-active foundation, not a production cluster implementation.
- True mid-call B2BUA node failover and RTPengine media-session migration are planned future enhancements.
- AI gateway support currently uses adapter boundaries and Rasa REST integration; real Whisper/Vosk STT and Piper/Coqui TTS media generation remain future work.
- Load profiles focus on SIP/RTP call completion and summary evidence; deep per-call RTCP and PCAP analysis is intentionally kept to single-call profiles.

## Upgrade Notes

This is the first packaged release. Existing local users should pull `main`, rebuild local images if needed, and use the Helm chart values to configure PlaySBC instead of ad hoc local JSON where possible.
