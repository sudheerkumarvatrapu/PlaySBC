# PlaySBC v1.5.0

PlaySBC `v1.5.0` is the Azure AKS public-cloud validation target.

## Scope

- Keep the existing kind/minikube Kubernetes regression path working with locally built v1.5.0 images.
- Evolve the AKS reference architecture from readiness validation to real cloud validation.
- Add production-style AKS networking guidance for SIP UDP/TCP/TLS and the RTP/SRTP media-range validation path.
- Prepare the three-hardphone lab: register three devices through Azure public SIP ingress and validate calls between them.
- Continue hardening active-active PlaySBC/RTPengine behavior, observability, and failure evidence.

## AKS Deployment Artifacts

- Helm chart: `playsbc-1.5.0.tgz`
- PlaySBC image: `ghcr.io/sudheerkumarvatrapu/playsbc:1.5.0`
- RTPengine image: `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.5.0`
- Regression runner image: `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.5.0`
- SIPp image: `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.5.0`

## Compatibility Rule

The kind/minikube safety path remains:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --all-profiles \
  --build-playsbc-image \
  --build-runner-image \
  --build-sipp-image \
  --build-rtpengine-image \
  --kind-load-images \
  --set-playsbc-image \
  --set-rtpengine-image \
  --kind-cluster playsbc
```

That command builds PlaySBC, RTPengine, SIPp, and regression-runner images from the current source tree, loads them into kind, and runs the full Kubernetes regression suite without depending on published v1.5.0 images.

## Validation Note

This release was packaged for Azure portal validation. Full AKS regression and real Azure device testing are expected to be run from the target Azure environment.
