# Mini Python Call Server

This is a small educational SIP + RTP media server written in Python.

It supports:

- SIP over UDP
- `REGISTER`, `OPTIONS`, `INVITE`, `ACK`, and `BYE`
- Auto-answer calls
- SDP answer generation
- RTP media receive/send
- RTP echo audio
- Basic G.711 transcoding between PCMU and PCMA when Python `audioop` is available
- Per-call log files
- Inbound RTP recording to WAV for G.711 PCMU/PCMA calls
- JSON config file support
- Optional SIP digest authentication for `REGISTER`
- RFC 2833 DTMF detection
- Explicit SIP dialog state tracking
- UDP server transaction cache and INVITE response retransmission timers
- SIPp regression scenarios and fresh per-run artifacts

It is meant for local testing and learning. It is not a production SIP server.

## Run

Port `5060` often needs elevated permissions or may already be used, so this example uses `5062`:

```bash
python3 mini_call_server.py --ip 0.0.0.0 --sip-port 5062 --rtp-min 10000 --rtp-max 10100 --debug
```

If your softphone is on another machine, advertise the real LAN IP instead:

```bash
python3 mini_call_server.py --ip 192.168.1.50 --sip-port 5062 --rtp-min 10000 --rtp-max 10100
```

You can also run from the example config:

```bash
python3 mini_call_server.py --config config.example.json
```

Command-line flags override config values, so this is valid for quick tests:

```bash
python3 mini_call_server.py --config config.example.json --sip-port 15062 --rtp-min 12000 --rtp-max 12010 --debug
```

Supported config keys:

```json
{
  "sip_ip": "0.0.0.0",
  "sip_port": 5062,
  "rtp_min": 10000,
  "rtp_max": 10100,
  "log_dir": "logs",
  "recording_dir": "recordings",
  "artifact_root": "artifacts",
  "run_id": "",
  "default_codec": "PCMU",
  "auth_realm": "mini-call-server",
  "users": {
    "1001": "secret-password"
  },
  "debug": false
}
```

When `artifact_root` is set, each server run creates a fresh folder such as:

```text
artifacts/
  run-20260526-104238/
    logs/
    recordings/
```

This keeps sanity and soak test outputs from overwriting older logs.

The smoke clients also default to a fresh transcript folder under `artifacts/`:

```bash
python3 smoke_register_client.py
python3 smoke_call_client.py
python3 smoke_transaction_client.py
```

Use `--output-dir` when you want both clients to write transcripts into the same run folder.

If `users` is non-empty, `REGISTER` requires SIP digest authentication. Leave `users` empty for open demo registration:

```json
{
  "users": {}
}
```

## Call Artifacts

Each answered call writes review artifacts under the current working directory:

```text
logs/
  smoke-call-001_127.0.0.1.log
recordings/
  smoke-call-001_127.0.0.1.wav
```

You can choose different artifact folders:

```bash
python3 mini_call_server.py --sip-port 5062 --log-dir call-logs --recording-dir call-recordings
```

The per-call log includes SIP flow, SDP codec choice, RTP port, packet counts, byte counts, call duration, and recording path. WAV recording requires Python `audioop` so G.711 PCMU/PCMA packets can be converted to 16-bit PCM.

## Softphone Test

Use a softphone such as Linphone, Zoiper, or MicroSIP.

Example account settings:

- SIP server: your machine IP
- SIP port: `5062`
- Transport: UDP
- Username: any value, for example `1001`
- Password: use the configured password when `users` is enabled, for example `secret-password`

Then call any SIP URI at the server, for example:

```text
sip:echo@127.0.0.1:5062
```

The server auto-answers and echoes received RTP audio back to the caller.

DTMF digits sent as RFC 2833 `telephone-event/8000` are logged in the per-call log.

Each call also tracks the dialog lifecycle:

```text
INIT -> RINGING -> ANSWERED -> TERMINATED
```

The dialog record keeps the `Call-ID`, local and remote tags, branch IDs, CSeq values, and lifecycle timestamps. UDP request retransmissions reuse cached responses. Final INVITE responses are retransmitted on timers until an ACK arrives or the transaction expires.

## SIPp Regression Harness

Install [SIPp](https://github.com/SIPp/sipp) on macOS:

```bash
brew install sipp
```

Run the scenario harness against a managed local mini server:

```bash
python3 tools/run_sipp_regression.py --start-server
```

Available scenarios:

```text
options
register_digest
call_echo
invalid_bye
```

Run a focused scenario:

```bash
python3 tools/run_sipp_regression.py --start-server --scenario options --calls 10 --rate 5
```

Prepare and inspect all SIPp commands without requiring SIPp to be installed:

```bash
python3 tools/run_sipp_regression.py --dry-run
```

Every harness execution creates a new folder:

```text
artifacts/
  sipp/
    sipp-20260602-120000/
      summary.json
      options/
      register_digest/
      call_echo/
```

The broader engineering path is documented in [docs/EVOLUTION_PLAN.md](docs/EVOLUTION_PLAN.md).

## Notes

- Open UDP SIP port `5062` and RTP ports `10000-10100` in your firewall.
- NAT traversal, TLS, SRTP, and real call bridging are intentionally not included.
- Python 3.13 may not include `audioop`; in that case same-codec RTP echo still works, but PCMU/PCMA transcoding falls back to pass-through.
- If `audioop` is unavailable, WAV recording is skipped with a per-call log warning.
- Use `config.local.json` for machine-specific config; it is ignored by Git.
