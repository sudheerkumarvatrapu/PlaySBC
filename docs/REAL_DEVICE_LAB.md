# PlaySBC Real-Device Lab

This guide covers the validated OBi1022 `1001` and Zoiper `1002` call flow in AKS and the local LAN lane. Build the AKS base with [AKS.md](AKS.md), or use the separate [kind real-device procedure](KUBERNETES_HELM_RUNBOOK.md#dedicated-local-real-device-lab).

```text
OBi1022 1001
  -> Internet/NAT -> Azure SIP LB -> PlaySBC -> RTPengine -> Azure RTP LB
  -> Internet/NAT -> Zoiper 1002
```

Validated baseline: SIP UDP `5062`, RTP/RTCP UDP `30000-30049`, PCMU/PCMA, digest REGISTER, and two-way RTPengine-anchored audio.

| Lane | SIP/RTP advertised address | Kube context | What it proves |
| --- | --- | --- | --- |
| AKS | Azure SIP and RTP public IPs | AKS context | Public LB, NAT, ACR, internet devices |
| Local kind | Mac LAN IPv4 | `kind-playsbc-real-device` | LAN registration, calls, media, capture |

Do not reuse the local values file in AKS or the AKS values file locally. The capture tool accepts `--context` so evidence collection cannot silently follow whichever context was selected last.

## 1. Apply Real-Device Values

Run in Cloud Shell after the base AKS services have public IPs:

```bash
export PLAYSBC_VERSION=3.0.0
export AKS_RG=playsbc-aks-rg
export NETWORK_RG=playsbc-network-rg
export AKS_NAME=playsbc-aks
export SIP_PIP_NAME=playsbc-sip-pip
export RTP_PIP_NAME=playsbc-rtp-pip

export ACR_NAME=$(az acr list --resource-group "$AKS_RG" --query '[0].name' -o tsv)
export ACR_LOGIN_SERVER=$(az acr show -g "$AKS_RG" -n "$ACR_NAME" --query loginServer -o tsv)
export SIP_PUBLIC_IP=$(az network public-ip show -g "$NETWORK_RG" -n "$SIP_PIP_NAME" --query ipAddress -o tsv)
export RTP_PUBLIC_IP=$(az network public-ip show -g "$NETWORK_RG" -n "$RTP_PIP_NAME" --query ipAddress -o tsv)

helm upgrade playsbc \
  "https://github.com/sudheerkumarvatrapu/PlaySBC/releases/download/v${PLAYSBC_VERSION}/playsbc-${PLAYSBC_VERSION}.tgz" \
  --namespace playsbc \
  --reuse-values \
  --atomic \
  --wait \
  --timeout 10m \
  --set image.repository="$ACR_LOGIN_SERVER/playsbc" \
  --set-string image.tag="$PLAYSBC_VERSION" \
  --set rtpengine.image.repository="$ACR_LOGIN_SERVER/playsbc-rtpengine" \
  --set-string rtpengine.image.tag="$PLAYSBC_VERSION" \
  --set rtpengine.rtpMin=30000 \
  --set rtpengine.rtpMax=30049 \
  --set-string rtpengine.advertisedIP="$RTP_PUBLIC_IP" \
  --set playsbc.config.rtp_min=30000 \
  --set playsbc.config.rtp_max=30049 \
  --set playsbc.config.media_backend=rtpengine \
  --set-string playsbc.config.rtpengine_url=udp://playsbc-playsbc-rtpengine:2223 \
  --set playsbc.config.reject_unknown_routes=true \
  --set playsbc.config.b2bua_invite_timeout=60.0 \
  --set playsbc.config.rtpengine_g711_only=true \
  --set playsbc.config.rtpengine_plain_rtp_sdp=true \
  --set playsbc.config.rtpengine_explicit_rtcp=true \
  --set playsbc.config.rtpengine_sip_source_address=true \
  --set playsbc.config.rtpengine_media_handover=true \
  --set playsbc.config.rtpengine_nat_wait=true \
  --set playsbc.config.rtpengine_pierce_nat=false \
  --set-string playsbc.config.sip_advertised_ip="$SIP_PUBLIC_IP" \
  --set-string playsbc.config.b2bua_advertised_ip="$SIP_PUBLIC_IP" \
  --set authSecret.enabled=true \
  --set-string authSecret.users.1001=secret-password \
  --set-string authSecret.users.1002=secret-password

kubectl -n playsbc rollout status deployment/playsbc-playsbc --timeout=240s
kubectl -n playsbc rollout status deployment/playsbc-playsbc-rtpengine --timeout=240s
kubectl -n playsbc get pods -o wide
kubectl -n playsbc get svc playsbc-playsbc-azure-sip-public playsbc-playsbc-azure-rtp-public -o wide
```

The SIP service must show `SIP_PUBLIC_IP`; the RTP service and RTPengine command must show `RTP_PUBLIC_IP` and `30000-30049`.

## 2. Configure OBi1022 As `1001`

Open `http://192.168.1.9` and disable old provider provisioning:

```text
System Management -> Auto Provisioning
OBiTALK Provisioning: Disabled
ITSP Provisioning: Disabled
Firmware Update: Manual or Disabled
```

Configure SP1:

```text
Service Providers -> ITSP Profile A -> SIP
ProxyServer: <SIP_PUBLIC_IP>
ProxyServerPort: 5062
ProxyServerTransport: UDP
RegistrarServer: <SIP_PUBLIC_IP>
RegistrarServerPort: 5062
OutboundProxy: blank
X_DnsSrv: false
X_UseTokenAuth: false
X_DiscoverPublicAddress: true
X_UsePublicAddressInVia: true
X_UseRport: true

Voice Services -> SP1 Service
Enable: checked
X_ServProvProfile: A
AuthUserName: 1001
AuthPassword: secret-password
URI: 1001
RegisterEnable: checked
KeepAliveEnable: checked

Service Providers -> ITSP Profile A -> RTP
X_RTPTransport: UDP
```

Submit and reboot the phone.

## 3. Configure Zoiper As `1002`

```text
Username: 1002
Password: secret-password
Domain / Host: <SIP_PUBLIC_IP>:5062
Transport: UDP
Outbound proxy: blank
```

## 4. Monitor Signalling And Media

Registration only:

```bash
kubectl -n playsbc logs deployment/playsbc-playsbc -f --since=10m \
  | grep -aE 'REGISTER|Registered|401|403|1001|1002'
```

Calls and media from both PlaySBC and RTPengine:

```bash
kubectl -n playsbc logs -f \
  -l app.kubernetes.io/instance=playsbc \
  --all-containers=true \
  --prefix \
  --max-log-requests=10 \
  --since=10m \
  | grep -aE 'SIP (INVITE|ACK|BYE|CANCEL)|SIP TX response|SIP response|INVITE ROUTE|B2BUA|SDP SUMMARY|RTPENGINE|RTPengine|RTP packet|RTCP|1001|1002'
```

Place both calls:

```text
OBi1022 1001 -> Zoiper 1002
Zoiper 1002 -> OBi1022 1001
```

Pass criteria:

- both devices register after a `401` challenge
- INVITE routes to the registered peer
- INVITE/100/180/200/ACK and BYE/200 complete
- SDP advertises the configured RTP public/LAN IP and ports inside `30000-30049`
- RTPengine reports `caller_to_callee=observed` and `callee_to_caller=observed`
- audio is heard in both directions

## 5. Capture One Evidence Archive

Use the released capture tool and one temporary host-network `netshoot` pod:

```bash
export PLAYSBC_VERSION=3.0.0

export AKS_CONTEXT=$(kubectl config current-context)

cd ~
rm -rf "PlaySBC-v$PLAYSBC_VERSION"
git clone --branch "v$PLAYSBC_VERSION" --depth 1 \
  https://github.com/sudheerkumarvatrapu/PlaySBC.git \
  "PlaySBC-v$PLAYSBC_VERSION"
cd "PlaySBC-v$PLAYSBC_VERSION"

PYTHONPYCACHEPREFIX=/tmp/playsbc-pycache \
python3 tools/run_real_device_capture.py \
  --context "$AKS_CONTEXT" \
  --namespace playsbc \
  --duration 120 \
  --capture-image nicolaka/netshoot:latest
```

If Docker Hub pulls are blocked:

```bash
az acr import \
  --name "$ACR_NAME" \
  --source docker.io/nicolaka/netshoot:latest \
  --image netshoot:latest \
  --force

PYTHONPYCACHEPREFIX=/tmp/playsbc-pycache \
python3 tools/run_real_device_capture.py \
  --context "$AKS_CONTEXT" \
  --namespace playsbc \
  --duration 120 \
  --capture-image "$ACR_LOGIN_SERVER/netshoot:latest"
```

Press `Ctrl-C` once when both calls finish. The tool saves evidence before deleting the capture pod.

Output:

```text
logs/Real-Device-Lab/real-device-capture-<timestamp>/
  capture.pcap
  sipmsg.log
  canonical-sip.json
  media-evidence.log
  media-evidence.json
  latest.html
  playsbc.log
  rtpengine.log
  rtpengine-verdict.log
  summary.log

logs/Real-Device-Lab/real-device-capture-<timestamp>.tgz
```

Download the `.tgz` immediately from Cloud Shell.

## 6. Interpret Evidence

- `capture.pcap` is the only raw packet capture.
- `sipmsg.log` collapses near-simultaneous host/LB/pod mirrors into one call ladder.
- Later repeated SIP-over-UDP messages remain as retransmission annotations.
- `media-evidence.log` separates PCMU/PCMA speech, RTCP, telephone events, and tiny NAT probes.
- Host-network capture counts are observations because one packet may appear on several interfaces. RTPengine query totals are authoritative for unique session packet counts.
- One-sided RTCP is reported as `endpoint-limited`; it does not invalidate proven two-way RTP.

## 7. Fast Troubleshooting

| Symptom | Check |
| --- | --- |
| No REGISTER | SIP public IP, UDP `5062`, home SIP ALG, OBi provisioning |
| Repeated `401` | User/password, token auth, realm |
| `404`/address incomplete | `INVITE ROUTE SELECTED`; both users must be registered |
| `Unparsable SDP` | Unknown-route fallback must remain disabled |
| `480` | Answer before the 60-second B2BUA timeout |
| One-way/no audio | RTP LB IP, advertised IP, `30000-30049`, NAT learning flags |
| RTP after `180` | Check whether packets are tiny NAT probes or real G.711 speech |
| Call remains connected | Verify BYE/200 on both B2BUA legs and RTPengine delete |
| Capture pod remains | Delete `pod/real-device-capture-*-pod` after confirming evidence copied |

Basic snapshot:

```bash
kubectl -n playsbc get pods,svc -o wide
kubectl -n playsbc logs deployment/playsbc-playsbc --tail=200
kubectl -n playsbc logs deployment/playsbc-playsbc-rtpengine --tail=200
```

## 8. Validated Result And Next Work

The `2026-08-23` v2.5.0 run passed OBi1022-to-Zoiper and Zoiper-to-OBi1022 signalling, two-way PCMU, normal teardown, combined PCAP, and archive generation. Zoiper emitted RTCP receiver reports; OBi1022 did not, so RTCP was correctly `endpoint-limited`.

Next real-device work:

- repeatable local-LAN testing through the dedicated kind cluster
- Poly VVX600 and Yealink SIP-T33G TCP/TLS registration
- longer calls, re-registration, multiple devices, and SIP ALG detection
- richer RTCP loss/jitter and MOS-style evidence
- local PlaySBC/RTPengine HA and failover during device calls
