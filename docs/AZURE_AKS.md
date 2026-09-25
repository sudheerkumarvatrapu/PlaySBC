# PlaySBC On Azure AKS

This is the canonical Azure guide for creating a small AKS lab, deploying PlaySBC/RTPengine, running AKS regression, downloading evidence, recovering credentials, and deleting the lab. Use [REAL_DEVICE_LAB.md](REAL_DEVICE_LAB.md) only after this base deployment is healthy.

The Azure exposure track was introduced in `v1.5.0`, and `v2.4.0` added strict public-media evidence validation. Every runnable command below targets the current `v4.0.0` baseline.

Keep AKS test windows short. Local kind is the normal lane for full regression and HA/failover iteration.

## Lab Shape

```text
Internet SIP peer
  -> Azure SIP LoadBalancer
     -> PlaySBC (1 pod)
        -> RTPengine (1 pod)
           -> Azure RTP LoadBalancer, UDP 30000-30049
```

The default AKS readiness lane is intentionally single-workload. Pass explicit HA settings only for a cloud HA milestone.

## 1. First-Time Azure Setup

Open Azure Cloud Shell in Bash and set stable names:

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
export PLAYSBC_VERSION=4.0.0
```

Cloud Shell variables are ephemeral. Re-export them after reconnecting. For an existing ACR, derive its name instead of generating a new one:

```bash
export ACR_NAME=$(az acr list --resource-group "$AKS_RG" --query '[0].name' -o tsv)
```

Register providers once:

```bash
for PROVIDER in \
  Microsoft.CloudShell \
  Microsoft.ContainerRegistry \
  Microsoft.ContainerService \
  Microsoft.Network \
  Microsoft.Compute \
  Microsoft.ManagedIdentity; do
  az provider register --namespace "$PROVIDER"
done

az provider list \
  --query "[?contains(namespace, 'Microsoft.Container') || namespace=='Microsoft.Network' || namespace=='Microsoft.Compute' || namespace=='Microsoft.ManagedIdentity'].{provider:namespace,state:registrationState}" \
  -o table
```

Continue when the required providers show `Registered`.

Create resource groups and ACR:

```bash
az group create --name "$AKS_RG" --location "$LOCATION"
az group create --name "$NETWORK_RG" --location "$LOCATION"

az acr create \
  --resource-group "$AKS_RG" \
  --name "$ACR_NAME" \
  --sku Basic
```

Import all release images:

```bash
for IMAGE in playsbc playsbc-rtpengine playsbc-k8s-regression playsbc-sipp; do
  az acr import \
    --name "$ACR_NAME" \
    --source "ghcr.io/sudheerkumarvatrapu/$IMAGE:$PLAYSBC_VERSION" \
    --image "$IMAGE:$PLAYSBC_VERSION" \
    --force
done

export ACR_LOGIN_SERVER=$(az acr show \
  --resource-group "$AKS_RG" \
  --name "$ACR_NAME" \
  --query loginServer -o tsv)

echo "$ACR_LOGIN_SERVER"
```

Always use Azure's returned `ACR_LOGIN_SERVER`. Do not construct `$ACR_NAME.azurecr.io`; an empty Cloud Shell variable produces `InvalidImageName` workloads.

Create one-node AKS:

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

If that VM size is unavailable, choose another two-vCPU size offered by the subscription/region.

Create static SIP and RTP public IPs:

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

Grant both AKS identities permission on the network resource group:

```bash
NETWORK_RG_ID=$(az group show --name "$NETWORK_RG" --query id -o tsv)
CLUSTER_ID=$(az aks show -g "$AKS_RG" -n "$AKS_NAME" --query identity.principalId -o tsv)
KUBELET_ID=$(az aks show -g "$AKS_RG" -n "$AKS_NAME" --query identityProfile.kubeletidentity.objectId -o tsv)

for OBJECT_ID in "$CLUSTER_ID" "$KUBELET_ID"; do
  az role assignment create \
    --assignee-object-id "$OBJECT_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "Network Contributor" \
    --scope "$NETWORK_RG_ID" || true
done
```

Connect:

```bash
az aks get-credentials \
  --resource-group "$AKS_RG" \
  --name "$AKS_NAME" \
  --overwrite-existing

kubectl get nodes
```

## 2. Deploy Or Upgrade

Re-export stable values in every new Cloud Shell session:

```bash
export PLAYSBC_VERSION=4.0.0
export AKS_RG=playsbc-aks-rg
export NETWORK_RG=playsbc-network-rg
export AKS_NAME=playsbc-aks
export SIP_PIP_NAME=playsbc-sip-pip
export RTP_PIP_NAME=playsbc-rtp-pip
export ACR_NAME=$(az acr list --resource-group "$AKS_RG" --query '[0].name' -o tsv)
export ACR_LOGIN_SERVER=$(az acr show -g "$AKS_RG" -n "$ACR_NAME" --query loginServer -o tsv)
export NODE_RG=$(az aks show -g "$AKS_RG" -n "$AKS_NAME" --query nodeResourceGroup -o tsv)
export SIP_PUBLIC_IP=$(az network public-ip show -g "$NETWORK_RG" -n "$SIP_PIP_NAME" --query ipAddress -o tsv)
export RTP_PUBLIC_IP=$(az network public-ip show -g "$NETWORK_RG" -n "$RTP_PIP_NAME" --query ipAddress -o tsv)
```

Verify before Helm changes anything:

```bash
: "${ACR_NAME:?Missing ACR_NAME}"
: "${ACR_LOGIN_SERVER:?Missing ACR_LOGIN_SERVER}"
: "${NODE_RG:?Missing NODE_RG}"
: "${SIP_PUBLIC_IP:?Missing SIP_PUBLIC_IP}"
: "${RTP_PUBLIC_IP:?Missing RTP_PUBLIC_IP}"

for IMAGE in playsbc playsbc-rtpengine; do
  az acr repository show \
    --name "$ACR_NAME" \
    --image "$IMAGE:$PLAYSBC_VERSION" \
    -o none
done
```

Deploy atomically so a failed pull cannot replace the healthy revision:

```bash
helm upgrade --install playsbc \
  "https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v${PLAYSBC_VERSION}/playsbc-${PLAYSBC_VERSION}.tgz" \
  --namespace playsbc \
  --create-namespace \
  --reuse-values \
  --atomic \
  --wait \
  --timeout 10m \
  --set cloud.provider=azure \
  --set cloud.azure.enabled=true \
  --set cloud.azure.nodeResourceGroup="$NODE_RG" \
  --set cloud.azure.sip.public.enabled=true \
  --set cloud.azure.sip.public.publicIPResourceGroup="$NETWORK_RG" \
  --set cloud.azure.sip.public.publicIPName="$SIP_PIP_NAME" \
  --set cloud.azure.media.public.enabled=true \
  --set cloud.azure.media.public.publicIPResourceGroup="$NETWORK_RG" \
  --set cloud.azure.media.public.publicIPName="$RTP_PIP_NAME" \
  --set cloud.azure.media.public.portRange.enabled=true \
  --set cloud.azure.media.public.portRange.min=30000 \
  --set cloud.azure.media.public.portRange.max=30049 \
  --set topology.activeActive.enabled=false \
  --set replicaCount=1 \
  --set image.repository="$ACR_LOGIN_SERVER/playsbc" \
  --set-string image.tag="$PLAYSBC_VERSION" \
  --set image.pullPolicy=Always \
  --set rtpengine.enabled=true \
  --set rtpengine.replicas=1 \
  --set rtpengine.image.repository="$ACR_LOGIN_SERVER/playsbc-rtpengine" \
  --set-string rtpengine.image.tag="$PLAYSBC_VERSION" \
  --set rtpengine.image.pullPolicy=Always \
  --set rtpengine.rtpMin=30000 \
  --set rtpengine.rtpMax=30049 \
  --set-string rtpengine.advertisedIP="$RTP_PUBLIC_IP" \
  --set playsbc.config.rtp_min=30000 \
  --set playsbc.config.rtp_max=30049 \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://playsbc-playsbc-rtpengine:2223 \
  --set-string playsbc.config.sip_advertised_ip="$SIP_PUBLIC_IP" \
  --set-string playsbc.config.b2bua_advertised_ip="$SIP_PUBLIC_IP"
```

Verify:

```bash
kubectl -n playsbc rollout status deployment/playsbc-playsbc --timeout=240s
kubectl -n playsbc rollout status deployment/playsbc-playsbc-rtpengine --timeout=240s
kubectl -n playsbc get pods -o wide
kubectl -n playsbc get svc playsbc-playsbc-azure-sip-public playsbc-playsbc-azure-rtp-public -o wide
```

Do not test until both services have external IPs and they match `SIP_PUBLIC_IP` and `RTP_PUBLIC_IP`.

## 3. Pending LoadBalancer

Inspect events:

```bash
kubectl -n playsbc describe svc playsbc-playsbc-azure-sip-public | sed -n '/Events:/,$p'
kubectl -n playsbc describe svc playsbc-playsbc-azure-rtp-public | sed -n '/Events:/,$p'
```

- `EnsuringLoadBalancer`: wait for Azure allocation.
- `AuthorizationFailed` on `Microsoft.Network/publicIPAddresses/read`: repeat the Network Contributor assignments for `CLUSTER_ID` and `KUBELET_ID`, wait for RBAC propagation, and let the services reconcile.
- Wrong public IP: verify the service annotations reference `$NETWORK_RG`, `$SIP_PIP_NAME`, and `$RTP_PIP_NAME`.

After correcting RBAC:

```bash
kubectl -n playsbc delete svc \
  playsbc-playsbc-azure-sip-public \
  playsbc-playsbc-azure-rtp-public

helm upgrade playsbc \
  "https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v${PLAYSBC_VERSION}/playsbc-${PLAYSBC_VERSION}.tgz" \
  --namespace playsbc \
  --reuse-values

watch -n 5 'kubectl -n playsbc get svc playsbc-playsbc-azure-sip-public playsbc-playsbc-azure-rtp-public -o wide'
```

## 4. Health Check

```bash
kubectl -n playsbc exec deployment/playsbc-playsbc -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/readyz').read().decode())"

kubectl -n playsbc exec deployment/playsbc-playsbc -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/metrics').read().decode()[:1000])"
```

Expected: `ready` and `playsbc_active_calls 0`.

## 5. Run AKS Regression

Use the latest `main` checkout for the Cloud Shell launcher and post-release safety checks. Keep all four runtime images pinned to the immutable release tag; do not use a mutable `latest` image tag. This gives each run the newest launcher fixes without silently changing the tested PlaySBC, RTPengine, runner, or SIPp version.

Refresh the launcher first. Cloning `main` creates a normal branch checkout and avoids the harmless detached-HEAD message produced when cloning a release tag:

```bash
cd ~
rm -rf PlaySBC-main
git clone --branch main --single-branch --depth 1 \
  https://github.com/sudheerkumarvatrapu/PlaySBC.git \
  PlaySBC-main
cd PlaySBC-main
git log --oneline -1
```

Run image import separately so a missing tag is obvious:

```bash
export PLAYSBC_VERSION=4.0.0
export AKS_RG=playsbc-aks-rg
export ACR_NAME=$(az acr list --resource-group "$AKS_RG" --query '[0].name' -o tsv)
export ACR_LOGIN_SERVER=$(az acr show -g "$AKS_RG" -n "$ACR_NAME" --query loginServer -o tsv)

for IMAGE in playsbc playsbc-rtpengine playsbc-k8s-regression playsbc-sipp; do
  echo "Importing $IMAGE:$PLAYSBC_VERSION"
  az acr import \
    --name "$ACR_NAME" \
    --source "ghcr.io/sudheerkumarvatrapu/$IMAGE:$PLAYSBC_VERSION" \
    --image "$IMAGE:$PLAYSBC_VERSION" \
    --force || break
done
```

Then run from the refreshed checkout:

```bash
cd PlaySBC-main

PYTHONPYCACHEPREFIX=/tmp/playsbc-pycache \
python3 tools/run_k8s_regression_job.py \
  --aks-profiles \
  --runner-image "$ACR_LOGIN_SERVER/playsbc-k8s-regression:$PLAYSBC_VERSION" \
  --runner-image-pull-policy Always \
  --sipp-image "$ACR_LOGIN_SERVER/playsbc-sipp:$PLAYSBC_VERSION" \
  --sipp-image-pull-policy Always \
  --playsbc-image "$ACR_LOGIN_SERVER/playsbc:$PLAYSBC_VERSION" \
  --rtpengine-image "$ACR_LOGIN_SERVER/playsbc-rtpengine:$PLAYSBC_VERSION" \
  --set-playsbc-image \
  --set-rtpengine-image \
  --aks-load-balancer-wait-timeout 1200 \
  --job-timeout 3600
```

Expected startup output includes the selected release/main commit from `git log`, followed by the AKS profile count. The report must show baseline image tag `4.0.0`; this proves the launcher and runtime image contract are aligned.

The `--aks-profiles` shortcut enforces Azure services, static SIP, public SIP/RTP ingress, UDP `30000-30049`, and single-workload topology. Image references are validated before Helm or Job mutation.

AKS profile runs default both regression images to `imagePullPolicy: Always`. Release tags are immutable; the pull policy protects explicit `main` compatibility runs from stale node-cached runner code. Confirm the GHCR workflow and all four ACR imports completed before launching the Job.

For the mixed `tls-srtp-to-udp-rtp` and `udp-rtp-to-tls-srtp` profiles, the runner starts `send_srtp_audio.py` as a separate, tracked process in the secure endpoint pod. The helper advertises a deterministic SDES key, binds before the call, learns RTPengine's source endpoint from the first received packet, and returns authenticated AES-CM/HMAC-SHA1-80 PCMU traffic. Its command, stdout, stderr, timeout, and exit status are first-class regression evidence. SIPp XML contains SDP only; it does not launch shell commands. This is synthetic regression-endpoint behavior and does not alter the real-device Helm media policy.

The secure helper binds reserved one-call profile ports `6000/UDP` for RTP and `6001/UDP` for RTCP. The same values are explicit in rendered SDP and the runner command. Plain RTP profiles continue using SIPp's dynamic media-port allocation. If encrypted packets reach the secure pod but none return, inspect `core-secure-srtp-sender/` or `peer-secure-srtp-sender/` before investigating Azure networking.

Stop only regression resources:

```bash
kubectl -n playsbc delete job \
  -l app.kubernetes.io/name=playsbc-k8s-regression-runner \
  --ignore-not-found

kubectl -n playsbc delete pod \
  -l app.kubernetes.io/name=playsbc-k8s-regression-runner \
  --ignore-not-found
```

## 6. Evidence

```bash
RUN=$(ls -td ~/PlaySBC-main/logs/AKS-Regression/aks-regression-* | head -1)
echo "$RUN"
tail -120 "$RUN/runner.log"
ls -lh "$RUN/AKS-reports/latest.html"

ARCHIVE=~/PlaySBC-main/logs/AKS-Regression/latest-aks-regression.tgz
ls -lh "$ARCHIVE"
tar -tzf "$ARCHIVE" | head -40
```

Download the `.tgz` immediately. Cloud Shell storage can be ephemeral.

Each single-call profile keeps one combined `capture.pcap`, one `sipmsg.log`, focused logs, and an HTML report. Load profiles may omit PCAP by design.

## 7. Credential Recovery

Try normal refresh first:

```bash
export AKS_RG=playsbc-aks-rg
export AKS_NAME=playsbc-aks

az aks get-credentials -g "$AKS_RG" -n "$AKS_NAME" --overwrite-existing
```

If Azure CLI returns `InvalidApiVersionParameter`, first verify that the active subscription contains the cluster:

```bash
az account show --query '{subscription:name,id:id}' -o table
az aks list --query '[].{name:name,resourceGroup:resourceGroup}' -o table
```

If `playsbc-aks` is absent, select the subscription that owns it and list the clusters again:

```bash
az account list -o table
az account set --subscription '<subscription-id-containing-playsbc-aks>'
az aks list --query '[].{name:name,resourceGroup:resourceGroup}' -o table
```

Then use the guarded REST fallback. It discovers the resource group from Azure, rejects empty variables, and only replaces kubeconfig after Azure returns a non-empty credential. Do not redirect the REST response straight to `~/.kube/config`: an Azure error would truncate the working file.

```bash
set -euo pipefail

export AKS_NAME=playsbc-aks
export AKS_RG=$(az aks list \
  --query "[?name=='playsbc-aks'].resourceGroup | [0]" \
  -o tsv)
export SUB_ID=$(az account show --query id -o tsv)

: "${SUB_ID:?Subscription ID is empty}"
: "${AKS_RG:?playsbc-aks was not found in the active subscription}"
: "${AKS_NAME:?AKS name is empty}"

echo "SUB_ID=$SUB_ID"
echo "AKS_RG=$AKS_RG"
echo "AKS_NAME=$AKS_NAME"

TMP_KUBECONFIG=$(mktemp)

az rest \
  --method post \
  --url "https://management.azure.com/subscriptions/${SUB_ID}/resourceGroups/${AKS_RG}/providers/Microsoft.ContainerService/managedClusters/${AKS_NAME}/listClusterUserCredential?api-version=2025-04-01" \
  --query 'kubeconfigs[0].value' \
  -o tsv | base64 -d > "$TMP_KUBECONFIG"

test -s "$TMP_KUBECONFIG"
mkdir -p ~/.kube
mv "$TMP_KUBECONFIG" ~/.kube/config
chmod 600 ~/.kube/config

kubectl config current-context
kubectl get nodes
kubectl get pods -n playsbc
```

An error URL containing `resourceGroups/providers/.../managedClusters/listClusterUserCredential` means both `$AKS_RG` and `$AKS_NAME` were empty. Re-run the guarded block above; it stops before calling Azure when cluster discovery fails.

## 8. Cleanup

Download the evidence archive first. Cloud Shell sessions can change tenants or subscriptions, so discover the active subscription and verify the exact PlaySBC groups before deleting anything:

```bash
unset SUB_ID
export SUB_ID=$(az account show --query id -o tsv)
export AKS_RG=playsbc-aks-rg
export NETWORK_RG=playsbc-network-rg

: "${SUB_ID:?No active Azure subscription}"

echo "SUB_ID=$SUB_ID"
az account show -o table
az group list \
  --subscription "$SUB_ID" \
  --query "[?name=='$AKS_RG' || name=='$NETWORK_RG'].{Name:name,Location:location}" \
  -o table
```

Both named groups must appear in the table. If they do not, stop and select the subscription that owns the AKS lab with `az account list -o table` and `az account set --subscription <id>`.

Submit both asynchronous deletions only after verification:

```bash
for RG in "$AKS_RG" "$NETWORK_RG"; do
  EXISTS=$(az group exists \
    --subscription "$SUB_ID" \
    --name "$RG")

  echo "$RG exists: $EXISTS"

  if [ "$EXISTS" = "true" ]; then
    az group delete \
      --subscription "$SUB_ID" \
      --name "$RG" \
      --yes \
      --no-wait
  fi
done
```

Monitor until both checks return `false`; the loop exits automatically:

```bash
while true; do
  AKS_EXISTS=$(az group exists --subscription "$SUB_ID" --name "$AKS_RG")
  NETWORK_EXISTS=$(az group exists --subscription "$SUB_ID" --name "$NETWORK_RG")

  date
  echo "AKS_RG exists: $AKS_EXISTS"
  echo "NETWORK_RG exists: $NETWORK_EXISTS"

  if [ "$AKS_EXISTS" = "false" ] && [ "$NETWORK_EXISTS" = "false" ]; then
    echo "PlaySBC AKS lab deletion completed."
    break
  fi

  sleep 30
done
```

The AKS-managed `MC_playsbc-aks-rg_playsbc-aks_eastus` group should disappear with `playsbc-aks-rg`; do not delete it separately. Keep Azure-managed groups such as `NetworkWatcherRG`. The lab is not cost-stopped until both PlaySBC groups report `false`.

## Boundaries

- Use local multi-node kind for frequent HA and failover work.
- Use AKS for Azure identity, ACR, public LoadBalancer, static IP, firewall, and real-device milestones.
- Use [REAL_DEVICE_LAB.md](REAL_DEVICE_LAB.md) for OBi1022/Zoiper configuration and packet capture.
- Production still requires external shared state, multi-zone proof, security hardening, capacity baselines, and long soak tests.
