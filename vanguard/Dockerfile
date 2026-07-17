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
# curl : sonde du HEALTHCHECK natif ci-dessous (#154, absent de python:3.12-slim par défaut).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY packages/aria-core /packages/aria-core
# Extra [agent_wallet] (cdp-sdk) : lecture des soldes reels du wallet agent CDP
# (agent_wallet_cdp_adapter.py, /agentwallet #204, 16/07) -- demande operateur
# explicite ("je veux tous voir"). Lecture seule ici (usdc_balance_usd/
# list_all_token_balances) : n'active PAS l'execution (swap/transfert restent
# gates separement OFF par defaut, cf. CLAUDE.md).
# extra x402 (17/07, #199) : le package `x402` (x402_cdp_signer.py, construction
# du header de paiement) n'etait pas installe -- meme categorie de bug que
# `[agent_wallet]` avant sa correction (#204) : extra jamais ajoute au build,
# trouve en testant le tout premier vrai paiement x402 en conditions reelles.
RUN pip install --no-cache-dir "/packages/aria-core[agent_wallet,x402]"

# Chromium headless (Playwright) : captures d'ecran reelles du site pour ux_watch
# (tache #155, skills/ux_watch.py) -- +300-500 Mo mesures, accepte tel quel (decision
# operateur, 13/07), meme doctrine que ffmpeg/#23 ci-dessus. --with-deps installe les
# libs systeme requises (fonts, libnss, etc.) en plus du binaire Chromium lui-meme.
RUN playwright install --with-deps chromium

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
# HEALTHCHECK natif Docker (#154) -- seul signal que willfarrell/autoheal (sidecar,
# vanguard/scripts/autoheal-circuit-breaker.sh) sait observer pour redémarrer un
# conteneur "unhealthy" (panne transitoire post-déploiement). Distinct du health-check
# HTTP externe de deploy.sh (commit cassé -> pas de bascule, jamais de restart en boucle).
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/api/health || exit 1
# --proxy-headers + --forwarded-allow-ips=127.0.0.1 : nginx (seul en façade, sur le
# loopback) fournit X-Forwarded-For ; uvicorn ne fait confiance qu'à lui pour reconstituer
# l'IP client réelle. Sans ça, request.client.host reste le loopback et le plafond anti-abus
# par IP ne peut pas fonctionner.
CMD ["sh", "-c", "echo \"aria-api boot commit=${GIT_COMMIT} PORT=${PORT:-8000}\" && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info --proxy-headers --forwarded-allow-ips=127.0.0.1"]