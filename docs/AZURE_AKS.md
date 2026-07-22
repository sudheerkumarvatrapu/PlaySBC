# PlaySBC On Azure AKS

This is the single Azure guide for PlaySBC. It covers the low-cost Cloud Shell lab, AKS deployment, SIP/RTP LoadBalancers, AKS regression, report download, credential recovery, and cleanup.

Use this as a lab path, not a production SBC claim. Keep test windows short and delete the resource groups when finished.

## What This Deploys

```text
Mac / SIPp / SIP peer
  -> Azure public SIP LoadBalancer
     -> PlaySBC pod
        -> RTPengine pod
           -> Azure public RTP LoadBalancer
```

The first Azure lab intentionally uses:

- one AKS node,
- one PlaySBC pod,
- one RTPengine pod,
- explicit lab RTP ports,
- AKS regression profiles only.

Active-active, full media ranges, hardphones, and production-grade state are future cloud hardening work.

## Release Track

| Release | Focus |
| --- | --- |
| `v1.4.3` | AKS Helm values and Azure LoadBalancer service templates. |
| `v1.4.4` | AKS validation profiles and Azure regression evidence. |
| `v1.5.0` | First Azure public-cloud validation target. |
| `v1.5.1` | Cloud Shell end-to-end lab playbook. |
| `v1.5.2` | Cloud Shell credential and ephemeral-report recovery. |
| `v1.5.3` | Async Azure cleanup monitoring. |
| `v1.5.4` | Merged Azure/Cloud Shell docs into this single crisp guide. |

## Cost Guardrail

The Azure free account may include starter credit, but AKS worker VMs, public IPs, load balancers, storage, and ACR can still consume that credit.

Final cleanup is complete only when both return `false`:

```bash
az group exists --name "$AKS_RG"
az group exists --name "$NETWORK_RG"
```

## 1. Start Cloud Shell

1. Open <https://portal.azure.com/>.
2. Start **Cloud Shell**.
3. Select **Bash**.
4. Use your free subscription.

If Cloud Shell asks for provider registration:

```bash
az provider register --namespace Microsoft.CloudShell
az provider show --namespace Microsoft.CloudShell --query registrationState -o tsv
```

Cloud Shell can be ephemeral. Download AKS regression evidence before closing the session.

## 2. Set Variables

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
export PLAYSBC_VERSION=1.5.4
```

## 3. Register Azure Providers

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

Continue after they show `Registered`.

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

az acr repository list --name "$ACR_NAME" -o table
```

## 6. Create AKS

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

If Azure rejects that VM size in your subscription or region, try:

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

Expected: one AKS node is `Ready`.

## 7. Create Public IPs

```bash
az network public-ip create \
  --resource-group "$NETWORK_RG" \
  --name "$SIP_PIP_NAME" \
  --sku Standard \
  --allocation-method static \
  --version IPv4 \
  --dns-name "$DNS_LABEL"

az network public-ip create \
  --resource-group "$NETWORK_RG" \
  --name "$RTP_PIP_NAME" \
  --sku Standard \
  --allocation-method static \
  --version IPv4 \
  --dns-name "$RTP_DNS_LABEL"
```

Grant AKS permission to attach them:

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

Create the lab values file:

```bash
NODE_RG=$(az aks show \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --query nodeResourceGroup \
  -o tsv)

cat > playsbc-azure-lab-values.yaml <<EOF
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
  -f playsbc-azure-lab-values.yaml \
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

Expected:

```text
playsbc-playsbc                    1/1 Running
playsbc-playsbc-rtpengine          1/1 Running
playsbc-playsbc-azure-sip-public   LoadBalancer EXTERNAL-IP
playsbc-playsbc-azure-rtp-public   LoadBalancer EXTERNAL-IP
```

## 9. Health Check

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

## 10. Optional External SIPp Check

From your Mac:

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

Then in Cloud Shell:

```bash
kubectl -n playsbc logs deployment/playsbc-playsbc --tail=120
```

Expected:

```text
SIP OPTIONS from <your-public-ip>:5065
```

## 11. Run AKS Regression

Clone the matching release source:

```bash
rm -rf PlaySBC-v$PLAYSBC_VERSION
git clone --branch v$PLAYSBC_VERSION --depth 1 https://github.com/sudheerkumarvatrapu/PlaySBC.git PlaySBC-v$PLAYSBC_VERSION
cd PlaySBC-v$PLAYSBC_VERSION
```

Run AKS profiles:

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

AKS profiles currently cover OPTIONS, REGISTER auth, registered inbound routing, RTPengine media, RTPengine transcoding, SIP TCP, SIP TLS, SRTP/RTP interop, and RTCP quality evidence.

## 12. Download Report Evidence

Find the latest run:

```bash
RUN=$(ls -td ~/PlaySBC-v$PLAYSBC_VERSION/logs/AKS-Regression/aks-regression-* | head -1)
echo "$RUN"
tail -120 "$RUN/runner.log"
ls -l "$RUN/AKS-reports"
```

Download the HTML report:

```text
$RUN/AKS-reports/latest.html
```

Package and download the full bundle:

```bash
cd ~/PlaySBC-v$PLAYSBC_VERSION/logs/AKS-Regression
tar -czf latest-aks-regression.tgz "$(basename "$RUN")"
ls -lh latest-aks-regression.tgz
```

Download this path from the Cloud Shell toolbar:

```text
/home/sudheer/PlaySBC-v1.5.4/logs/AKS-Regression/latest-aks-regression.tgz
```

If the path is missing after reconnecting to Cloud Shell, the session was ephemeral. Rerun regression and download the `.tgz` immediately.

## 13. Recover Kube Credentials

In a new Cloud Shell session, re-export stable names:

```bash
export LOCATION=eastus
export AKS_RG=playsbc-aks-rg
export NETWORK_RG=playsbc-network-rg
export AKS_NAME=playsbc-aks
export PLAYSBC_VERSION=1.5.4
export ACR_NAME=$(az acr list --resource-group "$AKS_RG" --query "[0].name" -o tsv)
```

Try:

```bash
az aks get-credentials \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --overwrite-existing
```

If Azure CLI returns `InvalidApiVersionParameter`, use the REST fallback:

```bash
SUB_ID=$(az account show --query id -o tsv)
mkdir -p ~/.kube

az rest \
  --method post \
  --url "https://management.azure.com/subscriptions/$SUB_ID/resourceGroups/$AKS_RG/providers/Microsoft.ContainerService/managedClusters/$AKS_NAME/listClusterUserCredential?api-version=2025-04-01" \
  --query "kubeconfigs[0].value" \
  -o tsv | base64 -d > ~/.kube/config

chmod 600 ~/.kube/config
kubectl get pods -n playsbc
```

## 14. Cleanup

Start cleanup:

```bash
az group delete --name "$AKS_RG" --yes --no-wait
az group delete --name "$NETWORK_RG" --yes --no-wait
```

`--no-wait` is asynchronous. It is normal to see:

```text
AKS_RG exists: true
NETWORK_RG exists: false
```

That means the network group is gone while AKS is still deleting. `kubectl` may still show pods until the AKS API server disappears.

Wait until both groups are gone:

```bash
while true; do
  date
  echo "AKS_RG exists: $(az group exists --name "$AKS_RG")"
  echo "NETWORK_RG exists: $(az group exists --name "$NETWORK_RG")"

  if [ "$(az group exists --name "$AKS_RG")" = "false" ] && \
     [ "$(az group exists --name "$NETWORK_RG")" = "false" ]; then
    echo "AKS lab cleanup completed."
    break
  fi

  if [ "$(az group exists --name "$AKS_RG")" = "true" ]; then
    az resource list \
      --resource-group "$AKS_RG" \
      --query "[].{name:name,type:type}" \
      -o table
  fi

  sleep 30
done
```

Final cost-stop gate:

```text
AKS_RG exists: false
NETWORK_RG exists: false
```

After that, `kubectl get pods -n playsbc` should fail with an AKS API DNS error. That is expected.

## Hardening Still To Do

- Full RTP/SRTP media range model for production Azure networking.
- Public/private realm separation with Azure CNI, Multus, or equivalent.
- Redis/PostgreSQL shared registrar and dialog state.
- Multi-zone AKS and node/pod failure testing.
- Three-hardphone registration and calling lab.
- Azure Monitor managed Prometheus / Managed Grafana option.

## References

- Azure free account: <https://azure.microsoft.com/free/>
- AKS Free tier: <https://learn.microsoft.com/azure/aks/free-standard-pricing-tiers>
- AKS static public IP: <https://learn.microsoft.com/azure/aks/static-ip>
- AKS Standard LoadBalancer: <https://learn.microsoft.com/azure/aks/configure-load-balancer-standard>
