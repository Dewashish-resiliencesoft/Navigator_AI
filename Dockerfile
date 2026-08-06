# syntax=docker/dockerfile:1
# Default slim build (~3 min). Full live-demo stack: --build-arg NAVIGATOR_EXTRAS=full

ARG NAVIGATOR_EXTRAS=slim

FROM node:22-bookworm-slim AS web
WORKDIR /build
COPY navigator/client/web/package.json navigator/client/web/package-lock.json ./
RUN npm ci
COPY navigator/client/web/ ./
RUN npm run build

FROM mcr.microsoft.com/playwright/python:v1.49.1-noble AS runtime
ARG NAVIGATOR_EXTRAS=slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NAVIGATOR_HEADFUL=0 \
    NAVIGATOR_DB_PATH=/data/navigator.db \
    NAVIGATOR_REGISTRY_DB=/data/registry.db \
    NAVIGATOR_CHROMA_PATH=/data/chroma \
    NAVIGATOR_CREDENTIAL_DB_PATH=/data/credentials.db \
    NAVIGATOR_PIPER_DATA_DIR=/data/voices \
    NAVIGATOR_EXPLORE_EPISODES_PATH=/data/explore_episodes \
    NAVIGATOR_TTS_PROVIDER=fish

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates docker.io docker-compose-v2 \
    && curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
        -o /usr/local/bin/cloudflared \
    && chmod +x /usr/local/bin/cloudflared \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY navigator ./navigator
COPY --from=web /build/dist ./navigator/client/web/dist

RUN if [ "$NAVIGATOR_EXTRAS" = "full" ]; then \
      pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
      && pip install --no-cache-dir -e '.[api,voice,memory,llm]'; \
    else \
      pip install --no-cache-dir -e '.[api,llm]' groq websockets; \
    fi

COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/attendee-local.docker-compose.yaml /app/docker/attendee-local.docker-compose.yaml
RUN chmod +x /entrypoint.sh

RUN mkdir -p /data

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/docs >/dev/null || exit 1

CMD ["uvicorn", "navigator.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
