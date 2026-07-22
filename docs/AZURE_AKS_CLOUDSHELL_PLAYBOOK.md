# Azure AKS Cloud Shell Playbook

This playbook is the exact first-cloud-lab path for PlaySBC on Azure: create a low-cost AKS test cluster, import PlaySBC images, deploy one PlaySBC pod plus one RTPengine pod, expose SIP and a small RTP media range with Azure LoadBalancers, run the AKS regression profiles from Cloud Shell, and download the evidence.

Official references:

- Azure free account: <https://azure.microsoft.com/free/>
- AKS Free tier: <https://learn.microsoft.com/en-us/azure/aks/free-standard-pricing-tiers>
- AKS static public IP and LoadBalancer annotations: <https://learn.microsoft.com/en-us/azure/aks/static-ip>

## Cost Guardrail

The Azure free account may include starter credit, but AKS worker VMs, public IPs, load balancers, storage, and ACR can still consume that credit. Use this lab for short validation runs, then delete both resource groups when finished.

```bash
az group delete --name "$AKS_RG" --yes --no-wait
az group delete --name "$NETWORK_RG" --yes --no-wait
```

## 1. Start From Azure Portal

1. Create or open your free Azure account at <https://portal.azure.com/>.
2. Open **Cloud Shell** from the top toolbar.
3. Pick **Bash**.
4. If Cloud Shell asks for a subscription, select your free subscription.
5. Ephemeral Cloud Shell is fine for this playbook; download the evidence bundle before closing the session.

If Cloud Shell asks for provider registration:

```bash
az provider register --namespace Microsoft.CloudShell
az provider show --namespace Microsoft.CloudShell --query registrationState -o tsv
```

## 2. Register Providers

```bash
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.ContainerService
az provider register --namespace Microsoft.Network
az provider register --namespace Microsoft.Compute
az provider register --namespace Microsoft.ManagedIdentity

az provider show --namespace Microsoft.ContainerRegistry --query registrationState -o tsv
az provider show --namespace Microsoft.ContainerService --query registrationState -o tsv
az provider show --namespace Microsoft.Network --query registrationState -o tsv
az provider show --namespace Microsoft.Compute --query registrationState -o tsv
az provider show --namespace Microsoft.ManagedIdentity --query registrationState -o tsv
```

Wait until all required providers show `Registered`.

## 3. Set Lab Variables

```bash
export LOCATION=eastus
export AKS_RG=playsbc-aks-rg
export NETWORK_RG=playsbc-network-rg
export AKS_NAME=playsbc-aks
export ACR_NAME=playsbcacr$RANDOM
export SIP_PIP_NAME=playsbc-sip-pip
export RTP_PIP_NAME=playsbc-rtp-pip
export DNS_LABEL=playsbc-sip-lab-$RANDOM
export RTP_DNS_LABEL=playsbc-rtp-lab-$RANDOM
export PLAYSBC_VERSION=1.5.1
```

Check the generated names:

```bash
echo "$ACR_NAME"
echo "$DNS_LABEL"
echo "$RTP_DNS_LABEL"
```

## 4. Create Resource Groups

```bash
az group create --name "$AKS_RG" --location "$LOCATION"
az group create --name "$NETWORK_RG" --location "$LOCATION"
```

## 5. Create ACR And Import Images

```bash
az acr create \
  --resource-group "$AKS_RG" \
  --name "$ACR_NAME" \
  --sku Basic

az acr import --name "$ACR_NAME" \
  --source ghcr.io/sudheerkumarvatrapu/playsbc:$PLAYSBC_VERSION \
  --image playsbc:$PLAYSBC_VERSION

az acr import --name "$ACR_NAME" \
  --source ghcr.io/sudheerkumarvatrapu/playsbc-rtpengine:$PLAYSBC_VERSION \
  --image playsbc-rtpengine:$PLAYSBC_VERSION

az acr import --name "$ACR_NAME" \
  --source ghcr.io/sudheerkumarvatrapu/playsbc-k8s-regression:$PLAYSBC_VERSION \
  --image playsbc-k8s-regression:$PLAYSBC_VERSION

az acr import --name "$ACR_NAME" \
  --source ghcr.io/sudheerkumarvatrapu/playsbc-sipp:$PLAYSBC_VERSION \
  --image playsbc-sipp:$PLAYSBC_VERSION
```

Verify:

```bash
az acr repository list --name "$ACR_NAME" -o table
az acr repository show-tags --name "$ACR_NAME" --repository playsbc -o table
az acr repository show-tags --name "$ACR_NAME" --repository playsbc-rtpengine -o table
```

## 6. Create AKS

Use the Free tier for this lab. Free tier means cluster management is free; the worker VM still costs money.

```bash
az aks create \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --location "$LOCATION" \
  --tier free \
  --node-count 1 \
  --node-vm-size Standard_D2as_v7 \
  --load-balancer-sku standard \
  --attach-acr "$ACR_NAME" \
  --generate-ssh-keys
```

If Azure rejects that VM size in your subscription or region, try one of these:

```text
Standard_D2s_v7
Standard_D2ds_v7
Standard_F2as_v7
```

Connect:

```bash
az aks get-credentials \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --overwrite-existing

kubectl get nodes
kubectl get pods -A
```

Expected: the AKS node is `Ready` and `kube-system` pods are `Running`.

## 7. Create Static Public IPs

SIP public IP:

```bash
az network public-ip create \
  --resource-group "$NETWORK_RG" \
  --name "$SIP_PIP_NAME" \
  --sku Standard \
  --allocation-method static \
  --version IPv4 \
  --dns-name "$DNS_LABEL"
```

RTP public IP:

```bash
az network public-ip create \
  --resource-group "$NETWORK_RG" \
  --name "$RTP_PIP_NAME" \
  --sku Standard \
  --allocation-method static \
  --version IPv4 \
  --dns-name "$RTP_DNS_LABEL"
```

Grant AKS permission to attach those IPs:

```bash
AKS_PRINCIPAL_ID=$(az aks show \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --query identity.principalId \
  -o tsv)

NETWORK_RG_ID=$(az group show \
  --name "$NETWORK_RG" \
  --query id \
  -o tsv)

az role assignment create \
  --assignee "$AKS_PRINCIPAL_ID" \
  --role "Network Contributor" \
  --scope "$NETWORK_RG_ID"
```

## 8. Deploy PlaySBC And RTPengine

Create a small Azure values file. This exposes SIP and 20 UDP RTP ports for lab validation.

```bash
NODE_RG=$(az aks show \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --query nodeResourceGroup \
  -o tsv)

cat > playsbc-azure-media-values.yaml <<EOF
cloud:
  provider: azure
  azure:
    enabled: true
    nodeResourceGroup: "$NODE_RG"
    sip:
      public:
        enabled: true
        publicIPResourceGroup: "$NETWORK_RG"
        publicIPName: "$SIP_PIP_NAME"
        dnsLabelName: "$DNS_LABEL"
    media:
      public:
        enabled: true
        publicIPResourceGroup: "$NETWORK_RG"
        publicIPName: "$RTP_PIP_NAME"
        dnsLabelName: "$RTP_DNS_LABEL"
        ports:
          - 30000
          - 30001
          - 30002
          - 30003
          - 30004
          - 30005
          - 30006
          - 30007
          - 30008
          - 30009
          - 30010
          - 30011
          - 30012
          - 30013
          - 30014
          - 30015
          - 30016
          - 30017
          - 30018
          - 30019

topology:
  activeActive:
    enabled: false

replicaCount: 1

observability:
  enabled: false

rtpengine:
  enabled: true
  replicas: 1
  hostNetwork: false
  rtpMin: 30000
  rtpMax: 30019
EOF
```

Deploy:

```bash
helm upgrade --install playsbc \
  https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v$PLAYSBC_VERSION/playsbc-$PLAYSBC_VERSION.tgz \
  --namespace playsbc \
  --create-namespace \
  -f playsbc-azure-media-values.yaml \
  --set image.repository="$ACR_NAME.azurecr.io/playsbc" \
  --set-string image.tag="$PLAYSBC_VERSION" \
  --set image.pullPolicy=Always \
  --set rtpengine.image.repository="$ACR_NAME.azurecr.io/playsbc-rtpengine" \
  --set-string rtpengine.image.tag="$PLAYSBC_VERSION" \
  --set rtpengine.image.pullPolicy=Always
```

Verify:

```bash
kubectl -n playsbc get pods -o wide
kubectl -n playsbc get svc -o wide
kubectl -n playsbc logs deployment/playsbc-playsbc --tail=80
kubectl -n playsbc logs deployment/playsbc-playsbc-rtpengine --tail=80
```

Expected services:

```text
playsbc-playsbc-azure-sip-public   LoadBalancer   EXTERNAL-IP
playsbc-playsbc-azure-rtp-public   LoadBalancer   EXTERNAL-IP
```

## 9. Check PlaySBC Health

Cloud Shell may reserve local port `8080`, so check from inside the pod:

```bash
kubectl -n playsbc exec deployment/playsbc-playsbc -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/readyz').read().decode())"

kubectl -n playsbc exec deployment/playsbc-playsbc -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/metrics').read().decode()[:1000])"
```

Expected:

```text
ready
playsbc_active_calls 0
```

## 10. Optional External SIPp Sanity

From your Mac, run OPTIONS to the Azure SIP public IP:

```bash
sipp <SIP_PUBLIC_IP>:5062 \
  -sf sipp/scenarios/options.xml \
  -s playsbc \
  -i 0.0.0.0 \
  -p 5065 \
  -m 1 \
  -trace_msg \
  -trace_err
```

Then check Cloud Shell:

```bash
kubectl -n playsbc logs deployment/playsbc-playsbc --tail=120
```

Expected:

```text
SIP OPTIONS from <your-public-ip>:5065
```

## 11. Run AKS Regression From Cloud Shell

Clone the release source because Cloud Shell is ephemeral:

```bash
rm -rf PlaySBC-v$PLAYSBC_VERSION
git clone --branch v$PLAYSBC_VERSION --depth 1 https://github.com/sudheerkumarvatrapu/PlaySBC.git PlaySBC-v$PLAYSBC_VERSION
cd PlaySBC-v$PLAYSBC_VERSION
```

Run the AKS profile set:

```bash
PYTHONPYCACHEPREFIX=/tmp/playsbc-pycache python3 tools/run_k8s_regression_job.py \
  --aks-profiles \
  --aks-mode \
  --aks-require-azure-services \
  --aks-require-static-sip \
  --aks-require-public-sip-ingress \
  --runner-image "$ACR_NAME.azurecr.io/playsbc-k8s-regression:$PLAYSBC_VERSION" \
  --sipp-image "$ACR_NAME.azurecr.io/playsbc-sipp:$PLAYSBC_VERSION" \
  --playsbc-image "$ACR_NAME.azurecr.io/playsbc:$PLAYSBC_VERSION" \
  --rtpengine-image "$ACR_NAME.azurecr.io/playsbc-rtpengine:$PLAYSBC_VERSION" \
  --set-playsbc-image \
  --set-rtpengine-image \
  --no-load-playsbc-image \
  --no-load-rtpengine-image \
  --no-load-sipp-image \
  --rtpengine-enabled \
  --no-active-active-topology \
  --job-timeout 3600
```

AKS profiles:

| Profile | What It Proves |
| --- | --- |
| `esbc-options-keepalive` | Public SIP listener and OPTIONS response. |
| `register-auth-success` | SIP Digest registration path. |
| `registered-inbound` | Registrar-backed B2BUA routing. |
| `rtpengine-media` | G.711 media anchored by RTPengine. |
| `rtpengine-transcoding` | RTPengine-backed G.711 transcoding profile. |
| `tcp-rtpengine-transcoding` | SIP over TCP with RTPengine-backed media. |
| `tls-transport-policy` | SIP TLS transport policy path. |
| `tls-srtp-to-udp-rtp` | TLS/SRTP side to UDP/RTP side interop. |
| `udp-rtp-to-tls-srtp` | UDP/RTP side to TLS/SRTP side interop. |
| `rtcp-receiver-quality` | RTCP receiver quality evidence. |

## 12. Open Or Download Reports

Find the latest run:

```bash
RUN=$(ls -td ~/PlaySBC-v$PLAYSBC_VERSION/logs/AKS-Regression/aks-regression-* | head -1)
echo "$RUN"
tail -120 "$RUN/runner.log"
ls -l "$RUN/AKS-reports"
```

HTML report:

```text
$RUN/AKS-reports/latest.html
```

Download from Cloud Shell:

1. Click **Upload/Download files** in the Cloud Shell toolbar.
2. Choose **Download**.
3. Paste the expanded path, for example:

```text
/home/sudheer/PlaySBC-v1.5.1/logs/AKS-Regression/aks-regression-YYYYMMDD-HHMMSS/AKS-reports/latest.html
```

For the full evidence bundle:

```bash
cd ~/PlaySBC-v$PLAYSBC_VERSION/logs/AKS-Regression
tar -czf latest-aks-regression.tgz "$(basename "$RUN")"
```

Download:

```text
/home/sudheer/PlaySBC-v1.5.1/logs/AKS-Regression/latest-aks-regression.tgz
```

## 13. Useful Debug Commands

```bash
kubectl -n playsbc get pods -o wide
kubectl -n playsbc get svc -o wide
kubectl -n playsbc get events --sort-by=.lastTimestamp | tail -80
kubectl -n playsbc logs deployment/playsbc-playsbc --tail=160
kubectl -n playsbc logs deployment/playsbc-playsbc-rtpengine --tail=160
helm -n playsbc get values playsbc
helm -n playsbc get manifest playsbc | less
```

## 14. Clean Up

When testing is finished:

```bash
az group delete --name "$AKS_RG" --yes --no-wait
az group delete --name "$NETWORK_RG" --yes --no-wait
```

This deletes AKS, ACR, public IPs, load balancers, and related lab resources.

