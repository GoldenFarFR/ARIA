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
#
# requirements-lock.txt en CONTRAINTES (-c) sur les deux pip install (05/08,
# reaction directe a l'attaque supply-chain npm keyv/cacheable du 04/08, cf.
# docs/HANDOFF_SECURITE.md) : sans lui, chaque build tirait silencieusement la
# DERNIERE version publiee de chaque dependance (directe et transitive) -- le
# vecteur exact qui a compromis les projets touches cote npm. Le lock fige les
# 132 versions du conteneur prod verifie sain ; les planchers `>=` de
# pyproject.toml/requirements.txt restent le contrat SCA (deliberement, voir le
# commentaire en tete de requirements.txt) -- un `-c` ne les remplace pas, il
# borne juste ce que le resolveur peut choisir. Mise a jour CONSCIENTE du lock :
# regenerer via `docker exec aria-api pip freeze --exclude-editable | grep -v
# '^aria-core'` apres un upgrade voulu et teste, jamais automatiquement.
COPY vanguard/backend/requirements-lock.txt ./backend/
RUN pip install --no-cache-dir -c backend/requirements-lock.txt "/packages/aria-core[agent_wallet,x402]"

# Chromium headless (Playwright) : captures d'ecran reelles du site pour ux_watch
# (tache #155, skills/ux_watch.py) -- +300-500 Mo mesures, accepte tel quel (decision
# operateur, 13/07), meme doctrine que ffmpeg/#23 ci-dessus. --with-deps installe les
# libs systeme requises (fonts, libnss, etc.) en plus du binaire Chromium lui-meme.
RUN playwright install --with-deps chromium

COPY vanguard/backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -c backend/requirements-lock.txt -r backend/requirements.txt

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
# --proxy-headers + --forwarded-allow-ips : nginx (seul en façade) fournit
# X-Forwarded-For ; uvicorn ne fait confiance qu'à un peer listé pour reconstituer l'IP
# client réelle. 18/07 (audit sécurité) -- bug réel trouvé et corrigé : la liste ne
# contenait QUE 127.0.0.1, mais nginx tourne sur l'HOTE et se connecte au port publié du
# conteneur (127.0.0.1:8000 côté host) -- le NAT Docker fait apparaître ce trafic, DANS le
# conteneur, comme venant de la passerelle du bridge Docker (172.17.0.1 par défaut), jamais
# 127.0.0.1. Résultat avant correctif : --forwarded-allow-ips=127.0.0.1 ne matchait JAMAIS
# le vrai peer, X-Forwarded-For était donc ignoré même une fois nginx corrigé pour
# l'envoyer -- request.client.host restait la passerelle bridge, identique pour TOUS les
# visiteurs, transformant le plafond anti-abus par IP en un seul bucket partagé mondial.
# Les deux valeurs sont listées (127.0.0.1 aussi) pour rester robuste à un appel local
# direct (tests, health-check) sans dépendre d'une IP de passerelle qui pourrait varier si
# la topologie réseau change un jour.
CMD ["sh", "-c", "echo \"aria-api boot commit=${GIT_COMMIT} PORT=${PORT:-8000}\" && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info --proxy-headers --forwarded-allow-ips=127.0.0.1,172.17.0.1"]