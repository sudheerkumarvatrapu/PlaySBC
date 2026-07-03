<p align="center">
  <a href="docs/assets/playsbc-logo.svg">
    <img src="docs/assets/playsbc-logo.svg?raw=1&amp;v=20260623-playful-sbc" alt="PlaySBC logo" width="720">
  </a>
</p>

<h1 align="center">PlaySBC</h1>

<p align="center">
  <strong>Playful Session Border Controller: break SIP here, not in production.</strong>
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

Architecture PDF: [docs/PlaySBC_Service_Network_Diagrams.pdf](docs/PlaySBC_Service_Network_Diagrams.pdf)

RTPengine setup: [docs/RTPENGINE_LOCAL.md](docs/RTPENGINE_LOCAL.md)

## Setup

```bash
git clone https://github.com/sudheerkumarvatrapu/PlaySBC.git
cd PlaySBC
brew install sipp helm
sipp -v
helm version --short
```

Windows users should run SIPp regression from WSL/Ubuntu. Install SIPp and Helm with the OS package manager.

## Local Regression

Start RTPengine first. See [docs/RTPENGINE_LOCAL.md](docs/RTPENGINE_LOCAL.md).

```bash
cd PlaySBC

python3 tools/check_rtpengine.py --url udp://127.0.0.1:2223
helm version --short

sudo -v

env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py \
  --skip-sipp-smoke \
  --all-b2bua-profiles \
  --b2bua-media-driver sipp-pcap \
  --b2bua-sipp-pcap-sudo \
  --timeout 420
```

This runs all B2BUA/SIPp profiles, including ESBC cases and the Docker-based real dual-realm profile, with Helm-rendered YAML config. Local regression uses `helm template`; Kubernetes install is not required.

## Real Core/Peer Topology

With Docker Desktop running:

```bash
python3 tools/run_real_topology.py
```

This runs a 60-second PCMU-to-PCMA call across two isolated networks:

```text
SIPp A 172.28.0.10 -> PlaySBC 172.28.0.20 | 192.168.28.20 -> SIPp B 192.168.28.30
RTP/RTCP 172.28.0.10 <-> RTPengine 172.28.0.40 | 192.168.28.40 <-> 192.168.28.30
```

PlaySBC configuration is rendered from `configs/topology/helm-values.yaml`. Evidence is saved under `logs/real-topology/` with one unified `capture.pcap`.

## Outputs

```text
logs/reports/latest.html
logs/b2bua-Regression/<testcase>/
logs/real-topology/<run>/
```

Runtime config examples live in `configs/`. Helm values live in `charts/playsbc/values.yaml`.

## Contributor

- [Sudheer Kumar Vatrapu](https://github.com/sudheerkumarvatrapu)
