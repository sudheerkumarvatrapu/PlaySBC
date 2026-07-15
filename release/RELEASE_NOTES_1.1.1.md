# PlaySBC 1.1.1

Release date: 2026-07-15

PlaySBC 1.1.1 is the first hotfix release on the 1.1.x line. It keeps the 1.1.0 Kubernetes/Rasa release intact and fixes the Kubernetes regression timeout policy for 60 second CHT load profiles.

## Fix

- Expanded Kubernetes SIPp timeout calculation for load profiles.
- Kept single-call profile timeout behavior unchanged.
- `load-5cps-60s` now uses a 300 second SIPp timeout in Kubernetes.
- `load-5cps-60s-rtpengine-transcoding` now uses a 600 second SIPp timeout in Kubernetes.
- Added unit coverage for the Kubernetes load timeout policy.

## Why

The original 1.1.0 Kubernetes runner inherited the Docker/local SIPp timeout formula:

```text
traffic_seconds + hold_seconds + 60
```

For 300 calls at 5 CPS with 60 second CHT, that produced a 180 second SIPp timeout. That was too tight for Kubernetes when pod scheduling, Helm rollouts, SIPp startup, RTPengine anchoring, and media drain time are all part of the same profile lifecycle.

## Validation

- `python3 -m unittest tests.test_sipp_harness`: 116 tests passed.
- Targeted Kubernetes regression run `k8s-regression-20260715-202747`:
  - `load-5cps-60s`: passed, 300/300 successful calls, 0 failed calls.
  - `load-5cps-60s-rtpengine-transcoding`: passed, 300/300 successful calls, 0 failed calls.

## Release Assets

- `playsbc-1.1.1.tgz`: Helm chart package.
- `playsbc-1.1.1.tgz.sha256`: checksum for the Helm chart package.
- GitHub source code ZIP and TAR archives: generated automatically for tag `v1.1.1`.

## GHCR Images

The `v1.1.1` tag publishes:

- `ghcr.io/sudheerkumarvatrapu/playsbc:1.1.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc:1.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.1.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.1.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.1.1`
- `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.1`

## Versioning

- `1.1.x` is the hotfix line for the 1.1.0 Kubernetes/Rasa release.
- Future hotfixes should increment the patch version through the 1.1.x line: `1.1.2` through `1.1.9` as needed.
- The next larger GA feature release should be `1.2.0`.
