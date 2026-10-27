FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build:public

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8600 \
    PUBLIC_DB_PATH=/data/offers \
    PUBLIC_DATA_DIR=/data/public
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=ui /ui/dist-public frontend/dist-public
RUN useradd --create-home --uid 10001 app && mkdir -p /data/offers /data/public logo_cache \
    && chown -R app:app /data logo_cache
USER app
EXPOSE 8600
CMD ["python", "public_server.py"]
