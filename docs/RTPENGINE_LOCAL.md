# Local RTPengine For PlaySBC

PlaySBC uses the open-source Sipwise RTPengine NG control protocol. Full regression starts its own dual-realm RTPengine in Docker; `udp://127.0.0.1:2223` is retained for standalone/manual development.

RTPengine runs as the media plane. PlaySBC remains the SIP/B2BUA control plane.

```text
SIP:  SIPp A <-> PlaySBC <-> SIPp B
RTP:  SIPp A <-> RTPengine <-> SIPp B
```

## Real Dual-Realm Lab

The standard regression uses actual Docker network separation for every profile:

```bash
python3 tools/run_regression_suite.py --skip-sipp-smoke --all-b2bua-profiles --timeout 420
```

The topology uses two isolated Docker bridges:

| Realm | SIPp | PlaySBC | RTPengine |
| --- | --- | --- | --- |
| Core | `172.28.0.10` | `172.28.0.20` | `172.28.0.40` |
| Peer | `192.168.28.30` | `192.168.28.20` | `192.168.28.40` |

PlaySBC and RTPengine are dual-homed. The initial RTPengine offer carries `direction=[core, peer]`, so its rewritten offer advertises the peer media address and its rewritten answer advertises the core media address. SIPp A and SIPp B never share a Docker network.

Runtime config is rendered by Helm for each profile. Each profile produces one SBC log bundle and one merged `capture.pcap` under `logs/b2bua-Regression/`.

The focused standalone topology call remains available with:

```bash
python3 tools/run_real_topology.py
```

## macOS Docker Quick Start

Run these commands from the PlaySBC repo root.

Steps 2-4 create an optional host-published RTPengine for manual development. Skip them when running the standard dual-realm regression suite.

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

If this fails, the standalone host-loopback harness reports RTPengine as `BLOCKED`. The dual-realm suite uses its own container readiness gate.

### 5. Run Local SIPp Regression

Run the full local B2BUA SIPp regression:

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py --skip-sipp-smoke --all-b2bua-profiles --timeout 420
```

The suite starts its own RTPengine, SIPp agents, and packet captures. Host `sudo` and host SIPp are not required.

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
python3 tools/run_regression_suite.py --skip-sipp-smoke --b2bua-profile rtpengine-media --b2bua-profile rtpengine-transcoding --timeout 420
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
