# PlaySBC

Educational SIP/RTP lab server focused on local SIPp regression for B2BUA and media experiments.

For the roadmap, see [docs/EVOLUTION_PLAN.md](docs/EVOLUTION_PLAN.md).

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

By default this runner uses a temporary output directory and does not create project logs. Add `--output-root <dir>` only when you want to keep the generic regression output.

Run one smoke scenario:

```bash
python3 tools/run_sipp_regression.py --start-server --scenario smoke_basic_call_media
```

Default smoke scenarios:

```text
smoke_register_digest
smoke_transaction_cache
smoke_invalid_bye
smoke_basic_call_media
smoke_bridge_two_leg
```

These replace the older Python smoke clients. Legacy scenario names such as `options`, `register_digest`, `call_echo`, and `invalid_bye` are still accepted for targeted debugging.

`smoke_basic_call_media` uses SIPp for the SIP dialog and a normal UDP PCAP sidecar for G.711 RTP echo verification, avoiding SIPp's root-only raw-socket PCAP playback requirement on macOS.

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

B2BUA dry-runs use a temporary output directory unless `--output-root <dir>` is provided. Inside that root, B2BUA logs are written to the single `b2bua-Regression` folder.

Run the combined regression suite and generate an HTML pass/fail report:

```bash
python3 tools/run_regression_suite.py
```

The combined suite runs the SIPp smoke scenarios plus these B2BUA profiles: `basic-signalling`, `basic-media`, `transcoding`, `registered-inbound`, and `registered-outbound`.

Run only B2BUA regression across all 8 profiles:

```bash
python3 tools/run_regression_suite.py --skip-sipp-smoke --all-b2bua-profiles
```

Temporary macOS workaround for SIPp `play_pcap_audio` raw-socket permission:

```bash
sudo -v
python3 tools/run_regression_suite.py --skip-sipp-smoke --all-b2bua-profiles --b2bua-media-driver sipp-pcap --b2bua-sipp-pcap-sudo
```

This prefixes only media-enabled SIPp PCAP processes with `sudo -n`. The PlaySBC server still runs as the normal user. If sudo credentials are not cached, the run fails fast and asks you to run `sudo -v`.

List the named B2BUA SIPp test profiles:

```bash
python3 tools/run_b2bua_sipp_smoke.py --list-profiles
```

Run named profiles:

```bash
python3 tools/run_b2bua_sipp_smoke.py --profile basic-signalling
python3 tools/run_b2bua_sipp_smoke.py --profile basic-media
python3 tools/run_b2bua_sipp_smoke.py --profile transcoding
python3 tools/run_b2bua_sipp_smoke.py --profile registered-inbound
python3 tools/run_b2bua_sipp_smoke.py --profile registered-outbound
python3 tools/run_b2bua_sipp_smoke.py --profile load-5cps-60s
```

RTPengine-backed profiles require RTPengine NG control to be reachable at `--rtpengine-url`:

```bash
python3 tools/check_rtpengine.py --url udp://127.0.0.1:2223
python3 tools/run_b2bua_sipp_smoke.py --profile rtpengine --rtpengine-url udp://127.0.0.1:2223
python3 tools/run_b2bua_sipp_smoke.py --profile load-5cps-60s-rtpengine-transcoding --rtpengine-url udp://127.0.0.1:2223
```

For a local lab, start RTPengine with NG control listening on the same URL before running those profiles:

```bash
rtpengine --interface=127.0.0.1 --listen-ng=127.0.0.1:2223 --foreground --log-stderr
```

The regression suite runs this RTPengine preflight automatically for RTPengine-backed profiles. If RTPengine is not reachable, the HTML report marks those profiles as `BLOCKED` instead of showing a generic SIPp call failure. Use `--skip-rtpengine-preflight` only when you intentionally want the call to run without that readiness check.

Notes:

- The one-call B2BUA run generates a unified SIP ladder log by default.
- Load runs should use `--no-ladder`.
- The B2BUA runner uses SIPp REGISTER before starting registered call flows.
- Registered inbound uses `uac-reg-inbound.xml` and `uas-reg-inbound.xml`.
- Registered outbound uses `uac-reg-outbound.xml` and `uas-reg-outbound.xml`.
- The `registered-outbound` profile also registers SIPp A and originates with that registered caller identity.
- The `transcoding` profile uses PCMU RTP media with server codec preference set to PCMA.
- G.711 media runs use Python UDP PCAP replay by default, so macOS raw-socket permission is not required.
- `--media-driver sipp-pcap` can be used only when SIPp has PCAP support and the OS allows raw-socket packet replay.
- On macOS, `--sipp-pcap-sudo` is available as a temporary workaround for SIPp `play_pcap_audio`.
- RTPengine mode expects RTPengine NG control on `udp://127.0.0.1:2223`; internal media remains the default.

## Local Logs

Only the B2BUA SIPp runner writes persistent project logs by default. All B2BUA runs append into one local regression folder:

```text
logs/b2bua-Regression/
```

Important files:

```text
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

The SIP ladder is written into `log.sip`. B2BUA call lifecycle events are written into `log.call`. SIPp tool output is consolidated into `log.sipp`. The saved folder does not contain separate SIPp A or SIPp B leg folders. Use `--run-id <label>` to label a run inside the same files, or `--log-folder <name>` only when you intentionally want a different consolidated folder.

Unit tests do not create log files. The generic SIPp regression runner writes to a temporary directory unless `--output-root <dir>` is provided.

Combined regression reports are written under:

```text
logs/reports/
```

The latest HTML report is also copied to `logs/reports/latest.html`.

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
sipp 127.0.0.1:25062 -sf sipp/scenarios/b2bua_uac_a.xml -s alice -i 127.0.0.1 -mi 127.0.0.1 -p 25081 -m 1 -r 1 -d 1000 -trace_msg -trace_err -trace_logs -min_rtp_port 26000 -max_rtp_port 26200
```

For full local B2BUA validation, prefer `tools/run_b2bua_sipp_smoke.py` because it starts SIPp B, registers the callee dynamically, starts the server, runs SIPp A, and collects logs in one run folder.
