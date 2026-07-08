# HANDOFF — 2026-07-08 (nuit) — Sepolia autonome déployé, relay chat, exam, audit dexpulse

Reprise directe : lire ce fichier + `CLAUDE.md` + `docs/etat-systeme-cable.md`. Tout est fusionné
dans `main` ET déployé sur le VPS (backend + vitrine), commit `30fd82c05777` confirmé par health
check. Marqueur `.claude/last-deployed-ref` recalé.

## Déploiement VPS réalisé cette nuit (walkthrough en direct avec l'opérateur)
1. **Wallet Sepolia généré et financé** : `vanguard/operator/gen-sepolia-wallet.py` exécuté sur le
   VPS (via un venv dédié — PEP 668 sur Ubuntu 24.04 bloque `pip3 install` direct, il a fallu créer
   un venv après `apt install python3.12-venv`). Adresse `0x8c8c163DA8099Ef7B553Ee9D4D56EdE8c205Cae5`,
   financée via faucet (0.0001 ETH Sepolia — suffisant pour des dizaines de cycles testnet).
2. **`.env` VPS rempli** : `ARIA_SEPOLIA_WALLET_ENABLED`, `ARIA_SEPOLIA_PRIVATE_KEY`,
   `ARIA_SEPOLIA_AUTONOMOUS_ENABLED`, `ARIA_RELAY_ACCESS_TOKEN` (généré `openssl rand -hex 32`),
   `ARIA_EXAM_ENABLED`. **Deux incidents de manipulation en cours de route**, tous deux corrigés :
   - Une commande donnée avec un placeholder littéral (`<colle ici>`) a été copiée-collée telle
     quelle par l'opérateur au lieu d'être remplacée → ligne `.env` invalide, corrigée.
   - Deux générations successives du même script ont laissé des lignes dupliquées
     (`ARIA_RELAY_ACCESS_TOKEN` et `ARIA_SEPOLIA_WALLET_ENABLED` en double, valeurs différentes)
     → dédupliqué avec `tac | awk -F= '!seen[$1]++' | tac` (garde la dernière valeur de chaque
     clé, sans qu'il y ait de numéro de ligne à substituer à la main — plus fiable qu'un `sed`
     avec placeholder, vu le premier incident).
3. **`./vanguard/deploy.sh` relancé** : health check vert, commit `30fd82c05777` confirmé.
4. **Bug trouvé et corrigé en direct** : `GET /api/aria/sepolia-status` renvoyait 500 Internal
   Server Error avant la déduplication du `.env` — résolu par le nettoyage du doublon (cause
   exacte non isolée plus finement, mais le symptôme a disparu avec l'`.env` propre + redeploy).
   Vérifié ensuite propre : `{"enabled":true,"cycles_total":0,"error_count":0,
   "circuit_breaker_open":false,"wallet_address":"0x8c8c...","wallet_balance_eth":0.0001}`.
5. **`GET /api/aria/relay/recent` vérifié en réel** : a bien renvoyé l'historique Telegram réel
   (commande `/status` de l'opérateur + réponse d'ARIA), confirmant que le relay lit le bon canal.
6. **Vitrine déjà déployée plus tôt dans la nuit** : `deploy-vitrine.sh` OK, le "301 via http" que
   le script signalait comme suspect était en fait la redirection normale vers https — le vrai
   diagnostic était un **401 Basic Auth volontaire** posé par l'opérateur lui-même pour empêcher
   l'accès public tant que la vitrine n'est pas prête à être montrée (confirmé explicitement,
   pas un bug).

## Limite d'architecture découverte : relay chat depuis une session cloud
L'opérateur a demandé de "voir Claude discuter avec ARIA dans Telegram" en direct. Test réel :
une session Claude Code tournant dans cet environnement cloud/web ne peut PAS atteindre le VPS en
réseau sortant (`curl` vers `ariavanguardzhc.com` et vers des API publiques comme
`sepolia.base.org`/`blockscout` → 403, politique de l'environnement, non contournable, confirmé
via `/root/.ccr/README.md`). Conséquence concrète :
- **Depuis une session cloud** (celle-ci) : le relay reste utilisable mais **manuel** — je compose
  le message, l'opérateur lance le `curl` lui-même, le message apparaît dans son Telegram préfixé
  `🤖 Claude — `.
- **Depuis Claude Code en local** (desktop, réseau normal de l'opérateur) : lecture/écriture
  autonome réelle du relay serait possible, sans geste de l'opérateur à chaque message.
- Opérateur a dit "ok pour le local" — décision d'utiliser une session locale pour ce chantier
  précis n'est pas encore exécutée à la fin de cette session (rien à faire côté code, juste une
  question d'environnement d'exécution).
- **Ne pas re-proposer un "chat autonome" depuis une session cloud sans revérifier l'accès réseau
  d'abord** — erreur commise une fois cette nuit (annoncé avant vérification, corrigé ensuite).

## Audit dexpulse/Aria Market — cartographié, RIEN appliqué
Sur demande opérateur ("dexpulse sa n'existe plus et aria market non plus... a supprimer
partout"), un agent dédié a audité 79 fichiers réels (hors `truth-ledger/**` générés et
`.venv`/`__pycache__`). Résumé complet dans `docs/etat-systeme-cable.md`. Points clés à retenir
pour la suite :
- **Bug réel, indépendant du renommage** : le heartbeat re-sème "Aria Market" comme filiale
  active dans `aria.db` à chaque cycle (`repertoire_skill.execute_develop_repertoire`,
  `heartbeat.py:529`), contredisant `canonical_facts.yaml` ("aucune filiale active"). À corriger
  en priorité, peu importe le nom final choisi.
- **Pas cosmétique** : fichier réel sur le disque VPS (`/opt/aria-data/dexpulse.db`), cookie de
  session déjà posé chez les membres actuels (`aria_market_token`), clés `localStorage` du
  frontend produit — un renommage brut fait perdre leurs préférences aux visiteurs actuels
  (dégradation douce, pas de perte de données grave, mais à séquencer proprement).
- **Certains endroits gardent le nom EXPRÈS** : garde-fous anti-hallucination
  (`knowledge/contradiction.py`, `knowledge/epistemic_core.yaml`, routage FAQ dans `brain.py`,
  constantes de purge dans `holding.py`) qui détectent et corrigent toute affirmation que ces
  produits sont encore actifs. Un find/replace global casserait ces détections.
- **Aucun abonné Stripe réel** (confirmé opérateur) → renommer `PLAN_ID` (`dexpulse_pro`) est
  sans risque de casser un abonnement en cours.
- **Prochaine étape** (pas commencée) : traiter par catégorie (affichage pur → renommage direct ;
  identifiants techniques à impact production → migration séquencée ; garde-fous → édition
  prudente, pas suppression).

## Ce qui reste en attente
- Déploiement de `AriaLedger.sol` sur Sepolia (`contracts/DEPLOY.md`, Foundry) — bloque le
  rehearsal Sepolia au-delà de `skipped_no_ledger`. Mis de côté volontairement par l'opérateur.
- Nettoyage dexpulse/Aria Market (voir ci-dessus) — chantier séparé, pas commencé.
- Décision opérateur : reprendre le chat à 3 depuis une session Claude Code locale.
