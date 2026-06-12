# Mini Python Call Server

Educational SIP/RTP lab server focused on local SIPp regression for B2BUA and media experiments.

For the roadmap, see [docs/EVOLUTION_PLAN.md](docs/EVOLUTION_PLAN.md).

## Download

macOS or Linux:

```bash
git clone https://github.com/sudheerkumarvatrapu/Mini-Call-Server.git
cd Mini-Call-Server
python3 --version
```

Windows PowerShell:

```powershell
git clone https://github.com/sudheerkumarvatrapu/Mini-Call-Server.git
cd Mini-Call-Server
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
git clone https://github.com/sudheerkumarvatrapu/Mini-Call-Server.git
cd Mini-Call-Server
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

## Local Test Order

Start with command generation only:

```bash
python3 tools/run_sipp_regression.py --dry-run
python3 tools/run_b2bua_sipp_smoke.py --callee alice --calls 1 --rate 1 --hold-ms 1000 --dry-run
```

Then run the local SIPp tests:

```bash
python3 tools/run_sipp_regression.py --start-server
python3 tools/run_b2bua_sipp_smoke.py --callee alice --calls 1 --rate 1 --hold-ms 1000
```

## Managed SIPp Regression

Run all current SIPp regression scenarios against a managed local server:

```bash
python3 tools/run_sipp_regression.py --start-server
```

Run one scenario:

```bash
python3 tools/run_sipp_regression.py --start-server --scenario options --calls 10 --rate 5
```

Available scenarios:

```text
options
register_digest
call_echo
invalid_bye
```

Preview the SIPp commands without running SIPp:

```bash
python3 tools/run_sipp_regression.py --dry-run
```

## B2BUA SIPp Regression

Run a basic SIPp A -> B2BUA -> SIPp B call:

```bash
python3 tools/run_b2bua_sipp_smoke.py --callee alice --calls 1 --rate 1 --hold-ms 1000
```

Run a one-minute G.711u media call:

```bash
python3 tools/run_b2bua_sipp_smoke.py --callee media-user --calls 1 --rate 1 --hold-ms 60000 --media-codec PCMU
```

Run a one-minute G.711a media call:

```bash
python3 tools/run_b2bua_sipp_smoke.py --callee media-user --calls 1 --rate 1 --hold-ms 60000 --media-codec PCMA
```

Run the basic 5 cps / 60 second hold load shape:

```bash
python3 tools/run_b2bua_sipp_smoke.py --callee load-user --calls 5 --rate 5 --hold-ms 60000 --no-ladder
```

Run the same B2BUA harness with RTPengine as the media backend:

```bash
python3 tools/run_b2bua_sipp_smoke.py --callee alice --calls 1 --rate 1 --hold-ms 1000 --media-backend rtpengine --rtpengine-url udp://127.0.0.1:2223
```

Preview the B2BUA commands without running SIPp:

```bash
python3 tools/run_b2bua_sipp_smoke.py --callee alice --calls 1 --rate 1 --hold-ms 1000 --dry-run
```

Notes:

- The one-call B2BUA run generates a unified SIP ladder log by default.
- Load runs should use `--no-ladder`.
- The B2BUA runner dynamically registers the callee contact before starting the call.
- G.711 media runs use Python UDP PCAP replay by default, so macOS raw-socket permission is not required.
- `--media-driver sipp-pcap` can be used only when SIPp has PCAP support and the OS allows raw-socket packet replay.
- RTPengine mode expects RTPengine NG control on `udp://127.0.0.1:2223`; internal media remains the default.

## Local Artifacts

Every regression run writes a fresh folder under:

```text
artifacts/sipp/
```

Important files:

```text
summary.json
server/stdout.log
sipp-a-uac/
sipp-b-uas/
server-artifacts/server/logs/
```

## Manual SIPp Debug Commands

For a direct SIPp UAC call into the mini server:

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

Then start SIPp A toward a running mini-call-server B2BUA on port `25062`:

```bash
sipp 127.0.0.1:25062 -sf sipp/scenarios/b2bua_uac_a.xml -s alice -i 127.0.0.1 -mi 127.0.0.1 -p 25081 -m 1 -r 1 -d 1000 -trace_msg -trace_err -trace_logs -min_rtp_port 26000 -max_rtp_port 26200
```

For full local B2BUA validation, prefer `tools/run_b2bua_sipp_smoke.py` because it starts SIPp B, registers the callee dynamically, starts the server, runs SIPp A, and collects logs in one run folder.
