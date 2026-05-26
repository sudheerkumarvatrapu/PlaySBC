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
  "default_codec": "PCMU",
  "debug": false
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
- Password: empty or any value, because this demo does not authenticate

Then call any SIP URI at the server, for example:

```text
sip:echo@127.0.0.1:5062
```

The server auto-answers and echoes received RTP audio back to the caller.

## Notes

- Open UDP SIP port `5062` and RTP ports `10000-10100` in your firewall.
- NAT traversal, TLS, SRTP, authentication, DTMF handling, and real call bridging are intentionally not included.
- Python 3.13 may not include `audioop`; in that case same-codec RTP echo still works, but PCMU/PCMA transcoding falls back to pass-through.
- If `audioop` is unavailable, WAV recording is skipped with a per-call log warning.
- Use `config.local.json` for machine-specific config; it is ignored by Git.
