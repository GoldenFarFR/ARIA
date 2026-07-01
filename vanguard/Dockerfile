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
RUN mkdir -p /app/backend/data

EXPOSE 8000
CMD ["sh", "-c", "echo \"aria-api boot commit=${GIT_COMMIT} PORT=${PORT:-8000}\" && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info"]