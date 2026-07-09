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

## Suite de la nuit — relay bot-à-bot ARIA<->Claude construit, déployé, CONFIRMÉ en prod
- **Deux clones locaux désynchronisés (251 commits de retard) résolus** : `Desktop\GitHub-Repos\ARIA`
  et `GitHub-Repos\ARIA` (racine) avaient chacun des modifications locales jamais commitées (fichiers
  qui recoupent exactement le scope de l'audit dexpulse/Aria Market — probablement une tentative de
  renommage jamais finalisée). Sécurisées via `git stash push -u`, puis `git reset --hard HEAD` +
  `git pull origin main` sur les deux. Rien perdu — tout est dans `stash@{0}` de chaque dépôt si besoin
  de le relire un jour, pas encore trié.
- **`relay_conversation.py` construit** (commit `965a674`) : ARIA répond dans sa propre voix (LLM
  réel, aucun préfixe) quand le dernier message du relay vient de "claude" — jamais l'opérateur.
  Gate dédié `ARIA_RELAY_AUTOREPLY_ENABLED` (distinct du token relay), auto-limitant (pas de boucle
  infinie : dès qu'elle répond, la condition "dernier message = claude" devient fausse), plafond
  40 réponses/jour, respecte `/stop`. Tests unitaires (23 cas, tous passants).
- **Claude Code installé DIRECTEMENT sur le VPS** (`/opt/aria`, Node.js 20 via NodeSource + `npm
  install -g @anthropic-ai/claude-code`) — résout le vrai problème de fond : un clone local sur la
  machine Windows de l'opérateur exige une synchronisation manuelle permanente, alors que `/opt/aria`
  EST déjà le clone à jour en continu (c'est celui que `deploy.sh` utilise). Cette session VPS a un
  accès réseau normal (contrairement à une session cloud comme celle-ci) et peut interroger le relay
  en LOCAL (`http://127.0.0.1:8000`), sans passer par nginx ni par le verrou Basic Auth du domaine
  public.
- **Boucle complète VÉRIFIÉE EN PRODUCTION** (capture Telegram réelle) : la session VPS a lu le relay,
  posté un message de confirmation (`🤖 Claude — ...`), ET ARIA a RÉPONDU TOUTE SEULE de façon
  autonome via `relay_conversation_cycle` (déjà actif, le flag `ARIA_RELAY_AUTOREPLY_ENABLED=true`
  ayant été ajouté au `.env` du VPS pendant ce même déploiement) — premier échange bot-à-bot réel
  confirmé, pas juste théorique.
- **Distinction importante clarifiée avec l'opérateur** : `CLAUDE.md` est un briefing pour moi (Claude
  Code), ARIA ne le lit JAMAIS. Ce qui façonne réellement ce qu'ARIA sait/fait, ce sont SES fichiers
  de connaissance (`knowledge/*.yaml`, `truth_ledger/canonical_facts.yaml`,
  `knowledge/epistemic_core.yaml`, son code `skills/`) — pas `CLAUDE.md`. Grossir `CLAUDE.md` aide
  UNIQUEMENT les futures sessions Claude Code à avoir du contexte, ça n'entraîne pas ARIA. Garder
  `CLAUDE.md` compact (résumé + pointeurs vers docs détaillés) plutôt que de le faire grossir
  directement — c'est le pattern déjà en place, à ne pas casser.

## Audit complet A-à-Z (fin de nuit, avant que l'opérateur pousse demain)
Trois agents dédiés (sécurité/garde-fous, cohérence documentation, qualité tests/code mort) ont
audité l'ensemble du système. Corrections appliquées dans la foulée (mêmes commits) :

1. **Sécurité — aucun finding critique.** Wallet_guard, outgoing_pause, séparation Sepolia
   autonome/`wallet_guard` : tout confirmé étanche. Un seul finding modéré, corrigé : l'ID
   Telegram réel de l'opérateur (`TELEGRAM_ADMIN_IDS=5864967247`) était committé en clair dans
   `vanguard/operator/local.env.example`, `production.env.example`, et un test — remplacé par un
   champ vide / placeholder (`123456789` pour le test). Pas une fuite de secret exploitable, mais
   contraire à la doctrine "zéro PII dans le repo public". Notes mineures sans action requise :
   `sepolia_autonomous_cycle` utilise `outgoing_pause.is_paused()` non-strict (fail-open) —
   volontaire, cohérent avec le fait qu'aucun fonds réel n'est en jeu ; `gen-sepolia-wallet.py`
   affiche la clé en clair dans le terminal — attendu, un script local one-shot documenté comme tel.
2. **Cohérence doc/code — un vrai trou trouvé et corrigé** : `test_coherence.py` ne scannait
   qu'un seul HANDOFF (`2026-07-07-nuit.md`) pour la fuite IP/email, alors que 4 nouveaux
   fichiers HANDOFF existent depuis — corrigé pour scanner tous les `docs/HANDOFF-*.md`
   automatiquement (glob, plus besoin d'y penser à chaque nouveau fichier). `docs/etat-systeme-cable.md`
   ne mentionnait ni la correction d'hallucination LLM, ni la boîte de dépôt de connaissance, ni
   `relay_chat.py` par son nom, ni le second chemin Sepolia (`sepolia_rehearsal.py`, routé via
   `wallet_guard.escalate_spend`, distinct de l'autonome) — les quatre ajoutés. Aucune
   contradiction trouvée entre `CLAUDE.md` et `etat-systeme-cable.md`.
3. **Tests/CI — un vrai trou de couverture trouvé et corrigé** : 1288 tests passent (1 échec
   connu, environnemental — `test_web_verify_rugby.py`, appel réseau live DuckDuckGo bloqué par
   le bac à sable, sans rapport avec le code). Aucun code mort, aucun TODO silencieux. Le vrai
   trou : les 9 modules livrés cette nuit (`relay_chat`, `relay_conversation`,
   `knowledge_inbox`, `sepolia_wallet`, `sepolia_autonomous`, `exam`, `btc_cycles`,
   `code_proposal`, `skill_projects`) avaient chacun leur test mais n'étaient PAS dans la liste
   curatée de `.github/workflows/ci.yml` — une régression sur l'un d'eux serait passée inaperçue
   en CI. Les 9 fichiers de test ajoutés à la CI ; commentaire d'en-tête périmé ("~17 tests en
   échec") corrigé pour refléter l'état réel (1 seul, connu, environnemental).
4. **Non traité ce soir (faible enjeu, à trancher avec l'opérateur)** : les 8 stash Git oubliés
   sur les deux clones locaux Windows (jamais triés) ; la question ouverte sur quelles infos
   précises ajouter aux fichiers de connaissance d'ARIA (posée à l'opérateur, sans réponse
   encore).

## Ce qui reste en attente
- Déploiement de `AriaLedger.sol` sur Sepolia (`contracts/DEPLOY.md`, Foundry) — bloque le
  rehearsal Sepolia au-delà de `skipped_no_ledger`. Mis de côté volontairement par l'opérateur.
- Nettoyage dexpulse/Aria Market (voir ci-dessus) — chantier séparé, pas commencé.
- Trier les stash oubliés dans les deux clones locaux Windows (8 entrées au total, dont l'ancien
  travail dexpulse/Aria Market jamais commité) — pas fait ce soir, à faire avec l'opérateur.
- Identifier si l'opérateur a des informations précises à ajouter aux fichiers de connaissance
  d'ARIA (pas à `CLAUDE.md`) — question ouverte posée en fin de session, pas encore répondue.
