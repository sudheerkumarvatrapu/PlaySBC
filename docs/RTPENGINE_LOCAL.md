# RTPengine For PlaySBC

PlaySBC owns SIP/B2BUA control. Sipwise RTPengine anchors RTP/RTCP and performs media transformation.

```text
SIP: endpoint A <-> PlaySBC <-> endpoint B
RTP: endpoint A <-> RTPengine <-> endpoint B
```

## Supported Lab Models

| Model | Use |
| --- | --- |
| Docker regression | Dual-realm SIPp media and fault profiles |
| Kubernetes active-active | Paired PlaySBC and RTPengine replicas |
| AKS real-device | Public SIP LB plus public RTP LB on UDP 30000-30049 |
| Standalone container | Local RTPengine control development |

Use [KUBERNETES_HELM_RUNBOOK.md](KUBERNETES_HELM_RUNBOOK.md) for Kubernetes commands and [AZURE_AKS.md](AZURE_AKS.md) for public Azure media exposure.

## Docker Regression

```bash
PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache \
python3 tools/run_regression_suite.py \
  --skip-sipp-smoke \
  --all-b2bua-profiles \
  --timeout 420
```

| Realm | SIPp | PlaySBC | RTPengine |
| --- | --- | --- | --- |
| Core | `172.28.0.10` | `172.28.0.20` | `172.28.0.40` |
| Peer | `192.168.28.30` | `192.168.28.20` | `192.168.28.40` |

The suite renders each profile, isolates core and peer networks, captures one merged PCAP, and validates RTPengine control, codec negotiation, transcoding, secure-media interworking, and fault behavior.

## Active-Active Pairing

Each PlaySBC node selects its paired RTPengine from `ha.rtpengine_pairs`. New calls can be drained from a node while existing dialog cleanup remains allowed.

```yaml
ha:
  enabled: true
  cluster_id: playsbc-aa-lab
  nodes:
    - node_id: playsbc-0
      state: active
    - node_id: playsbc-1
      state: active
  rtpengine_pairs:
    - node_id: playsbc-0
      rtpengine_url: udp://playsbc-rtpengine-0:2223
    - node_id: playsbc-1
      rtpengine_url: udp://playsbc-rtpengine-1:2223
```

Shared SQLite is a lab mechanism, not a production-grade cross-node state store. The multi-node roadmap is in [EVOLUTION_PLAN.md](EVOLUTION_PLAN.md).

## Standalone Container

```bash
docker build -f docker/rtpengine.Dockerfile -t playsbc/rtpengine:local .
docker rm -f playsbc-rtpengine 2>/dev/null || true
docker run -d --name playsbc-rtpengine \
  -p 2223:2223/udp \
  -p 30000-32000:30000-32000/udp \
  playsbc/rtpengine:local

python3 tools/check_rtpengine.py \
  --url udp://127.0.0.1:2223 \
  --timeout 1
```

Expected result: `RTPengine OK ... result=pong`.

## Evidence And Diagnosis

| Artifact | Meaning |
| --- | --- |
| `log.media` | offer, answer, query, endpoint learning, and packets |
| `log.transcoding` | codec path and transcoding owner |
| `sipmsg.log` | normalized SIP messages |
| `capture.pcap` | combined signalling, RTP, RTCP, and networking evidence |
| `latest.html` | profile ladder and final verdict |

For standalone failures, run `docker logs --tail 100 playsbc-rtpengine` and repeat the readiness check. For AKS one-way audio, verify the advertised RTP IP, the exact `30000-30049` range on both RTPengine and the Azure LoadBalancer, and learned packet counts in both directions.
