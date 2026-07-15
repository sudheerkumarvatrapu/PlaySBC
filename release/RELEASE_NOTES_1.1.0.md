# PlaySBC 1.1.0

Release date: 2026-07-14

PlaySBC 1.1.0 is the second packaged lab release. This release turns the Kubernetes and AI/Rasa work from today's lab into a repeatable release path: Helm deployment, Kubernetes Job-based regression, RASA-only evidence folders, GHCR image publishing, security scans, and release artifacts.

## Highlights

- Added optional real Rasa lab support through the Helm chart and `rasa/` project assets.
- Added RASA-only Kubernetes regression mode with separate output under `logs/RASA-Regression`.
- Added distinct AI/Rasa profile names and ladders for:
  - `ai-rasa-lab`: mock Rasa REST path.
  - `ai-rasa-rtpengine`: mock Rasa plus RTPengine-backed media path.
  - `ai-rasa-real-lab`: real Rasa pod plus RTPengine-backed media path.
- Stabilized the real Rasa pod startup using Helm-provided probes and longer rollout handling.
- Preserved full Kubernetes regression logs when running only Rasa profiles.
- Added Kubernetes regression Job execution that runs profiles inside the `playsbc` namespace and copies reports/evidence back locally.
- Added Kubernetes evidence collection for pods, deployment logs, CrashLoopBackOff previous logs, SIP ladders, PCAPs, RTCP observations, and regression reports.
- Kept local Docker regression behavior unchanged while adding the Kubernetes Job path.
- Documented deployment models for local regression, manual SIPp, local Kubernetes, published-image Kubernetes, external RTPengine, and maintainer publishing.

## Deployment Models Included

- Local Docker regression with PlaySBC, RTPengine, SIPp agents, RTCP helpers, and packet capture containers.
- Manual SIPp experiments on host loopback or Docker dual-realm agents.
- Local Kubernetes with locally built images using `kind` or `minikube`.
- Kubernetes deployment using GHCR images and the release Helm chart.
- Kubernetes deployment with external RTPengine.
- Maintainer release flow through GitHub Actions and Helm package assets.

## GHCR Images

The `v1.1.0` tag publishes the following images through GitHub Actions:

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.1.0`
- `ghcr.io/sudheerkumarvatrapu/playsbc:1.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.1.0`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.1.0`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.1.0`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.1`

The default branch build also publishes `latest` tags.

## Helm Install

Deploy PlaySBC and RTPengine from the release chart and GHCR images:

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.1.0/playsbc-1.1.0.tgz \
  --namespace playsbc \
  --create-namespace \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.1.0 \
  --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.1.0 \
  --set rtpengine.image.pullPolicy=Always \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://playsbc-playsbc-rtpengine:2223
```

## Kubernetes Regression

Full Kubernetes regression Job:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

RASA-only Kubernetes regression Job:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --rasa-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --kind-load-images \
  --kind-cluster playsbc
```

RASA-only output is written under `logs/RASA-Regression` and does not delete full-suite `logs/k8s-job` evidence.

## Release Assets

- `playsbc-1.1.0.tgz`: Helm chart package.
- `playsbc-1.1.0.tgz.sha256`: checksum for the Helm chart package.
- GitHub source code ZIP and TAR archives: generated automatically for tag `v1.1.0`.

## Validation

Validated before packaging:

- `python3 -m unittest tests.test_sipp_harness`
- `helm lint charts/playsbc`
- `helm package charts/playsbc --destination release/helm`
- `helm template playsbc release/helm/playsbc-1.1.0.tgz`
- `shasum -a 256 -c release/helm/playsbc-1.1.0.tgz.sha256`
- Kubernetes RASA regression run `rasa-regression-20260714-214532`: all 3 Rasa profiles passed.

GitHub Actions security scans run on push and release tags:

- CodeQL Python security and quality analysis.
- Trivy repository scan for vulnerabilities, secrets, and misconfigurations.
- Checkov Docker, Helm, Kubernetes, GitHub Actions, and secrets scan.
- Dependency Review on pull requests.

## Notes

- Issue/PR #49 remains the active tracking thread for the larger AI/Rasa lab work.
- Real STT/TTS speech synthesis and decoding remain future work behind the AI adapter boundary.
