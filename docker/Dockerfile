FROM python:3.11-slim

WORKDIR /app

# 安装 supercronic（支持 amd64 / arm64）
ARG TARGETARCH
ENV SUPERCRONIC_VERSION=v0.2.39

RUN set -ex && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    case ${TARGETARCH} in \
    amd64) \
      export SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64; \
      export SUPERCRONIC_SHA1SUM=c98bbf82c5f648aaac8708c182cc83046fe48423; \
      export SUPERCRONIC=supercronic-linux-amd64; \
      ;; \
    arm64) \
      export SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-arm64; \
      export SUPERCRONIC_SHA1SUM=5ef4ccc3d43f12d0f6c3763758bc37cc4e5af76e; \
      export SUPERCRONIC=supercronic-linux-arm64; \
      ;; \
    *) \
      echo "Unsupported architecture: ${TARGETARCH}"; \
      exit 1; \
      ;; \
    esac && \
    for i in 1 2 3; do \
      echo "Download attempt $i/3"; \
      if curl -fsSL --connect-timeout 30 --max-time 60 -o "$SUPERCRONIC" "$SUPERCRONIC_URL"; then \
        echo "Download successful"; break; \
      else \
        echo "Attempt $i failed"; \
        if [ $i -eq 3 ]; then echo "All attempts failed"; exit 1; fi; \
        sleep 2; \
      fi; \
    done && \
    echo "${SUPERCRONIC_SHA1SUM}  ${SUPERCRONIC}" | sha1sum -c - && \
    chmod +x "$SUPERCRONIC" && \
    mv "$SUPERCRONIC" "/usr/local/bin/${SUPERCRONIC}" && \
    ln -s "/usr/local/bin/${SUPERCRONIC}" /usr/local/bin/supercronic && \
    supercronic -version && \
    apt-get remove -y curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源代码
COPY src/ ./src/

# 复制并修正 entrypoint.sh（统一为 LF，避免 Windows CRLF 问题）
COPY docker/entrypoint.sh /entrypoint.sh.tmp
RUN sed -i 's/\r$//' /entrypoint.sh.tmp && \
    mv /entrypoint.sh.tmp /entrypoint.sh && \
    chmod +x /entrypoint.sh && \
    mkdir -p /app/config /app/data

ENV PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/app/config/config.yaml \
    APP_BASE_DIR=/app/src

ENTRYPOINT ["/entrypoint.sh"]
