FROM python:3.12-slim

WORKDIR /app

COPY mini_call_server.py /app/
COPY ai_gateway /app/ai_gateway
COPY rtp /app/rtp
COPY sip /app/sip
COPY tools/check_rtpengine.py /app/tools/check_rtpengine.py
COPY tools/mock_rasa_server.py /app/tools/mock_rasa_server.py
COPY tools/send_rtcp_reports.py /app/tools/send_rtcp_reports.py

CMD ["python3", "/app/mini_call_server.py", "--config", "/etc/playsbc/server.yaml"]
