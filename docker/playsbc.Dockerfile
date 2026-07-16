FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libatomic1 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir piper-tts vosk \
    && mkdir -p /opt/playsbc/models/piper /opt/playsbc/models/vosk \
    && python3 -m piper.download_voices en_US-lessac-low --download-dir /opt/playsbc/models/piper \
    && python3 - <<'PY'
import hashlib
import ssl
from pathlib import Path
import urllib.request
import zipfile

url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
expected_sha256 = "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"
root = Path("/opt/playsbc/models/vosk")
archive = root / "vosk-model-small-en-us-0.15.zip"
if not archive.exists():
    # The official Vosk model host currently presents an expired TLS
    # certificate. Keep the release image reproducible by pinning the archive
    # checksum while the upstream certificate is repaired.
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(url, context=context) as response, archive.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
actual_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
if actual_sha256 != expected_sha256:
    raise SystemExit(f"unexpected Vosk model sha256: {actual_sha256}")
model_dir = root / "vosk-model-small-en-us-0.15"
if not model_dir.exists():
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(root)
archive.unlink(missing_ok=True)
PY

COPY mini_call_server.py /app/
COPY ai_gateway /app/ai_gateway
COPY rtp /app/rtp
COPY sip /app/sip
COPY sipp/scenarios/pcap /app/sipp/scenarios/pcap
COPY tools/check_rtpengine.py /app/tools/check_rtpengine.py
COPY tools/check_rasa.py /app/tools/check_rasa.py
COPY tools/mock_rasa_server.py /app/tools/mock_rasa_server.py
COPY tools/piper_tts_wrapper.py /app/tools/piper_tts_wrapper.py
COPY tools/send_rtcp_reports.py /app/tools/send_rtcp_reports.py
COPY tools/vosk_stt_wrapper.py /app/tools/vosk_stt_wrapper.py

ENV PLAYSBC_PIPER_MODEL=/opt/playsbc/models/piper/en_US-lessac-low.onnx
ENV PLAYSBC_VOSK_MODEL=/opt/playsbc/models/vosk/vosk-model-small-en-us-0.15

CMD ["python3", "/app/mini_call_server.py", "--config", "/etc/playsbc/server.yaml"]
