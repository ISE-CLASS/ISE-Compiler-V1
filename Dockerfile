FROM node:20-bookworm-slim AS ui-build
WORKDIR /app/ui
COPY ui/package*.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
COPY --from=ui-build /app/ui/dist ./ui/dist

EXPOSE 8000
CMD ["sh", "-c", "python -m compiler.server --host 0.0.0.0 --port ${PORT:-8000}"]