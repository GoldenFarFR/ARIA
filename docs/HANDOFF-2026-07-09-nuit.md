# HANDOFF — 2026-07-09 nuit — Nettoyage Aria Market, suppression Stripe, swap réel Sepolia

Suite directe de `docs/HANDOFF-2026-07-09.md` (même journée, segment plus tardif). Lire les
deux + `CLAUDE.md` + `docs/etat-systeme-cable.md`. Tout est fusionné dans `main` (dernier commit
`2f23e7b`) ET déployé sur le VPS (backend) au moins jusqu'à `22f30384619c` confirmé par
l'opérateur — les 2 derniers commits (`db3bf67`, `8f12f15`) restent à redéployer si ce n'est pas
déjà fait au moment de la reprise (`./vanguard/deploy.sh` seul suffit, aucun changement backend
qui nécessite `deploy-vitrine.sh` séparément — sauf le dernier commit `8f12f15` qui touche
`vanguard/src`, donc **les deux scripts** sont nécessaires pour être à jour partout).

## Déclencheur : ARIA a donné une fausse réponse en Telegram
L'opérateur a demandé "Tu as des analyses en stock ?" — ARIA a répondu qu'il fallait "ajouter
des paires dans Aria Market" (payant, 10239 tokens). Faux : la vraie découverte est autonome
(`vc_crawl` toutes les 6h, `candidate_ranking`, `/watchlist`). Root cause tracée jusqu'au bout
plutôt que patchée en surface.

## Nettoyage narratif Aria Market — FAIT, testé, mergé (commit `9c6c1ec`)
`FLAGSHIP_PRODUCT = "Aria Market"` (retiré, mais toujours utilisé comme "actuel" dans ~20
endroits) a été corrigé partout où il affirmait qu'une filiale est live, alors que le fait
déjà correct ailleurs (`holding.py`, `canonical_facts.yaml`) dit l'inverse :
- `grounding.py:grounded_llm_identity()` — le bloc anti-hallucination lui-même mentait.
- `persona.md` — org chart, mission, exemple de ton, table d'autonomie.
- `knowledge/epistemic_core.yaml` — **deux croyances calibrées à `p_true: 1.0`** ("Aria Market
  est la filiale phare actuelle") : c'était le pire cas, une "certitude" fausse.
- `truth_ledger/canonical_facts.yaml` + `content/faq.yaml` — étaient auto-contradictoires (une
  moitié disait "retiré", l'autre "actuel", dans le MÊME fichier).
- `repertoire_skill.py` — `execute_develop_repertoire` auto-créait une fausse entrée "Aria
  Market" à chaque répertoire vide ; ne le fait plus. Suggestions basées sur les vraies
  données du répertoire, plus sur le nom retiré en dur.
- `comms_skill.py`, `brain.py`, `narrative.py` (~13 fonctions), `heartbeat.py`,
  `x_publication_policy.py`, `content/site_copy.py` (CTA public), `directives.md`,
  `proactive.py`, `locale.py` (le message exact qui a causé l'incident), `aria_goals.yaml`,
  `entrepreneur_skill.py`, `knowledge/seed.py`.
Tests mis à jour en conséquence (`test_narrative.py`). Suite complète verte (1349/1350, le seul
échec est le test rugby réseau déjà connu, sans lien).

**Correctif tardif (commit `db3bf67`)** : le titre FastAPI lui-même (`config.py:app_name`) et un
log de boot disaient encore "Aria Market" — repéré dans le health-check de l'opérateur après
déploiement. Corrigé en "Aria Vanguard ZHC".

## Suppression Stripe — FAIT (commit `05be585`)
Demande explicite opérateur : "supprime toute trace de stripe j'irai le recréer plus tard".
Retiré : `app/billing/subscriptions.py`, `api/routes/billing.py`, routes `/api/billing/*`,
réglages `stripe_*`/`market_pro_price_usd`, bloc `.env.example`, dépendance `stripe` du
`requirements.txt`, et `PricingSection.tsx`/`SubscribeProButton.tsx` (jamais montés dans
l'app réelle, vérifié). `is_pro_active`/`get_subscription` n'étaient consommés que par le
module billing lui-même — coupure nette, rien d'autre n'en dépendait.

**Reste à faire par l'opérateur** : les vraies variables `STRIPE_SECRET_KEY`/
`STRIPE_WEBHOOK_SECRET`/`STRIPE_PRICE_ID` sont toujours dans le `.env` du VPS — pas touché
depuis cette session cloud (pas d'accès VPS). À retirer manuellement si souhaité.

## Swap réel de test sur Sepolia — CODÉ, PAS ENCORE ARMÉ (commit `790134e`)
Décision opérateur explicite : le rehearsal Sepolia doit exécuter un vrai swap (signé,
diffusé, confirmé), pas seulement ancrer un hash de décision. Contrainte technique
importante clarifiée avec l'opérateur AVANT de coder : Base Sepolia et Base mainnet sont deux
chaînes séparées — le token candidat qu'ARIA analyse réellement n'existe pas sur Sepolia. Le
swap porte donc sur une **paire de test configurée**, jamais le candidat réel — teste le
mécanisme d'exécution (gas, nonce, confirmation), pas une thèse de marché. Choix confirmé par
l'opérateur après explication.

- `sepolia_wallet.send_test_swap_transaction` : wrap WETH (predeploy OP-stack, adresse fixe
  `0x4200...0006`, identique sur toutes les chaînes OP-stack) → `approve` → Uniswap V3
  `exactInputSingle`, trois transactions réellement signées. Verrouillé chain_id Sepolia
  (comme `send_anchor_transaction`), plafond dur `MAX_TEST_SWAP_WEI` (~0,002 ETH).
- Nouveau gate additif `ARIA_SEPOLIA_SWAP_ENABLED`, au-dessus du triple gate existant.
- Câblé dans `run_autonomous_cycle` : tentative de swap indépendante de l'ancrage sur BUY —
  échec de l'un n'efface jamais le succès de l'autre, les deux journalisés séparément
  (migration de schéma non-destructive, `swap_tx`/`swap_error`).
- `autonomous_status()` expose `swap_enabled`/`swap_tx_count`/`swap_error_count`.

**Bloquant avant activation** : le routeur/la pool réels sur Base Sepolia n'ont PAS pu être
vérifiés depuis cette session cloud — aucun accès RPC sortant (testé concrètement : erreur
proxy 403 sur `sepolia.base.org`, capturée). Ne jamais armer `ARIA_SEPOLIA_SWAP_ENABLED` sur
une adresse non vérifiée on-chain.

### Route de vérification — reprend l'accès RPC déjà fonctionnel d'ARIA (commit `5fe17cb`)
L'opérateur a suggéré : pourquoi pas demander à ARIA de vérifier elle-même, puisque son
backend a déjà un accès RPC qui marche (prouvé par la lecture du solde du wallet) ?
`GET /api/aria/sepolia/code?address=...` (gaté opérateur, `require_operator`, même patron que
`/dossier/{contract}`) fait un `eth_getCode` réel, lecture seule, aucune clé. Depuis le VPS :
```bash
curl -s -H "X-Admin-Secret: <secret>" \
  "http://127.0.0.1:8000/api/aria/sepolia/code?address=<routeur candidat>"
```
→ `has_code:true` confirme qu'un contrat existe réellement à cette adresse sur Sepolia.
Reste à faire : trouver une vraie pool liquide (suggestion donnée à l'opérateur : app.uniswap.org
en réseau Base Sepolia), puis renseigner `ARIA_SEPOLIA_SWAP_ROUTER`/`_TOKEN_OUT` et armer le gate.

## Découverte importante : `vanguard/product-frontend/` est RÉEL, pas mort (commit `8f12f15`)
En élargissant le nettoyage Aria Market à `vanguard/`, j'ai d'abord cru (à tort) que
`product-frontend/` (espace membre en iframe : OrgChart, RepertoirePanel, AgentPanel,
CorporatePanel...) était du code mort comme `PricingSection`. **Faux** — vérifié en lisant
`vanguard/Dockerfile` : il a une étape de build dédiée (`frontend-build`) et son résultat est
copié dans l'image finale, servi par `app/main.py` (`_mount_frontend`) à la racine même du
backend (`api.ariavanguardzhc.com/`). C'est le conteneur `deploy.sh` que l'opérateur a
redéployé plusieurs fois ce soir. **Ne jamais le supprimer.**

Ce qui EST confirmé mort en revanche (vérifié par absence totale d'import dans
`vanguard/src/pages/` — `App.tsx` ne route que vers `ClientSite`/`CockpitPage`/`VanguardSite`) :
le mécanisme qui devait OUVRIR `product-frontend` en iframe DEPUIS la vitrine —
`ProductFrame`, `MemberWelcome`, `ProductLaunchHint`, `ProductLaunchLink`,
`resolve-product-session.ts` — jamais montés nulle part, retirés (avec confirmation explicite
de l'opérateur avant suppression, le classificateur auto-mode a bloqué la première tentative
sans confirmation). `product-handoff.ts` allégé aux 2 fonctions encore utilisées par
`MemberGate` (nettoyage défensif d'un vieux `?launch=market`). Build vitrine complet vérifié
(`tsc -b && vite build`, propre) après suppression.

**`product-frontend/` reste nommé "Aria Market" dans son UI** (~15 endroits : titre, README,
OrgChart, RepertoirePanel, AgentPanel, CorporatePanel, EmbeddedChartPanel...) + quelques
labels backend qui le décrivent (`auth.py:site_name`, `websocket.py` message de connexion).
**Volontairement non touché** : demandé à l'opérateur par quoi remplacer "Aria Market" — sa
réponse : "je sais pas car ARIA est l'IA et les futurs produits auront leur propre nom".
Décision juste — ne jamais renommer en "ARIA" comme solution de facilité. **En attente d'un
vrai nom de produit avant de toucher à ces fichiers.** `docs/VISION.md`, `AGENTS.md`,
`ECOSYSTEM-REPOS.md` (racine vanguard) pas encore traités — même bucket.

## État des tests
Aria-core : 1349 passed / 7 skipped / 1 échec connu sans lien (rugby, réseau). Vanguard
backend : 48 passed. Coherence guardrail : 51 passed. Build vitrine (`tsc -b && vite build`) :
propre.

## Ce qui reste en attente
- Nom de remplacement pour `product-frontend`/"Aria Market" — décision opérateur, pas encore prise.
- Vérification on-chain réelle du routeur/pool Sepolia (route de vérification prête, à utiliser
  depuis le VPS) avant d'armer `ARIA_SEPOLIA_SWAP_ENABLED`.
- Retrait manuel des vraies clés `STRIPE_*` du `.env` VPS (si souhaité).
- Clé SSH + TOTP — toujours en pause (voir HANDOFF précédent), reprendre sur PC perso opérateur.
- `AriaLedger.sol` sur Sepolia (Foundry) — toujours pas déployé, rehearsal reste `skipped_no_ledger`.
- Backlog restant sans blocage : #8, #9, #10, #11, #13, #17, #19-23, #29, #32, #34, #40.
