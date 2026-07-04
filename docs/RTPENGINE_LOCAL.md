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

Helm renders the configuration for each profile. RTPengine uses `direction=[core, peer]`; SIPp A and SIPp B never share a Docker network.

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
