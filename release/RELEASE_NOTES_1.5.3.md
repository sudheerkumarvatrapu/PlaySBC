# PlaySBC v1.5.3

PlaySBC `v1.5.3` is an Azure AKS Cloud Shell cleanup hardening release.

## What Changed

- Documented that `az group delete --no-wait` starts cleanup asynchronously.
- Added the expected cleanup observation where:
  - `NETWORK_RG exists: false`
  - `AKS_RG exists: true`
- Explained that AKS pods can still appear briefly through `kubectl` while the AKS API server is still alive.
- Added commands to inspect remaining resources inside the AKS resource group during cleanup.
- Added a wait loop that confirms both lab resource groups are fully deleted.
- Added the final Azure cost-stop gate:
  - `AKS_RG exists: false`
  - `NETWORK_RG exists: false`

## Runtime Scope

No SIP, RTP, RTPengine, Kubernetes regression, or Helm runtime behavior changed in this release.

## Artifacts

- Helm chart: `playsbc-1.5.3.tgz`
- PlaySBC image: `ghcr.io/sudheerkumarvatrapu/playsbc:1.5.3`
- RTPengine image: `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.5.3`
- Regression runner image: `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.5.3`
- SIPp image: `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.5.3`

## Validation

- Helm chart lint.
- Helm template render with AKS values.
- Documentation pass for Azure Cloud Shell cleanup and deletion monitoring.

## Azure Notes

Do not treat `az group delete --no-wait` as immediate cleanup. The command returns before Azure finishes deleting the resources. Wait until both the AKS resource group and network resource group no longer exist before assuming the short-lived lab has stopped consuming resources.
