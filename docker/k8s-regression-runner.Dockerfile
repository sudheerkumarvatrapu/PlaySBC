FROM python:3.12-slim

ARG KUBECTL_VERSION=v1.30.7
ARG HELM_VERSION=v3.15.4

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gzip \
        openssl \
        tar \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
      amd64) kubectl_arch="amd64"; helm_arch="amd64" ;; \
      arm64) kubectl_arch="arm64"; helm_arch="arm64" ;; \
      *) echo "unsupported architecture: ${arch}" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${kubectl_arch}/kubectl"; \
    chmod +x /usr/local/bin/kubectl; \
    curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-${helm_arch}.tar.gz" -o /tmp/helm.tgz; \
    tar -xzf /tmp/helm.tgz -C /tmp; \
    mv "/tmp/linux-${helm_arch}/helm" /usr/local/bin/helm; \
    chmod +x /usr/local/bin/helm; \
    rm -rf /tmp/helm.tgz "/tmp/linux-${helm_arch}"

WORKDIR /workspace

COPY ai_gateway /workspace/ai_gateway
COPY charts /workspace/charts
COPY configs /workspace/configs
COPY rasa /workspace/rasa
COPY rtp /workspace/rtp
COPY sip /workspace/sip
COPY sipp /workspace/sipp
COPY tools /workspace/tools
COPY mini_call_server.py /workspace/mini_call_server.py
COPY VERSION /workspace/VERSION

ENV PYTHONPATH=/workspace
ENV PYTHONPYCACHEPREFIX=/tmp/playsbc-pycache

CMD ["python3", "/workspace/tools/run_k8s_regression.py", "--all-profiles", "--skip-namespace-check"]
