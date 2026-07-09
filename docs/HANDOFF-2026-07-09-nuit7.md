# HANDOFF — 2026-07-09 nuit (suite 7) — Shekel, endpoint arena-signal, panne CoinGecko découverte, session autonome

Suite directe des six HANDOFF précédents du 09/07 + `CLAUDE.md` + `docs/etat-systeme-cable.md`.
Segment couvrant : la diligence Shekel, la construction et le déploiement (mouvementé)
de l'endpoint `arena-signal/btc`, la découverte d'un vrai changement de politique
CoinGecko, et une session autonome de plusieurs heures pendant laquelle l'opérateur
s'est absenté ("tu bosse pendant 8 heures").

## Pilote Arena Virtuals — pivot vers Shekel (#60)

Après le premier trade réel réussi sur `dgclaw-skill` cette même nuit (voir nuit6),
l'opérateur a fait remarquer à raison que "trader avec juste un prompt" est fragile —
l'agent GAME brut n'a AUCUN outil de donnée de marché (confirmé sur le dépôt officiel
`dgclaw-skill` : seulement `join`/`acp trade`/`forum`/`leaderboard`, zéro prix/RSI/funding).

**Diligence menée sur `shekel.xyz`** (plateforme no-code d'agents de trading Hyperliquid,
partenariat confirmé avec Virtuals) avant d'y toucher, conformément à la nouvelle norme
"profondeur proportionnelle à l'enjeu" :
- **Non-custodial confirmé** : clé API Hyperliquid **trade-only** (jamais de retrait),
  aucune clé privée transmise — plus sûr encore que le modèle de signature locale d'`acp-cli`.
- **Mécanisme "Custom Data Endpoint"** déjà documenté et natif : exactement ce qu'on
  voulait construire nous-mêmes (injecter les vraies analyses d'ARIA dans le contexte
  de décision d'un agent tiers).
- Origine : évolution de "Kosher Capital" (jeton $SHEKEL, pool réel ~70 455$ de
  liquidité mais volume 24h quasi nul — sans impact, on n'achète/stake pas le jeton).
  Aucune plainte scam/rug trouvée sur X. Frais réel non documenté ailleurs : **0.75%
  par trade autonome** (Kosher Capital) — à vérifier avant d'engager du capital.
- **Alternatives vérifiées** (HyperAgent, Coinrule, Katoshi) : aucune n'a d'intégration
  Arène Virtuals — c'est le seul vrai avantage propre de Shekel pour notre objectif.
  HyperAgent est le plus crédible des trois si le critère Arène n'était pas requis
  (société suisse, non-custodial, journal d'incidents public).

**Prompt v6 rédigé** (Goal + Important Notes + Governance), traduisant les règles
absolues d'ARIA (autorité finale opérateur, jamais d'auto-modification silencieuse des
règles de risque, zéro trace IA dans les posts publics, fail-closed par défaut).
Compte Shekel pas encore créé par l'opérateur au moment d'écrire ceci (bloqué sur IONOS,
voir plus bas).

## Endpoint public `/api/aria/arena-signal/btc` — construit, déployé, débogué en 3 manches

Nouveau seam pour nourrir Shekel (et tout agent tiers futur) avec les VRAIES analyses
BTC d'ARIA plutôt qu'un prompt seul : `packages/aria-core/src/aria_core/skills/arena_signal.py`
+ route dans `vanguard/backend/app/main.py`. Réutilise `btc_cycles` (cycle macro) et
`entry_signals.rsi_series` (RSI de Wilder) — aucun doublon de client.

**Trois manches de débogage réel, chacune une vraie cause différente** :
1. **Mauvais domaine** : `ariavanguardzhc.com` = vitrine statique SEULEMENT, verrouillée
   par Basic Auth sur TOUT le chemin (y compris `/api/*`, qui retombe sur `index.html`
   faute de règle de proxy dans ce fichier nginx). Le vrai backend vit sur
   **`api.ariavanguardzhc.com`** (sous-domaine séparé, `proxy_pass` vers `localhost:8000`,
   **aucun Basic Auth** — déjà public par conception). Rien à corriger côté nginx, juste
   la bonne URL.
2. **Mauvais fichier `.env`** : `deploy.sh` lit `vanguard/backend/.env`, pas `/opt/aria/.env`
   à la racine — la clé CoinGecko ajoutée au mauvais endroit n'atteignait jamais le
   conteneur (confirmé vide via `docker exec aria-api env | grep COINGECKO`).
3. **Vraie découverte, la plus importante** : une fois la clé au bon endroit, l'endpoint
   renvoyait toujours `null`. Testé en DIRECT sur l'API CoinGecko réelle (`error_code
   10012`, HTTP 401) : **CoinGecko limite désormais TOUT le tier gratuit aux 365 derniers
   jours d'historique, quelle que soit la taille de la fenêtre demandée** (testé : une
   requête courte mais ancienne échoue pareil qu'une requête longue — ce n'est pas un
   problème de découpage, c'est une limite absolue sur l'ancienneté). Confirmé identique
   sur `market_chart/range` ET `market_chart?days=max`.

**Impact réel, pas cosmétique** : `btc_cycles.fetch_btc_history` (qui alimente aussi
l'overlay macro du rapport `/vc`, tâche #14, déjà validé et déployé le 09/07) demande
l'historique depuis 2015 — **structurellement incompatible avec le tier gratuit
désormais**. Corrigé pour le RSI (fenêtre de 90 jours au lieu de l'historique complet,
verrouillé par un test dédié qui distingue explicitement l'appel court du long) ; **pas
encore corrigé pour la segmentation complète des 3 cycles** — reste en dégradation
douce (renvoie `None`, jamais une valeur inventée) tant qu'une source alternative
gratuite fiable n'est pas trouvée et vérifiée (voir ci-dessous, bloqué sur l'accès réseau).

## Session autonome ("tu bosse pendant 8 heures", opérateur absent)

L'opérateur a explicitement demandé de continuer à construire sans lui, en prenant des
initiatives, avec un point d'attention qu'il a lui-même soulevé : **tenir une liste
cumulative de tous les domaines réseau nécessaires** plutôt que de le solliciter un par
un pendant son absence.

**Livré pendant ce segment autonome** :
- Fix RSI (fenêtre courte), voir ci-dessus.
- **Mineur de conversations opérateur/ARIA** (#57) : `skills/telegram_conversation_miner.py`,
  tâche heartbeat `telegram_miner_cycle` (60min, throttle ~1x/jour), gate OFF par défaut
  (`ARIA_TELEGRAM_MINER_ENABLED`). Relit `relay_chat.py` (rien dupliqué), propose un
  enseignement durable via ISSUE GitHub — même doctrine stricte que `knowledge_inbox`/
  `claude_mentor` (jamais commit/fusion autonome). **Garde-fou dédié construit dès le
  départ** (pas après-coup) : la source est une conversation privée (peut contenir des
  secrets — vécu en conditions réelles cette même nuit), la destination une issue GitHub
  PUBLIQUE, et une création d'issue ne passe PAS par le scan `detect-secrets` de la CI
  (qui ne couvre que les push). `_looks_like_secret` bloque toute publication au moindre
  doute. 16 tests.

**Incident auto-détecté et corrigé pendant la revue de sécurité de fin de session** :
en écrivant les tests du mineur, j'ai utilisé PAR ERREUR la vraie clé CoinGecko et la
vraie IP du VPS comme données de test pour vérifier le filtre anti-secret — commité et
poussé sur `main` avant d'être repéré. Corrigé immédiatement (valeurs remplacées par un
placeholder RFC 5737 et une fausse clé), et surtout : **le vrai trou qui a laissé passer
ça a été identifié et bouché** — `test_coherence.py` ne vérifiait les IP en clair que
dans une liste fixe de docs Markdown, jamais dans le code/tests Python. Étendu
(`test_no_public_ip_in_source_or_tests`), zéro faux positif vérifié sur tout le code
existant avant activation. `.secrets.baseline` régénéré (une seule entrée légitime :
une fausse clé privée factice dans un test, même précédent que le JWT du corpus
`security_sim`). **La clé CoinGecko doit être considérée comme potentiellement exposée
et régénérée par prudence** (rate-limitée, pas une clé de fonds — pas d'urgence, mais
à faire).

**Recherche approfondie mais bloquée (documenté, pas abandonné)** :
- Tâche #62 (source alternative pour l'historique BTC long) : deux candidats identifiés
  par recherche web (FRED/CBBTCUSD — mais données Coinbase sous copyright, zone grise
  légale non tranchée ; Bitcoin.com Charts API — profondeur non vérifiable, domaine non
  autorisé). Décision : ne pas brancher sans vérification directe, cohérent avec la
  norme de diligence qu'on vient d'écrire ensemble.
- Tâche #59 (Polymarket) : bloqué, aucun domaine Polymarket autorisé.
- Tâche #64 (audit B5, barres Potentiel-$) : le détail exact de l'audit original a été
  perdu dans une compaction de contexte antérieure. Plutôt que de deviner sur un rapport
  premium déjà validé visuellement par l'opérateur, la tâche reste en attente de
  clarification.

## Domaines réseau demandés/utilisés ce segment (liste cumulative)
Déjà autorisés et actifs : `*.virtuals.io`, `degen.virtuals.io`, `whitepaper.virtuals.io`,
`docs.game.virtuals.io`, `basescan.org`/`api.basescan.org`, `sepolia.base.org`,
`*.youtube.com`, `shekel.xyz`/`*.shekel.xyz`, `x.com`/`twitter.com`,
`geckoterminal.com`/`*.geckoterminal.com`, `coingecko.com`/`*.coingecko.com`/
`docs.coingecko.com`, `ariavanguardzhc.com`/`*.ariavanguardzhc.com`,
`cloudpanel.ionos.fr`/`*.cloudpanel.ionos.fr`, `hyperagent.ch`, `coinrule.com`, `katoshi.ai`.

**Encore bloqués, jamais autorisés** (pour reprendre #62/#66 dès que possible) :
```
blockchain.info / *.blockchain.info
bitcoin.com / *.bitcoin.com
fred.stlouisfed.org / *.stlouisfed.org
polymarket.com / gamma-api.polymarket.com / clob.polymarket.com
```

## IONOS — accès Control Panel instable (non résolu par nous)
L'opérateur a perdu l'accès au panneau IONOS (icône serveur cassée) au même moment où
il configurait Shekel. Vérifié depuis cette session : connexion TLS réinitialisée sur
`cloudpanel.ionos.fr` (impossible de distinguer une vraie panne IONOS d'un blocage
anti-bot depuis un client non-navigateur). Aucun incident actif confirmé sur la page de
statut officielle au moment de la vérification, mais un vrai incident Control
Panel/WordPress avait été résolu la veille (08/07) — cause probable non tranchée.
Aucune action possible de notre côté ; support IONOS ou nouvelle tentative navigateur.

## Ce qui reste en attente (priorité pour la prochaine session)
1. Vérifier si l'opérateur a récupéré l'accès IONOS et créé le compte Shekel.
2. Une fois `docs.coingecko.com`/etc. déjà autorisés : décider (avec l'opérateur) entre
   régénérer la clé CoinGecko exposée, et si on tranche sur FRED/Bitcoin.com/un plan
   payant pour restaurer la segmentation complète des cycles BTC.
3. Reprendre #59 (Polymarket) et #64 (audit B5) une fois les domaines/contexte disponibles.
4. Le trade réel HL Perps de nuit6 (0.0003 BTC long) reste ouvert — vérifier son état
   (`acp trade hl-status`) et si Vanguard ZHC est enfin apparu au classement public.
5. Toujours en attente depuis plusieurs segments : durcissement SSH VPS (#17), JWT non
   vérifié dans `skills/core/memory/ACP VIRTUAL PROTOCOL/20260628_1139_source.md:211`.

## Auto-critique honnête
Point positif : la revue de sécurité de fin de session a réellement trouvé et corrigé
deux problèmes concrets (fuite de vraie clé/IP dans un test, trou de couverture dans
`test_coherence.py`) — la norme "vérif sécurité après chaque construction" a payé très
concrètement ce segment, pas comme une case à cocher. Point à surveiller : j'ai
moi-même introduit l'erreur (vraie clé/IP dans un test) que la revue a ensuite
corrigée — la discipline "jamais de vraie valeur, même en test" doit devenir un réflexe
dès l'écriture, pas seulement en relecture. Sur les tâches bloquées (#62, #66, #64) :
choix assumé de ne pas deviner/brancher à l'aveugle plutôt que de livrer vite et mal —
cohérent avec la norme de diligence, mais laisse trois tâches du backlog sans
progression concrète ce segment.
