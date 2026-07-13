<p align="center">
  <img src="docs/assets/playsbc-logo.svg?raw=1" alt="PlaySBC logo" width="620">
</p>

<h1 align="center">PlaySBC</h1>

<p align="center"><strong>Playful Session Border Controller: break SIP here, not in production.</strong></p>

<p align="center">
  <img alt="Python 3.x" src="https://img.shields.io/badge/-Python%203.x-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="SIPp Regression" src="https://img.shields.io/badge/-SIPp%20Regression-16A34A?style=flat-square">
  <img alt="B2BUA" src="https://img.shields.io/badge/-B2BUA-2563EB?style=flat-square">
  <img alt="G711 Transcoding" src="https://img.shields.io/badge/-G711u%20%7C%20G711a-9333EA?style=flat-square">
  <img alt="RTPengine" src="https://img.shields.io/badge/-RTPengine-0F766E?style=flat-square">
  <img alt="AI Rasa" src="https://img.shields.io/badge/-AI%20Rasa%20Gateway-BE185D?style=flat-square">
  <img alt="Version" src="https://img.shields.io/badge/-v1.0.0-111827?style=flat-square">
  <img alt="License MIT" src="https://img.shields.io/badge/-MIT%20License-F59E0B?style=flat-square">
</p>

Python SIP/RTP lab for B2BUA routing, G.711 media, transcoding, RTPengine, HA state experiments, AI voice gateway, and SIPp regression across real core and peer realms.

[Evolution plan](docs/EVOLUTION_PLAN.md) | [RTPengine runbook](docs/RTPENGINE_LOCAL.md) | [AI Voice Gateway](docs/AI_VOICE_GATEWAY.md) | [Kubernetes lab](docs/KUBERNETES_LOCAL.md)

## Release

Current version: `1.0.0`

License: MIT

Helm package: `release/helm/playsbc-1.0.0.tgz`

## Setup

Install Docker Desktop on macOS/Windows, or Docker Engine with Compose on Linux. Install [Helm](https://helm.sh/docs/intro/install/), then:

```bash
git clone https://github.com/sudheerkumarvatrapu/PlaySBC.git
cd PlaySBC
docker info
helm version --short
```

Host SIPp, RTPengine, and `sudo` are not required by the standard regression.

## Regression

```bash
env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py \
  --skip-sipp-smoke \
  --all-b2bua-profiles \
  --timeout 420
```

```text
Core: SIPp A 172.28.0.10 -> PlaySBC 172.28.0.20 -> RTPengine 172.28.0.40
Peer: RTPengine 192.168.28.40 <- PlaySBC 192.168.28.20 <- SIPp B 192.168.28.30
```

Helm renders every profile with HA enabled: shared registrar/dialog state, node identity, external-LB policy, node draining hooks, and PlaySBC-to-RTPengine pairing. Coverage also includes trunk failover, active OPTIONS recovery, normalization, hunt/CAC metrics, UDP/TCP/TLS, TLS/SRTP-to-RTP interworking, RTPengine faults, RTCP quality, auth, media, AI-to-Rasa REST, AI media via RTPengine, and load. Every testcase produces one combined SBC log bundle and live PCAP.

```text
logs/reports/latest.html
logs/b2bua-Regression/<testcase>/
```

## Contributor

[Sudheer Kumar Vatrapu](https://github.com/sudheerkumarvatrapu)
