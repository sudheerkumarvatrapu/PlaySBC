# PlaySBC Release Artifacts

This directory contains immutable release notes and packaged Helm charts. Deployment and regression commands live in the canonical runbooks so they do not drift between releases.

## Current Release

| Item | Value |
| --- | --- |
| Version | `3.0.0` |
| Chart | `helm/playsbc-3.0.0.tgz` |
| Checksum | `helm/playsbc-3.0.0.tgz.sha256` |
| Notes | `RELEASE_NOTES_3.0.0.md` |
| Product guide | `../output/pdf/PlaySBC-v3.0.0-Product-Guide.pdf` |
| License | MIT |

Published images:

```text
ghcr.io/sudheerkumarvatrapu/playsbc:3.0.0
ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:3.0.0
ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:3.0.0
ghcr.io/sudheerkumarvatrapu/playsbc-sipp:3.0.0
```

The chart contains Kubernetes manifests and configuration, not image layers.

## Canonical Workflows

| Task | Runbook |
| --- | --- |
| Docker, kind, minikube, AKS, and real-device administration | [PlaySBC v3.0.0 Product Guide](../output/pdf/PlaySBC-v3.0.0-Product-Guide.pdf) |
| Release scope and next milestones | [EVOLUTION_PLAN.md](../docs/EVOLUTION_PLAN.md) |

## Build The Chart

```bash
helm package charts/playsbc --destination release/helm
shasum -a 256 release/helm/playsbc-3.0.0.tgz \
  > release/helm/playsbc-3.0.0.tgz.sha256
```

## Release Gate

Before tagging a release:

1. Update `VERSION`, `charts/playsbc/Chart.yaml`, the current release notes, and package checksum.
2. Keep `README.md` and the canonical runbooks on the same version.
3. Verify local Docker/SIPp, kind or minikube, AKS, AI/Rasa, and real-device paths affected by the change.
4. Build and publish all four container images.
5. Confirm the chart asset is attached to the GitHub release.

Historical `RELEASE_NOTES_<version>.md` files are release records and should not be rewritten during documentation cleanup.
