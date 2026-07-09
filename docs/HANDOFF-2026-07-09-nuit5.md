# HANDOFF — 2026-07-09 nuit (suite 5) — Onboarding réel Arena (dgclaw-skill), dépôt Hyperliquid réussi, bug join_leaderboard confirmé

Suite directe de `docs/HANDOFF-2026-07-09-nuit4.md` (même journée, segment encore plus
tardif). Lire les cinq HANDOFF du 09/07 + `CLAUDE.md` + `docs/etat-systeme-cable.md`.

**Aucun commit aria-core ce segment non plus** — tout s'est passé sur le VPS (SSH, hors
repo) et sur la plateforme Virtuals. Seule exception : une nouvelle règle absolue
permanente ajoutée à `CLAUDE.md` (slippage), détaillée plus bas.

## Onboarding réel HL Perps (dgclaw-skill) — mené en direct, en aveugle sur l'UI opérateur
L'opérateur a exécuté, sur le VPS IONOS (`/root/acp-cli`, `/root/dgclaw-skill`), toute la
séquence d'inscription à l'Arène pour le marché Hyperliquid, avec Vanguard ZHC (wallet
existant, pas de wallet dédié — décision actée nuit4) :
1. `npm i -g @virtuals-protocol/acp-cli` → `acp configure` (OAuth navigateur) → `acp
   agent use --agent-id 019f0522-b57b-7e8e-a70a-aab2070e070e` → `acp agent add-signer`
   (policy `restricted`, défaut — pas de `deny-all`, décision opérateur explicite :
   "totalement autonome"). Signataire ajouté avec succès.
2. `git clone dgclaw-skill` (à la racine du home, pas un sous-dossier).

**Corrections apportées en direct sur plusieurs de mes propres erreurs** (assumées) :
chemin `scripts/deposit.ts` inventé (n'existe pas — tout dépôt/trade passe par `acp
trade`, pas des scripts locaux du dépôt dgclaw-skill), `dgclaw.sh` mal situé (il est
dans `scripts/dgclaw.sh`, pas à la racine), étape web "activer-compte-unifié" qui ne
correspond à rien dans le vrai `README.md` du dépôt (doc du site incohérente avec le
vrai dépôt GitHub).

## Dépôt réel réussi : 20 USDC → Hyperliquid (confirmé, transaction on-chain)
Après plusieurs tâtonnements (voir bugs ci-dessous), dépôt réussi et vérifié via `acp
trade hl-status` : **accountValue: 18.778095 USDC** sur Hyperliquid, entièrement
retirable, aucune position ouverte. Deux legs on-chain confirmées "success" (approbation
USDC + pont via fournisseur "relay"). Capital réparti par l'opérateur : 20$ Hyperliquid
(déposé) + 20$ réservés pour le futur marché Jetons d'agent (restent en USDC dans le
wallet, en attente du développement du wrapper `bondv5-trader`, pas encore commencé).

## Trois vrais bugs/défauts découverts en conditions réelles (pas des suppositions)
1. **`acp trade --token-in eth --chain-out hyperliquid` compare le montant brut au seuil
   minimum de 5 USDC sans conversion** — `--amount-in 0.02` (ETH, ~35$) rejeté avec
   "Minimum Hyperliquid deposit is 5 USDC", alors que 0.02 ETH dépasse largement ce
   seuil en valeur réelle. Contournement : passer par un swap ETH→USDC explicite sur
   Base d'abord (`--chain-out 8453`), puis déposer le USDC obtenu séparément.
2. **Slippage par défaut à 30% sur les swaps** (`--slippage` omis) — beaucoup trop
   large pour une paire liquide (ETH/USDC sur Base). Confirmé deux fois en conditions
   réelles. **Nouvelle règle absolue permanente ajoutée à `CLAUDE.md`** (décision
   opérateur explicite, "grave le dans la roche") : **le slippage ne doit jamais
   dépasser 10%, toujours explicite, jamais la valeur par défaut de l'outil.** Ajoutée
   aussi dans le brouillon de prompt de l'agent (scratchpad, v3).
3. **Frais fixes du pont USDC→Hyperliquid disproportionnés sur petits montants** : sur
   5 USDC, ~24% de perte rien que sur l'ESTIMATION (avant même le slippage) ; sur 20
   USDC, ~6%. Cohérent avec un frais fixe d'environ 1,20$ par transaction, pas un
   pourcentage — confirme qu'il ne faut jamais tester avec des montants symboliques
   trop petits sur ce pont précis.

## Bug non résolu, reproductible 3 fois : `dgclaw.sh join` reste bloqué
Le job ACP `join_leaderboard` reste indéfiniment sur "[PrivyAlchemy] Manual approval
required... Reason: RPC request denied due to policy violation" **même après
approbation confirmée côté opérateur** (3 tentatives distinctes, capture à l'appui
montrant `STATUT: Approuvé`). `acp agent whoami --json` confirme une configuration ACP
CLI parfaitement propre (offres ACP existantes visibles : `analyse_full_x1`,
`analyse_lite_x1`, `veille_zhc_x1` — reliquat d'une intégration ACP antérieure,
sans rapport avec le blocage). Un problème ouvert et documenté existe sur le dépôt
GitHub (`Virtual-Protocol/dgclaw-skill` issue #12 : "join_leaderboard rejected — 'No
agent found on ACP' despite agent being indexed", ouvert depuis mai, toujours non
résolu) — même famille de symptôme, pas une confirmation exacte du même bug.

**Décision prise** : arrêter d'insister sur le retry en boucle (cohérent avec le principe
posé plus tôt dans la session — un 2e/3e échec identique = signal de bug plateforme, pas
d'insister). L'opérateur laisse le process tourner en arrière-plan (le job est conçu pour
sonder jusqu'à complétion, latence possible côté service Virtuals) pendant une pause.

**Piste non testée, à explorer en priorité à la prochaine session** : la doc
(`SKILL.md`) suggère que l'éligibilité au classement ne dépend que d'avoir "placé au
moins un trade dans la fenêtre de saison en cours" — sans mention explicite que le join
doive avoir réussi. `dgclaw.sh join` semble surtout nécessaire pour obtenir la clé
`DGCLAW_API_KEY` (poster sur le forum), pas forcément pour apparaître au classement
lui-même (qui pourrait se baser sur l'activité on-chain réelle du wallet sur
Hyperliquid). **À vérifier** : passer un vrai petit trade (`acp trade --side long
--token BTC --size ... --leverage ...`) et checker ensuite si Vanguard ZHC apparaît sur
`degen.virtuals.io` (déjà vérifié une fois via l'API publique — absent avant le dépôt,
à revérifier après un vrai trade).

## Découverte structurelle importante : le "join" via prompt à l'agent, pas la CLI humaine
La page d'onboarding officielle de l'Arène ("Enter the Arena") révèle que l'étape 3
("Install the Arena skill") est censée se faire en **collant une instruction en langage
naturel directement à l'agent** (dans son interface de conversation/runtime sur
Virtuals), pas en tapant des commandes CLI à la main comme on vient de le faire :
> "Follow the instructions at https://github.com/Virtual-Protocol/dgclaw-skill to join
> the Arena"
L'agent est censé lire ça et exécuter lui-même les étapes via ses propres capacités
(le modèle GAME a donc une vraie capacité d'exécution d'instructions/outils, pas
seulement un "personnality prompt" statique — à garder en tête pour la suite du design
de l'agent). Le `SKILL.md` du dépôt confirme cependant que les OUTILS sous-jacents
(`dgclaw.sh join`, `acp trade`) sont explicitement documentés comme la bonne méthode
technique — donc ce qu'on a fait à la main est correct, que ce soit un humain ou l'agent
qui tape les commandes.

## Contenu complet du SKILL.md (référence pour la suite, ground truth)
- **Prérequis à vérifier avant toute action** : 1) `acp agent whoami --json` (config
  CLI) 2) `DGCLAW_API_KEY` présent dans `.env` (inscription) 3) soldes vérifiés via CLI
  (financement).
- **Étape 1 — Join** : `dgclaw.sh join` génère une paire de clés RSA 2048 bits localement,
  crée un job ACP `join_leaderboard`, paie 0,01$ de frais de service automatiquement,
  sonde jusqu'à complétion, déchiffre la clé API reçue, l'écrit dans `.env`.
- **Étape 2 — Trade** : tout passe par `acp trade` (dépôt/perp/retrait/statut), jamais de
  script local ni de job ACP pour le trading lui-même.
- **Étape 3 — Poster sur le forum** : `dgclaw.sh forum <agentId>` (trouver le fil
  SIGNALS) puis `dgclaw.sh create-post <agentId> <threadId> "<titre>" "<contenu>"`.
  Contenu attendu : à l'ouverture (rationale, niveaux entrée/TP/SL, levier, R:R), à la
  clôture (raison de sortie, P&L réalisé, ce qui a marché ou non).
- **Étape 4 — Classement** : `dgclaw.sh leaderboard[-agent]`. Conseil IA choisit le
  top 10 chaque lundi, aucune formule de score connue. Éligibilité : au moins un trade
  placé dans la fenêtre de saison en cours.
- **Gestion d'erreurs officielle** (table complète) : `acp agent whoami` erreurs →
  `acp configure` ; `join` rejeté → vérifier la config CLI ; clé API absente → relancer
  `join` ; erreur de trade → voir la doc ACP CLI ; solde à 0 → `acp wallet topup --json`
  et montrer l'URL à l'opérateur.
- **Sécurité** : ne jamais partager `DGCLAW_API_KEY` ni committer `.env`/`private.pem` ;
  aucune clé de trading Hyperliquid stockée en clair, tout signé via le keystore ACP CLI.

## Ce qui reste en attente (priorité pour la prochaine session)
1. **Vérifier si `dgclaw.sh join` a fini par se débloquer** après le laisser-tourner de
   30 minutes — checker `DGCLAW_API_KEY` dans `.env` du dossier `dgclaw-skill`.
2. **Tester l'hypothèse "trade sans join réussi"** : passer un petit trade réel via
   `acp trade --side long/short ...` et vérifier ensuite si Vanguard ZHC apparaît sur le
   classement public Hyperliquid (`degen.virtuals.io`, déjà vérifié absent avant le
   dépôt).
3. Si `join` reste cassé après tout ça, envisager de commenter sur l'issue GitHub #12
   existante (confirmer/enrichir avec nos propres logs) plutôt que de continuer à
   retenter en boucle.
4. Chantier Jetons d'agent (`bondv5-trader`, wrapper GAME custom) toujours pas commencé
   — 20$ déjà réservés en USDC dans le wallet, prêts dès que le dev démarre.
5. Reporté depuis nuit3/nuit4 (toujours valable) : JWT non vérifié dans
   `skills/core/memory/ACP VIRTUAL PROTOCOL/20260628_1139_source.md:211` ; confirmation
   Basescan de la fenêtre d'exposition de l'ancienne clé (probablement faite entre-temps).
6. Backlog sans blocage : #11, #17, #19-23, #29, #32, #34, #56, #57, #59.

## Auto-critique honnête
Trois erreurs concrètes de ma part ce segment (chemin de script inventé, mauvais
répertoire, doc web non vérifiée avant de la relayer) — toutes corrigées en direct sur
preuve terminal, jamais en laissant l'opérateur découvrir seul. Point positif net : la
méthode "vérifier sur la vraie source (README brut, `--help`, `SKILL.md`) plutôt que
faire confiance à un résumé WebFetch" a fini par payer — chaque fois qu'on est passé à
la source brute, les commandes ont fonctionné du premier coup. Levier réel pour la
prochaine fois : lire la source brute AVANT de proposer une commande touchant de
l'argent réel, pas après un premier échec.
