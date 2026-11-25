FROM python:3.12-slim

ARG CBM_VERSION=v0.12.1
ARG CBM_SHA256=8c3d0341064301e78ecc84cf7348d4e2c6562a0f57448243ea560aaa34de36c1
ARG CBM_ARCHIVE=codebase-memory-mcp-linux-amd64-portable.tar.gz

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tar \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://github.com/DeusData/codebase-memory-mcp/releases/download/${CBM_VERSION}/${CBM_ARCHIVE}" -o /tmp/cbm.tar.gz \
    && echo "${CBM_SHA256}  /tmp/cbm.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/cbm.tar.gz -C /usr/local/bin codebase-memory-mcp \
    && chmod 0755 /usr/local/bin/codebase-memory-mcp \
    && rm -f /tmp/cbm.tar.gz

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

RUN mkdir -p /data/cbm-cache /data/home /repos \
    && chown -R 10001:10001 /data /app

USER 10001:10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8081 \
    SERVICE_NAME=codebase \
    DATA_DIR=/data \
    HOME=/data/home \
    TMPDIR=/tmp \
    CBM_BIN=/usr/local/bin/codebase-memory-mcp \
    CBM_CACHE_DIR=/data/cbm-cache \
    CBM_ALLOWED_ROOT=/repos \
    CBM_LOG_LEVEL=warn

EXPOSE 8081

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8081}"]
