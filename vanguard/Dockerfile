# Monorepo ARIA — build depuis la racine du repo :
#   docker build -f vanguard/Dockerfile .
FROM node:22-slim AS frontend-build
WORKDIR /frontend
ENV NODE_OPTIONS=--max-old-space-size=768
COPY vanguard/product-frontend/package.json vanguard/product-frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY vanguard/product-frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

ARG GIT_COMMIT=unknown

# ffmpeg : assemblage local des videos marketing (tache #23, skills/marketing_video.py)
# -- binaire systeme uniquement, aucune cle API, licence libre (LGPL/GPL selon build).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY packages/aria-core /packages/aria-core
RUN pip install --no-cache-dir /packages/aria-core

COPY vanguard/backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY vanguard/backend/ ./backend/
COPY --from=frontend-build /frontend/dist ./frontend/dist
WORKDIR /app/backend
ENV PYTHONPATH=/app/backend
ENV GIT_COMMIT=$GIT_COMMIT
RUN python -c "from app.main import app; print('import check ok')"
ENV SERVE_FRONTEND=true
ENV DEBUG=false
ENV DATA_DIR=/app/backend/data
# Cache TTL des analyses VC (secondes) — un même contrat redemandé dans la
# fenêtre renvoie le résultat mémorisé (zéro re-scan, zéro token). 0 = désactivé.
ENV ARIA_VC_CACHE_TTL=300
RUN mkdir -p /app/backend/data

EXPOSE 8000
# --proxy-headers + --forwarded-allow-ips=127.0.0.1 : nginx (seul en façade, sur le
# loopback) fournit X-Forwarded-For ; uvicorn ne fait confiance qu'à lui pour reconstituer
# l'IP client réelle. Sans ça, request.client.host reste le loopback et le plafond anti-abus
# par IP ne peut pas fonctionner.
CMD ["sh", "-c", "echo \"aria-api boot commit=${GIT_COMMIT} PORT=${PORT:-8000}\" && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info --proxy-headers --forwarded-allow-ips=127.0.0.1"]