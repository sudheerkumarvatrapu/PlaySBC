FROM debian:bookworm-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        rtpengine-daemon \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 2223/udp
EXPOSE 30000-32000/udp

ENTRYPOINT ["/bin/sh", "-lc"]
CMD ["set -- $(hostname -i); exec rtpengine --foreground --log-stderr --interface=eth0/${1}!127.0.0.1 --listen-ng=0.0.0.0:2223 --port-min=30000 --port-max=32000 --table=-1"]
