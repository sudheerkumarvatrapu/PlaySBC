# Local RTPengine For PlaySBC

PlaySBC uses the open-source Sipwise RTPengine NG control protocol at `udp://127.0.0.1:2223`.

RTPengine runs as the media plane. PlaySBC remains the SIP/B2BUA control plane.

```text
SIP:  SIPp A <-> PlaySBC <-> SIPp B
RTP:  SIPp A <-> RTPengine <-> SIPp B
```

## Real Dual-Realm Lab

The standard local regression below keeps its processes on loopback. To run actual network separation instead, start Docker Desktop and run:

```bash
python3 tools/run_real_topology.py
```

The topology uses two isolated Docker bridges:

| Realm | SIPp | PlaySBC | RTPengine |
| --- | --- | --- | --- |
| Core | `172.28.0.10` | `172.28.0.20` | `172.28.0.40` |
| Peer | `192.168.28.30` | `192.168.28.20` | `192.168.28.40` |

PlaySBC and RTPengine are dual-homed. The initial RTPengine offer carries `direction=[core, peer]`, so its rewritten offer advertises the peer media address and its rewritten answer advertises the core media address. SIPp A and SIPp B never share a Docker network.

Runtime config is rendered by Helm from `configs/topology/helm-values.yaml`. The run produces SBC logs, SIPp traces, `topology.log`, `result.txt`, and one merged `capture.pcap` under `logs/real-topology/<timestamp>/`.

The runner reuses a complete local image set and builds automatically when images are missing. Force a refresh after changing containerized PlaySBC code or Dockerfiles with:

```bash
python3 tools/run_real_topology.py --rebuild
```

## macOS Docker Quick Start

Run these commands from the PlaySBC repo root.

### 1. Start Docker Desktop

Open Docker Desktop first. If `docker` is not in your shell path, add Docker Desktop's bundled CLI:

```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
```

Check Docker:

```bash
docker version
```

### 2. Build The Local RTPengine Image

The local image is built from `docker/rtpengine.Dockerfile` and installs the Debian `rtpengine-daemon` package.

```bash
docker build -f docker/rtpengine.Dockerfile -t playsbc/rtpengine:local .
```

### 3. Start RTPengine

Start RTPengine in the background:

```bash
docker rm -f playsbc-rtpengine 2>/dev/null || true
docker run -d --name playsbc-rtpengine \
  -p 2223:2223/udp \
  -p 30000-32000:30000-32000/udp \
  playsbc/rtpengine:local
```

Check the container:

```bash
docker ps --filter name=playsbc-rtpengine
docker logs --tail 50 playsbc-rtpengine
```

Expected log line:

```text
Startup complete
```

### 4. Run The RTPengine Readiness Gate

```bash
python3 tools/check_rtpengine.py --url udp://127.0.0.1:2223 --timeout 1
```

Expected:

```text
RTPengine OK: udp://127.0.0.1:2223 replied with result=pong
```

If this fails, RTPengine-backed SIPp profiles should report `BLOCKED` instead of a false regression failure.

### 5. Run Local SIPp Regression

For media PCAP replay on macOS, cache sudo first:

```bash
sudo -v
```

Run the full local B2BUA SIPp regression:

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py --skip-sipp-smoke --all-b2bua-profiles --b2bua-media-driver sipp-pcap --b2bua-sipp-pcap-sudo --timeout 360
```

This includes:

- Basic B2BUA signalling
- Basic B2BUA media
- Internal transcoding
- Registered inbound/outbound calls
- RTPengine signalling
- RTPengine G.711 media
- RTPengine PCMU-to-PCMA transcoding
- 5 cps / 60 second CHT load profiles

The `load-5cps-60s` profiles generate `300` total calls at `5 cps`, with `60` second call hold time.

### 6. Run Only RTPengine Profiles

```bash
python3 tools/run_b2bua_sipp_smoke.py --profile rtpengine
sudo -v
python3 tools/run_b2bua_sipp_smoke.py --profile rtpengine-media --sipp-pcap-sudo
python3 tools/run_b2bua_sipp_smoke.py --profile rtpengine-transcoding --sipp-pcap-sudo
python3 tools/run_b2bua_sipp_smoke.py --profile load-5cps-60s-rtpengine-transcoding --sipp-pcap-sudo
```

### 7. Check Logs

Regression logs are written under:

```text
logs/b2bua-Regression/
```

For RTPengine media calls, check:

- `log.media` for `RTPENGINE OFFER`, `RTPENGINE ANSWER`, `RTPENGINE QUERY`, and `rtpengine_media_anchored`
- `log.transcoding` for `owner=rtpengine`
- `log.platform` for pass/fail/blocked result lines
- `capture.pcap` for non-load single-call profiles

In RTPengine profiles, PlaySBC internal RTP counters should remain zero because RTP bypasses PlaySBC:

```text
server_rtp_received_packets_total=0
```

### 8. Stop RTPengine

```bash
docker stop playsbc-rtpengine
docker rm playsbc-rtpengine
```

## Linux VM Direct Start

On Linux, you can run the Sipwise RTPengine daemon directly instead of Docker:

```bash
sudo rtpengine \
  --foreground \
  --log-stderr \
  --interface=127.0.0.1 \
  --listen-ng=127.0.0.1:2223 \
  --port-min=30000 \
  --port-max=32000 \
  --table=-1
```

Then run the same readiness gate:

```bash
python3 tools/check_rtpengine.py --url udp://127.0.0.1:2223 --timeout 1
```

## Troubleshooting

If Docker is running but the readiness gate fails:

```bash
docker ps --filter name=playsbc-rtpengine
docker logs --tail 100 playsbc-rtpengine
```

If SIPp PCAP media profiles fail with sudo errors:

```bash
sudo -v
```

Then rerun the regression command in the same terminal.
