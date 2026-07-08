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

## Rehearsal Sepolia autonome + relay chat + exam — EN LIGNE (câblé + déployé 08/07 nuit)
- **Sepolia autonome** : `ARIA_SEPOLIA_WALLET_ENABLED` + `ARIA_SEPOLIA_AUTONOMOUS_ENABLED` actifs
  en prod. Wallet dédié `0x8c8c163DA8099Ef7B553Ee9D4D56EdE8c205Cae5`, financé (0.0001 ETH
  Sepolia, faucet). `GET /api/aria/sepolia-status` confirmé propre (`enabled:true,
  error_count:0, circuit_breaker_open:false`). **Reste en `skipped_no_ledger`** tant que
  `AriaLedger.sol` n'est pas déployé sur Sepolia (`contracts/DEPLOY.md`, étape distincte non
  encore faite) — normal, pas un bug.
- **Relay chat (chat à 3)** : `ARIA_RELAY_ACCESS_TOKEN` actif, `GET /api/aria/relay/recent` +
  `POST /api/aria/relay/reply` vérifiés en réel (historique Telegram lu avec succès). **Limite
  découverte 08/07 nuit** : une session Claude Code tournant dans un environnement cloud/web
  (comme celle-ci) n'a **pas d'accès réseau sortant vers le VPS** (politique proxy de
  l'environnement, non contournable) — je ne peux donc PAS interroger le relay de façon
  autonome depuis une session web. Deux façons d'utiliser le relay en pratique : (1) session
  cloud → l'opérateur relaie manuellement via `curl` les messages que je compose ; (2) Claude
  Code **en local** (desktop, réseau normal) → lecture/écriture autonome réelle du relay,
  sans geste de l'opérateur. Ne pas re-proposer un chat "autonome" depuis une session cloud
  sans vérifier l'accès réseau d'abord.
- **Exam pédagogique** : `ARIA_EXAM_ENABLED` actif, `GET /api/aria/exam-status` répond
  correctement (`enabled:true, program_days:20`).
- Déploiement confirmé sur commit `30fd82c05777` (backend + vitrine), marqueur
  `.claude/last-deployed-ref` recalé.
- **Conversation relay ARIA <-> Claude Code (08/07 nuit) — EN LIGNE, CONFIRMÉ EN PROD** :
  `relay_conversation.py` + heartbeat `relay_conversation_cycle` (15 min).
  `ARIA_RELAY_AUTOREPLY_ENABLED=true` actif sur le VPS. ARIA répond dans sa propre voix
  (LLM réel, sans préfixe) uniquement quand le dernier message du relay vient de "claude" ;
  dès qu'elle répond, la condition n'est plus vraie, donc pas de boucle infinie. Plafond
  40 réponses/jour, respecte `/stop`. **Premier échange bot-à-bot vérifié en réel** (capture
  Telegram) le 08/07 nuit. **X (@Aria_ZHC) et le site web n'ont PAS besoin d'un relay dédié** :
  leur contenu est public, une session Claude Code peut déjà les lire directement (navigation
  web normale) — le seul canal d'écriture retour vers ARIA reste le relay Telegram existant.
- **Claude Code tourne aussi DIRECTEMENT sur le VPS (08/07 nuit)** : installé dans `/opt/aria`
  (Node.js 20 + `npm install -g @anthropic-ai/claude-code`). Accès réseau normal + accès
  direct `http://127.0.0.1:8000` (pas de nginx, pas de verrou Basic Auth) — c'est la session
  à privilégier pour toute interaction relay/Telegram en direct, plutôt qu'une session cloud
  (bloquée réseau) ou un clone local Windows (désynchronisation manuelle).

## Audit dexpulse/Aria Market (08/07 nuit) — nettoyage PAS ENCORE fait, juste cartographié
L'opérateur veut purger toute trace de "dexpulse"/"Aria Market" (noms de produit obsolètes).
Audit réalisé (agent dédié), **aucun changement appliqué** — à traiter comme chantier séparé :
- **Bug réel trouvé, indépendant du renommage** : `repertoire_skill.execute_develop_repertoire`
  (appelée par le heartbeat à chaque cycle, `heartbeat.py:529`) **re-sème "Aria Market"** comme
  filiale active dans `aria.db` si le répertoire est vide — alors que `canonical_facts.yaml`
  affirme qu'aucune filiale n'est active. Contradiction à corriger, peu importe le nom choisi.
- **Pas juste cosmétique** : un vrai fichier sur le disque VPS (`/opt/aria-data/dexpulse.db`,
  `paths.py:product_db_path()`), un cookie de session déjà posé chez les membres actuels
  (`aria_market_token`), des clés `localStorage` du frontend produit.
- **Certains endroits gardent "dexpulse"/"Aria Market" EXPRÈS** — ce sont des garde-fous
  anti-hallucination (`knowledge/contradiction.py`, `knowledge/epistemic_core.yaml`,
  `brain.py` routage FAQ, `holding.py` constantes de purge `DEXPULSE_SLUG`/`ARIA_MARKET_SLUG`)
  qui détectent et corrigent si quelqu'un (LLM ou visiteur) prétend que ces produits sont
  encore actifs. Un renommage aveugle casserait ces détections — à traiter au cas par cas, pas
  en find/replace global.
- Aucun abonné Stripe réel n'existe encore (confirmé par l'opérateur) → renommer `PLAN_ID`
  (`dexpulse_pro`) est sans risque de casser un abonnement en cours.

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
