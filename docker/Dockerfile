# PantryOS product image (ADR 0018 §4): one container serving API + built UI
# on a single port. Multi-arch (amd64/arm64) — published by the release
# workflow as ghcr.io/wildstone-systems/pantryos. Dev images stay separate
# (Dockerfile.backend / Dockerfile.frontend keep the Vite dev-server flow).

# --- Stage 1: build the frontend ---------------------------------------------
FROM node:26-alpine AS frontend-build

WORKDIR /build

COPY frontend/package*.json ./
RUN npm ci

COPY frontend ./
RUN npm run build

# --- Stage 2: backend runtime + baked frontend --------------------------------
FROM python:3.11-slim

ARG APP_VERSION=dev

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app
COPY --from=frontend-build /build/dist /app/frontend-dist

ENV FRONTEND_DIST=/app/frontend-dist \
    APP_VERSION=${APP_VERSION}

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
