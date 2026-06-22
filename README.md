<p align="center">
  <a href="docs/assets/playsbc-logo.svg">
    <img src="docs/assets/playsbc-logo.svg?raw=1&amp;v=20260614-tagline-small" alt="PlaySBC logo" width="720">
  </a>
</p>

<h1 align="center">PlaySBC</h1>

<p align="center">
  <strong>SIP, RTP, B2BUA, Transcoding and regression play ground.</strong>
</p>

<p align="center">
  <img alt="Python 3.x" src="https://img.shields.io/badge/-Python%203.x-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img alt="SIPp Regression" src="https://img.shields.io/badge/-SIPp%20Regression-16A34A?style=for-the-badge">
  <img alt="B2BUA Enabled" src="https://img.shields.io/badge/-B2BUA%20Enabled-2563EB?style=for-the-badge">
  <img alt="Transcoding G711u | G711a" src="https://img.shields.io/badge/-Transcoding%20G711u%20%7C%20G711a-9333EA?style=for-the-badge">
  <img alt="RTPengine Preflight" src="https://img.shields.io/badge/-RTPengine%20Preflight-0F766E?style=for-the-badge">
</p>

PlaySBC is a Python SIP/RTP lab server for learning B2BUA routing, SIPp regression, G.711 media, transcoding, and RTPengine media anchoring.

Roadmap: [docs/EVOLUTION_PLAN.md](docs/EVOLUTION_PLAN.md)

RTPengine setup: [docs/RTPENGINE_LOCAL.md](docs/RTPENGINE_LOCAL.md)

## Download

macOS/Linux:

```bash
git clone https://github.com/sudheerkumarvatrapu/PlaySBC.git
cd PlaySBC
python3 --version
```

Windows: use WSL/Ubuntu for SIPp regression.

```powershell
wsl --install -d Ubuntu
```

Inside Ubuntu:

```bash
sudo apt update
sudo apt install -y git python3 sipp
git clone https://github.com/sudheerkumarvatrapu/PlaySBC.git
cd PlaySBC
```

## Dependencies

macOS:

```bash
brew install sipp
```

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y sipp
```

Check:

```bash
sipp -v
```

## Main Regression

Start RTPengine first when running RTPengine profiles. See [docs/RTPENGINE_LOCAL.md](docs/RTPENGINE_LOCAL.md).

```bash
cd /Users/sudheerkumar/Documents/Codex/2026-05-18/Mini-Call-Server

python3 tools/check_rtpengine.py --url udp://127.0.0.1:2223

sudo -v

env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py --skip-sipp-smoke --all-b2bua-profiles --b2bua-media-driver sipp-pcap --b2bua-sipp-pcap-sudo --timeout 360
```

Latest report:

```text
logs/reports/latest.html
```

## Targeted Runs

```bash
python3 tools/run_b2bua_sipp_smoke.py --list-profiles
python3 tools/run_b2bua_sipp_smoke.py --profile basic-signalling
python3 tools/run_b2bua_sipp_smoke.py --profile basic-media
python3 tools/run_b2bua_sipp_smoke.py --profile transcoding
python3 tools/run_b2bua_sipp_smoke.py --profile rtpengine-transcoding --sipp-pcap-sudo
python3 tools/run_b2bua_sipp_smoke.py --profile tcp-rtpengine-transcoding --sipp-pcap-sudo
python3 tools/run_b2bua_sipp_smoke.py --profile load-5cps-60s
```

## Logs

Each B2BUA testcase writes one bundle:

```text
logs/b2bua-Regression/<run-id-or-profile>/
```

Useful files:

```text
log.sip
log.media
log.transcoding
log.platform
log.sipp
capture.pcap
```

Single-call profiles include SIP ladders and one combined `capture.pcap`. Load profiles skip ladders and PCAPs.

## Manual SIPp

Start PlaySBC:

```bash
python3 mini_call_server.py --ip 127.0.0.1 --sip-port 5062 --rtp-min 10000 --rtp-max 10100 --debug
```

Run a basic SIPp UAC:

```bash
sipp 127.0.0.1:5062 -sn uac -s 1001 -m 1 -r 1 -trace_msg -trace_err
```

For TCP:

```bash
python3 mini_call_server.py --ip 127.0.0.1 --sip-port 5062 --sip-transport tcp --rtp-min 10000 --rtp-max 10100 --debug
sipp 127.0.0.1:5062 -sn uac -s 1001 -m 1 -r 1 -t t1 -trace_msg -trace_err
```

## Contributor

- [Sudheer Kumar Vatrapu](https://github.com/sudheerkumarvatrapu)
