# PlaySBC v1.5.0 Draft

PlaySBC `v1.5.0` is the Azure AKS public-cloud validation target.

## Initial Scope

- Keep the existing kind/minikube Kubernetes regression path working with locally built v1.5.0 images.
- Evolve the AKS reference architecture from readiness validation to real cloud validation.
- Add production-style AKS networking guidance for SIP UDP/TCP/TLS and RTP/SRTP media ranges.
- Prepare the three-hardphone lab: register three devices through Azure public SIP ingress and validate calls between them.
- Continue hardening active-active PlaySBC/RTPengine behavior, observability, and failure evidence.

## Compatibility Rule

Before any v1.5.0 release, this command shape must remain valid in kind:

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
