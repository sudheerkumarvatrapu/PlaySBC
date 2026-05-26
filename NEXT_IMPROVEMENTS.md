# Next Improvements

This project currently provides a small educational SIP/RTP call server with RTP echo and basic G.711 PCMU/PCMA handling. The next improvements below are ordered from most useful for validation to more advanced production-style features.

## Status

Completed:

- Per-call log files
- RTP recording to WAV for G.711 PCMU/PCMA calls
- Config file support

Next recommended item:

- SIP digest authentication

## Recommended Build Order

1. Per-call log files - done
2. RTP recording to WAV - done
3. Config file support - done
4. SIP digest authentication
5. DTMF detection
6. Call bridging
7. Docker packaging
8. Automated tests and GitHub Actions

## 1. Per-Call Log Files

Create one log file per SIP Call-ID.

Suggested output:

```text
logs/
  smoke-call-001_127.0.0.1.log
```

Each call log should include:

- INVITE request summary
- SDP offer payload types
- SDP answer payload type
- Local RTP port
- ACK received
- BYE received
- Call duration
- RTP packet count
- RTP bytes received and sent

Why this matters:

- Easier debugging
- Cleaner proof for smoke tests
- Better call trace review

## 2. RTP Recording To WAV

Record inbound RTP audio to a WAV file.

Suggested output:

```text
recordings/
  smoke-call-001_127.0.0.1.wav
```

Implementation notes:

- Convert G.711 PCMU or PCMA payloads to 16-bit PCM.
- Write mono, 8000 Hz WAV files.
- Use Python's `wave` module.
- If `audioop` is unavailable, keep packet capture metadata and skip WAV conversion with a clear warning.

Why this matters:

- Confirms media is actually received
- Lets users listen to call audio
- Makes RTP testing more concrete than packet counts alone

## 3. Config File Support

Add a simple JSON or YAML config file.

Example:

```json
{
  "sip_ip": "0.0.0.0",
  "sip_port": 5062,
  "rtp_min": 10000,
  "rtp_max": 10100,
  "log_dir": "logs",
  "recording_dir": "recordings",
  "default_codec": "PCMU"
}
```

Why this matters:

- Less command-line typing
- Easier repeatable deployments
- Cleaner future Docker support

## 4. SIP Digest Authentication

Add optional authentication for `REGISTER`.

Initial scope:

- Static users from config
- SIP digest challenge with `401 Unauthorized`
- Validate username, realm, nonce, URI, method, and response hash

Example config:

```json
{
  "users": {
    "1001": "secret-password"
  }
}
```

Why this matters:

- Prevents open registration
- Makes softphone testing closer to real SIP systems

## 5. DTMF Detection

Add support for RFC 2833 telephone-event RTP payloads.

Initial scope:

- Parse `telephone-event/8000` from SDP
- Detect common DTMF digits
- Log digit, duration, and call ID

Why this matters:

- Useful for IVR tests
- Verifies RTP event handling beyond simple audio echo

## 6. Call Bridging

Move from echo server to basic two-party bridging.

Initial scope:

- Accept two registered users
- Route INVITE from one user to another
- Relay RTP packets between the two endpoints
- Keep transcoding only for PCMU/PCMA initially

Why this matters:

- Turns the demo into a tiny call server
- Enables real endpoint-to-endpoint calls

## 7. Docker Packaging

Add Docker support.

Suggested files:

```text
Dockerfile
docker-compose.yml
```

Expose:

- UDP `5062` for SIP
- UDP `10000-10100` for RTP

Why this matters:

- Easier repeatable testing
- Cleaner deployment on Linux hosts

## 8. Tests And GitHub Actions

Add automated tests for SIP parsing and call smoke behavior.

Suggested tests:

- SIP header parsing
- Compact SIP headers
- SDP payload negotiation
- OPTIONS response
- INVITE answer generation
- BYE session cleanup

Suggested GitHub Actions workflow:

```text
.github/workflows/python.yml
```

Run:

```bash
python3 -m py_compile mini_call_server.py smoke_call_client.py
python3 -m unittest
```

Why this matters:

- Catches regressions
- Documents expected behavior
- Makes future changes safer

## Practical Next Step

The best next implementation task is:

```text
Add SIP digest authentication for REGISTER.
```

That is now the best next improvement because config-file support is implemented. Authentication will make registration behavior closer to real SIP systems.
