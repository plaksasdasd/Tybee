# Build context: the repository root (docker-compose: context ".", dockerfile site/Dockerfile).
FROM node:22-slim AS frontend
WORKDIR /build
COPY site/frontend/package.json site/frontend/package-lock.json ./
RUN npm ci
COPY site/frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/site/backend
WORKDIR /app

COPY site/requirements.txt site/requirements-lock.txt ./site/
RUN pip install --no-cache-dir --require-hashes -r site/requirements-lock.txt

COPY app ./app
COPY assets ./assets
COPY site/backend ./site/backend
COPY --from=frontend /build/dist ./site/frontend/dist

CMD ["python", "-m", "panel"]
