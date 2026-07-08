# État du système ARIA — ce qui est DÉJÀ câblé (faits établis, ne pas re-demander)

> Fiche destinée à **toute nouvelle session** (agent ou opérateur). Elle répond aux questions
> récurrentes sur « comment mon système fonctionne » pour éviter de les reposer. Faits vérifiés
> par audit (nuit 07/07/2026 : intégrations + câblage + sécurité). Si tu changes le câblage,
> mets cette fiche à jour dans le même commit.

## Principe de base : aria-core est AUTONOME pour la donnée
La librairie cœur `packages/aria-core/src/aria_core/` a **ses propres clients d'API externes**.
Elle ne dépend **pas** du backend `vanguard/` pour aller chercher la donnée on-chain. Le backend
la **configure au démarrage** (`register_aria_host_integrations` → `bootstrap.configure`) puis
l'utilise — il ne lui « fournit » pas la donnée.

## Réponses aux questions récurrentes

- **Comment aria-core récupère l'OHLCV (bougies) ?**
  → `services/ohlcv.py` appelle **directement GeckoTerminal** (`api.geckoterminal.com/api/v2/.../ohlcv`).
  C'est déjà fait, autonome, testé. **Ne pas** porter/abstraire/passer par le backend : c'est un
  doublon inutile. (Le backend a AUSSI son propre `app/services/geckoterminal.py` pour son API web —
  deux couches distinctes, c'est voulu.)

- **Comment aria-core lit le prix / la liquidité / les paires ?**
  → `skills/acp_onchain_scan.py` appelle **DexScreener** directement (`api.dexscreener.com`).

- **Comment aria-core lit le contrat / holders / is_contract / fonctions sensibles ?**
  → `services/blockscout.py` (Base Blockscout, sans clé). C'est « les yeux on-chain ».

- **Comment aria-core lit market cap / FDV / catégories ?**
  → `services/coingecko.py` (sans clé, throttlé).

- **Détection honeypot / taxes réelles / owner caché ?**
  → `services/goplus.py` (GoPlus Security, gratuit), branché data-gated (`include_honeypot`) dans
  le scan + barrières `safety_screen`. Actif sur l'analyse VC.

- **Le LLM est-il actif ?**
  → Oui en prod (provider **Virtuals/Spark**, `compute.virtuals.io`), gaté par `ARIA_LLM_ENABLED`
  + clé `VIRTUALS_API_KEY` dans le `.env` du VPS. En CLI, `simulate_lifecycle` configure l'hôte
  pour la parité prod.

- **Où sont les données persistantes (track record, carnet, prédictions, paper-trading) ?**
  → SQLite dans `DATA_DIR` = `/opt/aria-data` sur le VPS (bind-mount Docker). GitHub sauvegarde le
  CODE, pas ces données. Sauvegarde : `vanguard/backup-data.sh`.

- **Comment tourne l'autonomie ?**
  → boucle `heartbeat.py` démarrée par l'hôte (`main.py`), coupée par le kill-switch
  (`outgoing_pause`). Jobs réels : `vc_crawl` (découverte→filtre→pool), `vc_resolve`,
  `vc_weekly_forecast`, `vc_self_report`, `vc_radar_x`, `vc_thesis_review`, `paper_trade_cycle` (gaté).

## Ce qui est un SEAM VIDE (préparé mais pas branché — ne pas le présenter comme actif)
- `services/x_social.py` : le radar social tourne mais **en veille** (aucune vraie source X/Farcaster injectée → renvoie []).
- `release_pipeline.py` (campagne X/TikTok) : complet mais **aucun déclencheur** ne l'appelle (rien ne l'arme).
- **TikTok** : publisher non branché (`tiktok_publisher=None`).
- `aria_core.x_profile` : module **non livré** (imports gardés en try/except pour ne pas crasher).

## Ce qui est CODÉ mais ÉTEINT faute de clé (normal)
LLM Vision, images xAI, ACP (CLI absent du conteneur = exécution financière de-facto non câblée).
Stripe/Privy actifs seulement si leurs clés sont dans le `.env`.

- **SMTP Gmail (rapports email) : ACTIF en prod.** `/vc <contrat>` (mode normal, hors `test`)
  demande la **langue du rapport** (boutons Telegram FR/EN) avant de lancer l'analyse LLM, puis
  envoie un **PDF sécurisé** (reportlab + chiffrement pypdf, permissions limitées à l'impression
  — dissuasif, jamais inviolable) en pièce jointe, avec filigrane nominatif traçable (destinataire
  + empreinte SHA-256). Le corps de l'email ne contient qu'un **teaser court** (badges, R/R) —
  la thèse et le rapport détaillé complet ne sont JAMAIS en clair dans le corps, seulement dans
  le PDF joint. Destinataire fixe (jamais demandé). Voir `skills/vc_delivery.py`,
  `skills/vc_report_pdf.py`, `skills/vc_i18n.py` (`SUPPORTED_VC_LANGS = (fr, en)` seulement —
  ES/IT/ZH pas encore supportés, à faire si demandé).

## Cockpit « ARIA en direct » (#21) — EN LIGNE (câblé + déployé 08/07)
- `/cockpit` sur la vitrine : pouls public (`GET /api/pulse`, sans auth — heartbeat vivant/mort,
  derniers cycles, badges paper-trading/exécution réelle/ancrage onchain) + dossier par contrat
  (`GET /api/aria/dossier/{contract}`, **gaté opérateur uniquement**, jamais public/abonné).
- Secret opérateur : **`sessionStorage` uniquement** (jamais `localStorage`), transmis **en
  header** (`X-Admin-Secret` + `X-Admin-Totp` optionnel), jamais en query-string. Verrouillé par
  `test_coherence`.
- Commande Telegram **`/watchlist [n]`** (admin, n∈[1,30], défaut 10) : classement du pool
  screené (`candidate_ranking.top_candidates`) — c'est LA checklist des contrats qu'ARIA suit.

## Déploiement VPS — DEUX scripts séparés, ne pas confondre
- `./vanguard/deploy.sh` déploie **uniquement le backend** (conteneur Docker `aria-api`).
- `./vanguard/deploy-vitrine.sh` déploie **uniquement la vitrine statique** (build Vite → webroot
  nginx, publication atomique). Aucune dépendance croisée : builder/déployer le backend ne touche
  jamais aux fichiers statiques déjà servis, et vice-versa.
- **Toute évolution du frontend (`vanguard/src/**`) exige de lancer les DEUX scripts** — sinon le
  site sert encore l'ancien bundle malgré un backend à jour (piège rencontré le 08/07 : `/cockpit`
  affichait l'ancienne page d'accueil après un `deploy.sh` seul, faute d'avoir aussi lancé
  `deploy-vitrine.sh`).

## Doctrine câblage (rappel)
- Ajouter une source de donnée = un nouveau `services/<x>.py` (même dôme : throttle + backoff +
  dégradation gracieuse) branché **additif et data-gated** sur `scan_base_token` via un drapeau
  `include_<x>`. Sans la donnée, comportement inchangé. Voir `docs/architecture-extensibilite.md`.
- Ne jamais dupliquer un client déjà existant (ex. OHLCV) « pour découpler » : c'est un doublon.
