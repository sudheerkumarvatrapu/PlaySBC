# RTPengine For PlaySBC

PlaySBC uses the open-source Sipwise RTPengine. PlaySBC owns SIP/B2BUA control; RTPengine anchors and transforms media.

```text
SIP: SIPp A <-> PlaySBC <-> SIPp B
RTP: SIPp A <-> RTPengine <-> SIPp B
```

## Standard Regression

The suite starts its own dual-homed RTPengine, SIPp agents, and packet capture. No host RTPengine, SIPp, or `sudo` is required.

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py \
  --skip-sipp-smoke \
  --all-b2bua-profiles \
  --timeout 420
```

| Realm | SIPp | PlaySBC | RTPengine |
| --- | --- | --- | --- |
| Core | `172.28.0.10` | `172.28.0.20` | `172.28.0.40` |
| Peer | `192.168.28.30` | `192.168.28.20` | `192.168.28.40` |

Helm renders the configuration for each profile. RTPengine uses `direction=[core, peer]`; SIPp A and SIPp B never share a Docker network. Fault profiles cover control loss, session exhaustion, and invalid interfaces. Mixed secure profiles exercise TLS plus `RTP/SAVP` on one leg and UDP/TCP plus `RTP/AVP` on the other.

## Active-Active HA Lab Model

PlaySBC can run as a named HA node:

```yaml
ha:
  enabled: true
  cluster_id: playsbc-aa-lab
  node_id: playsbc-a
  shared_state_path: /var/lib/playsbc/ha-state.sqlite3
  nodes:
    - node_id: playsbc-a
      state: active
      weight: 100
    - node_id: playsbc-b
      state: active
      weight: 100
  load_balancing:
    enabled: true
    policy: external-lb
    drain_new_calls: true
  failover:
    dialog_restore: true
    mid_call_failover: dialog-restore-only
    rtpengine_session_migration: planned
  rtpengine_pairs:
    - node_id: playsbc-a
      rtpengine_url: udp://rtpengine-a:2223
    - node_id: playsbc-b
      rtpengine_url: udp://rtpengine-b:2223
```

Every dual-realm regression profile renders HA enabled. Each PlaySBC node selects its paired RTPengine from `ha.rtpengine_pairs`. Registrar contacts and dialog checkpoints are written to the shared SQLite lab store, so sibling nodes can resolve shared registrations and restore dialog state for HA experiments. A node marked `draining` rejects new INVITEs with `503 Node Draining`; existing dialog cleanup remains allowed.

## Focused Topology Call

```bash
python3 tools/run_real_topology.py
```

## Optional Host RTPengine

Use this only for standalone development against `udp://127.0.0.1:2223`.

```bash
docker build -f docker/rtpengine.Dockerfile -t playsbc/rtpengine:local .
docker rm -f playsbc-rtpengine 2>/dev/null || true
docker run -d --name playsbc-rtpengine \
  -p 2223:2223/udp \
  -p 30000-32000:30000-32000/udp \
  playsbc/rtpengine:local

python3 tools/check_rtpengine.py --url udp://127.0.0.1:2223 --timeout 1
```

Expected: `RTPengine OK ... result=pong`.

Stop it with:

```bash
docker rm -f playsbc-rtpengine
```

## Linux Direct Start

```bash
sudo rtpengine --foreground --log-stderr \
  --interface=127.0.0.1 \
  --listen-ng=127.0.0.1:2223 \
  --port-min=30000 --port-max=32000 --table=-1
```

## Evidence And Troubleshooting

- `log.media`: offer/answer/query and packet accounting
- `log.transcoding`: transcoding owner and codec path
- `capture.pcap`: live core/peer signalling and media
- `logs/reports/latest.html`: profile and lifecycle verdicts

If Docker is unavailable, start Docker Desktop and run `docker info`. For standalone RTPengine failures, inspect `docker logs --tail 100 playsbc-rtpengine` and rerun the readiness check.
