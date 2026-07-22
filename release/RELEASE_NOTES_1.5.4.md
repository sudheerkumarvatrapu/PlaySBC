# PlaySBC v1.5.4

PlaySBC `v1.5.4` is an Azure documentation cleanup release.

## What Changed

- Merged the separate Azure AKS and Cloud Shell playbook content into one guide: `docs/AZURE_AKS.md`.
- Removed the duplicate `docs/AZURE_AKS_CLOUDSHELL_PLAYBOOK.md` page.
- Trimmed the Azure guide into one clearer flow:
  - Cloud Shell setup,
  - Azure providers,
  - ACR import,
  - AKS creation,
  - SIP/RTP public IPs,
  - PlaySBC/RTPengine deploy,
  - health checks,
  - AKS regression,
  - evidence download,
  - kube credential recovery,
  - cleanup verification.
- Removed the stale Cloud Shell playbook link from the README.
- Updated the evolution plan with the v1.5.4 documentation milestone.

## Runtime Scope

No SIP, RTP, RTPengine, Kubernetes regression, or Helm runtime behavior changed in this release.

## Artifacts

- Helm chart: `playsbc-1.5.4.tgz`
- PlaySBC image: `ghcr.io/sudheerkumarvatrapu/playsbc:1.5.4`
- RTPengine image: `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.5.4`
- Regression runner image: `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.5.4`
- SIPp image: `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.5.4`

## Validation

- Helm chart lint.
- Helm template render with AKS values.
- Documentation reference scan for the removed Cloud Shell playbook path.
