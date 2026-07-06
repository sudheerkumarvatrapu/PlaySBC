FROM debian:bookworm-slim AS builder

ARG SIPP_VERSION=v3.7.7

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        cmake \
        g++ \
        git \
        libncurses-dev \
        libnet1-dev \
        libpcap-dev \
        libssl-dev \
        make \
    && git clone --branch "${SIPP_VERSION}" --depth 1 https://github.com/SIPp/sipp.git /src/sipp \
    && cmake -S /src/sipp -B /src/sipp/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DUSE_SSL=1 \
        -DUSE_PCAP=1 \
    && cmake --build /src/sipp/build --parallel \
    && strip /src/sipp/build/sipp

FROM debian:bookworm-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        iproute2 \
        libncurses6 \
        libncursesw6 \
        libnet1 \
        libpcap0.8 \
        libssl3 \
        tcpdump \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /src/sipp/build/sipp /usr/local/bin/sipp

ENTRYPOINT ["sipp"]
