# PlaySBC v1.5.2

PlaySBC `v1.5.2` is an Azure AKS Cloud Shell resilience release.

## What Changed

- Documented the Cloud Shell credential-refresh failure where `az aks get-credentials` can hit an Azure CLI API-version mismatch.
- Added the working `az rest` fallback using `listClusterUserCredential?api-version=2025-04-01`.
- Documented that ephemeral Cloud Shell sessions can lose local AKS regression logs after reconnect.
- Added explicit steps to re-export Azure lab variables after a fresh Cloud Shell session.
- Added an evidence-bundle workflow for AKS regression reports:
  - find the latest `logs/AKS-Regression/aks-regression-*` run,
  - package it as `latest-aks-regression.tgz`,
  - download it immediately from Cloud Shell.
- Restored the Azure release-track wording so `v1.5.1` remains the first Cloud Shell playbook milestone and `v1.5.2` is the recovery/evidence hardening milestone.

## Runtime Scope

No SIP, RTP, RTPengine, regression-runner, or Helm runtime behavior changed in this release.

## Artifacts

- Helm chart: `playsbc-1.5.2.tgz`
- PlaySBC image: `ghcr.io/sudheerkumarvatrapu/playsbc:1.5.2`
- RTPengine image: `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.5.2`
- Regression runner image: `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.5.2`
- SIPp image: `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.5.2`

## Validation

- Helm chart lint.
- Helm template render.
- Documentation pass for the validated Azure free-account AKS flow.

## Azure Notes

If a later Cloud Shell session cannot find old report files, the files were not persisted. The AKS workloads may still be running, but the local HTML/JSON evidence is gone. Rerun the AKS regression and download `latest-aks-regression.tgz` before closing Cloud Shell.
