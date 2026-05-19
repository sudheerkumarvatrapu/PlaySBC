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
