FROM python:3.12-slim

WORKDIR /app

COPY mini_call_server.py /app/
COPY rtp /app/rtp
COPY sip /app/sip
COPY tools/check_rtpengine.py /app/tools/check_rtpengine.py

CMD ["python3", "/app/mini_call_server.py", "--config", "/etc/playsbc/server.yaml"]
