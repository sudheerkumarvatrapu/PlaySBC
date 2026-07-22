# PlaySBC v1.5.1

PlaySBC `v1.5.1` is the Azure AKS Cloud Shell playbook release.

## Scope

- Add a complete Azure Cloud Shell deployment playbook for a low-cost first AKS lab.
- Document the validated path from free Azure account to working PlaySBC and RTPengine pods.
- Capture the exact ACR import, AKS creation, static public SIP/RTP LoadBalancer, Helm deploy, health check, AKS regression, evidence download, and cleanup steps.
- Keep `v1.5.0` runtime behavior intact while making the Azure onboarding path repeatable.

## Artifacts

- Helm chart: `playsbc-1.5.1.tgz`
- PlaySBC image: `ghcr.io/sudheerkumarvatrapu/playsbc:1.5.1`
- RTPengine image: `ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:1.5.1`
- Regression runner image: `ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.5.1`
- SIPp image: `ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.5.1`

## New Documentation

- Azure Cloud Shell steps. These are now merged into `docs/AZURE_AKS.md` so the Azure path has one guide.

The playbook covers:

- Azure provider registration.
- Resource group creation.
- ACR creation and GHCR image import.
- AKS Free tier cluster creation.
- Static public SIP and RTP IP creation.
- AKS identity `Network Contributor` permission.
- One PlaySBC pod plus one RTPengine pod deployment.
- SIP and RTP Azure LoadBalancer validation.
- In-pod health and metrics checks.
- Optional external SIPp OPTIONS sanity.
- AKS regression execution from Cloud Shell.
- HTML report and evidence bundle download.
- Full Azure cleanup.

## Validation

The playbook was created from a successful Azure Cloud Shell run where:

- PlaySBC pod was `Running`.
- RTPengine pod was `Running`.
- Public SIP LoadBalancer was assigned.
- Public RTP LoadBalancer was assigned.
- External SIP OPTIONS and REGISTER reached PlaySBC.
- AKS regression profiles completed green.

## Notes

This remains a lab validation release, not a carrier-production Azure SBC claim. Keep Azure test windows short and delete the lab resource groups after evidence is downloaded.
