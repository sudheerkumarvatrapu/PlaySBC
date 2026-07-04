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

PlaySBC is a Python SIP/RTP lab server for learning B2BUA routing, SIPp regression, G.711 media, transcoding, and RTPengine media anchoring across real core and peer realms.

Roadmap: [docs/EVOLUTION_PLAN.md](docs/EVOLUTION_PLAN.md)

Architecture PDF: [docs/PlaySBC_Service_Network_Diagrams.pdf](docs/PlaySBC_Service_Network_Diagrams.pdf)

RTPengine setup: [docs/RTPENGINE_LOCAL.md](docs/RTPENGINE_LOCAL.md)

## Setup

```bash
git clone https://github.com/sudheerkumarvatrapu/PlaySBC.git
cd PlaySBC
brew install helm
helm version --short
docker info
```

Install Docker Desktop on macOS/Windows. Linux needs Docker Engine, Compose, and Helm. Host SIPp is optional because regression runs SIPp in Docker.

## Local Regression

```bash
cd PlaySBC

helm version --short
docker info

env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py \
  --skip-sipp-smoke \
  --all-b2bua-profiles \
  --timeout 420
```

Every profile runs on the same real topology:

```text
Core: SIPp A 172.28.0.10 -> PlaySBC 172.28.0.20 -> RTPengine 172.28.0.40
Peer: RTPengine 192.168.28.40 <- PlaySBC 192.168.28.20 <- SIPp B 192.168.28.30
```

PlaySBC is dual-homed. Helm renders a fresh profile config before each test. Load media captures use a bounded ring; single-call media profiles require RTCP evidence.

## Outputs

```text
logs/reports/latest.html
logs/b2bua-Regression/<testcase>/
```

Runtime config examples live in `configs/`. Helm values live in `charts/playsbc/values.yaml`.

Each testcase retains one combined `capture.pcap` plus SBC category logs. Load profiles omit ladders and keep bounded capture evidence.
The HTML report uses a Robot-style execution log with measured preparation, configuration, setup, test, teardown, and evidence-validation timings for every profile.

## Contributor

- [Sudheer Kumar Vatrapu](https://github.com/sudheerkumarvatrapu)
