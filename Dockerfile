# One container: the API (FastAPI) also serves the built website, so the whole app lives at one address.
#   docker build -t uc2 . && docker run -p 8000:8000 -e APP_ENV=prod -e UC2_SEED_DEMO=1 uc2

# ---- 1. build the website
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ARG VITE_PUBLIC_DEMO=0
ENV VITE_PUBLIC_DEMO=$VITE_PUBLIC_DEMO
RUN npm run build

# ---- 2. the API
FROM python:3.13-slim
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/config ./config
COPY --from=web /web/dist /app/frontend/dist
# Data lives outside the image. Mount a disk at /data to keep it between deploys.
ENV APP_ENV=prod \
    UC2_DB_PATH=/data/uc2.db \
    UC2_STORAGE_DIR=/data/storage \
    FRONTEND_DIST=/app/frontend/dist \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
