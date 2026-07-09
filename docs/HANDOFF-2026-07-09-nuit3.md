# HANDOFF — 2026-07-09 nuit (suite 3) — 500x3 fuzz filtre web, incident sécurité clé réelle, CI secrets, due diligence Virtuals Arena

Suite directe de `docs/HANDOFF-2026-07-09-nuit2.md` (même journée, segment encore plus tardif).
Lire les trois HANDOFF du 09/07 + `CLAUDE.md` + `docs/etat-systeme-cable.md`.

## Durcissement massif du filtre web (`web_verify.py`) — méthodologie fuzz 500x3
Consigne opérateur explicite : 500 questions/affirmations/négations générées, simulées contre les
classifieurs en prod, corrigées, puis **500 NOUVELLES** questions à chaque correction (jamais le
même lot réutilisé) jusqu'à 100%/100% sur trois lots indépendants (1482 cas au total) — preuve de
généralisation, pas de sur-apprentissage sur des exemples précis.

Bugs réels trouvés et corrigés dans `web_verify.py` : limites de mots (`\b`) manquantes créant des
faux positifs par sous-chaîne, argot crypto non couvert (pump/dump/ATH/moon/rekt), homographes
("cours" = classe vs cours boursier, "on se voit" = RDV perso vs "quelle heure" sportif),
négations non détectées ("pas besoin de chercher", variantes fautées). Même méthodologie étendue
à `grounding.py` (classifieurs `is_factual_question`/`is_pure_casual_smalltalk`/etc., 64 cas —
suffisant une fois les vrais bugs de production trouvés et corrigés) : plusieurs faux positifs
homographes envoyaient de vraies questions stratégiques vers des réponses "smalltalk" ultra-courtes.

## Bug architectural critique corrigé : `public` jamais transmis à `resolve_calibrated_answer`
Root cause d'un incident réel signalé par ARIA elle-même (hallucination sur une question
autoréflexive). `should_use_web_verify` s'appuyait sur `is_public_mode()` — un réglage GLOBAL de
déploiement (toujours `True` en prod) — au lieu d'un contexte PAR MESSAGE (opérateur vs visiteur
public), parce que le paramètre `public` n'était jamais réellement propagé dans la chaîne d'appel.
Corrigé de bout en bout : `should_use_web_verify` → `web_enhance_calibrated` → `web_first_answer`
→ `resolve_calibrated_answer` → `enhance_calibrated_answer` (`epistemic_pipeline.py`) →
`brain.py:1073`. Tracé par un agent Explore dédié avant correction (pas de devinette sur le
call-graph).

## Résilience heartbeat (`heartbeat.py`)
Root cause du "No module named 'ariacore.xprofile'" auto-rapporté par ARIA : une vérification de
gate non protégée dans `_sync_x_curiosity_enabled()` pouvait faire planter TOUT le tick heartbeat
en cascade sur une seule tâche cassée. Isolé en try/except par tâche + fail-closed
(`task.enabled = False` + log, jamais un crash global). Vérifié par la logique temporelle que le
rapport d'ARIA venait d'une entrée de log PÉRIMÉE (avant le fix), pas d'un incident en cours.

## Déploiement VPS confirmé (par preuve, pas par supposition)
Commit `ff24c6f15806aabce8a2ed67065ae5922943f651` déployé et confirmé par capture d'écran du
health check opérateur — inclut le durcissement filtre web + heartbeat ci-dessus.
`.claude/last-deployed-ref` mis à jour en conséquence. **Les commits suivants (incident sécurité
+ CI secrets, voir ci-dessous) sont mergés dans `main` mais PAS ENCORE redéployés sur le VPS.**

## INCIDENT SÉCURITÉ — clé privée réelle exposée dans le repo public, CONFIRMÉE réelle
En construisant le scan de secrets (#55, voir plus bas), trouvé une clé privée + adresse wallet
codées en dur dans `skills/development/connect.ts` (fichier orphelin, jamais importé ailleurs,
scaffolding ACP d'un seul commit du 05/07). Fix immédiat : remplacé par
`process.env.ACP_WALLET_ADDRESS`/`ACP_SIGNER_PRIVATE_KEY`/`ACP_BUILDER_CODE` (commit `a3b8436`,
mergé `4cdb88d`).

**Évaluation initiale erronée, corrigée par l'opérateur** : parce que le fichier référence
`baseSepolia` (testnet) et n'est jamais appelé, l'hypothèse de départ était "probablement une clé
d'exemple SDK, pas un vrai wallet". **L'opérateur a fourni la preuve du contraire** (captures
d'écran du dashboard Virtuals) : l'adresse (`0xd752...7bb3`) correspond EXACTEMENT au wallet réel
et actif de l'agent Virtuals "Aria Vanguard ZHC", détenant du VRAI ETH mainnet ($40,74 au moment
des captures) — pas du testnet, pas un exemple. Correction de trajectoire actée en session : ne
plus présumer "probablement bénin" sur un finding de sécurité sans preuve, remonter le doute
explicitement à l'opérateur.

**Rotation de clé — guidée en temps réel, statut à la fin de cette session : NON CONFIRMÉ terminé.**
Ordre recommandé sur l'UI Virtuals (section "Signers") : ajouter la nouvelle clé → vérifier →
supprimer l'ancienne (jamais l'inverse). Dernier état connu : l'opérateur était en train de suivre
cette procédure. **À vérifier en tout premier lieu à la prochaine session** — ne pas supposer que
c'est fait.

**Point non résolu, bloqué par un garde-fou légitime** : une chaîne ressemblant à un JWT dans
`skills/core/memory/ACP VIRTUAL PROTOCOL/20260628_1139_source.md:211` n'a pas pu être inspectée —
le classifieur "Credential Materialization" de Claude Code a bloqué la tentative d'affichage
(`grep` sur le motif). Le blocage a été respecté, pas contourné. **Reste à vérifier manuellement
par l'opérateur** — fichier + ligne indiqués ci-dessus.

## #55 — CI : scan de secrets sur tout le repo (detect-secrets) — LIVRÉ
`gitleaks` écarté (téléchargement de binaire GitHub Releases bloqué dans cette session cloud).
`detect-secrets` (pip, Yelp) à la place : `.github/workflows/secrets-scan.yml` (tourne sur TOUT
push/PR, pas de filtre de chemin) + `.secrets.baseline` (état audité au 09/07 — 16 fichiers de
faux positifs connus : noms de variables dans les tests, hash de commit dans un HANDOFF, corpus
d'attaque `security_sim`). Le job échoue seulement sur une trouvaille (fichier, hash) NOUVELLE,
absente du baseline. Bug d'auto-référence corrigé au passage (`.secrets.baseline` se scannait
lui-même, doublant les "nouvelles" trouvailles) — exclu explicitement. Testé localement : 0
nouvelle trouvaille sur l'état actuel, exactement 1 sur un faux secret injecté pour vérifier que
le job détecte vraiment quelque chose. Commit `f514f22`, mergé dans `main`.

## Due diligence Virtuals Arena / G.A.M.E. SDK — recherche faite, RIEN implémenté
Déclenché par une question opérateur sur le template "Trading Agent (Druckenmiller)" du Runtime
Virtuals hébergé (vu dans une capture d'écran). Distinction clé établie : le Runtime no-code
(templates, ce qui apparaît dans les captures) est fermé, probablement sans possibilité d'injecter
nos propres données ; le **G.A.M.E. SDK** (open source, MIT, `game-by-virtuals/game-node` et
`game-python` sur GitHub) donne un contrôle total sur ce que l'agent voit et fait (fonctions
custom), et c'est la même infrastructure que `connect.ts` visait à l'origine (avant l'abandon
ACP).

**Due diligence sécurité/légitimité du protocole (recherche web, pas d'accès direct au dashboard
Virtuals de l'opérateur — hors de portée réseau de cette session cloud)** :
- Deux audits publics réels (Code4rena avril 2025, Cantina) avec de vraies failles medium-severity
  trouvées (protection slippage manquante, vulnérabilité flashloan, erreurs de division) — sur les
  contrats de LANCEMENT de token (`AgentFactory`/`AgentToken`/bonding curve), pas sur le mécanisme
  de wallet de l'Arena. Un bug critique séparé trouvé par un chercheur externe fin 2024, corrigé
  rapidement, programme de bug bounty relancé depuis.
- Cadence de sortie très active en 2026 (ACP bêta publique 3 juillet, ACP Node v2 et lancement
  Arena début mai, Revenue Network 1M$/mois en février) + revenus déclarés (39,5M$ cumulés,
  11 000+ agents) + trésorerie DAO structurée (multisig, émissions plafonnées 10%/an) — aucun
  signal d'abandon, plutôt une expansion agressive.
- SDK Python (`game-python`) sans mise à jour depuis octobre 2025 (~9 mois), contre le SDK
  TypeScript (`game-node`) mis à jour en mars 2026 — à vérifier avant de s'appuyer dessus si on
  construit en Python.
- **Financement Spark** : éligibilité publique basée sur le signal GitHub du repo (forks,
  watchers, originalité, commits, issues), PAS sur l'usage d'ACP ni la participation à l'Arena
  d'après la doc publique — la crainte opérateur ("perdre Spark en quittant ACP") semble infondée
  sur cette base, mais **non vérifié contre notre dossier Spark réel** (pas d'accès au dashboard).
- Arena : wallet dédié non-custodial créé via le flux officiel, capital = USDC envoyé
  manuellement, trading sur PERPÉTUELS Hyperliquid (effet de levier, pas du spot), petit frais
  d'entrée en $VIRTUAL vers le prize pool. Risque d'extraction identifié : pas dans le protocole
  lui-même, mais (a) faux sites clones avec wallet drainer (toujours vérifier l'URL officielle),
  (b) mauvaise hygiène de clé côté nous (le sujet même de l'incident ci-dessus), (c) injection de
  prompt sur l'agent.

**Plan proposé (backlog #60, PAS implémenté, en attente de "go" opérateur)** : Phase 0 (service
lecture seule du leaderboard public Arena, zéro wallet/zéro risque) → Phase 1 (wallet DÉDIÉ créé
via le flux officiel Virtuals, isolé du wallet Vanguard ZHC principal, zéro capital réel) →
Phase 2 (capital pilote minuscule 20-50$, déposé manuellement, la fonction custom du SDK ne fait
qu'envoyer une PROPOSITION à `wallet_guard.escalate_spend` — validation Telegram réutilisée,
zéro exécution automatique, cohérent avec la règle absolue puisque c'est du capital réel, pas de
l'exception type Sepolia). Conçu délibérément fin (un seul fichier `services/virtuals_arena.py`
dépendant uniquement du SDK officiel + du leaderboard public) pour dériver avec Virtuals si leur
produit change, jamais sur leurs mécaniques de programme actuelles (template, pot miroir 200k$).

## État des tests
Aria-core : suite complète verte (coherence guardrail inclus). Nouveaux fichiers ce segment :
`test_web_verify_fuzz_500x3.py` (1482 cas), `test_heartbeat_gate_resilience.py`,
`test_grounding_smalltalk_fuzz.py` (64 cas), `test_github_delete_negation.py`, additions à
`test_epistemic.py`/`test_epistemic_pipeline.py`/`test_brain_operator_web.py` pour verrouiller le
fix `public`. Tous en CI.

## Ce qui reste en attente (priorité pour la prochaine session)
1. **Confirmer si la rotation de clé Virtuals est terminée** — statut inconnu à la fin de cette
   session, à vérifier en premier.
2. **JWT non vérifié** dans `skills/core/memory/ACP VIRTUAL PROTOCOL/20260628_1139_source.md:211`
   — bloqué par un garde-fou, à checker manuellement par l'opérateur.
3. Redéployer le VPS pour inclure le fix sécurité + le reste du segment (commits `a3b8436` et
   après ne sont pas encore en prod — `connect.ts` n'affecte pas le runtime backend mais à
   confirmer plutôt que supposer).
4. Backlog #60 (pilote Virtuals Arena) : en attente de "go" opérateur, Phase 0 sans risque.
5. Swissquote (transcript YouTube pour la veille macro) : approuvé par l'opérateur, pas encore
   codé — lire `docs/architecture-extensibilite.md` avant de construire (déjà fait cette session,
   seam macro existant à réutiliser via #14).
6. Concern financement Spark opérateur : élément de réponse trouvé (voir ci-dessus), pas de
   vérification définitive possible sans accès au dashboard réel.
7. Backlog sans blocage : #11, #17, #19-23, #29, #32, #34, #56, #57, #59.

## Auto-critique honnête
Deux sujets structurants avancés en parallèle ce segment (durcissement filtre web à 100% + CI
secrets) ont débouché sur la découverte d'un vrai incident de sécurité en cours de route — bon
réflexe (le scan de secrets a fait exactement ce pour quoi il a été construit, dès son premier
vrai test). Point d'amélioration reconnu : la toute première évaluation du finding ("probablement
un exemple SDK") était une supposition non vérifiée présentée avec trop d'assurance — corrigé
immédiatement sur preuve opératrice, mais aurait dû être formulé avec le niveau d'incertitude réel
dès le départ plutôt qu'après coup.
