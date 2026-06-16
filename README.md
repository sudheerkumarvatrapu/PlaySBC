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
  <img alt="RTPengine Getting Ready" src="https://img.shields.io/badge/-RTPengine%20Getting%20Ready-0F766E?style=for-the-badge">
</p>

---

For the roadmap, see [docs/EVOLUTION_PLAN.md](docs/EVOLUTION_PLAN.md).

## Lab Focus

| Area | Current focus |
| --- | --- |
| SIP signaling | Registrar-backed SIPp flows, B2BUA call setup, clear ladders |
| RTP media | G.711u/G.711a PCAP replay, packet summaries, media logs |
| Transcoding | Internal PCMU/PCMA checks and RTPengine-oriented profiles |
| Regression | One-command local B2BUA regression with HTML pass/fail report |

## Contributors

- [Sudheer Kumar Vatrapu](https://github.com/sudheerkumarvatrapu) - Project owner and maintainer

See [CONTRIBUTORS.md](CONTRIBUTORS.md).

## Download

macOS or Linux:

```bash
git clone https://github.com/sudheerkumarvatrapu/PlaySBC.git
cd PlaySBC
python3 --version
```

Windows PowerShell:

```powershell
git clone https://github.com/sudheerkumarvatrapu/PlaySBC.git
cd PlaySBC
py -3 --version
```

For Windows SIPp regression, use WSL/Ubuntu:

```powershell
wsl --install -d Ubuntu
```

Then inside Ubuntu:

```bash
sudo apt update
sudo apt install -y git python3 sipp
git clone https://github.com/sudheerkumarvatrapu/PlaySBC.git
cd PlaySBC
```

## SIPp Setup

macOS:

```bash
brew install sipp
```

Ubuntu/Debian Linux:

```bash
sudo apt update
sudo apt install -y sipp
```

Windows:

```text
Use WSL/Ubuntu and install SIPp there.
```

Check SIPp:

```bash
sipp -v
```

## Quick Local B2BUA Regression

Recommended one-command local run from any terminal:

```bash
cd /path/to/PlaySBC && sudo -v && env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py --skip-sipp-smoke --all-b2bua-profiles --b2bua-media-driver sipp-pcap --b2bua-sipp-pcap-sudo --timeout 240
```

On this Mac, the project may still live in the old folder name:

```bash
cd /Users/sudheerkumar/Documents/Codex/2026-05-18/Mini-Call-Server && sudo -v && env PYTHONPYCACHEPREFIX=/private/tmp/playsbc-pycache python3 tools/run_regression_suite.py --skip-sipp-smoke --all-b2bua-profiles --b2bua-media-driver sipp-pcap --b2bua-sipp-pcap-sudo --timeout 240
```

What this does:

| Step | Behavior |
| --- | --- |
| Scope | Runs only B2BUA SIPp regression, not the old smoke suite |
| Coverage | Runs all 8 B2BUA profiles |
| Media | Uses SIPp `play_pcap_audio` for media profiles |
| Sudo | Prompts once, then uses `sudo -n` only for SIPp PCAP replay |
| Logs | Deletes old passed/blocked bundles; keeps failed bundles |
| Report | Writes HTML output to `logs/reports/latest.html` |

Useful targeted commands:

```bash
python3 tools/run_b2bua_sipp_smoke.py --list-profiles
python3 tools/run_b2bua_sipp_smoke.py --profile basic-signalling
python3 tools/run_b2bua_sipp_smoke.py --profile basic-media
python3 tools/run_b2bua_sipp_smoke.py --profile transcoding
python3 tools/run_b2bua_sipp_smoke.py --profile load-5cps-60s
```

RTPengine-backed profiles are marked `BLOCKED` unless RTPengine NG control is reachable at `udp://127.0.0.1:2223`:

```bash
python3 tools/check_rtpengine.py --url udp://127.0.0.1:2223
```

## Local Logs

Each B2BUA testcase gets one clean log bundle:

```text
logs/b2bua-Regression/<run-id-or-profile-run-id>/
```

Important files:

```text
capture.pcap
log.sip
log.media
log.transcoding
log.platform
log.networking
log.udp
log.tcp
log.tls
log.call
log.sipp
```

Single-call profiles include SIP and registration ladders in `log.sip`. Non-load B2BUA profiles also generate one combined `capture.pcap` after the call completes, built from SIP traces, RTP media packets for media-enabled calls, and PlaySBC protocol logs. The PCAP uses a logical lab topology by default so Wireshark shows separate nodes for SIPp A (`10.10.10.10`), PlaySBC (`10.10.10.20`), and SIPp B (`10.10.10.30`) even when the local runtime binds to `127.0.0.1`. Use `--pcap-topology runtime` to preserve runtime loopback IPs, or override the display IPs with `--pcap-uac-ip`, `--pcap-server-ip`, and `--pcap-uas-ip`.

Load profiles do not generate ladders or PCAP captures. SIPp output is consolidated in `log.sipp`; media and transcoding summaries are in `log.media` and `log.transcoding`. Regression reports are written to `logs/reports/`, with the latest report copied to `logs/reports/latest.html`.

## Manual SIPp Debug Commands

For a direct SIPp UAC call into PlaySBC:

```bash
python3 mini_call_server.py --ip 127.0.0.1 --sip-port 5062 --rtp-min 10000 --rtp-max 10100 --debug
```

```bash
sipp 127.0.0.1:5062 -sn uac -s 1001 -m 1 -r 1 -trace_msg -trace_err
```

For manual B2BUA scenario debugging, start SIPp B:

```bash
sipp -sf sipp/scenarios/b2bua_uas_b.xml -s alice -i 127.0.0.1 -mi 127.0.0.1 -p 25082 -m 1 -trace_msg -trace_err -trace_logs -min_rtp_port 27000 -max_rtp_port 27200
```

Then start SIPp A toward a running PlaySBC B2BUA on port `25062`:

```bash
sipp 127.0.0.1:25062 -sf sipp/scenarios/b2bua_uac_a.xml -s alice -i 127.0.0.1 -mi 127.0.0.1 -p 25081 -m 1 -r 1 -d 1000 -trace_msg -trace_err -trace_logs -min_rtp_port 36000 -max_rtp_port 36200
```

For full B2BUA validation, prefer the quick regression command above because it starts the server, registers users, runs SIPp A/B, and collects the log bundle automatically.
