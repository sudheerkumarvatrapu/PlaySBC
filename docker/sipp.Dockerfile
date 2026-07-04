FROM debian:bookworm-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        iproute2 \
        sip-tester \
        tcpdump \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["sipp"]
