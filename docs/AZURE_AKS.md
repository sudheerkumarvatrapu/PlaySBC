# PlaySBC On Azure AKS

This is the Azure-first deployment track for PlaySBC. The goal is to move from a local SBC lab to an AKS-hosted SBC reference architecture without pretending the first cloud cut is already carrier production.

## Release Track

| Release | Focus |
| --- | --- |
| `v1.4.3` | AKS Helm values, Azure Load Balancer service templates, static public IP wiring, private SIP service option, and observability-ready install commands. |
| `v1.4.4` | AKS validation profiles, Azure-specific regression evidence, TLS certificate runbook, split public/private SIP exposure hardening, and single-call media dataplane checks. |
| `v1.5.0` | Production-style AKS reference architecture with full RTP/SRTP media range model, dedicated node pools, NSG/Azure Firewall rules, external shared state, backup/restore, multi-zone failure tests, and a three-hardphone lab. |

## Target AKS Shape

```text
Internet / SIP trunk peers
  -> Azure Standard Load Balancer
     -> PlaySBC active-active StatefulSet
        -> paired RTPengine StatefulSet
           -> RTP/SRTP media ports

Private enterprise/core side
  -> optional internal Azure Load Balancer
     -> same PlaySBC active-active pods

Observability
  -> Prometheus
  -> Grafana PlaySBC Core/Peer SBC Lab dashboard
```

## What v1.5.0 Provides

- `configs/kubernetes/aks-values.yaml`
- Azure public SIP LoadBalancer service:
  - SIP UDP on `service.sipPort`
  - SIP TCP on `service.sipPort`
  - SIP TLS on `service.tlsPort`
  - health TCP on `service.healthPort`
- Optional Azure internal SIP LoadBalancer service for private/core-side reachability.
- Optional RTPengine public UDP media service for explicit lab ports.
- Per-exposure source CIDR controls for public SIP, private SIP, and lab RTP services.
- `--aks-profiles` Kubernetes regression shortcut with dedicated `logs/AKS-Regression` evidence.
- AKS bundle evidence:
  - `aks-services.json`
  - `aks-services-wide.log`
  - `aks-services-describe.log`
  - `aks-validation.json`
- 31-day Prometheus retention and Grafana dashboard enabled by values.
- Published `v1.5.0` image/chart coordinates for Azure portal validation.

## Important Media Note

Kubernetes Service objects do not express a compact UDP port range like `30000-32000`. Listing thousands of RTP ports in one Service is ugly and not the production answer.

For `v1.5.0`, PlaySBC keeps RTPengine media range configuration in Helm values and supports a small explicit media-port list for lab exposure. Full production RTP/SRTP range exposure on AKS remains a cloud-validation hardening item using dedicated Azure networking: node pools, NSGs, Azure Firewall or equivalent, static IP/NAT behavior, and RTPengine advertised-address handling.

## Azure Prerequisites

- Azure CLI logged in.
- AKS cluster using Standard Load Balancer.
- Helm and `kubectl`.
- GHCR images reachable from the AKS nodes.
- Static Public IP for SIP ingress.
- Network Contributor permission for the AKS cluster identity on the Public IP resource group.

Microsoft AKS currently recommends service annotations such as `service.beta.kubernetes.io/azure-pip-name` or `service.beta.kubernetes.io/azure-load-balancer-ipv4` for static IP assignment rather than relying on deprecated `loadBalancerIP`.

## Create Azure Resources

Set your names:

```bash
export LOCATION=eastus
export AKS_RG=playsbc-aks-rg
export NETWORK_RG=playsbc-network-rg
export AKS_NAME=playsbc-aks
export SIP_PIP_NAME=playsbc-sip-pip
export DNS_LABEL=playsbc-sip-lab
```

Create resource groups:

```bash
az group create --name "$AKS_RG" --location "$LOCATION"
az group create --name "$NETWORK_RG" --location "$LOCATION"
```

Create AKS:

```bash
az aks create \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --location "$LOCATION" \
  --node-count 3 \
  --load-balancer-sku standard \
  --generate-ssh-keys

az aks get-credentials --resource-group "$AKS_RG" --name "$AKS_NAME"
kubectl create namespace playsbc
```

Create a static public IP:

```bash
az network public-ip create \
  --resource-group "$NETWORK_RG" \
  --name "$SIP_PIP_NAME" \
  --sku Standard \
  --allocation-method static \
  --version IPv4
```

Allow AKS to attach that IP:

```bash
export AKS_PRINCIPAL_ID=$(az aks show \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --query identity.principalId \
  -o tsv)

export NETWORK_RG_ID=$(az group show --name "$NETWORK_RG" --query id -o tsv)

az role assignment create \
  --assignee "$AKS_PRINCIPAL_ID" \
  --role "Network Contributor" \
  --scope "$NETWORK_RG_ID"
```

Find the AKS node resource group:

```bash
export NODE_RG=$(az aks show \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --query nodeResourceGroup \
  -o tsv)
```

## Deploy PlaySBC

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.5.0/playsbc-1.5.0.tgz \
  --namespace playsbc \
  --create-namespace \
  -f configs/kubernetes/aks-values.yaml \
  --set cloud.azure.nodeResourceGroup="$NODE_RG" \
  --set cloud.azure.sip.public.publicIPResourceGroup="$NETWORK_RG" \
  --set cloud.azure.sip.public.publicIPName="$SIP_PIP_NAME" \
  --set cloud.azure.sip.public.dnsLabelName="$DNS_LABEL" \
  --set image.repository=ghcr.io/sudheerkumarvatrapu/playsbc \
  --set-string image.tag=1.5.0 \
  --set rtpengine.image.repository=ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine \
  --set-string rtpengine.image.tag=1.5.0
```

Wait for workloads:

```bash
kubectl -n playsbc rollout status statefulset/playsbc-playsbc --timeout=300s
kubectl -n playsbc rollout status statefulset/playsbc-playsbc-rtpengine --timeout=300s
kubectl -n playsbc rollout status deployment/playsbc-playsbc-prometheus --timeout=300s
kubectl -n playsbc rollout status deployment/playsbc-playsbc-grafana --timeout=300s
```

Check services:

```bash
kubectl -n playsbc get svc -o wide
kubectl -n playsbc describe svc playsbc-playsbc-azure-sip-public
```

## TLS

Create a TLS secret before enabling SIP TLS:

```bash
kubectl -n playsbc create secret tls playsbc-sip-tls \
  --cert=/path/to/tls.crt \
  --key=/path/to/tls.key

helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v1.5.0/playsbc-1.5.0.tgz \
  --namespace playsbc \
  -f configs/kubernetes/aks-values.yaml \
  --set tls.enabled=true \
  --set tls.existingSecret=playsbc-sip-tls
```

## Run AKS Readiness Regression

Run this after the Helm rollout is ready. It validates the Azure LoadBalancer service objects before each profile and stores local evidence under `logs/AKS-Regression`.

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --aks-profiles \
  --runner-image ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.5.0 \
  --sipp-image ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.5.0 \
  --playsbc-image ghcr.io/sudheerkumarvatrapu/playsbc:1.5.0 \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --no-load-sipp-image
```

Use the stricter form only when Azure has already assigned the external SIP IP:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --aks-profiles \
  --aks-require-public-sip-ingress \
  --runner-image ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:1.5.0 \
  --sipp-image ghcr.io/sudheerkumarvatrapu/playsbc-sipp:1.5.0 \
  --playsbc-image ghcr.io/sudheerkumarvatrapu/playsbc:1.5.0 \
  --set-playsbc-image \
  --no-load-playsbc-image \
  --no-load-sipp-image
```

The v1.5.0 AKS profile set covers:

| Profile | Purpose |
| --- | --- |
| `esbc-options-keepalive` | SIP listener and OPTIONS reachability. |
| `register-auth-success` | SIP Digest REGISTER plus B2BUA call setup. |
| `registered-inbound` | Registrar-backed inbound call routing. |
| `rtpengine-media` | G.711 RTP call anchored by RTPengine. |
| `rtpengine-transcoding` | PCMU-to-PCMA transcoding intent with RTPengine. |
| `tcp-rtpengine-transcoding` | SIP over TCP plus media anchoring. |
| `tls-transport-policy` | SIP over TLS transport policy. |
| `tls-srtp-to-udp-rtp` | TLS/SRTP core leg to UDP/RTP peer leg. |
| `udp-rtp-to-tls-srtp` | UDP/RTP core leg to TLS/SRTP peer leg. |
| `rtcp-receiver-quality` | RTCP receiver-report quality analytics. |

## Firewall And Port Checklist

| Direction | Protocol | Port / Range | Purpose |
| --- | --- | --- | --- |
| Internet or SIP peer -> PlaySBC | UDP | `5060` | SIP UDP |
| Internet or SIP peer -> PlaySBC | TCP | `5060` | SIP TCP |
| Internet or SIP peer -> PlaySBC | TCP | `5061` | SIP TLS |
| Load Balancer -> PlaySBC | TCP | `8080` | Health and metrics |
| SIP/RTP peers -> RTPengine | UDP | `30000-32000` | RTP/SRTP media range target |
| PlaySBC -> RTPengine | UDP | `2223` | RTPengine NG control |
| Operators -> Grafana | TCP | `3000` | Dashboard, usually via private access or port-forward |
| Operators -> Prometheus | TCP | `9090` | Metrics, usually private |

Keep production source ranges narrow. Do not expose Grafana or Prometheus publicly.

## Observability

```bash
kubectl -n playsbc port-forward svc/playsbc-playsbc-grafana 3000:3000
```

Open:

```text
http://127.0.0.1:3000/d/playsbc-sbc-lab/playsbc-core-peer-sbc-lab
```

Prometheus keeps 31 days of data by default in the AKS values file.

## AKS Hardening Work Remaining

- Full RTP/SRTP media range design using Azure networking rather than giant Service port lists.
- Public/private realm separation with Multus or Azure CNI overlay-friendly alternatives.
- Redis/PostgreSQL shared registrar/dialog state.
- Multi-zone AKS, PodDisruptionBudgets, node affinity, topology spread, and controlled drain tests.
- Azure Monitor managed Prometheus / Managed Grafana option beside the in-chart lab stack.
- Capacity tests with increasing registrations, CPS, concurrent calls, RTP sessions, and soak duration.
- Real hardphone lab: register three SIP devices and call between them through Azure public SIP IP/DNS.

## References

- Microsoft AKS static public IP with LoadBalancer: <https://learn.microsoft.com/azure/aks/static-ip>
- Microsoft AKS Standard Load Balancer annotations and client IP behavior: <https://learn.microsoft.com/azure/aks/configure-load-balancer-standard>
- Microsoft AKS internal LoadBalancer: <https://learn.microsoft.com/azure/aks/internal-lb>
