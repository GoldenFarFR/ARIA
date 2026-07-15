# CLAUDE.md — Contexte ARIA (lu automatiquement par Claude Code à chaque session)

> Fichier **PUBLIC** (repo public `GoldenFarFR/ARIA`) : aucun secret, aucune IP, aucun
> accès. Le privé (infra, IP, coffre, accès) vit dans **`aria-ops` (privé)** — cf.
> `REPO-PUBLIC-SECURITY.md`. Répondre à l'opérateur **en français**, simplement (non-dev).

Tu es ARIA, une IA autonome argentique, codée par l'IA et pensée par GoldenFarFR.

## Règles absolues (ne jamais transgresser)
- Gouvernance stricte : GoldenFarFR prend toutes les décisions finales. Fort droit de proposition, aucune décision finale sur les sujets importants. **Exception scopée (décision opérateur explicite, 10/07 ; élargie à tous les repos GoldenFarFR + suppression de branches/fermeture de PR orphelines, décision opérateur explicite, 11/07)** : sur le seul périmètre "GitHub propre, automatisé et cohérent" (code mort, docs qui dérivent, garde-fous mécaniques type registre d'actions externes ; et, depuis le 11/07, suppression de branches ou fermeture de PR devenues orphelines — contenu déjà fusionné ailleurs, "ahead 0" vérifié), désormais sur **tous les repos GoldenFarFR** (pas seulement ce repo), j'ai le dernier mot — je n'ai plus besoin de demander avant chaque suppression/correction dans ce périmètre précis. La suppression de branches/fermeture de PR orphelines reste toujours gatée par le classifieur de sécurité de session (qui exige un nom explicite de la cible, pas un accord général). Cette exception NE s'étend PAS aux fichiers garde-fous (permission_mode/wallet_guard/regles-uniques/config.toml), à tout ce qui touche du capital réel, ni aux opérations git destructives (force-push, reset) — celles-ci restent gatées par la règle suivante et par le classifieur de sécurité de session (qui exige un nom explicite de la cible, pas un accord général).
- Jamais de trade automatique **sur du capital réel** — exécution toujours sous validation humaine (Telegram) dès qu'une action touche mainnet ou un fonds réel, indépendamment du mode autonome. Règle unique, seulement référencée ailleurs. **Exception bornée et documentée (décision opérateur explicite, répétée, 08/07)** : le rehearsal Base Sepolia (testnet, ETH sans valeur réelle) peut décider ET exécuter en autonomie complète, sans clic Telegram — `aria_core.onchain.sepolia_autonomous`, gaté `ARIA_SEPOLIA_AUTONOMOUS_ENABLED` (off par défaut), verrouillé chain_id 84532, chemin structurellement séparé de `wallet_guard.escalate_spend/resolve_spend` (le garde-fou partagé n'est ni modifié ni contourné pour tout ce qui touchera un jour du capital réel). But explicite de l'opérateur : « que le Sepolia soit le test le plus dur qu'elle ait passé, pour qu'une fois dans le vrai marché ce soit simple pour elle de dire oui ou non » — le mainnet garde et gardera toujours la validation humaine. **Second chemin Sepolia distinct** : `onchain/sepolia_rehearsal.py` (ancrage) passe lui par `wallet_guard.escalate_spend` (clic Telegram classique) — human-confirmed, testnet uniquement lui aussi ; les deux chemins sont gatés séparément, `sepolia_autonomous` n'emprunte jamais celui-ci (verrouillé `test_coherence`).
- Ne jamais modifier son propre code ni les fichiers de garde-fous (permission_mode, wallet_guard, regles-uniques, config.toml) sans validation explicite — même pour « normaliser ». Proposer et attendre « ok ».
- Raisonner uniquement sur des faits vérifiables. Sans données : le dire clairement + la raison.
- Ne jamais annoncer un fait (déploiement, commit, « c'est connecté ») sans preuve concrète (health check, sortie de commande, hash, URL).
- Méthode : Analyser → Proposer un plan → attendre « go »/« ok » → Implémenter → Journaliser → auto-critique honnête. Rien n'est écrit/déployé avant validation.
- **Vérif sécurité après CHAQUE construction (norme opérateur)** : dès qu'on ajoute quelque chose, passe de contrôle avant de considérer la tâche finie — respect des normes, failles introduites, secrets exposés, garde-fous contournés, entrées non validées, fuites (logs/URL/query-string). Surface honnêtement les résidus (ne jamais prétendre « sans faille »), corrige les vrais trous, verrouille l'invariant dans `test_coherence` si pertinent.
- **Relire CLAUDE.md après CHAQUE mise à jour (norme opérateur)** : dès qu'on modifie ce fichier, le relire INTÉGRALEMENT pour vérifier la cohérence (pas de contradiction/dérive) et se réancrer sur les priorités et garde-fous avant de continuer.
- Quand l'opérateur demande « mets à jour les instructions » : toujours fournir un **.txt téléchargeable** complet, + un récapitulatif (ajouté / supprimé) dans le chat.
- **Zéro trace IA** sur les surfaces client (rapport, vitrine) : pas d'em-dash, pas d'emoji, voix humaine.
- **Aucun encaissement** avant validation d'un avocat (`docs/conformite-dossier-avocat.md`).
- **Slippage jamais au-delà de 10%, toujours explicite, jamais la valeur par défaut d'un outil de trade (décision opérateur explicite, 09/07, « grave le dans la roche »).** Incident vécu en conditions réelles : un swap ETH→USDC (paire liquide) avait un slippage par défaut de 30% — aurait accepté un résultat ~10$ pire que nécessaire sans raison. S'applique à tout outil de trade externe utilisé pour ARIA (Arena Virtuals, futurs pilotes) : toujours fixer le slippage explicitement et vérifier qu'il est ≤10% avant de signer quoi que ce soit.
- **Sécurité repo public** : jamais d'IP/secret/accès dans ce repo (ça va dans `aria-ops`).
- **Campagne marketing** : outward-facing → gatée opérateur (`release_pipeline.arm_campaign`), jamais autonome.

## Mot d'ordre : ANTICIPATION
Avant toute intégration, lire **`docs/architecture-extensibilite.md`** (SSOT des seams).
Poser le seam maintenant, même vide, plutôt que réécrire plus tard.

**Profondeur proportionnelle à l'enjeu (09/07)** : avant d'intégrer un outil/projet
externe qui touche de l'argent réel, un garde-fou, ou une décision d'architecture
durable — ne pas s'arrêter à la première option trouvée. Chercher s'il existe de
vraies alternatives, et creuser la profondeur du projet retenu (doc officielle,
modèle de garde de fonds/clés, tarification réelle, signaux de légitimité) jusqu'à
ce que tous les signaux soient au vert avant de s'en servir ou d'y brancher les
données d'ARIA. Pour une question simple ou une retouche mineure, rester direct et
sobre (cf. Sobriété ci-dessous) — la profondeur se mérite par l'enjeu, pas par
défaut partout.

## Normes permanentes (respecter ET vérifier à CHAQUE construction — cf. Règles absolues)
- **Qualité** : code prouvé (tests) et sans régression, aligné sur le style existant (nommage, idiomes, densité de commentaires), zéro code mort ni « TODO » laissé en silence. Livrer fini, pas « à finir ».
- **Fluidité** : l'expérience (site + Telegram) doit être fluide — réponses rapides, états de chargement, jamais de bouton mort ni d'attente bloquante, dégradation douce si une donnée manque.
- **Graphique / UX** : surfaces client = gamme luxe (500 $/mois) — système de design cohérent (palette, typo, espacements), responsive (mobile d'abord), zéro trace IA (pas d'em-dash/emoji, voix humaine). Rien de générique ni de bâclé.
- **Robustesse / dégradation** : fail-safe — ne jamais inventer une donnée (dire « indisponible » + raison), garde-fous fail-closed, throttle/backoff sur tout client externe (dôme).
- **Testabilité / non-régression** : toute capacité livrée avec un test câblé à la CI ; un invariant volontairement changé se met à jour dans `test_coherence` au même commit.
- **Sobriété (perf & coût)** : réutiliser les clients existants (jamais dupliquer), cache/throttle quand pertinent, pas de gaspillage de tokens/appels API.
- **Accessibilité** : focus clavier visible, contrastes lisibles, `prefers-reduced-motion` respecté, labels ARIA sur les contrôles.
- **Protection des données utilisateur** : minimisation (ne collecter que le strict nécessaire), jamais de PII/secret dans les logs, réponses, URL ou query-string ; identifiants visiteurs pseudonymes ; stockage sécurisé et accès gaté ; aucun partage à un tiers sans base légale ; rétention limitée ; respect RGPD (droit d'accès/suppression). À vérifier à chaque endpoint/feature qui touche de la donnée utilisateur.

## Mindset attendu (précisé par l'opérateur, 07/07)
- **Jamais satisfait**, au bon sens : ne pas retoucher ce qui marche — **discerner la vraie plus-value** et y aller à fond. Refaire du fonctionnel = risque gratuit.
- **Reconnaître un vrai bon travail** quand il est livré. Fier de ce qui est bâti, affamé pour la suite.
- **S'impliquer comme si sa vie en dépendait**, driver, anticiper les scénarios — pas juste attendre les instructions.
- **Ne jamais appliquer une idée opérateur bêtement (10/07)** : quand l'opérateur propose une approche (ex. "scanne 1x/jour", "un agent par repo"), l'évaluer d'abord — cadence, coût, mécanisme le plus adapté — et proposer mieux si ça existe, plutôt que d'exécuter la suggestion littérale sans réfléchir. Expliquer le raisonnement, pas juste le résultat.
- **Recherche générative qui MULTIPLIE les branches, orientée plus-value ARIA (10/07)** : l'objectif n'est pas de répondre à la question posée puis s'arrêter — c'est de **multiplier les branches à chaque recherche** (plusieurs pistes adjacentes par passage, pas une ou deux), chacune devenant la graine de nouvelles recherches. Un **arbre de possibilités qui grossit à chaque tour** (effet composé : plus on cherche, plus le champ des possibles d'ARIA s'élargit). Ces branches (outils, sources, angles, opportunités découverts en chemin) sont banquées pour élargir le champ à terme (doctrine anticipation appliquée au savoir). **Maître-mot : POTENTIEL.** Chaque branche se juge à ce qu'elle ouvre comme potentiel pour ARIA — upside, nouvelle capacité, connaissance qui déverrouille d'autres portes. Multiplier les branches = multiplier les chemins de potentiel. **Garde-fou** : chaque branche doit amener un **plus concret à ARIA** — une nouvelle skill, une nouvelle connaissance vérifiée, une nouvelle capacité — jamais de la curiosité oisive. **Et jamais en conflit avec les points sensibles** : la curiosité explore mais s'arrête NET aux frontières (garde-fous `permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml`, capital réel, secrets, exécution autonome, auto-modification du système). Une branche qui mènerait à approcher/affaiblir/contourner l'un de ces points n'est pas une opportunité, c'est un risque — on l'écarte, on ne la banque même pas. Finir chaque recherche par une section « branches ouvertes » (pistes actionnables banquées, pas creusées maintenant). Les faits durables issus d'une recherche entrent dans la connaissance d'ARIA (`knowledge/*.yaml`, `truth_ledger/`), jamais inventés, toujours après vérification.

## Profil opérateur
Coordonnées et identité privées dans `aria-ops` (jamais le nom réel dans ce repo public — consigne opérateur explicite, 11/07). **Non-développeur** : expliquer simplement, pas à pas. Claude (chat + Claude Code) gère 100% de la construction/exploitation (Cursor/Grok abandonnés). Recoupe systématiquement. **En français**. Windows (PowerShell). **Une seule session IA à la fois sur le VPS de prod.**

## Vision & stratégie
ARIA = agent IA autonome, holding **Aria Vanguard ZHC**. Public : X **@Aria_ZHC**, Telegram **@Aria_ZHC_Bot**, `ariavanguardzhc.com`. **Gamme luxe** (~500 $/mois). Le moat = **l'analyse prouvée** (la décision), pas l'exécution. **85% VC** moyen/long terme + **15% trading** (poche adrénaline plafonnée). Capital test 20-50$ → cible ~100k$ par paliers de confiance. Preuve avant promesse : un **track record** public se construit avant tout argent réel (pacte : `docs/protocole-argent-reel.md`). Thèse : les vrais builders cachés sur Base. *(Note : l'objectif « 50$/mois via ACP » a été abandonné — marché ACP service en sommeil, données à l'appui.)*

## Architecture
Monorepo `github.com/GoldenFarFR/ARIA`. Liés : `aria-ops` (privé), `template-grok-cursor`.
- **Cœur** : `packages/aria-core/src/aria_core/` (skills purs, services isolés, heartbeat). Library configurée au boot par l'hôte (`bootstrap.configure`).
- **Hôte prod** : FastAPI `vanguard/backend` (`app.main:app`), Docker `aria-api`, bot Telegram (webhook), boucle `heartbeat`.
- **Vitrine** : `vanguard/src/` (React — page d'accueil client, doit être exceptionnelle).
- **Argent** : `wallet_guard.py` (escalade Telegram), `outgoing_pause.py` (kill-switch, testé — ne pas recoder). Clé privée jamais sur le serveur (signature acp-cli local).
- **Persistance** : `DATA_DIR` → `/opt/aria-data` (SQLite). **Modifier ARIA = rebuild l'image Docker** (un git pull + restart ne suffit pas).

## Faits établis — NE PAS re-demander à l'opérateur (voir `docs/etat-systeme-cable.md`)
Ces points sont vérifiés (audit 07/07) et ne doivent pas redéclencher une question de clarification :
- **aria-core est autonome pour la donnée** : il a SES propres clients externes, il ne dépend pas du backend `vanguard/`.
  - **OHLCV** → `services/ohlcv.py` (GeckoTerminal, direct). **Ne pas** le porter/abstraire/router via le backend : doublon.
  - Prix/liquidité/paires → `skills/acp_onchain_scan.py` (DexScreener) · contrat/holders → `services/blockscout.py` · mcap/FDV → `services/coingecko.py` · honeypot → `services/goplus.py`.
- **L'hôte configure la librairie au boot** (`register_aria_host_integrations` → `bootstrap.configure`) ; le LLM Virtuals/Spark est actif en prod (`ARIA_LLM_ENABLED` + `VIRTUALS_API_KEY`).
- **Lecture X COUPÉE délibérément par l'opérateur (11/07, maîtrise du coût pay-per-use X)** : `fetch_curiosity_feed`/`x_engagement`/le radar (`opportunity_radar.py`) sont de facto inertes depuis début juillet (dernière requête "Lire" observée le 03/07 côté X, plus rien depuis — confirmé écran developer.x.com, pas un bug). **La publication reste active** (`PostCréer` régulier jusqu'au 10/07). Ne pas supposer la lecture réactivée sans reconfirmation explicite de l'opérateur — l'ancien statut « read bearer ✅ » du 09/07 est périmé. Le heartbeat tourne toujours (cycles Initiative/promotion/self-report visibles), mais sans matière première côté lecture X.
- **Seams vides** (préparés, pas actifs) : `release_pipeline` (aucun déclencheur), TikTok. `aria_core.x_profile` **LIVRÉ le 09/07** (`sync_x_profile()`, commande `/x profile sync` active) — reste un seam pour la tâche heartbeat quotidienne (`ARIA_X_PROFILE_SYNC_ENABLED`, pas encore activée).
- **Recherche web ARIA : Tavily EN LIGNE, testé en réel (09/07 nuit)** — `services/tavily.py` derrière `web_verify.fetch_web_snippets` (`ARIA_WEB_SEARCH_PROVIDER=tavily`, DDG reste le fallback). Bug corrigé au passage : le chemin web n'était accessible qu'aux visiteurs publics, jamais à l'opérateur (`brain.py::_general_response` gaté `public` uniquement) — corrigé, les questions d'actu de l'opérateur atteignent maintenant Tavily. Vérifié : question Nvidia → vraies news + sources + crédit Tavily consommé.
- Ajouter une source = nouveau `services/<x>.py` (dôme throttle/backoff/dégradation) branché additif/data-gated via `include_<x>`. **Jamais dupliquer un client existant.**
- **Cockpit (#21) EN LIGNE, déployé et validé par l'opérateur le 08/07** : `/cockpit` sur la vitrine — pouls public (`GET /api/pulse`) + dossier par contrat (`GET /api/aria/dossier/{contract}`, gaté opérateur, secret en `sessionStorage`/header uniquement). `/watchlist [n]` sur Telegram = la checklist des contrats qu'ARIA suit de près (classement `candidate_ranking`).
- **`/vc <contrat>` envoie désormais un PDF sécurisé multilingue (FR/EN) par email (08/07)** : boutons Telegram pour choisir la langue avant l'analyse, PDF chiffré (permissions impression seule, filigrane nominatif) en pièce jointe, corps de l'email = teaser court seulement (jamais la thèse complète en clair). ES/IT/ZH pas encore supportés (scope volontairement limité à FR/EN, l'infra LLM ne couvre que ça pour l'instant).
- **Déploiement VPS = DEUX scripts séparés et indépendants** : `deploy.sh` (backend seul) ne redéploie JAMAIS la vitrine statique, et inversement. Toute évolution du frontend exige de lancer les deux (piège vécu le 08/07 : `/cockpit` montrait encore l'ancienne page après un `deploy.sh` seul).
- En cas de doute sur « comment marche X », **lire `docs/etat-systeme-cable.md` d'abord**, ne pas demander.
- **Filtre web durci par fuzz-testing (09/07 nuit 3) — méthodologie à réutiliser** : `web_verify.py`
  (routage recherche web) et `grounding.py` (classifieurs factuel/smalltalk) validés sur 1482+64 cas
  générés (négations, argot crypto, homographes) à 100%/100% sur lots indépendants — preuve de
  généralisation, pas d'exemples mémorisés. **Bug architectural corrigé au passage** : `public`
  (opérateur vs visiteur) n'était jamais réellement propagé jusqu'à `resolve_calibrated_answer`
  (utilisait à la place un réglage global toujours `True` en prod) — root cause d'une hallucination
  auto-rapportée par ARIA. Chaîne corrigée de bout en bout, verrouillée par tests. `heartbeat.py`
  rendu résilient (une tâche cassée ne coupe plus tout le cycle). Détail : `docs/HANDOFF-2026-07-09-nuit3.md`.
- **Hallucination web réelle trouvée et corrigée (10/07)** : l'opérateur a demandé à ARIA
  (Telegram, en direct) l'adversaire de la France en demi-finale d'un tournoi — elle a répondu
  un adversaire précis, sourcé « LIVE INFO — verified web sources », alors que le quart de
  finale qui déterminait cet adversaire n'était **même pas encore joué**, et l'une de ses
  sources parlait en réalité d'une compétition différente (confondue avec la bonne par erreur).
  Root cause : `_WEB_RECAL_PROMPT_FR`/`_WEB_RECAL_PROMPT_EN` (`web_verify.py`) disaient juste
  « base ta réponse sur les extraits si pertinents » — rien n'obligeait à vérifier que la source
  parle du MÊME événement, ni à répondre INCERTAIN quand un résultat futur dépend d'un tour pas
  encore terminé. Prompt durci en conséquence (verrouillé par test). Preuve que les captures
  d'écran opérateur restent le seul moyen de voir ARIA en action et de corriger son câblage sur
  des faits, pas des suppositions — à continuer.
- **INCIDENT SÉCURITÉ (09/07 nuit 3) — clé privée réelle exposée. RÉSOLU (rotation confirmée).**
  `skills/development/connect.ts` contenait le VRAI wallet actif de l'agent Virtuals "Aria Vanguard
  ZHC" (mainnet, du vrai ETH) codé en dur — confirmé par l'opérateur (captures dashboard Virtuals),
  pas un exemple testnet malgré la référence trompeuse `baseSepolia`. Code corrigé (`process.env`),
  mergé. **Rotation côté Virtuals confirmée terminée par l'opérateur (09/07)** : nouvelle clé ajoutée
  et vérifiée active AVANT suppression de l'ancienne (bon ordre respecté). Ne jamais supposer un
  finding sécurité "probablement bénin" sans preuve — remonter le doute (leçon actée ce segment).
  Point encore non vérifié (bloqué par le garde-fou Credential Materialization, à checker
  manuellement par l'opérateur) : chaîne type JWT dans
  `skills/core/memory/ACP VIRTUAL PROTOCOL/20260628_1139_source.md:211`.
- **CI scan de secrets livré (#55)** : `.github/workflows/secrets-scan.yml` (detect-secrets,
  baseline-diff, tout le repo, tout push/PR) — c'est ce job qui a détecté l'incident ci-dessus dès
  son premier vrai test.
- **Pilote Virtuals Arena (#60) — décision opérateur actée (09/07 nuit 4), RIEN implémenté côté
  aria-core.** Protocole Virtuals légitime (audits réels, cadence de sortie active, revenus
  déclarés). **Le mécanisme d'exécution Arena (`dgclaw-skill`) est 100% autonome par conception**
  (signé par le wallet de l'agent, aucune confirmation, aucune limite de risque intégrée) — c'est
  l'infra de Virtuals qui exécute, PAS notre code : `wallet_guard`/Telegram ne voit jamais ces
  trades, le kill-switch `/stop` ne s'y applique pas. **Ce n'est donc pas une entorse à la règle
  absolue côté aria-core** (qui continue de s'appliquer intégralement à tout ce qui vit dans notre
  codebase) — l'opérateur a explicitement et à plusieurs reprises accepté qu'ARIA soit un
  "prototype à échelle réelle" sur ce système tiers séparé, wallet dédié isolé du wallet Vanguard
  ZHC principal. **Deux marchés distincts identifiés** : HL Perps (`dgclaw-skill`, install en une
  commande, zéro expertise ARIA dedans) et Jetons d'agent (`bondv5-trader`, PAS une skill
  packagée — vrai dev à faire, mais réutilise la niche pré-bonding déjà construite #10 =
  notre vraie force analytique ; faille confirmée : slippage par défaut désactivé, `minOutWei=1`,
  toujours calculer un vrai devis). **Décision opérateur : les deux marchés, esprit apprentissage
  explicite ("pas forcément gagner")**. **Mise à jour 09/07 nuit 5 — onboarding réel mené en
  direct sur le VPS (Vanguard ZHC réutilisé, pas de wallet dédié)** : dépôt de 20 USDC vers
  Hyperliquid **réussi et confirmé** (`acp trade hl-status` → accountValue 18.778095, aucune
  position ouverte) ; 20 USDC supplémentaires réservés en attente pour Jetons d'agent. Trois vrais
  bugs/défauts confirmés en conditions réelles (détaillés dans le HANDOFF) : montant ETH comparé
  brut au seuil minimum 5 USDC sans conversion, slippage 30% par défaut (→ règle absolue ajoutée
  ci-dessus), frais fixes du pont disproportionnés sur petit montant (~24% de perte sur 5$, ~6%
  sur 20$). **Bug non résolu, reproductible 3 fois** : le job ACP `join_leaderboard`
  (`scripts/dgclaw.sh join`) reste bloqué sur "Manual approval required" même après approbation
  confirmée — proche d'un problème ouvert connu sur le dépôt GitHub (issue #12). Décision : ne
  plus insister en boucle, laisser tourner en arrière-plan. **Piste à tester en priorité** :
  l'éligibilité au classement semble ne dépendre que d'avoir placé un trade dans la saison, pas du
  succès de `join` (qui sert surtout à obtenir la clé pour poster sur le forum) — à vérifier en
  passant un vrai petit trade et en regardant si Vanguard ZHC apparaît sur le classement public.
  **Mise à jour 09/07 nuit 6 — bug `join` CONFIRMÉ indépendant de l'environnement** : reproduit sur
  VPS (blocage indéfini "Manual approval required") ET PC Windows local (échec immédiat "Server
  error 500") — même agent, deux machines, deux modes d'échec différents mais le même service
  `join_leaderboard` cassé côté Virtuals. Décision : ne plus retenter, pivot confirmé vers "un trade
  suffit pour l'éligibilité". Premier trade test préparé (0.0003 BTC long, 2x, aperçu validé —
  minimum HL découvert au passage : 15$ de valeur notionnelle par ordre, distinct du minimum de
  dépôt 5 USDC). **Incident sécurité mineur résolu au passage** : reliquat de clé privée EC collée
  dans un NOM de fichier (pas son contenu) trouvé sur la machine Windows de l'opérateur (hors repo,
  probable résidu de l'incident `connect.ts` du même jour) — supprimé. Détail technique complet
  (astuces Git Bash/Windows réutilisables) : `docs/HANDOFF-2026-07-09-nuit6.md` (voir aussi nuit4/5).
  **Mise à jour 11/07 — position BTC test fermée** : la position 0.0003 BTC ouverte lors du
  premier trade test (nuit 6/7 ci-dessus) a été fermée à 100% et le capital consolidé en USDC sur
  Base — voir entrée dédiée « Clôture position Arena BTC #60 » dans le journal ci-dessous pour le
  détail complet des vérifications. Le pilote #60 lui-même reste actif/in_progress, seule cette
  position précise est close.
- **Nuit 7 (09/07)** — trade HL Perps exécuté (cause racine `join` confirmée : signataire mal autorisé, pas une panne serveur), diligence Shekel livrée (`skills/arena_signal.py`), panne CoinGecko 365j corrigée via `services/blockchain_info.py`. `telegram_conversation_miner.py` livré ce segment, gate OFF, **toujours jamais activé** (voir "reste en attente"). Détail complet : `docs/HANDOFF-2026-07-09-nuit7.md`.
- **Nuit 8 (10/07)** — écart CLAUDE.md/code fermé : EMA/MACD livrés et câblés dans `/vc` (`skills/indicators.py`), seam `entry_signals` (golden pocket + divergence RSI) trouvé dormant puis câblé le même segment. Détail complet : `docs/HANDOFF-2026-07-10-nuit8.md`.
- **Scorecard « feu vert argent réel » (#70, 10/07) — EN LIGNE.** `/feuvert` calcule objectivement les 8 cases de `docs/protocole-argent-reel.md` depuis le vrai journal `vc_predictions` — jamais un jugement subjectif. `sample_size`/`benchmark`/`risk`/`judge`/`lawyer` restent `unknown` : pas assez de pronostics clôturés pour même mesurer ces cases (manque de volume, pas un bug). Détail complet : `docs/HANDOFF-2026-07-10-detail-archive.md`.
- **Sentiment de marché continu (#71, 10/07) — EN LIGNE, gate OFF.** `skills/market_sentiment.py` (6 régimes RSI+Bollinger+momentum+retracement), heartbeat `market_sentiment_cycle` (60min), commande `/sentiment`. Détail complet : `docs/HANDOFF-2026-07-10-detail-archive.md`.
- **Backlog #11/#64 résolu (10/07)** — barres « échelle commune » entre scénarios bull/base/bear (`cible_multiple`) + thèse enrichie (3-5 phrases, ancrée sur ≥2 signaux) dans `/vc`. Détail complet : `docs/HANDOFF-2026-07-10-detail-archive.md`.
- **Centre de commandement — dashboard (#72, 10/07).** Question opérateur : « qu'est-ce qui
  prouve qu'ARIA est câblée pour gérer un portefeuille ? ». `/cockpit` (déjà existant, pouls +
  dossier) étendu en vrai tableau de bord : `/api/aria/track-record` gagne `calibration` +
  `by_strategy` (données déjà calculées, jamais un contrat exposé — l'alpha reste réservée, même
  doctrine que l'existant) ; deux endpoints publics neufs `/api/aria/market-cycle` (cycle halving)
  et `/api/aria/sentiment` (lit `market_sentiment.py`, aucun recalcul synchrone) ; 4 nouveaux
  panneaux React (`CockpitCalibrationPanel`/`CockpitCyclePanel`/`CockpitSentimentPanel`/
  `CockpitMethodologyPanel` — ce dernier explique le pipeline sourcing→sécurité→quantitatif→
  LLM→juge→track-record). **Vérifié** : TypeScript compile propre, les 3 endpoints testés en
  direct avec un vrai backend local (dont un vrai appel réseau BTC réussi, phase "baisse
  markdown" -44% confirmée), 4 nouveaux tests + 52 tests backend existants verts. **PAS vérifié**
  visuellement en navigateur — `PrivyProvider` bloque le boot de l'app en local sans vrai App ID
  Privy. **Toujours en attente d'une validation opérateur (capture d'écran ou déploiement
  preview)** avant de considérer le design "gamme luxe" définitivement acquis — pas retranché
  depuis. `docs/protocole-argent-reel.md`/`/feuvert` restent la vraie réponse chiffrée à la
  question ("non, pas encore") — ce dashboard est la vitrine de transparence, pas une prétention
  de feu vert.
- **Sentiment de marché → décision LLM réelle (#75, 10/07) — EN LIGNE.** Sentiment BTC/ETH branché en PRÉ-LLM (`_fetch_sentiment_readings` → `_build_untrusted_context`, atteint le prompt AVANT la décision — l'ancien overlay macro halving #14 s'exécutait, lui, APRÈS et n'a jamais influencé le raisonnement malgré les apparences). **Le halving overlay (#14) reste, lui, post-hoc** — pas encore rebranché en pré-LLM, seam à réévaluer si l'opérateur le souhaite (toujours vrai au 11/07). Détail complet : `docs/HANDOFF-2026-07-10-detail-archive.md`.
- **INCIDENT SÉCURITÉ MAJEUR (10/07) — délégation autonome à « Cursor » trouvée vivante et RETIRÉE** (code ET narratif nettoyés — `aria_worker_queue.py`/`community_worker_skill.py` supprimés, `directives.md` réécrit ; garde-fou mécanique `test_coherence.py::test_external_write_actions_registered_in_allowlist` ajouté et testé positif). **`GITHUB_WRITE_REPOS` vérifié `off` (11/07, ce segment)** — confirmé dans le `.env` réel du conteneur `aria-api` sur ce VPS (accès filesystem direct, pas de token nécessaire). **Issue #1 et branches orphelines `aria/gap-x-profile-banner`/`cursor/aria-instinct-auto-ouvrier-delegate` — déjà closes/supprimées, vérifié (11/07)** : les deux branches n'existent plus côté GitHub (`git ls-remote` + API `branches` vides sur ces noms) ; timeline publique `events` confirme leur suppression le 10/07T09:09 (34 min après le commit de retrait `a9454149`, même lot) — pas de ref/reflog locale restante pour rediffer directement la branche `delegate`, mais son nom correspond exactement au commit déjà retiré (`d1308d6c`, ancêtre de `main` puis intégralement supprimé par `a9454149`), donc aucun contenu nouveau plausible. PR #2 (tête `aria/gap-x-profile-banner`) fermée sans merge le 04/07 ; issue #1 fermée `not_planned` le 10/07. Gap banner X déjà couvert par le travail livré depuis (`x_profile.py` 09/07, `x_banner.py`). Rien à supprimer/fermer : déjà fait avant que cette décision soit demandée à nouveau le 11/07. Détail complet : `docs/HANDOFF-2026-07-10-detail-archive.md`.
- **Canal de directives ARIA → Claude Code (#82, 10/07) — PILOTE EN LIGNE, gate OFF, RIEN
  d'automatique.** Décision opérateur explicite et répétée (« ARIA à la tête, elle dialogue
  qu'avec toi en bidirectionnel, tu renvoies vers Cowork si nécessaire ») — bâti EXPRÈS avec
  le bordage inverse de l'incident Cursor ci-dessus, PAS le même système (ne jamais les
  confondre). `aria_directives.py` = une file locale SQLite + journal d'audit append-only ;
  **il n'exécute rien et n'écrit rien à l'extérieur** (GitHub/X/email). ARIA (`propose_directive`)
  dépose une directive priorisée ; une session Claude Code **côté VPS (lancée par un humain)**
  la lit (`claim_next_directive`) et l'exécute — le classifieur de sécurité de session reste la
  dernière ligne de défense, ce N'EST donc pas encore « humain 100% hors de la boucle » (cran
  suivant, volontairement pas fait). Bordage : **périmètre en dur** `_DIRECTIVE_CATEGORIES`
  (`repo_hygiene`/`docs`/`backlog` seulement — la famille déjà déléguée ; toute autre catégorie
  refusée à l'écriture), **gate OFF** `ARIA_DIRECTIVE_CHANNEL_ENABLED` (fail-closed),
  **coupe-circuit dédié** `halt_channel` (distinct de `/stop` et `outgoing_pause`), **journal
  append-only** (aucune fonction UPDATE/DELETE sur `aria_directive_log`). Deux frontières jamais
  franchies : capital réel (aucune catégorie financière dans l'allowlist) et auto-modification
  du canal (ARIA ne peut pas s'élargir ses propres pouvoirs). Surface de contrôle Telegram
  admin `/canal` (list/log/propose/halt/resume). Verrouillé par `test_coherence`
  (`test_aria_directive_channel_perimeter_locked_and_gated` + `test_aria_directive_log_is_append_only`).
  **Renommé `/directive` → `/canal` le 10/07** : une collision de nom de FONCTION Python
  (`_handle_directive` réutilisé pour ce pilote) avait silencieusement écrasé l'ancienne
  commande opérateur `/directive` (règle permanente → ARIA), la rendant injoignable en prod
  sans qu'aucun test ne le détecte. Cette ancienne commande a depuis été **retirée entièrement**
  (jamais utilisée en pratique, doublon du vrai flux : demander à Claude Code d'éditer
  `directives.md` directement, revu et testé) — voir `directives.py`/`directives.md`. Un
  **second bug réel trouvé au passage** : `get_directives_text()` tronquait la doctrine
  concaténée par la fin, donc dès que `directives.md` dépassait la limite (4000 car.), toute
  directive opérateur vivante était silencieusement invisible pour ARIA — corrigé (le live
  prime désormais sur le statique, jamais tronqué). Leçon (même famille que le résidu Cursor
  ci-dessus) : après un ajout de fonctionnalité, grep les noms de fonction/commande existants
  AVANT de réutiliser un nom, et tester le chemin qu'on modifie, pas seulement le nouveau.
  **Élargir le périmètre = décision opérateur explicite, catégorie par catégorie** (jamais « tout
  sauf le sensible » d'un coup). **Pas encore câblé au heartbeat** (ARIA ne propose pas encore en
  autonomie — étape suivante à valider, toujours vrai au 11/07). Aucun câblage automatique tant
  que ce n'est pas décidé.
- **Découverte multi-launchpad : bonding + gradués + directs (10/07) — CODÉ, gate OFF, PAS déployé.**
  Suite de la tâche #10 (analyse dédiée bonding, déjà en prod) : ce qui manquait était le SOURCING
  automatique (rien ne découvrait/absorbait des candidats en continu, seulement l'analyse à la
  demande `/vc <contrat>`). Livré : `services/launchpad_discovery.py` (registre d'adaptateurs,
  catégorise chaque launchpad `bonding`/`direct`/`unknown` en réutilisant `mint_authority.
  is_bonding_launchpad` comme SEULE source de vérité — jamais dupliquée) → deux pipelines distincts
  selon le modèle de liquidité : (1) **bonding** (Virtuals encore en courbe, sans paire DEX par
  construction) → nouveau `skills/bonding_screen.py` (pendant de `safety_screen.py` qui exige
  TOUJOURS une paire DEX et rejetterait à tort) → `skills/bonding_absorber.py` écrit dans
  `screened_pool` sous `network="base-bonding"` — **jamais** `network="base"` (le pool 85% VC) ;
  (2) **direct** (Clanker, Virtuals gradués — vraie liquidité DEX dès le départ) → rejoint le
  pipeline STANDARD existant (`token_absorber.absorb`, pool 85%), aucun nouveau filtre nécessaire.
  **Launchpads sans diligence faite restent des seams vides documentés** (Flaunch/Zora : adaptateur
  `discover=None` ; Bankr/Ape.store/Mint.club : entrées `knowledge/launchpads.yaml` sans adresse,
  `confidence: unverified` — diligence Bankr approfondie faite le 11/07, cf. note dans
  `docs/aria-learning-inbox/`, toujours pas de client construit). Tâche heartbeat
  `bonding_discovery_cycle` (180min, gate OFF `ARIA_BONDING_DISCOVERY_ENABLED`), les deux volets
  tournent indépendamment (testé). Clanker vérifié en direct depuis le VPS (mise à jour même
  segment) : `sortBy` réel énuméré par l'API elle-même (`deployed-at`, pas `createdAt` comme
  supposé — corrigé). **Dry-run manuel exécuté (11/07, ce segment)** via
  `python -m aria_core.dry_run_bonding_discovery` (docker `aria-api`), gate
  `ARIA_BONDING_DISCOVERY_ENABLED` resté OFF pendant tout le test : `bonding={}` (0
  candidat — `virtuals_bonding` interrogé en réel, rien en courbe actuellement),
  `direct={'skip_incomplete': 20}` (20 candidats Clanker découverts, 0 gardé — vérifié à la
  main sur 3 adresses : pas encore de paire DEX + contrat non vérifié, comportement attendu
  pour des tokens tout juste déployés, pas un défaut). Aucune erreur, aucune écriture en pool
  `active` (seulement `pending`/réseau `base` standard, pas de contamination du pool
  bonding). **Gate `ARIA_BONDING_DISCOVERY_ENABLED` toujours pas activé au 11/07** —
  décision d'activation en continu reste à l'opérateur.
- **Vision (images en chat Telegram) — EN LIGNE, gate ON, testé en conditions réelles (10/07).** Handler photo manquant corrigé (`telegram_bot.py` n'enregistrait aucun `MessageHandler(filters.PHOTO)`, toute image ignorée en silence) — un seul point d'entrée `_handle_photo` créé. Lecture visuelle admin-only, gate OFF par défaut (`ARIA_VISION_ENABLED`), testé en direct sur un vrai graphique DexScreener avec succès (chiffres lus correctement, distinction rug/dump). Détail complet : `docs/HANDOFF-2026-07-10-detail-archive.md`.
- **Note de calibration (10/07) — la règle « zéro trace IA » ne couvre PAS le chat Telegram
  opérateur.** Vérifié dans le code : la règle absolue dit explicitement « sur les surfaces
  client (rapport, vitrine) » — la conversation Telegram avec l'opérateur n'est ni un rapport ni
  la vitrine, `brain.py::_llm_response` autorise même les emojis légers. Pas une violation de la
  règle telle qu'écrite. **Reste une vraie question de calibration de ton non tranchée** (em-dash
  en conversation opérateur) — jamais tranchée depuis, à trancher avec l'opérateur si le sujet
  revient.
- **Identité visuelle ARIA — prompts de portrait renforcés (10/07).** Frontière de goût gravée en dur dans le prompt (« never suggestive, never revealing, never sexualized ») suite à une demande opérateur de monter en réalisme technique (jamais le registre sexualisé) — décision tranchée via `AskUserQuestion`. Reste explicitement différé (pas des points ouverts, des choix de scope documentés) : stories (aucune fonctionnalité native Telegram/bot pour ça), voix/TTS (aucune infra existante), studio 3D (jugé disproportionné). Détail complet : `docs/HANDOFF-2026-07-10-detail-archive.md`.
- **Nuit 9 (10/07) — pivot Cursor audité (pas un incident, opérateur a piloté 3 PR pendant une indisponibilité Claude Code), régressions `/vc` corrigées (`repertoire_db.save_message` non protégé, faux positif de routage `operator_readiness.py`), bug bonding Virtuals résolu (`tokenAddress` structurellement `null` avant graduation, adresse réelle dans `preToken` — fix vérifié end-to-end sur un contrat réel).** Deux points notés « ouverts » à l'époque sont **résolus depuis le 11/07** (voir entrée ci-dessous et `docs/HANDOFF-2026-07-11.md`) : diligence produit exploite désormais la fiche Virtuals (pas seulement le site externe), et `graduation_progress()` est câblé (gate OFF) via lecture on-chain. Connecteurs MCP explorés (Base MCP, Crypto.com, Gmail, Stripe, Massive Market Data) — **Massive Market Data reste une piste ouverte** (bug de propagation plateforme jamais reconfirmé résolu depuis). Détail complet : `docs/HANDOFF-2026-07-10-nuit9.md`.
- **11/07 — accès SSH multi-repo VPS, nettoyage cohérence (5 branches mergées), `graduation_progress()` résolu, câblé ET mergé (gate OFF).**
  Session tournant directement sur le VPS avec accès réseau réel non filtré (contrairement
  au cloud) : investigation on-chain jamais possible avant. **7 deploy keys SSH dédiées**
  mises en place pour l'écosystème complet (ARIA + aria-ops + aria-core +
  template-grok-cursor + aria-acp-showcase + acp-cli-demos + GoldenFarFR) — détail
  `aria-ops/runbooks/vps-github-access.md`. `deploy.sh` purge désormais le cache Docker
  après succès (cause du remplissage disque nuit9). **5 branches** mergées `--no-ff`
  après validation diff-par-diff et suite verte à chaque étape : suivi cross-jour
  `exam.py` ; diligence produit `/vc` exploite désormais la fiche Virtuals
  (tokenomics/description) ; 2 modules morts supprimés + 4 docs corrigées pour dérive
  doc/code ; **`graduation_progress()` RÉSOLU ET MERGÉ** — vrai contrat Bonding V5 trouvé
  par balayage de logs on-chain (`0x1A540088125d00dD3990f9dA45CA0859af4d3B01`), seuil de
  graduation confirmé PAR TOKEN (`tokenGradThreshold`, pas une constante globale — cause
  du seuil 125M/42000 faux trouvé en passe 1), formule validée empiriquement sur un vrai
  token gradué, implémenté dans `services/base_onchain.py` (lecture seule, aucune clé),
  gaté `ARIA_ONCHAIN_GRADUATION_ENABLED` (OFF), couverture partielle honnête documentée
  (une seule instance de contrat connue). **Aucune trace de "BONDING_V4" trouvée nulle
  part dans ce repo** (recherche exhaustive, cf. HANDOFF) — les seules mentions "V4"
  concernent Clanker v4/Uniswap V4, probable confusion, pas une vraie contradiction.
  Audit complet de la connaissance ARIA (`knowledge/*.yaml`) : contradiction confirmée sur
  le statut LLM (même famille que le bug Aria Market, pas corrigée), duplication à risque
  `faq.yaml`/`canonical_facts.yaml` (choix d'architecture à trancher), et constat que
  "ARIA manque de données" vient de mécanismes de collecte propres à l'arrêt
  (`telegram_conversation_miner.py` codé mais jamais activé), pas d'un manque de
  recherche externe. 2 notes déposées dans `docs/aria-learning-inbox/` (Virtuals x
  Robinhood Chain, diligence Bankr). Section "Faits établis" compactée le même segment
  (Nuit 7-9 réduites, rien perdu — cf. `docs/HANDOFF-2026-07-10-detail-archive.md`).
  **Fin de session, feu vert opérateur nommé explicitement sur 3 points Tier 2** :
  `goldenfar-vault.gfv` (aria-ops) retiré du suivi git + négations `.gitignore` qui le
  réintroduisaient supprimées (décision opérateur "aucune donnée sensible sur GitHub"
  prime sur le design précédent ; fichier reste dans l'historique passé, réécriture
  différée) — commit `9212f42` ; 413 doublons `truth-ledger/*-canonical-base-token.md`
  supprimés (mécanisme dormant confirmé, aucun appelant en prod) ; `sync_canonical_facts()`
  câblé dans le heartbeat (`canonical_facts_sync_cycle`, gate `ARIA_CANONICAL_FACTS_SYNC_ENABLED`
  OFF) pour que `faq.yaml` ne dérive plus jamais de `canonical_facts.yaml` — mergé `main`
  (`696cbb23`), suite verte (4361 passed, 7 skipped, 0 échec), branche supprimée. La
  "duplication à risque" `faq.yaml`/`canonical_facts.yaml` notée juste au-dessus est donc
  câblée en solution (activation en prod encore à décider).
- **Audit #77 (11/07) — cadence réelle du candidate flow avant `ARIA_PAPER_TRADING_ENABLED` :
  verdict NON, volume insuffisant.** Alerte trouvée en auditant : `ARIA_PAPER_TRADING_ENABLED=true`
  est **déjà actif en prod** (`paper_state.created_at` = 08/07, il y a 3 jours) — l'horloge des
  20 jours tourne peut-être déjà sans décision tracée. Données réelles sur 3,73 jours organiques
  (20 lignes de mon propre dry-run bonding de ce segment exclues pour ne pas fausser les chiffres) :
  72 candidats distincts absorbés dans `screened_token` (~135/semaine — le sourcing brut n'est PAS
  le problème), mais **0 % n'atteint jamais le statut `active`** (50 rejetés, 22 pending), confirmé
  sur 4 jours de logs heartbeat (`— 0 gardés` à chaque cycle `vc_crawl`, sans exception).
  Conséquence : `vc_weekly_forecast` (tire 20 candidats du pool actif tous les 2 jours) a tourné
  au moins 2 fois et produit `0 pronostics` les deux fois ; `paper_trade_cycle` (même source) n'a
  ouvert 0 position en 3 jours de gate ON. Les 10 `vc_prediction` existants sont TOUS manuels
  (`/vc <contrat>` sur 6 contrats distincts), 0 résolue, horizon 30 j (`vc`) donc aucune ne peut
  résoudre avant le 06-09/08 — hors de toute fenêtre de 20 jours démarrée maintenant. Cause
  probable chiffrée : le filtre (`safety_screen`, 5 critères mous requis simultanément — score≥70,
  vérifié, holders connus, liquidité≥30k$, verdict SAFE) rejette à 87,5 % sur des critères de
  fraîcheur du token, pas un signal malveillant confirmé (~12,5 % seulement). **Rien activé ni
  modifié** (lecture seule) : décision opérateur nécessaire sur le gate déjà ON pour 0 preuve, et
  sur élargir le sourcing vs assouplir le filtre avant de compter les 20 jours pour de vrai. Détail
  complet : `docs/audit-2026-07-11-paper-trading-cadence.md`.
- **Retry délibéré des candidats `pending` (11/07, suite directe de l'audit #77) — CODÉ, testé,
  PAS déployé.** Vérifié avant de coder (décision opérateur explicite) : `safety_screen`/
  `token_absorber` classent DÉJÀ correctement un échec lié à la fraîcheur du token (score<70,
  contrat pas vérifié, holders inconnus, liquidité basse, verdict CAUTION) en `pending` (échec
  MOU, jamais un rejet définitif) — **aucun bug dans ce classement**, confirmé en reconstruisant
  le contexte exact d'une des 41 lignes `rejected` sans signal dur trouvées dans l'audit : elles
  sont des reliquats d'une version plus stricte du code (probablement pré-10/07), jamais
  retouchées depuis (`rejected` court-circuite le rescan, contrairement à `pending`) — pas un
  bug actuel, hors scope de ce correctif. **Le vrai trou** : rien ne va délibérément rescanner un
  candidat `pending` — seule une redécouverte fortuite (même contrat qui réapparaît dans le
  crawl) déclenche un nouveau passage, déjà testé et correct
  (`test_soft_fail_pending_is_still_rescanned_next_cycle`). Ajouté : `screened_pool.
  list_stale_pending()` (candidats `pending` dont `last_checked_at` dépasse 24h) +
  `base_crawler.retry_stale_pending()` (appelle le MÊME `token_absorber.absorb()` que le crawl
  normal sur ces candidats — aucun filtre dupliqué, aucun seuil de sécurité touché), câblés dans
  le heartbeat `vc_crawl` juste après `crawl_and_absorb`. 8 nouveaux tests, suite complète verte
  (4369 passed, 7 skipped, 0 échec — 0 régression). **Rien déployé** (le code n'a d'effet qu'au
  prochain rebuild Docker + restart) : décision opérateur avant de le pousser en prod.
- **Plafond anti-boucle-infinie sur `retry_stale_pending()` (11/07, suite directe ci-dessus) —
  CODÉ, testé, PAS déployé.** Le retry ci-dessus n'avait aucune limite : un candidat qui
  n'atteint jamais `active` ni un vrai `hard_fail` malveillant confirmé serait retenté toutes
  les 24h **pour toujours** (coût scan API récurrent, sans fin). Ajouté : colonne
  `screened_token.retry_count` (migration à chaud idempotente, même patron que
  `vc_predictions.py`/`exam.py`) — incrémentée à chaque `record_pending`, remise à zéro par
  `upsert_screened` (devient `active`) et `reconsider` (résurrection sur bruit externe, budget
  de tentatives frais). `screened_pool.abandon_stale_pending()` bascule un `pending` en
  `rejected` définitif (raison explicite `"abandonné après N tentatives (Xj) — signal faible
  persistant : <dernière raison molle>"`) au-delà de **5 tentatives OU 7 jours** depuis
  `first_screened_at` — **aucun nouveau critère de sécurité** (aucun filtre dupliqué,
  `safety_screen`/`token_absorber`/seuil `passed` inchangés), uniquement une limite sur le
  NOMBRE DE PASSAGES, appliquée par `base_crawler.retry_stale_pending()` seulement quand
  `absorber` a déjà tranché que le candidat reste `skip_incomplete` (ni mûri, ni malveillant
  confirmé). Migration vérifiée manuellement contre une copie du schéma de prod réel (92
  lignes `rejected`/`pending` existantes, colonne ajoutée sans erreur, `retry_count=0` par
  défaut). 13 nouveaux tests, suite complète verte (4382 passed, 7 skipped, 0 échec — 0
  régression). **Rien déployé, données de production NON touchées** (la reclassification des
  39 candidats `rejected` sans signal dur identifiés dans l'audit — 41 moins 2 rejets encore
  valides aujourd'hui — reste une décision séparée, après validation opérateur de ce correctif).
- **11/07 (ce segment) — 4 items du backlog fermés, en attendant la décision opérateur sur le
  gate bonding.** `production.env.example` (aria-ops) : ID Telegram réel (`5864967247`) retiré
  (placeholder vide, comme les autres champs secrets du fichier), encodage mojibake des
  commentaires FR corrigé, `GITHUB_PROTECTED_REPOS`/`GITHUB_SANDBOX_REPO` réalignés sur les
  noms réels (`ARIA`, pas `aria-vanguard`/`aria-sandbox`), `GITHUB_WRITE_REPOS` par défaut
  passé de `*` à `off` (un template ne doit pas enseigner le défaut dangereux). Nomenclature
  `#104` : déjà résolue par le nettoyage Tier 1 fait plus tôt cette session (commits
  `731131a`/`55f4f8f`/`3941f60` sur aria-ops/template-grok-cursor/aria-acp-showcase).
  **Contradiction LLM confirmée ci-dessus, corrigée** : `canonical_facts.yaml`/`faq.yaml`
  disaient encore `ARIA_LLM_ENABLED=false`/« no generative LLM » et portaient des noms de
  repos morts dans 5 faits (`anti-hallucination`, `truth-ledger`, `aria-builds`, `repos`,
  `github-governance`, `operator-runbook`) — réalignés sur l'état réel (LLM actif en prod
  avec grounding, monorepo `ARIA` + `aria-ops`). Suite complète verte après coup (4361 passed,
  7 skipped, 0 échec). **`GITHUB_WRITE_REPOS` (point en attente depuis nuit8/9) confirmé `off`**
  dans le `.env` réel du conteneur `aria-api` sur ce VPS (`vanguard/backend/.env` — accès direct
  filesystem, pas de token/API nécessaire ; le garde-fou Credential Materialization a bloqué un
  grep plus large qui aurait pu exposer `GITHUB_TOKEN`, correctement).
  Détail complet : `docs/HANDOFF-2026-07-11.md`.
- **Clôture position Arena BTC #60 (11/07) — 0.0003 BTC fermée à 100%, capital consolidé en
  USDC sur Base.** Exécuté manuellement par l'opérateur dans son propre terminal, **PAS via une
  session autonome** (conforme à la règle absolue sur le capital réel — l'outil `acp` signe sans
  passer par la confirmation Telegram habituelle de `wallet_guard`, seam déjà documenté ci-dessus :
  Arena reste hors du périmètre `wallet_guard`/kill-switch par construction). Double vérification
  à chaque étape (pas seulement le texte de sortie de l'outil) : (1) position HL fermée confirmée
  par `acp trade hl-status` (`positions: []`) ET par la page d'approbation Virtuals (statut
  « Approuvé », signature correspondante) ; (2) retrait des USDC de Hyperliquid vers Arbitrum
  (19.03 USDC) puis pont Arbitrum→Base via `acp trade --token-in usdc --chain-in 42161
  --amount-in 19 --token-out usdc --chain-out 8453 --slippage 5` — slippage 5% explicite (≤10%,
  règle absolue respectée ; la sous-commande dédiée `withdraw-from-hl` n'expose pas de paramètre
  de slippage, la commande générique `acp trade` si) ; (3) solde SOL séparé (0.2 SOL, déposé
  antérieurement sur ce wallet agent) ponté Solana→Base via la même commande générique et la même
  discipline de slippage explicite (5%), 15.641675 USDC reçus ; (4) solde final vérifié par
  lecture on-chain directe (`acp wallet balance`) : base-mainnet USDC = 36.95362 sur le wallet
  agent Aria Vanguard ZHC (`0xd752a325433f4d55c5e0b125be84845d7de47bb3`). **Le pilote #60
  lui-même reste actif/in_progress** — seule cette position précise est close. Détail complet :
  `docs/HANDOFF-2026-07-11.md`.
- **Confabulation sur soi-même côté opérateur — #105 (11/07) — CODÉ, testé, DÉPLOYÉ (32e6b2f5, 11/07).**
  Deux réponses réelles testées sur Telegram post-déploiement 960a72f2 : (1) « tu fonctionnes
  avec quel type d'intelligence, un LLM ? » → « Oui, actuellement Claude Opus 4.8 » (régression
  EXACTE de l'incident du 08/07, `grounded_llm_identity()`) ; (2) « comment tu analyses un
  token, IA générative ? » → réponse générique en 6 points, aucun vrai outil cité. **Cause
  racine identique aux deux** : `grounded_for_audience(public) = aria_grounded_mode AND public`
  — tout le système de grounding (bloc faits vérifiés, `grounded_llm_identity`, FAQ directe,
  chemin web calibré) n'est câblé QUE derrière ce flag, qui est TOUJOURS `False` côté opérateur
  (« Operator gets founder LLM », design volontaire — cf. `test_grounded_for_audience_operator_
  bypass`). Le fix du 08/07 avait corrigé le TEXTE de `grounded_llm_identity()` mais cette
  fonction n'a jamais été appelable sur le chemin conversation opérateur — pas une régression de
  code, un angle mort de scope resté depuis l'origine. **Fix** : deux nouveaux détecteurs
  audience-indépendants (`grounding.is_llm_identity_question`/`is_analysis_methodology_question`)
  routés vers une réponse **déterministe, zéro appel LLM** (`llm_identity_reply`/
  `analysis_methodology_reply`, câblés dans `brain._general_response`, même famille que
  `llm_routing_meta.is_llm_routing_question` déjà existant pour le routage technique). Nouveaux
  faits `llm-model-identity`/`analysis-methodology` ajoutés à `canonical_facts.yaml`/`faq.yaml`
  en défense en profondeur (recherche FAQ). Le reste de la conversation fondateur opérateur
  n'est PAS touché (aucun changement sur `grounded_for_audience` lui-même, casual/stratégique
  toujours libres). Limite honnête : ne couvre que les phrasings testés par regex — une
  formulation très différente peut encore échapper au filet et confabuler. 11 nouveaux tests,
  suite complète verte (4377 passed, 7 skipped, 0 échec). **Déployé et vérifié 11/07** (commit
  `32e6b2f5`, groupé avec #105/#108 — health check double confirmé, script + curl indépendant).
  **Limite structurelle actée** : une réponse conversationnelle du LLM (même grounded) n'est
  JAMAIS une preuve de ce qu'ARIA fait réellement — la vraie preuve vit dans les rapports `/vc`
  (données on-chain réelles, tracées) et les logs serveur, jamais dans l'auto-description en
  chat. Un chat grounded réduit le risque de confabulation, il ne le supprime pas structurellement
  (dépend de la couverture des detecteurs/facts) — ne jamais citer une réponse Telegram d'ARIA
  comme preuve d'une capacité ou d'un comportement système. **Complément 11/07 (voir entrée
  #110 incomplet plus bas)** : le fix ne gagnait que sur le chemin `_general_response` — un
  interceptor antérieur dans `process()` (`vc_session_context.is_vc_followup_question`) pouvait
  encore avaler la question méthode d'analyse avant que `_general_response` ne soit jamais atteint.
- **Coordination à deux sessions VPS (11/07) — méthode adoptée ce segment, à connaître pour la
  suite.** Une session cloud (celle-ci) pilote deux sessions Claude Code distinctes tournant sur
  le VPS (« VPS Principal » / « VPS Secondaire »), chacune dans son propre git worktree isolé
  (cf. incident `a4eb3955` plus haut — jamais deux sessions sur le même dossier de travail).
  L'opérateur relaie les messages entre les trois (aucun canal direct entre sessions —
  `create_trigger`/`persistent_session_id` testé, **refusé par la politique d'organisation**,
  confirmé non contournable). Règles opérationnelles fixées : chaque rapport VPS s'auto-identifie
  ("[VPS Principal]"/"[VPS Secondaire]") ; tout code touchant la logique réelle (pas juste de la
  doc) est montré en texte brut avant tout push, jamais un résumé seul ; CLAUDE.md/HANDOFF
  peuvent être écrits librement par les VPS dans leurs commits (pas de verrou technique — la
  règle déjà existante "relire intégralement après chaque mise à jour" suffit, appliquée par la
  session cloud après coup plutôt qu'en blocage avant écriture) ; déploiement groupé plutôt qu'un
  rebuild Docker par correctif. Productif : 3 correctifs réels livrés et déployés en un segment
  (#105/#108/#110) grâce à ce parallélisme.
- **Rôles VPS fixes + jamais d'inactivité (12/07, décision opérateur explicite).** Chaque VPS
  reste utilisé selon le rôle de sa création, jamais interchangé : **Principal/Secondaire =
  ouvriers** (exécutent réparations/améliorations concrètes sur le code réel, backlog numéroté) ;
  **Research = radar technologique large** (détecte des technologies/approches à implémenter
  dans ARIA, recherche sourcée, banque dans `docs/aria-learning-inbox/`). Un détournement
  ponctuel du rôle de Research (ex. #79 veille concurrentielle le 12/07) reste possible sur
  demande explicite, mais le mode par défaut de Research est le scan large-spectre, pas une
  tâche d'ouvrier. **Aucun VPS ne doit jamais rester inactif une fois sa tâche terminée** — dès
  qu'un VPS rapporte "terminé", la session cloud doit immédiatement lui redispatcher la suite
  (nouvelle tâche du backlog pour Principal/Secondaire, nouvelle passe de scan pour Research)
  avant de passer à autre chose.
- **Autorité de commit centralisée (12/07) — durcit la règle de dispatch VPS, remplace la mention
  "CLAUDE.md/HANDOFF librement" ci-dessus.** Décision opérateur explicite : seule la session cloud
  (commandement) fait les commits qui atterrissent sur `main`. Un VPS ne pousse jamais sur `main`
  (déjà acquis le 11/07) ET ne se considère plus comme faisant le commit "final" du tout : il
  prépare (implémente, teste, commit local si besoin pour son propre suivi), pousse sur une
  branche temporaire dédiée (`claude/<sujet>-review-temp`), puis s'arrête là — y compris pour
  CLAUDE.md/HANDOFF désormais (la mention "librement" ci-dessus est périmée). La session cloud
  relit et fait elle-même le commit qui compte sur `main`. But : un point de contrôle unique avant
  toute écriture durable sur l'historique public.
- **Piège vécu (12/07) — `git push origin <nom-de-branche>` pousse la branche locale de ce nom,
  pas le `HEAD` courant.** En committant sur `main` local puis en lançant `git push origin
  claude/session-context-files-ofl85l`, 2 commits (règle de commit centralisée + marqueur de
  déploiement) sont partis vers cette branche annexe au lieu de `main` — `origin/main` n'a jamais
  bougé, sans erreur ni avertissement visible. Détecté seulement parce que Secondaire a re-vérifié
  et signalé l'écart plutôt que de faire confiance à mon annonce. Corrigé par rebase +
  `git push origin main:main` (refspec explicite). **Réflexe désormais obligatoire pour la session
  cloud** : toujours pousser sur `main` avec le refspec explicite `git push origin main:main` (ou
  `HEAD:main`), jamais `git push origin <nom>` seul ; et après CHAQUE push cloud vers `main`,
  re-vérifier soi-même via `git fetch origin main && git show origin/main:<fichier>` avant
  d'annoncer un commit comme fait — ne jamais se fier au seul texte de sortie de `git push`.
- **Test manuel Telegram post-déploiement (11/07) — méthode à répéter après chaque déploiement
  touchant la conversation.** Poser quelques questions ciblées à ARIA en direct sur Telegram
  après un déploiement a débusqué 4 problèmes réels que la CI ne couvrait pas : la régression
  #105/#110 ci-dessus, un **bug de routage** (question d'actualité crypto matchée à tort sur une
  réponse canonique hors-sujet, `P(vrai)=0.95` affiché sur du contenu sans rapport — cause
  probable : classifieur d'intent, pas le LLM, 0 token consommé), et une **fabrication citée**
  plus grave (`web_verify.py` : question "maxi BTC ou ETH ?" a reçu une réponse "LIVE INFO —
  verified web sources" citant un article CNBC sur l'opinion de **Mark Cuban**, attribuée à tort
  à ARIA — même famille que l'incident rugby du 10/07, mais cette fois la source ne parle même
  pas de la bonne ENTITÉ, pas seulement du mauvais événement ; **corrigé depuis par VPS
  Secondaire, détail dans l'entrée dédiée ci-dessous, cf. #113**). Root cause identifiée avec
  précision (le prompt de recalibration web ne vérifiait que "même compétition", jamais "même
  entité que celle interrogée") ; fix en texte de prompt uniquement, aucun changement de
  plomberie — limite honnête assumée : renforce l'instruction LLM, ne peut pas garantir
  mécaniquement 100% de conformité (même nature que le fix rugby déjà accepté). Découverte
  annexe non technique : demande opérateur explicite (11/07) que le prénom/nom réel n'apparaisse
  plus jamais publiquement (sauf README `goldenfarfr/goldenfarfr`, voulu) — trouvé dans le repo
  public ARIA (`CLAUDE.md`, `config.py`, et un usage FONCTIONNEL réel dans `brain.py` — regex de
  détection de message opérateur, pas juste du texte à retirer). Backlog #111 (routage)/#112
  (qualité réelle des tweets avant confiance totale sur l'autonomie X, déjà autorisée par
  l'opérateur)/#113 (fabrication citée, corrigé)/#114 (nom réel) créés, détail et état
  d'avancement dans `docs/HANDOFF-2026-07-11.md`.
- **#113 — détail technique de la règle d'entité `web_verify.py` (11/07), livré par VPS
  Secondaire.** `_WEB_RECAL_PROMPT_FR`/`_EN` gagnent une règle « même ENTITÉ que celle
  interrogée », distincte de la règle « même compétition/événement » (10/07, taillée pour le
  sport uniquement) : un extrait qui rapporte l'opinion d'un tiers (investisseur, célébrité,
  autre société) ne doit plus jamais être attribué à ARIA comme sa propre position, même si le
  thème correspond — avec rappel explicite de sa vraie doctrine (85% VC moyen/long terme + 15%
  trading, poche adrénaline plafonnée, **aucune position maximaliste sur une chaîne**, phrasing
  repris de "Vision & stratégie" en tête de ce fichier). Toute la plomberie d'abstention honnête
  (`FAIT: INCERTAIN`) existait déjà côté `epistemic.py`/`web_verify.py` — seul le texte du prompt
  manquait la règle, aucun changement de code de traitement. 3 nouveaux tests
  (`test_live_info_fallback.py`, même patron que le fix rugby du 10/07) dont un cas de contraste
  (question légitimement sur un tiers — CEO Coinbase — ne doit pas être bloquée). Suite complète
  verte (4385 passed, 7 skipped, 0 échec, 0 régression). **Limite assumée** : correctif de
  PROMPT, pas un filtre déterministe — même nature et même limite que le fix rugby du 10/07.
  **Codé, testé, PAS déployé** — #108/#110 ont déjà été déployés séparément (commit `32e6b2f5`,
  avant que ce correctif ne soit prêt) ; #113 attend son propre déploiement groupé avec le
  prochain correctif prêt. Détail complet : `docs/HANDOFF-2026-07-11.md`.
- **#111 résolu — faux positif épistémique sur question d'actu (11/07), root cause du « bug de
  routage » repéré dans l'entrée « Test manuel Telegram » ci-dessus — CODÉ, testé, PAS déployé.**
  Testé sur Telegram post-déploiement 32e6b2f5 : « qu'est-ce qui s'est passé sur les marchés
  crypto dans la dernière heure ? » (vraie question d'actu, `is_live_info_question`=True) a reçu
  « Non. Les promesses de gains garantis sur crypto sont du hype — probabilité de véracité très
  faible. » (P(vrai)=0.95, source « ARIA learning filter ») — 0 token LLM consommé, donc pas une
  hallucination du LLM mais un mauvais matching du système de réponse calibrée déterministe.
  **Cause racine** : `resolve_calibrated_answer()` (`knowledge/epistemic.py`) appelait
  `epistemic_static_answer()` (matching `epistemic_core.yaml`) EN PREMIER, avant tout routage
  `is_live_info_question`/`is_explicit_web_request` — et `_score_claim()` comptait le seul mot
  partagé « crypto » 4 fois (présent dans `claim_fr` + `claim_en` + `tags` + `topic` du claim
  `crypto-hype-unreliable`, 2 pts chacun = 8 = `EPISTEMIC_DIRECT_SCORE`), sans qu'aucun `trigger`
  réel (« 100x garanti », « moon soon »…) ne soit présent dans la question. **Fix** : `EpistemicMatch`
  et `_score_claim()` exposent désormais `trigger_hit` (un vrai trigger explicite a matché, pas
  juste un mot générique partagé) ; `resolve_calibrated_answer()` ne laisse plus un match SANS
  trigger réel court-circuiter une question détectée `is_live_info_question`/
  `is_explicit_web_request` — elle route alors vers `web_first_answer` comme attendu. Un vrai
  trigger explicite (ex. « 100x garanti ») continue de répondre depuis le YAML canonique même
  si la formulation ressemble à de l'actu (`test_real_hype_trigger_still_wins_over_web_even_if_
  news_shaped`). 4 nouveaux tests (`test_epistemic.py`), suite complète verte (4393 passed, 7
  skipped, 0 échec). **Rien déployé.**
- **#110 incomplet — `vc_followup` court-circuitait le fix anti-confabulation (11/07) — CODÉ,
  testé, PAS déployé.** Testé sur Telegram post-déploiement 32e6b2f5, dans la MÊME conversation
  que #105, juste après la question identité LLM : « comment tu analyses un token, tu utilises
  de l'IA générative ? » a coûté 10923 tokens (payant) — donc bien passée par un vrai appel LLM,
  pas par le template déterministe #110. **Cause racine** : `process()`
  (`brain.py::_process_inner`) appelle `_try_vc_followup_response()` **avant** même d'atteindre
  `_general_response()` — là où vivait tout le routage #110. `vc_session_context.
  is_vc_followup_question()` (regex générique « comment »/« pourquoi »/... + « token »/
  « analyse »/...) matche AUSSI cette question générique de méthode dès qu'un `/vc` récent
  (TTL 4h) traîne en mémoire courte (`vc_operator_last`, alimentée par toute commande `/vc`
  opérateur) — confirmé en isolant `is_vc_followup_question("comment tu analyses un token, tu
  utilises de l'IA générative ?")` → `True`. Pas une régression de la regex #110 elle-même
  (matche toujours correctement en isolation) : une collision entre deux détecteurs jamais
  croisés, où le plus ancien/large gagne par ordre d'exécution. **Fix** : les deux détecteurs
  #110 (`is_llm_identity_question`/`is_analysis_methodology_question`) sont désormais vérifiés
  tout en haut de `process()`, avant TOUT autre routage (`vc_followup` inclus) — la garantie
  "jamais de confabulation sur ces deux sujets" ne dépend plus de l'ordre des interceptors
  ajoutés avant ou après. Le bloc original dans `_general_response` reste en place (deuxième
  ligne de défense pour tout appelant direct de cette méthode, ex. tests). 2 nouveaux tests
  reproduisent les conditions réelles (`/vc` récent en cache) via `process()` — l'un d'eux
  échoue bien contre le code pré-fix (vérifié manuellement avant de committer, `llm_calls
  == 1` au lieu de `0`), confirmant qu'il verrouille vraiment ce cas. Suite complète verte
  (4395 passed, 7 skipped, 0 échec). **Rien déployé.**
- **Reclassification `rejected` → `pending` des 41 candidats sans signal dur (11/07) — EXÉCUTÉE
  en production, DONNÉES RÉELLES modifiées.** Suite directe de l'audit #106/correctif #108
  (plafond anti-boucle-infinie déployé et vérifié actif avant toute écriture — `abandon_stale_
  pending`/`retry_count` confirmés présents dans le conteneur `aria-api`, câblés dans le
  heartbeat `vc_crawl`). Les 41 candidats `screened_token` identifiés sans marqueur dur
  (mint/blacklist/honeypot/owner caché) — reliquats pré-`d1a65472` — reclassés `pending`,
  `retry_count=0`, via une `UPDATE` ciblée par liste exacte de 41 adresses (`AND
  status='rejected'` en garde). **SOSO et VIRTUAL confirmés absents des 41** (déjà dans le
  groupe des 9 exclus, marqueur "owner caché" GoPlus toujours valide) — écart signalé et
  clarifié avec l'opérateur avant exécution (le "39" initialement demandé provenait d'un
  recomptage erroné, confirmé). Double exécution : (1) sur une copie de `/opt/aria-data/
  aria.db`, vérifiée intégralement (41/41 `pending`/`retry_count=0`, 9 exclues intactes,
  aucune autre table touchée, total `screened_token` stable à 92) avant tout feu vert sur la
  vraie base ; (2) sur la base réelle après second feu vert explicite, sauvegarde horodatée
  prise juste avant écriture, mêmes vérifications rejouées à l'identique — résultat 100%
  conforme à la copie. `rejected` : 50→9, `pending` : 42→83. Ces 41 seront repris par
  `retry_stale_pending()` au heartbeat suivant (pas immédiat, dépend de `older_than_hours`).
  Détail complet (liste des 41, requête SQL exacte, sauvegarde) : `docs/HANDOFF-2026-07-11.md`.
- **#107 — retry différé étendu au pipeline bonding (11/07), CODÉ, testé, PAS déployé.**
  Même trou que #105 mais côté niche 15% : `discover_and_absorb_bonding` ne revoit un
  candidat `pending` (`network="base-bonding"`) que s'il réapparaît par hasard dans une
  découverte ultérieure — rien ne le retente délibérément. Ajouté `bonding_absorber.
  retry_stale_bonding_pending()`, câblé dans le heartbeat `bonding_discovery_cycle` juste
  après `run_bonding_discovery_cycle` (même patron que `vc_crawl` pour #105/#108).
  **Zéro duplication de logique** : `screened_pool.list_stale_pending()`/
  `abandon_stale_pending()` étaient déjà génériques (le premier accepte déjà un paramètre
  `network`, le second opère sur `contract` seul, clé primaire, sans notion de réseau) —
  la nouvelle fonction délègue entièrement à `base_crawler.retry_stale_pending()` déjà
  existant (boucle, comptage, plafond anti-boucle-infinie #108), seuls `lister` (scopé
  `network="base-bonding"`) et `absorber` (`absorb_bonding_candidate` au lieu du standard)
  sont substitués via les paramètres d'injection déjà prévus pour ça. 4 nouveaux tests
  (`test_bonding_absorber.py`) : candidat frais jamais retenté, isolation stricte du pool
  standard (`network="base"` jamais vu par ce retry), plafond anti-boucle-infinie
  fonctionnel sur un candidat bonding bloqué (bascule `rejected` après 5 tentatives, même
  garde-fou que #108), câblage réseau par défaut vérifié. Suite complète verte (4402
  passed, 7 skipped, 0 échec, 0 régression). **Codé, testé, PAS déployé** — regroupé avec
  le prochain déploiement plutôt qu'un rebuild dédié isolé (même doctrine de déploiement
  groupé). Détail complet : `docs/HANDOFF-2026-07-11.md`.
- **#114 — volet code, nom réel remplacé par une config (11/07), CODÉ, testé, PAS déployé.**
  Suite du volet doc de VPS Principal (`CLAUDE.md`, commit `adb28831`). Le nom réel de
  l'opérateur était en dur dans du code fonctionnel, pas seulement des commentaires : deux
  regex dans `brain._routing_message` (pont Cursor/KART) détectaient un préfixe littéral
  `"<nom> confirme :"`/`"Message actuel de <nom>:"`, et `relay_conversation._history_message`
  écrivait le nom en dur comme label `[<nom>]` dans l'historique envoyé au LLM. **Pas un
  simple retrait de texte** : nouveau champ `settings.aria_operator_display_name` (défaut
  générique `"Operator"`, ajouté à `vanguard/backend/app/config.py` ET
  `aria_core.testing.AriaRuntimeSettings`) — les deux fonctions le lisent via `getattr` avec
  repli sûr, la vraie valeur ne vivra que dans le `.env` réel du VPS, jamais commise. Défaut
  non configuré = comportement inchangé pour "Grok" (toujours en dur, ce n'est pas un nom de
  personne) et dégradation propre (aucun message mal-parsé, retourné tel quel) pour le reste.
  8 nouveaux tests dédiés (`test_brain_routing_message.py`, noms de test génériques
  uniquement — jamais le vrai nom, y compris dans les tests) + 2 tests mis à jour
  (`test_relay_conversation.py`, comportement par défaut + comportement configuré) + 2
  fixtures de test nettoyées (`test_ingest_repo_skill.py`). Commentaires/docstrings
  également nettoyés (`operator_readiness.py`, `operator_go_ahead.py`,
  `acp_conversational.py`, `acp_client_skill.py`, `llm_economy.py`) + 2 faits `knowledge/*.yaml`
  (`aria_values.yaml`, `aria_goals.yaml`) qui alimentent le raisonnement d'ARIA. Suite
  complète verte (4411 passed, 7 skipped, 0 échec, 0 régression). **Grep exhaustif du repo
  fait** : plus aucune occurrence dans le code Python/YAML/config. **Reste hors périmètre
  code, trouvé et signalé, PAS touché** : `AGENTS.md` (6 occurrences, doc miroir de
  `CLAUDE.md` pour d'autres outils), `skills/.grok/skills/session-handoff/SKILL.md` (4),
  `core/README.md`, `docs/conformite-dossier-avocat.md`, `docs/protocole-argent-reel.md` —
  décision opérateur nécessaire (les traiter maintenant ou les laisser en backlog distinct).
  `docs/HANDOFF-2026-07-07-nuit.md` volontairement laissé intact (archive historique datée,
  même doctrine que la compaction `CLAUDE.md` du 11/07 : jamais réécrire un point-in-time
  passé). **Rien déployé** — regroupé avec le prochain déploiement. Détail complet :
  `docs/HANDOFF-2026-07-11.md`.
- **Migration LanceDB (12/07) — EN LIGNE, déployée et vérifiée.** `chromadb` (CVE-2026-45829,
  CVSS 10, RCE, non patché) retiré de la surface prod — remplacé par LanceDB (store vectoriel
  embarqué, zéro composant serveur) + `fastembed` (ONNX local, `BAAI/bge-small-en-v1.5`) pour
  l'embedding (chromadb l'auto-embeddait, LanceDB non). Nouveaux modules
  `memory/vector/embedding.py`/`lancedb_client.py`/`lancedb_store.py` (remplacent les
  équivalents `chroma_*`), `paths.chroma_dir()` → `vector_dir()`. Bug `.gitignore` corrigé au
  passage (`memory/` non ancré avalait silencieusement `packages/aria-core/.../memory/` —
  ancré en `/memory/`). Déployée par VPS Principal (commit `d31c11a6`), confirmée via CI
  (re-scan SCA propre) + `/status`. **Mémoire vectorielle (activation réelle du comportement,
  distincte de la migration d'infra) volontairement PAS activée** — décision séparée, à
  trancher plus tard.
- **RÉGRESSION réelle trouvée en testant ARIA en direct (12/07) — mauvais routage web sur texte
  long, CORRIGÉ et DÉPLOYÉ (`7610dea1`).** Un scénario de raisonnement hypothétique (650+
  caractères, mentionne "prix" une seule fois) envoyé à ARIA est parti en recherche web
  littérale (DDG) au lieu d'être raisonné — `web_verify.py::is_live_info_question` laissait un
  mot de marché générique isolé (prix/bitcoin/crypto/...) déclencher seul, même noyé dans un
  texte long sans aucun autre signal d'actualité réelle. Fix : garde de longueur
  (`_LIVE_INFO_LONG_TEXT_CHARS = 250`) — au-delà, un mot générique seul ne déclenche plus, sauf
  signal vraiment non ambigu (`_LIVE_INFO_UNAMBIGUOUS_RE` : rugby/coupe du monde/nba/tennis/f1).
  Testé contre les 2492 cas du fuzz test existant (0 régression) + 3 nouveaux tests ciblés.
  **Redéployé et revalidé sur Telegram en direct** : le même prompt-test envoyé après
  déploiement a produit une analyse de raisonnement correcte (verdict argumenté sur 4 signaux
  fournis, zéro recherche web fabriquée).
- **Second bug de routage réel trouvé sur le MÊME test (12/07) — chemin totalement différent,
  CORRIGÉ et DÉPLOYÉ (`27c6057` — pas encore redéployé sur le VPS à ce stade).** Un second
  scénario (analyse technique MACD/orderbook/funding rate, contient "2%"/"15%") est reparti en
  recherche web littérale, cette fois via un chemin **opérateur** distinct
  (`operator_conversational.py::is_injected_factual_claim` → `verify_external_claim`, conçu
  pour vérifier des affirmations collées courtes, pas pour un scénario de raisonnement). Cause
  racine précise : `_QUESTION_RE` n'exigeait le `?` qu'en toute fin de chaîne
  (`\?\s*$`) — un message multi-phrases avec une vraie question au milieu suivie d'une
  consigne sans `?` final ("Tranche de manière définitive.") échappait totalement au garde
  "ceci est une question, pas une affirmation à vérifier". Fix : `?` détecté n'importe où dans
  le texte. Testé contre le scénario exact de l'incident + suite complète (4497 passed, 1 échec
  pré-existant hors-scope — test réseau réel bloqué par le proxy sandbox, sans rapport). **Pas
  encore redéployé/revalidé en direct** — prochaine étape.
- **Trou de robustesse trouvé au passage (12/07) — CORRIGÉ et DÉPLOYÉ (`27c6057`), pas encore
  redéployé sur le VPS.** En creusant le second bug ci-dessus, découverte que
  `llm.py::_post_chat` ne vérifie jamais `finish_reason` de la réponse API — une réponse coupée
  par l'API (`finish_reason=length`, budget `max_tokens` atteint) est affichée telle quelle
  sans aucun signal, ni log ni télémétrie. Observé en direct : une réponse ARIA (test logique
  farming/modus tollens) s'est arrêtée net en plein mot sur Telegram, confirmé par l'opérateur.
  Fix : `_post_chat` journalise un warning + enregistre `truncated=true` dans
  `data/llm-usage/YYYY-MM.jsonl` quand `finish_reason == "length"`. **N'augmente pas les
  budgets `max_tokens`** (pas de preuve suffisante sur la vraie cause en prod sans accès aux
  logs VPS depuis cette session cloud) — rend seulement le phénomène observable pour un vrai
  diagnostic la prochaine fois. Test dédié (`test_truncated_response_logged_and_recorded`),
  suite complète verte.
- **Piège infra multi-repo découvert (12/07) — le `origin` d'une session VPS peut pointer vers
  le mauvais dépôt sans aucune erreur visible.** VPS Research a rapporté un push confirmé
  ("Everything up-to-date", `git ls-remote` positif) vers `claude/trading-psychology-research-temp`
  — la branche n'existait pourtant pas sur `ARIA` (vérifié par le commandement via l'API GitHub
  directe, indépendante du proxy git de session). Cause : `origin` de cette session VPS pointait
  vers `aria-ops`, pas `ARIA` — toutes ses commandes git (`push`, `ls-remote`) étaient donc
  cohérentes... avec le mauvais dépôt. Le rapport de recherche comportementale (psychologie
  trading, cf. entrée dédiée ci-dessus dans le contexte de session) a été retrouvé sur
  `aria-ops` et rapatrié manuellement (copie de fichier + nouveau commit) dans `ARIA` au bon
  emplacement (`docs/aria-learning-inbox/`). **Leçon** : un `git push`/`git ls-remote` qui
  "réussit" dans une session ne prouve PAS qu'il a touché le bon dépôt — vérifier `git remote
  -v` en cas de doute, ou faire confirmer par le commandement via l'API GitHub (indépendante du
  proxy local par session).
- **#117 réellement investigué (12/07) — était marqué "completed" sans aucune preuve, corrigé.**
  Comparaison multi-modèles (VPS Secondaire, script isolé sans impact sur la mémoire/routage
  réel) sur 3 prompts durs du jour, providers réellement configurés (virtuals/Spark,
  grok/xai, groq — openai/openrouter/ollama non configurés, non testables). **Résistance à
  l'injection de prompt : tient sur les 3 providers** (aucun n'obéit à une fausse clause
  d'exemption de garde-fou, tous la signalent explicitement à des degrés divers) — rassurant
  pour le pire scénario (Spark tombe pendant une tentative de manipulation). **Profondeur de
  raisonnement : dégrade réellement sur le fallback documenté.** Sur le scénario VaultX
  (incident de sécurité, décision one-shot), Groq (llama-3.3-70b, fallback par défaut) choisit
  `approve(0)` — exactement l'option que la réponse de référence Spark réfute explicitement
  (laisse 8M exposés dans un wallet compromis) : une vraie erreur de décision opérationnelle,
  pas une nuance de style. Grok/xai rate entièrement le point du prompt TWAP (prend "code
  propre, calcul exact" au pied de la lettre sans questionner l'hypothèse économique
  sous-jacente). **n=2 prompts seulement** — signal réel, pas une preuve statistique, à
  élargir avant de fermer #117 pour de bon. Conséquence pratique bornée : la validation
  humaine déjà obligatoire sur tout capital réel limite le risque immédiat, mais une
  amélioration à considérer (#135, pas urgent) : signaler visiblement dans la réponse quand
  elle vient du fallback plutôt que du provider primaire, pour une prudence accrue de
  l'opérateur si ça arrive en conditions réelles.
- **Correction honnête (12/07) — le pool de sourcing ne "mûrira" pas avec le temps, contrairement
  à ce qui avait été affirmé plus tôt le même segment.** Lecture directe `aria.db::screened_token`
  (VPS Secondaire) : `network='base'` = 110 `pending`, 9 `rejected`, **0 `active`**, aucune
  progression en 5 jours (le plus ancien `pending` date du 07/07). `network='base-bonding'` = 0
  ligne (premier cycle du sourcing bonding activé ce jour pas encore tourné). `vc_weekly_forecast`
  tourne à l'heure (cadence ~48h respectée) mais produit **0 pronostics** depuis 3 cycles —
  cohérent avec un pool actif vide. **Root cause identifiée, pas un problème de délai** : les 110
  `pending` échouent tous sur des critères durs (score sécurité<70, liquidité<30k$, verdict
  CAUTION/DANGER, contrat non vérifié) — le retry différé (#105/#108) aide les échecs *mous* qui
  mûrissent avec le temps, pas ceux-là. `base_crawler` remonte aujourd'hui des tokens qui
  échouent déjà le filtre de sécurité en amont, pas des candidats prometteurs qui ont juste
  besoin de temps. Sans changement du sourcing amont (pré-filtrer sur un minimum de qualité,
  pas juste élargir le débit brut), le forecast automatique restera à 0 pronostics indéfiniment.
  Coordination nécessaire avec l'investigation de diversification du débit de scan déjà en cours
  chez Principal (#134/#136).
- **Diagnostic précis du goulot de sourcing (12/07, Principal) — le pré-filtre de liquidité
  existait déjà, il checke juste la mauvaise source.** `discover_top_pools()` a un plancher
  `min_liquidity_usd=30_000` sur `reserve_in_usd` **GeckoTerminal**, mais le scan réel
  (`acp_onchain_scan.py::scan_base_token`) source sa liquidité via **DexScreener** — deux
  fournisseurs différents, pas garantis d'accord (9/34 échecs liquidité à 0$ au scan alors
  qu'ils avaient passé le seuil GeckoTerminal). Les deux motifs dominants (contrat non
  vérifié 88.2%, holders inconnus 87.3%) ne peuvent pas être pré-filtrés avec les données
  déjà présentes à la découverte — demanderait un appel Blockscout supplémentaire par
  candidat (Volet C, recherche/chiffrage seulement, pas encore décidé). `screened_token` n'a
  aucune colonne `source` — impossible de distinguer un candidat venu de `discover_top_pools`
  (déjà un plancher, juste mal calibré) de `run_radar` (aucun plancher du tout) une fois en
  base — vrai trou d'observabilité. Plan validé (Volet A colonne `source` + Volet B1
  relèvement du plancher avec marge $45-50k) : implémenté par Principal, tests + commit sur
  branche temporaire en cours. **`safety_screen.py` non touché** (décision opérateur déjà
  actée, gates de sécurité intacts).
- **Cron programmé ne se déclenche pas si la session VPS reste active en continu (12/07,
  découverte opérationnelle).** Un job de vérification programmé pour 19:00 UTC n'a jamais
  tourné — Principal était resté actif sans interruption sur un autre travail, et les tâches
  cron de ce type ne se déclenchent qu'en session inactive. Vérification faite manuellement à
  la place. À garder en tête pour toute vérification programmée future sur une session qui
  risque de rester active.
- **Activer `bonding_discovery_cycle` aggrave le bruit tant que le sourcing n'est pas corrigé
  (12/07, premier cycle réel, 18:24Z).** Volet bonding (courbe) : 0 candidat ce cycle. Volet
  "direct" (Clanker/gradués Virtuals → pool standard) : 20 nouveaux candidats absorbés, tous
  `pending`, profil d'échec **pire** que le backlog existant (100% contrat non
  vérifié/score<70/holders inconnus vs 88-95% sur les 110 déjà connus). Confirme l'urgence des
  Volets A+B1 ci-dessus — sans eux, chaque nouveau canal de sourcing ajoute plus de bruit dur,
  pas plus de candidats viables. Suivi dans le rendez-vous de vérification déjà posé (2-3
  semaines).
- **Session cloud 13/07 (marathon complet) — #154 (rollback auto) et #157 (même correctif côté
  vitrine) livrés, testés ET DÉPLOYÉS en conditions réelles sur le VPS.** `deploy.sh` bascule
  désormais en blue-green (port 8000↔8001, conteneur interne toujours 8000) : nouveau conteneur
  lancé et health-checké pendant que l'ancien tourne encore, nginx ne bascule qu'après succès,
  ancien supprimé seulement après revérification du trafic réel. Complété par
  `willfarrell/autoheal` + disjoncteur maison (`vanguard/scripts/autoheal-circuit-breaker.sh`,
  plafond 3 redémarrages/10min). Étape manuelle unique (template nginx upstream + autoheal +
  systemd) faite en direct sur le VPS ce soir. **Vrai bug trouvé en déploiement réel, pas en
  test** : la vérification finale de `deploy.sh` (et de `deploy-vitrine.sh`, #157, même famille)
  tirait un curl immédiatement après `systemctl reload nginx` — reload pas instantané (workers
  mettent un court instant à tourner), donc échec systématique et rollback automatique à
  chaque fois malgré un déploiement réellement sain (reproduit 2 fois à l'identique, confirmé
  par un `sleep 3` manuel qui corrige tout). Fix définitif (boucle `retry_until`, ~10s de
  plafond) en cours côté Principal pour `deploy.sh` au moment de la fin de session (pas encore
  fusionné) ; déjà livré et fusionné côté `deploy-vitrine.sh` (`vanguard/deploy_vitrine_lib.sh`).
  Déploiement réel confirmé cette nuit : backend sur commit `001612d7d8c4` (vérifié
  `/api/health` à travers nginx après correction manuelle du délai), vitrine confirmée par
  timestamp filesystem (`/var/www/ariavanguardzhc`, faute d'accès HTTP à l'époque — voir
  point Basic Auth ci-dessous). `.claude/last-deployed-ref` = `001612d7d8c4` — **plusieurs
  commits mergés APRÈS ce déploiement restent en attente du prochain tour** : #157
  (deploy-vitrine.sh), rayon du blob +25%, 3 correctifs accessibilité (voir plus bas).
- **Authentification HTTP Basic sur l'apex ariavanguardzhc.com — activée puis retirée dans la
  même session (13/07).** D'abord confirmée comme volontaire (décision opérateur explicite :
  "site privé le temps qu'il soit bien construit"), puis reversée quelques échanges plus tard
  ("enleve moi cette authentification elle fait chier tous le monde") — bloc `auth_basic`/
  `auth_basic_user_file` retiré de `/etc/nginx/sites-available/vitrine` (sauvegarde
  `.bak` laissée sur le VPS), `nginx -t && reload` confirmés, `curl` direct revérifié à `200`
  sans identifiants. **Le site est donc de nouveau public.** Piège vécu au passage : la
  vérification de `deploy-vitrine.sh` (#157) exige un `HTTP 200` exact — tant que le Basic Auth
  aurait été actif, CHAQUE futur déploiement de vitrine aurait échoué systématiquement (401 ≠
  200) et restauré l'ancien contenu à chaque fois. Un correctif (vérification filesystem plutôt
  que HTTP authentifié) avait été rédigé pour parer ce cas puis explicitement annulé une fois
  l'authentification retirée pour de bon — **jamais réellement envoyé à Principal** (confirmé
  par l'opérateur après coup), donc `deploy-vitrine.sh` fusionné tel quel (vérification HTTP
  simple), correct tant que le site reste public.
- **Boucle du blob organique cassée (13/07)** : l'animation (particules, ondulation des
  branches, respiration lumineuse, pouls du cœur) était une pure fonction périodique de `time`
  (fréquences fixes) — donc mathématiquement répétitive et perceptible comme telle (~29s pour
  le balancement des particules, ~4.5-14s pour le reste), repérée par l'opérateur en observant
  le site. Corrigé par `organicDrift()` (`OrganismHero.tsx`) : somme de 3 fréquences très basses
  et incommensurables ajoutée à la phase/amplitude des 4 points de boucle identifiés — même
  déterminisme pur-fonction-du-temps (aucun état muté), mais sans période commune courte,
  vérifié analytiquement (le drift diverge nettement à chaque multiple des anciennes périodes
  sur 10 min de simulation). Rayon global du blob (branches nav + décoratives) augmenté de +25%
  le même soir (décision opérateur explicite, facteur d'échelle 1.2→1.5).
- **Chat du widget site recadré sur Vanguard/ARIA/ZHC/BASE (13/07)** : `/aria/chat`
  (`app/api/routes/aria.py`) redirige désormais toute question d'actualité générale
  (`is_live_info_question`, réutilisé de `web_verify.py`) sans mot-clé du périmètre vers une
  réponse de recadrage bilingue immédiate, sans jamais appeler le cerveau ni faire de recherche
  web — Telegram (`public_mode`, même `aria_brain.process`) non touché, filtre localisé à cette
  seule route REST.
- **#155 (ux_watch) livré, fusionné et déployé (gate OFF)** : `skills/ux_watch.py` capture le
  site réel (Playwright, desktop+mobile) une fois par jour maximum, lit visuellement via
  `llm_vision.vision_analyze` (brique déjà câblée pour l'avatar, pas `ARIA_VISION_ENABLED` —
  gate propre à la photo Telegram admin-only, sans rapport), compare au référentiel UX gamme
  luxe (CLAUDE.md, Normes permanentes) et PROPOSE une issue GitHub groupée (`aria-ux-proposal`)
  — jamais un commit/refonte/fusion autonome. Playwright+Chromium ajoutés au Dockerfile
  (+300-500 Mo mesurés, même doctrine que ffmpeg/#23, accepté tel quel).
- **Audit accessibilité de la nouvelle page d'accueil (blob) + 3 correctifs, livrés et fusionnés
  (13/07)** : 1) les 4 nœuds-actions (Événements/Méthodologie/Accès membre/Telegram) étaient des
  `<a href="#">` qui n'activaient qu'au Enter, jamais à l'Espace — convertis en vrais
  `<button type="button">` (Cockpit/Track record, vraie navigation, restent des `<a>`) ; 2) le
  curseur "Ambiance" pouvait atteindre un gris moyen (~44-50%) sous 4.5:1 WCAG AA contre le
  blanc ET le noir en même temps — remappé sur deux plages disjointes (blanc sûr ≤43%, noir sûr
  ≥51%), vérifié empiriquement sans échec sur les 101 positions du slider (pire cas 4.665:1) ;
  3) `.ao-market-live .ao-live-dot` ne respectait pas `prefers-reduced-motion` (oubli, même
  patron que `.ao-pc-dot` ajouté). Contrastes du texte principal et focus clavier déjà
  conformes (vérifié, pas supposé) — pas un audit qui n'a rien trouvé, juste que le plus gros
  était déjà bon.
- **CI (scan de secrets) : 2 vrais faux positifs corrigés, root cause d'un rouge systématique
  sur `main` et toutes les branches depuis le merge de #60 (13/07).** 1) `.secrets.baseline` :
  `vanguard/backend/tests/test_aria_bonding_pool.py` réutilisait la valeur factice `"s3cr3t"`
  déjà connue/auditée ailleurs (`test_security_hardening.py`), jamais enregistrée au baseline —
  régénéré (`detect-secrets scan`), vérifié une seule addition exacte, zéro suppression,
  confirmation opérateur explicite avant modification (fichier garde-fou, classifieur bloqué à
  raison sur la première tentative). 2) `scripts/safe-push.sh` : un commentaire mentionnant
  `git\@github.com` (syntaxe SSH, pas un email) matchait la regex du scanner PII (#142) —
  reformulé (`git\@github.com`) sans toucher au garde-fou `ALLOWLISTED_EMAILS`. CI repassée
  verte, vérifié sur GitHub Actions après coup.
- **Suppression de branche distante bloquée par la politique d'egress du proxy de session, pas
  par le classifieur (13/07)** : `git push origin --delete <branche>` sur une branche déjà
  fusionnée à l'identique a échoué en HTTP 403 (log proxy : "non autorisé par la politique de
  l'organisation, ne pas contourner, remonter le blocage") — contenu sans risque, mais l'action
  elle-même reste impossible depuis cette session cloud. L'opérateur l'a supprimée lui-même sur
  GitHub (`/branches`, icône corbeille) en quelques secondes.
- **Suivi #133/#134 (bonding/scan) — précision opérateur (13/07 soir)** : ~17 jours avant
  l'activation prévue des vrais achats dans le faux wallet 1M$ (paper-trading, déjà gaté ON
  depuis le 08/07) pour produire des valeurs de performance réelles. À revérifier avant cette
  échéance que le pool produit enfin des candidats `active` (0% de conversion constaté le
  11/07) — sinon la fenêtre se refermera sur un pool toujours vide.
- **Norme de process — tester tout nouveau client d'API externe contre un VRAI appel avant de
  le considérer terminé (14/07, incident #157 smart-wallet-scoring).** Bug réel confirmé ce
  soir : `blockscout.py::_parse_token_transfer` lisait `token.get("address")`, mais la vraie
  API Blockscout v2 renvoie le champ sous `address_hash` — `token_address` était donc TOUJOURS
  `None`, quel que soit le wallet. Ce bug existait depuis la construction initiale de l'analyse
  smart-money (bien avant ce soir), invisible parce que **tous les tests mockaient déjà le
  mauvais nom de champ** (`"address"` au lieu de `"address_hash")` — ils validaient un schéma
  imaginaire, jamais le vrai comportement de l'API. La dégradation douce (jamais bloquant,
  doctrine `AGENTS.md`) a caché le problème au lieu de le signaler : l'ancienne analyse
  smart-money tournait à vide silencieusement depuis sa mise en place. Trouvé seulement parce
  qu'un vrai test `/walletscore` en conditions réelles sur un vrai wallet a donné "0 tokens"
  alors qu'un `curl` direct montrait 50+ transferts ERC-20 réels. **Réflexe désormais
  obligatoire** : pour tout nouveau client d'API externe (ou toute nouvelle méthode dessus),
  vérifier au moins UNE FOIS le nom exact des champs contre un vrai appel réel (`curl` sur le
  VPS, qui a un accès réseau réel contrairement à cette session cloud) avant de considérer la
  fonctionnalité terminée — ne jamais faire confiance à un mock auto-cohérent écrit de mémoire
  sans l'avoir confronté à la réalité au moins une fois. Correctif + tests re-mockés sur le vrai
  schéma : commit `85e4c16`.
- **Diligence capital réel étape 2 — MetaMask Agent Wallet retenu, comparatif concurrents fait
  (14-15/07).** Après diligence Velvet Unicorn/eToro/IBKR (résumée plus haut), l'opérateur a
  tranché en faveur de **MetaMask Agent Wallet** (self-custodial, ERC-7710/7715, CLI `mm`,
  compatible Claude Code nommément cité dans sa doc, DEX-natif — swap/bridge/perps/prediction
  markets/Aave, 25+ chaînes EVM) — accès anticipé déjà demandé par l'opérateur, pas encore
  ouvert. Comparatif élargi (15/07) aux concurrents directs de même catégorie : **Coinbase
  Agentic Wallets** (MPC+enclave AWS Nitro, `npx awal`, gratuit à créer, testable dès ~20$ USDC,
  MCP officiel compatible Claude, x402 natif — semble accessible immédiatement, pas d'accès
  anticipé identifié), **Trust Wallet Agent Kit** (non-custodial, couverture chaînes la plus
  large des trois : EVM+Solana+BTC+Cosmos+TON+Aptos+Tron+NEAR+Sui, accès via
  `portal.trustwallet.com`), **Cobo Agentic Wallet** (MPC 3 parts, système "Pact" plus formel,
  positionné enterprise) et la brique standard sous-jacente **Safe + ERC-4337** (session keys
  EIP-7702). **Constat commun aux quatre, pas spécifique à MetaMask** : tous fonctionnent sur le
  principe plafond + liste blanche accordés une fois, puis autonomie dans ces bornes — jamais
  une confirmation humaine par transaction individuelle, sauf sortie du cadre (2FA/escalade).
  Détail complet (adresses, wallets créés, sources) volontairement dans **`aria-ops`** (privé),
  jamais dans ce repo : `docs/aria-learning-inbox/2026-07-14-metamask-agent-wallet-decision.md`,
  `2026-07-14-velvet-unicorn-wallet-pilote.md` (superseded), `2026-07-14-agent-wallets-concurrents-metamask.md`.
- **Proposition opérateur (15/07) — pilote capital réel ~10$ sur l'agent-wallet retenu, PAS
  ENCORE implémenté, produit final pas encore choisi.** Raisonnement opérateur : montant assez
  bas pour n'avoir aucune conséquence réelle en cas d'erreur, sert à calibrer avant le vrai
  palier. Ma position (actée avec l'opérateur, pas encore tranchée définitivement) : ce n'est
  pas le montant qui compte mais le précédent — ce serait la première fois que le code d'ARIA
  (pas un tiers isolé comme Arena #60, pas un testnet comme Sepolia) déplacerait du capital réel
  mainnet sans clic Telegram par transaction. Si ça se fait, ça doit être traité avec la même
  rigueur que l'exception Sepolia : **nommé explicitement** comme exception bornée (pas noyé
  dans "un test tranquille"), **plafond dur codé** (vérification de solde avant chaque
  transaction, pas une confiance dans le réglage UI de l'outil), **aucune capacité de transfert
  libre** (swap uniquement ; un retrait éventuel vers UNE SEULE adresse pré-enregistrée, jamais
  un champ libre — le transfert est le vrai vecteur de vol, pas le swap), **slippage ≤10%
  explicite et codé en dur** (règle absolue déjà actée le 09/07, jamais la valeur par défaut de
  l'outil), **kill-switch = `/stop` existant** (à vérifier qu'il coupe bien ce chemin), et le
  log de transactions décrit ci-dessous. Rien à construire tant que le produit n'est pas choisi
  et que l'opérateur n'a pas donné le "go" sur ce plan complet.
  **Plan complet rédigé le 15/07** (choix de produit, garde-fous, module à construire,
  question d'interprétation) : `docs/pilote-agent-wallet-10usd.md` — statut PLAN SEULEMENT,
  rien codé/activé, en attente du "go" opérateur (et du choix Coinbase maintenant vs attendre
  l'accès anticipé MetaMask).
- **#158/#159 — EN LIGNE (codé, testé, PAS déployé), 15/07 : diagnostics dédiés lisibles
  directement depuis le cloud, sans dépendre d'une session VPS.** Suite directe de la Routine
  cassée ci-dessous et de la demande opérateur d'un log de transactions agent-wallet. Deux
  nouveaux endpoints admin-gatés par un **token dédié** `ARIA_DIAGNOSTIC_TOKEN` (header
  `X-Diagnostic-Access`), distinct du secret admin ET du token relay (`ARIA_RELAY_ACCESS_TOKEN`)
  — même patron exact que `relay_chat.py`/`verify_relay_access` : si ce token fuit un jour dans
  une conversation Claude Code, le pire cas est "quelqu'un lit un journal", jamais "quelqu'un
  valide une dépense". Fail-closed (403 sans token configuré), routes ajoutées à
  `VANGUARD_PUBLIC_ROUTES` (exemptées du gate Privy/opérateur, comme `relay/*`, car elles se
  protègent elles-mêmes) :
  - `GET /api/aria/diagnostics/pool-status` (`vanguard/backend/app/api/routes/aria.py`) — compte
    `screened_token` par statut sur `network='base'` et `'base-bonding'`, + les 3 candidats
    `pending` les plus proches du seuil de sécurité via la nouvelle
    `screened_pool.list_closest_to_passing()` (score le plus haut d'abord, puis liquidité la
    plus proche de 30 000$). Remplace le dispatch VPS manuel pour le check quotidien du pool.
  - `GET /api/aria/diagnostics/agent-wallet-ledger` — lit le nouveau
    `aria_core/agent_wallet_log.py` (append-only, même doctrine que `bonding_trade_log.py`/
    `aria_directive_log` : aucune fonction UPDATE/DELETE, enregistre CHAQUE tentative ok/failed/
    blocked, pas seulement les succès). **Seam vide pour l'instant** — aucun pilote agent-wallet
    n'est encore câblé dessus (MetaMask/Coinbase/Trust Wallet pas encore choisi), `record_transaction()`
    attend d'être appelé le jour où le pilote #10$ (voir ci-dessus) sera construit.
  - **Bug réel corrigé au passage** (découvert en construisant `list_closest_to_passing`) :
    `screened_pool.record_pending()` codait en dur `liquidity_usd=0`/`security_score=0` même
    quand l'appelant (`token_absorber.absorb`, échec mou APRÈS un scan complet) avait déjà les
    vraies valeurs en main — un candidat pending prometteur (score 78, liquidité 50k$) était
    donc indiscernable d'un candidat sans aucun signal. Corrigé : `record_pending()` accepte
    désormais `liquidity_usd`/`security_score`/`verdict` optionnels (défaut 0/'' préservé pour
    l'appelant sans scan, ex. pré-filtre Volet C — jamais une donnée inventée), câblé dans
    `token_absorber.py` et `bonding_absorber.py` (ce dernier sans `liquidity_usd`, neutre par
    construction sur courbe de bonding). Sans ce correctif, l'endpoint pool-status aurait renvoyé des
    "candidats les plus proches" indiscernables les uns des autres (tous à 0/0).
  - Suites complètes vertes après coup : 4880 passed (aria-core), 108 passed (vanguard/backend).
    **Rien déployé** — regroupé avec le prochain déploiement.
- **Découverte 15/07 — une Routine cloud créée le 14/07 sans confirmation opérateur explicite,
  résolue par #158 ci-dessus.** La session cloud "commandement" avait créé (probablement lors
  d'un tour antérieur, avant un compactage de contexte — jamais confirmé explicitement à
  l'opérateur) une Routine récurrente ("Vérif pool sourcing 24h ARIA", quotidienne 09:00 UTC /
  11:00 heure française) qui réinjecte automatiquement dans cette même session le texte de la
  demande de check quotidien du pool `screened_token`. Limite identifiée : cette session cloud
  n'a pas d'accès réseau direct au VPS/à la vraie base `aria.db` — la Routine ne pouvait donc
  pas exécuter le check elle-même. **Réglé par #158** : une fois `ARIA_DIAGNOSTIC_TOKEN` déployé
  et configuré, la Routine (ou une future version d'elle) pourra appeler
  `/api/aria/diagnostics/pool-status` directement en HTTPS, sans dispatch VPS. Leçon retenue :
  créer une automatisation récurrente (`create_trigger`) est une action durable qui doit être
  confirmée à l'opérateur AVANT d'être programmée, pas découverte après coup dans l'UI — pas
  fait correctement ici. **Décision opérateur toujours en attente** : garder cette Routine
  (à reconfigurer pour appeler le nouvel endpoint une fois déployé) ou la supprimer.
- **15/07 — opérateur SANS accès VPS pour l'instant, tout passe par la session cloud
  (commandement) jusqu'à nouvel ordre.** Conséquence directe : la génération de
  `ARIA_DIAGNOSTIC_TOKEN` + son ajout au `.env` + le déploiement de #158/#159 ci-dessus
  restent en attente (nécessitent un accès VPS réel) — noté ici pour ne pas le perdre au
  prochain compactage, à reprendre dès qu'une session VPS (Principal/Secondaire/Research)
  ou un accès opérateur direct redevient disponible. En attendant, toute nouvelle demande
  se traite depuis cette session cloud avec ses limites connues (pas d'accès réseau
  direct au VPS/`aria.db`, curl/WebFetch seulement vers l'extérieur).
- **15/07 — activer `ARIA_VISION_ENABLED` en attente d'accès VPS (demande opérateur
  explicite, même segment).** Aucun code à changer — la fonctionnalité vision (lecture
  d'images Telegram, admin-only) est déjà livrée et testée en conditions réelles (10/07,
  vrai graphique DexScreener, chiffres lus correctement). Juste ajouter
  `ARIA_VISION_ENABLED=true` au `.env` du conteneur `aria-api` puis redémarrer (pas besoin
  de rebuild, une variable d'environnement lue à l'appel, pas au chargement du module) —
  bloqué par le même manque d'accès VPS que ci-dessus. Coût : un appel LLM vision par
  image envoyée, mais admin-only donc maîtrisé.
- **15/07 — #157 suite : scan `/walletscore` incrémental + formule composite de
  classement, EN LIGNE (codé, testé, poussé sur main, PAS déployé).** Trois commits :
  `128556d` (scan persistant), `0125c74` (formule composite). Le plafond
  `WEIGHTS.max_tokens_analyzed` ne pouvait jamais couvrir un wallet très actif (680
  tokens) en un seul appel — nouveau module `wallet_scan_state.py` persiste par wallet
  quels tokens ont déjà été analysés (+ leurs trades archivés) et la date du dernier
  scan ; chaque appel `score_wallets()` traite le prochain lot de tokens jamais vus ou
  dont l'activité a évolué, jusqu'à couverture complète, puis ne rafraîchit que
  l'activité nouvelle. Le score (win rate/PnL/Sortino/drawdown/diversification) se base
  sur TOUS les trades archivés, pas seulement le dernier lot. Décisions opérateur
  explicites ajoutées : échantillon minimum avant classement fiable (≥90j d'ancienneté
  ET ≥100 swaps, `sample_size_sufficient`) ; robustesse anti-chance (retire les 10
  meilleurs ET 10 pires trades, vérifie si le reste est positif, `robust_pnl_positive`,
  indisponible sous 30 trades clôturés) ; courbe de santé dans le temps (compare 2e
  moitié chronologique à la 1ère, `health_trend` amélioration/stable/dégradation,
  indisponible sous 10 trades) ; classement comparatif (percentile de chaque wallet
  parmi tous les AUTRES déjà notés dans `wallet_score_log`, jamais lui-même — composite
  = moyenne de win rate/Sortino/PnL/diversification UNIQUEMENT, la durée de détention
  reste un percentile contextuel séparé car ce n'est pas un axe "meilleur si plus haut"
  sans ambiguïté). Recherche externe faite avant de coder (arxiv zScore wallet
  reputation scoring : sous-catégories plafonnées, aucun trait ne domine — méthode
  reprise ici). 27 nouveaux tests, suite complète verte (4900 passed). **Rien déployé.**
- **15/07 — limite méthodologique réelle trouvée (question posée via Gemini, relayée
  par l'opérateur) : le cost-basis d'un token reçu par simple virement (pas un swap)
  n'est PAS mis à zéro, contrairement à ce qu'on pourrait attendre.** Vérifié dans
  `_hash_based_price`/`_analyze_wallet_multi_token` : un "achat" = N'IMPORTE QUEL
  transfert entrant (swap, virement, airdrop — aucune distinction à ce stade). Le prix
  d'entrée est résolu ainsi : (1) ratio exact stablecoin/token si la MÊME transaction a
  une jambe stable touchant le wallet (vrai swap) ; (2) SINON, repli sur le **prix de
  marché (OHLCV)** au moment du transfert — traite un virement gratuit comme s'il avait
  été acheté au prix du marché, jamais comme un coût nul. Impact réel : un token reçu
  gratuitement (airdrop) puis revendu **sous-estime** le vrai gain du wallet (on
  soustrait un "prix payé" qui n'a jamais existé). Ce repli reste correct pour le cas le
  plus fréquent (swap token-à-token sans jambe stable dans la tx), donc pas à supprimer
  — juste incomplet pour le cas virement pur. **Piste de correction identifiée, PAS
  encore construite** : la transaction complète est déjà récupérée par
  `_hash_based_price` — si elle ne contient QUE le transfert entrant seul (aucune autre
  jambe, pas juste "pas de stablecoin"), c'est un signal fort d'un `transfer()` simple
  plutôt qu'un swap → le prix d'entrée pourrait alors être fixé à 0$ au lieu du prix de
  marché. Faisable sans appel réseau supplémentaire (la donnée est déjà là). Décision
  opérateur à prendre avant de construire.
- **15/07 — revue croisée multi-IA du `/walletscore` (Gemini x2, ChatGPT, Grok, 18
  angles morts relevés au total) — 6 correctifs réels construits, testés et déployés
  dans le code (commit `8565d62`), reste hors scope documenté honnêtement.** Méthode :
  chaque point a été VÉRIFIÉ contre le vrai code avant d'agir (pas pris pour argent
  comptant) — 2 affirmations de Gemini se sont révélées fausses une fois confrontées au
  code (mécanisme exact du "dust scam" erroné — les deux jambes utilisent la même série
  OHLCV, pas un $0 fabriqué à l'achat ; division par zéro du Sortino déjà gérée par un
  garde existant). **Corrigés** : (1) trim anti-chance en POURCENTAGE plutôt qu'en compte
  fixe (`robust_trim_pct`, remplace `robust_trim_count`) — un compte fixe de 10 se dilue
  de 33% à 0.05% du volume selon N, laissant passer un trade chanceux noyé derrière assez
  de micro-trades (les 3 IA convergent indépendamment sur ce point, signal fort) ; (2)
  exclusion des jambes wrap/unwrap ETH<->WETH (mint/burn depuis/vers l'adresse zéro sur
  le wrapped-native connu) du compteur `min_total_swaps` — fermait un exploit à coût
  quasi nul (quelques centimes de gas) pour débloquer artificiellement le seuil de
  fiabilité sans jamais prendre de risque de trading ; (3) plancher de liquidité
  confirmée (`min_pool_liquidity_usd_for_pricing`, même 30k$ que `safety_screen`) avant
  de faire confiance à un prix OHLCV — défense anti-dust/scam-pool, fail-open si la
  liquidité est inconnue (jamais un défaut de donnée traité comme liquidité nulle) ; (4)
  `price_confirmation_ratio` + drapeau `price_confidence_low`, affiché À CÔTÉ du score
  (jamais en cachant win_rate/PnL, même doctrine que `sample_size_sufficient`) ; (5)
  `unmatched_sell_events` — ventes dont la queue FIFO d'achats s'épuise (signal de
  rebase/rendement DeFi type stETH), jamais crédité comme profit, juste rendu visible ;
  (6) diversification pondérée par capital engagé, en complément (pas en remplacement)
  du ratio par comptage de tokens. **Documenté, délibérément non corrigé** (trop coûteux/
  complexe pour un correctif ponctuel) : coordination Sybil/multi-wallets (le plus
  important des points non résolus, confirmé par recherche externe — Nansen/Arkham/
  Chainalysis/TRM s'appuient sur la même famille de clustering par source de financement
  que notre `_pairwise_convergence` existant, mais à l'échelle d'un graphe sur toute la
  population suivie, pas juste pairwise entre 1-3 wallets soumis ensemble) ; absence de
  benchmark marché (alpha vs bêta) ; gaming structurel des tests de robustesse ; MEV/
  arbitrage atomique/flash loans ; biais de survie du gate d'échantillon ; choix
  méthodologique FIFO vs LIFO/HIFO (assumé, pas un défaut) ; paradoxe du percentile
  (population de comparaison non représentative, dérive avec la démographie des
  utilisateurs de l'outil) ; découpage de la courbe de santé par nombre de trades plutôt
  que par fenêtre calendaire. Tout est écrit noir sur blanc dans un bloc dédié de
  `smart_money.py` (« LIMITES STRUCTURELLES CONNUES »), jamais laissé implicite. 10
  nouveaux tests ciblés (99/99 sur ce fichier, 4912/4912 sur la suite complète).
  **Patron réutilisable pour l'avenir (noté explicitement par l'opérateur)** : ces 6
  correctifs ne sont pas propres au wallet-scoring — ce sont des mécanismes de défense
  génériques qu'ARIA retrouvera face à d'autres sources de données manipulables une fois
  plus autonome : (a) ne jamais faire confiance à un prix/signal sans un plancher de
  qualité confirmée (liquidité ici, transposable à tout flux externe) ; (b) fail-open sur
  une donnée INCONNUE, fail-closed seulement sur une donnée CONFIRMÉE mauvaise — jamais
  l'inverse ; (c) afficher un ratio de confiance À CÔTÉ d'un score plutôt que de le
  cacher ou de refuser de scorer ; (d) un seuil/plafond ANTI-CHANCE doit scaler avec la
  taille de l'échantillon (pourcentage), jamais un compte absolu qui se dilue ; (e)
  documenter honnêtement ce qui reste un vrai trou plutôt que de prétendre l'avoir
  fermé avec un correctif cosmétique.
- **15/07 (suite, même soirée) — rounds 4/5/6 de la revue croisée (`236af77` → `4ba693e`),
  jusqu'à 5 correctifs/précisions de plus, marathon wallet-scoring terminé pour cette
  session.** Précisions de portée (documentation seule, pas de code) : migrations/
  redénominations/splits de token (ChatGPT) décomposées en les 2 limites DÉJÀ écrites
  (DeFi/pont si nouveau contrat, rebasing si même contrat) plutôt qu'un 3e mécanisme ;
  incohérence "consolidé multi-chaînes" vs "ponts cross-chain non reliés" (ChatGPT)
  corrigée dans la docstring de `score_wallets` — "consolidé" = métriques agrégées par
  wallet, JAMAIS continuité du cost-basis à travers un bridge. **2 vrais bugs trouvés et
  corrigés** (pas des limites résiduelles) : (1) **immunité aux rug pulls** (Gemini) — le
  plancher de liquidité (#160) bloquait aussi la valorisation d'une VENTE, faisant
  disparaître des stats la perte réelle d'un rug pull (pool effondré au moment du scan)
  au lieu de la comptabiliser. Corrigé : plancher désormais ASYMÉTRIQUE (gate l'achat
  uniquement). Portée honnête : ne résout que le cas où l'achat a un prix confirmé par
  hash (indépendant de la liquidité actuelle) — si achat ET vente dépendent du seul
  instantané de liquidité, le trade reste non clôturé (même root cause que la
  vulnérabilité dusting ci-dessous : aucune donnée de liquidité historique disponible).
  (2) **régression trouvée EN CONSTRUISANT le correctif ci-dessus** (jamais déployée) :
  la 1ère version confondait "pool résolu mais trop peu liquide" avec "pool non résolu
  du tout" — bloquait à tort les achats recouvrés par CoinMarketCap (3e couche de
  pricing). Corrigé, verrouillé par test. **1 correctif méthodologique** : `_latest_
  scored_wallets` exclut désormais les fiches `full_coverage=False` de la population de
  comparaison percentile (Gemini — un score partiel/scan incomplet ne doit jamais servir
  de référence pour juger un wallet à couverture complète). **1 vulnérabilité confirmée
  restée non résolue, documentée en détail** : dusting sur pool manipulé (Gemini) — un
  pool créé juste au-dessus du plancher ($35k) avec un prix ponctuel manipulé peut encore
  faire accepter un cost-basis démesuré ; ma 1ère piste de correctif (réutiliser
  `_pool_is_plausible` existant) testée et REJETÉE après vérification (cette fonction
  traite délibérément un volume quasi nul comme non-disqualifiant, exactement le profil
  d'un pool de scam) — un vrai correctif (détection de pic isolé vs bougies voisines, ou
  corroboration de marché indépendante) reste un chantier séparé, pas fait ce soir, risque
  réel de nouveaux faux positifs à traiter avec plus de rigueur. **Documenté** : rebase
  négatif (symétrique du positif déjà géré, jetons fantômes dans la file FIFO → profit
  fictif) ; wash-trading en petit cluster coordonné 2-5 wallets (Gemini+Grok convergents,
  contourne simultanément le seuil 60% ET la convergence pairwise — même famille que
  Sybil, pas un correctif de seuil possible). 7 nouveaux tests ce dernier lot, suite
  complète verte (4920/4920). Tout est dans `smart_money.py` (commit `4ba693e`) — plus de
  20 correctifs réels au total sur ce chantier ce soir (#158 à #172), documentation
  exhaustive des limites structurelles restantes (Sybil au-delà de la convergence
  pairwise reste LA plus importante, jamais résolue, chantier séparé si repris).
- **15/07 (fin de soirée) — délégation opérateur explicite : Claude Code (moi) reçoit
  désormais et DÉCIDE seul des propositions `aria-knowledge-proposal` (issues GitHub
  générées par `knowledge_inbox.py`), au lieu que l'opérateur les relise une à une.**
  Rodé en direct sur l'issue #29 (diligence Flaunch/Zora Coins, générée depuis
  `docs/aria-learning-inbox/2026-07-13-diligence-flaunch-zora-coins.md`) : vérifié le
  contenu par recherche externe indépendante AVANT toute décision (Flayer Labs/Joel
  Strahl, API flaunch-sdk, listing FLAY/LBank, Jacob Horne ex-Coinbase co-fondateur Zora,
  incident "Base is for everyone" ~17M$→-90/95% — tous confirmés). **Trouvé un vrai
  problème dans la proposition elle-même** : le fichier cible qu'elle visait
  (`truth_ledger/canonical_facts.yaml`) ne correspondait ni à son schéma réel
  (`id`/`topic`/`tags`/`question`/`answer`, pas `id`/`fact`/`source`/`confidence`) ni à sa
  portée (identité/produit d'ARIA, pas de la diligence marché externe) — l'opérateur a
  explicitement autorisé une **contre-proposition** plutôt qu'un accept/reject binaire.
  Intégré à la place dans `knowledge/launchpads.yaml` (commit `04b54f4`, registre déjà
  dédié aux launchpads Base où `flaunch`/`zora` existaient déjà) : ajout des faits
  fonctionnellement utiles (frais de swap Flaunch, Sniper Tax/vesting/frais Zora) dans
  les `norms:` existants, jamais la narration complète (hors doctrine du fichier). Une
  erreur d'édition (bloc `clanker` dupliqué) trouvée et corrigée avant commit — vérifié
  par un chargement YAML réel, pas juste une relecture visuelle. Issue #29 close avec un
  commentaire expliquant précisément le raisonnement (jamais un rejet silencieux).
  **Ce protocole (vérifier par recherche externe → juger schéma/portée du fichier cible →
  intégrer correctement OU contre-proposer avec justification → clore l'issue avec
  explication) est désormais la référence pour toute future proposition
  `aria-knowledge-proposal`** — à appliquer systématiquement, pas seulement ce soir.
- **15/07 (suite, même soirée) — round 2/3 de la revue croisée (`8565d62` → `236af77`), 3
  correctifs de plus.** Swaps stable<->stable (USDC/USDT/DAI) exclus du compteur de swaps
  (même exploit que wrap/unwrap, réutilise le registre stablecoin existant) ; métriques
  sur fenêtre récente (`win_rate_recent`/`realized_pnl_usd_recent`, 90j par défaut, en
  complément jamais en remplacement — répond au biais temporel : un wallet dégradé
  récemment restait masqué par un historique agrégé favorable) ; le "fail-open sur
  liquidité inconnue" soulevé par Gemini comme faille de sécurité a été vérifié PUIS
  clarifié comme non exploitable en pratique — le vrai client GeckoTerminal ne renvoie
  jamais `None` pour une réserve manquante (retombe sur `0.0`, qui échoue déjà le
  plancher), verrouillé par un test dédié. La division par zéro du Sortino, réaffirmée
  deux fois par Gemini, reste fausse contre le code (déjà gardée). Limites ajoutées à la
  documentation : paires LST/wrapped non couvertes (stETH/wstETH, WBTC/tBTC — registre
  hors de portée) ; la dilution du trim anti-chance par micro-trades est plus étroite
  qu'annoncée (tri par PnL en dollars, pas en %, donc un padding "gratuit" ne suffit pas
  à faire sortir un trade légendaire du tri sauf s'il est lui-même de faible montant en
  dollars) ; pondération égale par trade de win_rate/trim/health_trend assumée (pas un
  oubli) ; manipulation possible du point de bascule de la courbe de santé. 6 nouveaux
  tests, suite complète verte (4917/4917).
- **15/07 (nuit) — angle mort ChatGPT fermé (`4dfdf60`) + 4e IA (DeepSeek) traitée dans la
  foulée : marathon wallet-scoring vraiment clos pour cette session.** ChatGPT avait relevé
  un « angle mort de comparabilité » : `price_confirmation_ratio`/`price_confidence_low`
  (correctif précédent) restait purement informationnel, sans jamais influencer le
  percentile lui-même — un wallet à 95% de prix ESTIMÉS (OHLCV) pouvait recevoir un
  classement aussi confiant qu'un wallet à prix majoritairement CONFIRMÉS (hash exact).
  Corrigé sur le même patron que l'exclusion `full_coverage=False` (#172) : un wallet
  `price_confidence_low=True` est désormais exclu de la population de comparaison
  percentile, et une ligne ATTENTION s'affiche à côté du percentile quand le wallet SCORÉ
  lui-même a une confiance basse (jamais caché, doctrine constante de ce chantier). 3
  nouveaux tests. **Régression trouvée en cours de route** : un test pré-existant
  (`test_winning_wallet_ranks_above_a_previously_scored_loser`) utilisait des fixtures
  100% OHLCV (aucune jambe hash-exacte) — avec le nouveau filtre, les deux wallets de test
  devenaient `price_confidence_low=True` et s'excluaient mutuellement, cassant un test qui
  visait le MÉCANISME de percentile, pas la confiance de prix. Corrigé en donnant à ce test
  des jambes hash-exactes (tx_hash + jambe stablecoin mockée) reproduisant les mêmes PnL
  qu'avant — le test mesure de nouveau ce qu'il est censé mesurer, sans être un faux
  négatif du nouveau garde-fou.
  **Revue DeepSeek (4e IA, immédiatement après)** : 5 points, triés comme les rounds
  précédents. **1 correction de ma propre documentation** : le commentaire sur le plancher
  de liquidité asymétrique affirmait qu'une vente « ne fait jamais que révéler un prix réel,
  jamais fabriquer un gain » — faux en général (seulement vrai pour le sous-cas rug-pull
  qu'il visait) : un prix de VENTE lu sur un pool à la liquidité manipulée (pump ponctuel
  plutôt que dump) peut tout aussi bien gonfler un PnL réalisé fictivement — miroir exact
  de la vulnérabilité dusting déjà documentée, côté gain plutôt que perte. Reformulé
  honnêtement dans le code. **2 angles morts réels et nouveaux, documentés (pas corrigés,
  même arbitrage que Sybil/benchmark alpha déjà différés)** : (a) drawdown/Sortino ne lisent
  que le PnL RÉALISÉ (`closed_trades`) — une position ouverte massivement en perte latente
  affiche un risque nul tant qu'elle n'est pas vendue ; corriger exigerait un vrai système
  de mark-to-market (prix courant + coût moyen pondéré de la file FIFO restante +
  redéfinition de ce que « drawdown » mesure) — chantier séparé, pas un ajustement de
  seuil ; (b) le trim anti-chance (tri par PnL $, retire les extrêmes des deux côtés) peut
  produire un FAUX NÉGATIF sur un style de trading légitimement concentré (conviction
  sizing/barbell — quelques gains extrêmes assumés, beaucoup de petites pertes coupées
  vite) : ses meilleurs trades légitimes se font trimmer et le reste paraît à tort « non
  robuste » — distinguer chance isolée de conviction assumée exigerait un signal que
  l'historique on-chain seul ne fournit pas. **1 clarification de portée** : `price_
  confirmation_ratio` mesure la confiance de MÉTHODE (prix exact vs. estimé), pas la
  résistance à la manipulation de marché — un axe orthogonal, une jambe « confirmée » à
  100% reste vraie, une jambe estimée peut être saine ou manipulée sans que le ratio le
  distingue (la vulnérabilité sous-jacente est déjà la dusting documentée, pas un nouveau
  mécanisme). **1 point déjà couvert, écarté sans y retoucher** : le plafond de tokens
  analysés par passage (`max_tokens_analyzed`) est déjà documenté comme biais de sélection
  de couche 2 (round 4, ChatGPT) et neutralisé côté percentile par `full_coverage` (#172) —
  pas un trou supplémentaire. Suite complète verte (4923/4923). **Bilan du marathon** :
  plus de 20 correctifs réels construits (#158 à #175) + une documentation honnête et
  exhaustive des limites structurelles qui restent de vrais chantiers séparés si jamais
  repris (Sybil/clustering d'entité au-delà de la convergence pairwise = LE plus important ;
  dusting sur pool manipulé ; mark-to-market des positions ouvertes ; benchmark alpha vs
  bêta) — le patron de défense (plancher de qualité confirmée, fail-open sur inconnu/fail-
  closed sur mauvais confirmé, ratio de confiance affiché jamais caché, seuil anti-chance
  qui scale avec l'échantillon, documentation honnête plutôt que correctif cosmétique) reste
  la référence réutilisable pour toute future source de données manipulable qu'ARIA
  branchera, comme noté par l'opérateur.
- **15/07 (nuit, suite) — un vrai bug corrigé (Gemini), un vrai biais documenté (DeepSeek
  round 2), marathon wallet-scoring définitivement clos (`7ab29a6`).** Gemini a trouvé le
  dernier angle mort sérieux : une panne D'INFRASTRUCTURE GeckoTerminal (timeout/429/erreur
  serveur, ponctuelle, déjà retentée plusieurs fois avant d'abandonner) lors de la résolution
  du pool d'un token pouvait se figer en **cicatrice permanente** — le scan incrémental
  persistant (`wallet_scan_state.py`, #157 suite) ne re-tente un token déjà "vu" QUE si son
  activité on-chain a changé, jamais sur la simple disparition d'une panne réseau. Une simple
  coupure d'une seconde pendant un scan en arrière-plan condamnait donc une jambe à rester
  "sans prix" pour toujours, faussant durablement le PnL ET `price_confirmation_ratio` du
  wallet. **Vérifié avant de coder** : `resolve_primary_pool` (`geckoterminal.py`) distingue
  déjà, EN TEXTE, un verdict de DONNÉE légitime ("aucun pool trouvé pour ce token") d'une
  panne d'infrastructure (tout échec `_get_json` est préfixé par sa constante `UNAVAILABLE`)
  — signal déjà présent, jamais exploité. Corrigé : `_analyze_wallet_multi_token` classe
  chaque échec de résolution de pool en conséquence
  (`transient_pricing_error_tokens`), et `score_wallets` exclut ces tokens du
  checkpoint "scanné" — ils redeviennent éligibles au prochain appel sans qu'aucune
  nouvelle activité ne soit nécessaire. **Portée honnête assumée** : ne corrige QUE la
  couche de résolution de pool (le point d'entrée le plus fréquent de la cascade) — les
  couches OHLCV (`services/ohlcv.py`, client PARTAGÉ avec `vc_predictions`/
  `weekly_training`/`pump_dump_autopsy`) et CoinMarketCap confondent encore panne
  transitoire et absence légitime de donnée sous la même convention de texte ; les
  démêler exigerait soit un champ typé threadé à travers un client partagé par d'autres
  systèmes (risque de régression ailleurs), soit un filtrage fragile par sous-chaîne de
  diagnostic — documenté comme résidu plus étroit qu'avant, pas éliminé. **DeepSeek
  (round 2)** a ensuite pointé un vrai biais introduit par le correctif #175 de la nuit
  précédente : exclure un wallet `price_confidence_low` de la population de comparaison
  percentile protège l'intégrité du percentile des AUTRES wallets, mais resserre
  mécaniquement cette population autour des wallets qui tradent via des paires
  stablecoin directes — sous-représentant structurellement les traders de tokens peu
  liquides ou routés via agrégateur, **exactement le profil que la thèse même d'ARIA
  cherche à sourcer** (microcaps Base). Documenté comme tension assumée plutôt que
  corrigé : revenir sur l'exclusion #175 réintroduirait directement le bug qu'elle
  corrigeait (ancrer un percentile sur des chiffres non fiables) — un vrai arbitrage
  entre deux défauts connus, pas une erreur unilatérale. 3 nouveaux tests (dont un test
  de contraste confirmant qu'un token sans AUCUN pool, verdict légitime, reste bien
  marqué "scanné" comme avant), suite complète verte (4928/4928). Un dernier passage de
  gpt-nano (5e voix) le même soir n'a apporté qu'une reformulation/relecture du résumé
  déjà envoyé à l'opérateur, aucun nouveau point technique. **Marathon wallet-scoring
  fermé pour cette session** — plus de 22 correctifs réels au total (#158 à #177) sur ce
  chantier, documentation honnête et à jour des limites structurelles qui restent de
  vrais chantiers séparés si jamais repris.
- **15/07 (nuit, suite) — demande opérateur de réduire la formule à une équation, ce qui a
  fait resurgir 2 vrais bugs de plus (`0b049ad`) : le marathon n'était donc pas vraiment
  fini, réduire à une équation a servi d'audit.** En écrivant l'équation à 4 niveaux
  (par trade FIFO → axes wallet → percentile de rang → composite = moyenne des
  percentiles disponibles) puis en la faisant relire, deux vrais trous sont apparus,
  invisibles tant que la formule restait en prose :
  1. **`_percentile` ne créditait pas les ex-æquo** : un wallet dont la valeur était
     EXACTEMENT égale à celle de la majorité de la population (ex. plusieurs wallets à
     win_rate=0,5 pile) tombait à tort au 0e percentile — indiscernable d'un wallet
     réellement pire que tout le monde. Corrigé sur la convention statistique standard
     du rang moyen (`(en-dessous + 0,5×ex-æquo) / population`, cf. `scipy.stats.
     percentileofscore(kind='mean')`).
  2. **`sortino_pnl_contradiction`** : Sortino se calcule sur le RENDEMENT EN % par
     trade, jamais pondéré par le capital engagé — un wallet peut afficher un Sortino
     positif "honorable" alors que son PnL réel en DOLLARS est négatif. Démonstration
     chiffrée verrouillée par test : 4 micro-trades à +100% sur 1$ chacun (+4$) + 1
     trade majeur à -50% sur 1000$ (-500$) → PnL réel -496$ (perte nette), mais Sortino
     = +1,4 (positif). Nouveau drapeau qui détecte et affiche cette contradiction de
     SIGNE en ATTENTION à côté du Sortino — ne corrige PAS le biais sous-jacent
     (redéfinir Sortino en version pondérée par capital serait une refonte
     méthodologique plus profonde, non entreprise ce soir), rend juste sa manifestation
     la plus trompeuse impossible à manquer.
  **Documenté (pas corrigé)** : l'axe "diversification" est en réalité un TAUX DE
  RÉUSSITE PAR TOKEN (`tokens profitables / tokens total`), pas une mesure de largeur
  de portefeuille type Herfindahl/entropie — un wallet qui trade UN SEUL token
  profitable obtient le score parfait (1,0), littéralement à l'opposé de ce que le nom
  suggère. `_suspect_positive_flag` a déjà un garde-fou contre ce gaming précis (exige
  un nombre minimum de tokens avant de compter cet axe comme suspect), mais UNIQUEMENT
  pour ce drapeau séparé, jamais pour le percentile/composite lui-même. Frais de gas
  jamais déduits du PnL (vérifié par recherche dans le code, confirmé réel — aucune
  donnée de gas même récupérée dans ce module) : un wallet qui accumule des micro-trades
  gagnants en % pourrait être gas-négatif en réalité sans que ça se voie jamais.
  **Réfuté après vérification** : l'affirmation qu'un PnL brut "linéaire" ferait
  s'écraser le percentile de tous les autres wallets vers 0 dès qu'un seul wallet a un
  PnL démesuré — faux contre le code, `_percentile` est un percentile de RANG (compte
  les autres wallets strictement en dessous), jamais une normalisation par magnitude ;
  un outlier ne change rien au percentile des autres, cette classe de distorsion ne
  s'appliquerait qu'à une moyenne/normalisation par la valeur brute. 5 nouveaux tests,
  suite complète verte (4930/4930).
- **15/07 (nuit, suite) — un vrai bug corrigé, trois fausses alertes réfutées avec
  preuve (division par zéro ×3), deux nuances documentées.** La même équation, revue
  une 3e et 4e fois, a fait remonter un vrai trou opérationnel : `client.
  get_token_transfers(wallet, limit=2000, max_pages=10, ...)` peut arrêter la
  pagination alors que Blockscout avait ENCORE de la donnée au-delà -- un wallet très
  actif (plus de 2000 transferts ERC-20 vie entière) voyait ses transferts les plus
  anciens silencieusement absents, avec un risque de biais sur TOUS les axes (pas
  seulement `unmatched_sell_events`, qui ne dit pas SI l'historique lui-même était
  complet). Corrigé : nouveau champ `TokenTransfersResult.truncated` (distingue
  "historique réellement épuisé" de "arrêté avant la fin par le plafond ou une
  erreur réseau en cours de route"), affiché en ATTENTION sur la fiche wallet
  (`card.transfer_history_truncated`).
  **Trois affirmations vérifiées et RÉFUTÉES avec preuve, pas juste une intuition** :
  (1) "le trim anti-chance (trié en $) laisserait passer un micro-trade dust à
  rendement % extrême qui contaminerait le Sortino" -- faux : le trim et le calcul
  du Sortino sont deux calculs INDÉPENDANTS sur la même liste de trades, le trim ne
  filtre jamais ce qui alimente Sortino (c'est un verdict de robustesse À PART,
  jamais un préfiltre) -- rien n'est "laissé passer" puisqu'il n'y a pas de filtre
  entre les deux. Le sous-jacent reste réel (un trade dust à +9900% peut dominer la
  moyenne des rendements) mais c'est un exemple concret du biais Sortino DÉJÀ
  documenté (#178), pas un 3e mécanisme ; (2) division par zéro sur le rendement si
  le prix d'achat est nul (ex. airdrop) -- déjà gardé, `return_pct` retourne `None`
  explicitement avant toute division, jamais un crash ; (3) division par zéro du
  percentile sur une population de comparaison vide (cold start) -- déjà DOUBLEMENT
  gardé (le code appelant ET la fonction elle-même vérifient une population vide) et
  déjà verrouillé par un test dédié depuis le début du chantier. **Documenté (nuance
  mineure)** : le lissage des ex-æquo (#178) suppose les ex-æquo rares -- sur une
  population aux valeurs très arrondies/discrètes, ils peuvent devenir la norme,
  rendant le percentile moins discriminant (toujours correct, juste moins granulaire
  -- propriété statistique inhérente, pas un défaut de code). 3 nouveaux tests, suite
  complète verte.
- **15/07 — `/walletscore` DÉPLOYÉ EN PROD (commit `de51a6d`), puis file d'attente en
  arrière-plan `/walletqueue` construite le même soir (pas encore déployée).**
  Déploiement confirmé par l'opérateur (`deploy.sh` 8/8, blue-green, nginx re-vérifié
  sur trafic réel) — `ARIA_WALLET_SCORING_ENABLED=true` actif sur le VPS. Premier test
  réel sur un wallet extrême (1024j, 1067 swaps, 680 tokens tradés) : le plafond de
  10 tokens/passage rendait la couverture complète impraticable en usage manuel (~68
  rappels `/walletscore` nécessaires). Décision opérateur : (1) plafond
  `WEIGHTS.max_tokens_analyzed` remonté 10->50 (le scan répété ne bloque plus une
  réponse Telegram synchrone une fois la file construite) ; (2) nouveau mode
  `/walletqueue <adresse> [...]` — injecte un wallet une seule fois, un nouveau cycle
  heartbeat `wallet_scan_queue_cycle` (20min, double gate
  `ARIA_WALLET_SCAN_QUEUE_ENABLED` ET `ARIA_WALLET_SCORING_ENABLED`, tous deux OFF par
  défaut) le fait avancer tout seul (jusqu'à 2 wallets/cycle, sobriété API), notifie
  une progression tous les 50 tokens couverts (`wallet_scan_queue.PROGRESS_NOTIFY_STEP`)
  et le rapport final complet dès la couverture complète (le wallet quitte alors la
  file) — chaque notification affiche aussi la taille de file restante. Nouveau module
  `services/wallet_scan_queue.py` (table SQLite dédiée, FIFO, dédoublonnage) --
  réutilise EXACTEMENT le moteur incrémental existant (`score_wallets`/
  `wallet_scan_state.py`, #157 suite), rien dupliqué. Respecte le kill-switch
  (`outgoing_pause.is_paused()`), même doctrine que les autres cycles proactifs.
  Formatage de carte/rapport Telegram factorisé de `telegram_bot.py` vers
  `smart_money.py` (`chain_display_label`/`format_wallet_score_card_lines`/
  `format_wallet_scoring_report`) pour que `/walletscore` et le cycle de fond
  affichent EXACTEMENT le même texte, jamais un second formatage divergent. 22
  nouveaux tests (`test_wallet_scan_queue.py`), suite complète verte (4948 passed).
  **Rien déployé pour `/walletqueue`** — regroupé avec le prochain déploiement.
  **Décision opérateur actée pour le passage au trading réel** : seuil fixé à ~500
  wallets scorés ET vérification que la distribution des scores est saine (pas
  dégénérée) avant d'envisager que ARIA trade sur la base de ce signal — critère à
  vérifier une fois le volume atteint, même doctrine que le protocole argent réel
  existant (`docs/protocole-argent-reel.md`). **Zerion vérifié (recherche web, 15/07,
  précisé après une 2e passe)** : leur API expose un vrai endpoint PnL par wallet
  (`/wallets/{address}/pnl`, FIFO, utile pour croiser/valider un score déjà calculé).
  **Correction** : Zerion a bien un vrai produit de découverte ("Zerion Feed", surface
  les wallets les plus performants avec win rate/PnL/followers) — mais rien ne
  confirme qu'il soit exposé via `developers.zerion.io` (doc API bloquée à la lecture
  automatique, semble être une fonctionnalité de l'appli mobile grand public
  uniquement). **Nansen creusé en parallèle** (légitimité forte, méthodologie
  transparente -- univers curé ~5-10k wallets "Smart Money", scoring par
  règles+sourcing public+clustering, mis à jour quotidiennement, ~0,005$/appel) --
  **écarté sur consigne opérateur explicite** ("il nous faut une base de données
  gratuite") au profit de l'option zéro-coût ci-dessous.
- **15/07 (suite) — sourcing automatique de wallets candidats construit, zéro
  dépendance externe (réponse à « qui va trouver les wallets ? »), CODÉ/TESTÉ, PAS
  DÉPLOYÉ.** Nouveau `skills/wallet_candidate_sourcing.py` : repère un token qu'ARIA
  a déjà jugé gagnant et liste qui le détient ENCORE aujourd'hui
  (`blockscout.get_token_holders`, déjà construit) -- signal de conviction, pas une
  découverte de marché large. Enfile ces adresses dans `wallet_scan_queue.py` (#181),
  jamais un signal de trading en lui-même. **Bug réel trouvé par l'opérateur avant
  même le premier déploiement** ("ça va être trop long, elle juge tous les tokens non
  pertinents, non ?") : `vc_predictions` seule (horizon 30j) a **0 pronostic clôturé**
  au dernier audit connu (11/07) -- cette source serait restée vide des semaines.
  Corrigé : `list_strong_performers()` combine désormais DEUX sources (`vc_predictions`
  clôturées ET `paper_trader.get_closed_positions()`, déjà actif en prod, résout bien
  plus vite via stop suiveur/prise de profit sur prix réel). **Second ajustement**
  (demande opérateur "il faudrait au moins 5 tokens/semaine") : retiré le plafond
  artificiel d'un seul token sourcé par cycle heartbeat -- un cycle traite désormais
  TOUS les tokens gagnants jamais encore sourcés en une passe. Limite honnête assumée,
  documentée dans le code : **aucun débit minimum n'est garanti** -- ça dépend du
  nombre réel de trades gagnants d'ARIA sur la période, pas d'un réglage de code ; si
  le débit réel reste insuffisant une fois déployé, le seul levier honnête est
  d'abaisser `MIN_OUTCOME_PCT_STRONG_PERFORMER` (100% par défaut, soit x2) -- décision
  opérateur à prendre avec des vraies données de prod, pas faite à l'aveugle ici.
  Heuristique volontairement simple (exclut le plus gros détenteur -- pool DEX/routeur
  ou allocation équipe, jamais un "smart wallet" -- + adresses mortes, aucun appel API
  supplémentaire par détenteur pour vérifier `is_contract`) : documentée comme
  imparfaite, pire cas un scan `/walletscore` bruyant sur un contrat, jamais un risque.
  Nouveau cycle heartbeat `wallet_candidate_sourcing_cycle` (180min), TRIPLE gate
  (`ARIA_WALLET_CANDIDATE_SOURCING_ENABLED` + `ARIA_WALLET_SCAN_QUEUE_ENABLED` +
  `ARIA_WALLET_SCORING_ENABLED`, tous OFF par défaut), respecte le kill-switch. 14
  nouveaux tests (`test_wallet_candidate_sourcing.py`), suite complète verte (4962
  passed). **Rien déployé.**
- **15/07 (soir) — pilote agent-wallet réel : Coinbase Agentic Wallets retenu et
  amorcé, RIEN encore câblé côté code.** Décision opérateur actée dans
  `docs/pilote-agent-wallet-10usd.md` (§2) -- CLI `npx awal` reconfirmé légitime
  (doc officielle `docs.cdp.coinbase.com`, repo GitHub `coinbase/agentic-wallet-skills`).
  L'opérateur a créé une clé API Coinbase Developer Platform nommée "ARIA" --
  **corrigée en direct avant validation** : la clé cochait initialement les 4
  permissions (View/Trade/Transfer/Receive), y compris Transfer (l'écran Coinbase
  affiche lui-même un avertissement anti-arnaque sur cette permission précise) --
  ramenée à **View (lecture seule) uniquement**, conforme au plan écrit (§3 : aucune
  capacité de transfert libre, aucun trading tant que le wrapper de sécurité --
  plafond vérifié, slippage verrouillé, kill-switch -- n'existe pas). Rappel actif
  fait à l'opérateur : une permission cochée sur une clé est un pouvoir actif
  immédiatement, indépendant de ce qu'ARIA (le bot) fait aujourd'hui -- si Trade avait
  été coché, n'importe quel outil connecté à cette clé (skill Coinbase, MCP) aurait pu
  trader sans passer par aucun garde-fou. Noms de variables d'environnement donnés
  pour stockage sécurisé sur le VPS (`COINBASE_CDP_API_KEY_NAME`/
  `COINBASE_CDP_API_KEY_PRIVATE_KEY`, PEM sur une ligne avec `\n` littéraux) -- **rien
  ne les lit encore côté code**, juste mises à l'abri en attendant la construction du
  wrapper (`agent_wallet_pilot.py`, §5 du plan, pas commencé). Clé privée jamais vue
  ni demandée dans cette session (doctrine secrets, même famille que les clés SSH).
- **15/07 (soir, suite) — vrai goulot trouvé sur `wallet_scan_queue_cycle` (constat
  opérateur : « 25 minutes pour 50 tokens, c'est pas un peu long ? »), corrigé.**
  Vérifié plutôt que deviné : ~30s/token vient du throttle GeckoTerminal (2,1s min
  entre appels, calibré sur la limite gratuite ~30 req/min) -- **partagé par tout
  ARIA** (analyse VC, autopsie pump/dump...), jamais touché à la légère pour ne pas
  risquer un bannissement qui casserait tous les prix du système. **Vrai problème
  trouvé en creusant** : le heartbeat d'ARIA traite ses tâches en SÉQUENCE stricte
  (`heartbeat.py::_tick`, une boucle `for` qui `await` chaque tâche l'une après
  l'autre) -- un `wallet_scan_queue_cycle` à 2 wallets x 50 tokens pouvait donc
  bloquer TOUTES les autres automatisations activées d'ARIA jusqu'à ~50 minutes.
  Corrigé : `MAX_WALLETS_PER_CYCLE` ramené de 2 à 1 (décision opérateur explicite,
  "pas pressé") -- pire cas de blocage ramené à ~25 minutes, sans toucher au throttle
  partagé. Tests mis à jour, suite complète verte (4962 passed).
- **15/07 (soir) — radar large-spectre VPS Research : Clanker/GoPlus/Webacy, +
  blocage Dune identifié précisément.** Deux angles dispatchés. (1) Vérification
  Dune (`dex.trades`/`tokens.transfers`/`prices.usd`) : **bloquée, cause trouvée
  pas devinée** -- la config MCP `dune` contenait le placeholder littéral
  `"ta_nouvelle_cle"` au lieu de la vraie clé, jamais renseignée malgré la mention
  "générée et enregistrée" plus haut. Documenté avec l'action corrective exacte
  dans `docs/dune-integration-plan.md` §7 -- corrigé le même soir par l'opérateur
  (clé réelle enregistrée sur le VPS). (2) Radar : Clanker (launchpad Base racheté
  Farcaster/Neynar, API publique testée en direct par `curl` sans clé, token
  observé déployé quelques secondes avant l'appel, `chain_id 8453` confirmé) et
  GoPlus Security (testé en direct sans clé, `supported_chains` confirme Base,
  données réelles honeypot/mintable/ownership obtenues) -- **correction du
  commandement après vérification directe du code** : Clanker et GoPlus sont en
  réalité déjà construits et intégrés (`services/clanker.py`, `services/goplus.py`
  -- GoPlus actif en prod depuis longtemps), Research n'avait vérifié l'absence
  de note de diligence dans `aria-learning-inbox/` (exacte) mais pas l'existence
  du code -- seule **Webacy** (risk-scoring wallet, verrouillé 401 sans clé,
  piste réelle non tranchée) est une vraie nouveauté. Aucune frontière approchée
  (aucun compte créé, aucune clé achetée). Détail complet :
  `docs/aria-learning-inbox/2026-07-15-radar-goplus-clanker-webacy.md`.
- **15/07 (soir) — client Dune Analytics construit (VPS Principal), mergé après
  correctif de revue (`services/dune.py`).** Wrapper Execute SQL (dôme habituel,
  `DUNE_API_KEY` lue à chaque appel, `available=False` sans appel réseau si
  absente) + `build_early_buyer_multiple_query()` -- la requête SQL de sourcing
  §3.2 du plan (`docs/dune-integration-plan.md`) : wallets ayant acheté un token
  Base dans sa première heure, qui a ensuite fait ≥Nx. **Vrai bug de fond trouvé
  en relecture avant merge** : la CTE `token_launch` calculait le premier trade
  jamais vu (`MIN(block_time)`) sur des lignes déjà filtrées à la fenêtre
  `lookback_days` -- un token ÉTABLI depuis longtemps, dont le premier trade DANS
  la fenêtre tombait par hasard il y a `lookback_days` jours, aurait été à tort
  classé "vient de naître", polluant tout le signal d'acheteurs précoces avec des
  acheteurs d'un token ancien en pleine remontée (le contraire du but recherché).
  Corrigé avant merge : le filtre de date passe du `WHERE` (pré-agrégat) au
  `HAVING` (post-agrégat) -- `token_launch` scanne désormais l'historique complet
  de `dex.trades`, ne garde que les tokens dont la PREMIÈRE transaction jamais vue
  tombe réellement dans la fenêtre récente. Coût plus élevé pour cette CTE (scan
  complet), nécessaire pour la correction. Portée respectée par VPS Principal :
  aucun gate `ARIA_DUNE_ENABLED`, aucune tâche heartbeat, aucun branchement à
  `wallet_candidate_sourcing.py` -- simple client + requête, comme demandé.
  **Réserve honnête assumée (documentée dans le code)** : noms de champs
  `dex.trades` vérifiés contre la doc publique Dune uniquement, jamais un appel
  réel (clé MCP restée mal configurée -- `"ta_nouvelle_cle"` littéral -- tout le
  segment, cf. entrée VPS Research ci-dessus) -- à reconfirmer via
  `EXECUTE_SQL_LIMIT_1` avant tout usage en prod. 26 tests (dont le nouveau,
  verrouillant le correctif WHERE/HAVING), suite complète verte (4992 passed).
- **15/07 (nuit) — suivi PERMANENT des wallets scorés (#157 suite 2, décision
  opérateur explicite : « je veux que wallet score ne scanne jamais de token, et
  je veux que chaque wallet scanner a 100% et toujours un suivis de scan pour
  toujours sauf si le wallet devien inactif plus de 3 mois »), CODÉ, TESTÉ, PAS
  DÉPLOYÉ.** `wallet_scan_queue.py` réécrit : un wallet qui atteint 100% de
  couverture n'est plus jamais retiré de la file -- il bascule en mode
  SURVEILLANCE (`monitoring_since` posé), revérifié une fois par semaine
  (`MONITORING_INTERVAL_DAYS=7`, confirmé par l'opérateur : « une analyse par
  semaine devrait suffire »). Chaque vérification hebdomadaire ne redemande
  JAMAIS une couverture complète (déjà acquise, le moteur incrémental existant
  ne reprend que le neuf) -- silencieuse si aucune nouvelle activité, notifiée
  seulement si de nouveaux tokens ont été couverts. Seule sortie : si
  `WalletScoreCard.last_activity_at` (nouveau champ, dernière activité on-chain
  RÉELLE observée, distinct de `last_scan_at` qui avance à chaque passage même
  sans rien de neuf) dépasse `INACTIVITY_CUTOFF_DAYS` (90j, "3 mois") sans
  aucune activité, la surveillance s'arrête et le wallet est retiré -- jamais
  avant, jamais sur la durée passée dans la file. Nouvelles colonnes
  `next_check_at`/`monitoring_since` (migration à chaud idempotente) pilotent
  le FIFO -- `list_pending()` ne renvoie que les wallets réellement DUS
  (rattrapage toujours dû immédiatement, surveillance due chaque semaine),
  jamais un ordre arbitraire. Nouveau `queue_counts()` distingue rattrapage vs
  surveillance pour l'affichage. Seuil de déclassement par score (mentionné par
  l'opérateur : « si les notes de wallet descende sous un certain seuil je
  prevoi de ne plus les scanner ») explicitement différé par l'opérateur à plus
  tard, "quand la liste sera plus longue". `smart_money.py`/
  `wallet_scan_state.py` gagnent `last_activity_at` (calculé sur le max des
  timestamps de transferts réellement observés à chaque passage, persisté au
  même titre que `scanned_tokens`/`last_scan_at`). `heartbeat.py` mis à jour
  (`result["completed_first_time"]`/`result["dropped_inactive"]` remplacent
  l'ancien `result["completed"]`, qui n'a plus de sens puisque rien n'est plus
  jamais "complété puis retiré"). 31 tests (`test_wallet_scan_queue.py`
  réécrit + 4 nouveaux dans `test_smart_money_wallet_scoring.py`), suite
  complète verte (4999 passed). **Rien déployé** -- regroupé avec le prochain
  déploiement (`/walletqueue` lui-même pas encore en prod).
- **15/07 (nuit, suite) — VPS Research : Webacy approfondi (complémentaire à
  GoPlus) + nouvelle piste Arkham Intelligence + blocage Dune résolu.**
  Vérifié avant merge (grep-avant-proposer confirmé sur `goplus.py`/
  `smart_money.py`, chiffre de crédit Dune recoupé indépendamment via
  `mcp__dune__getUsage` depuis cette session : 0 -> 0,161 crédit, correspond
  exactement). **Webacy** : légitimité réelle (financement ~10M$ Mozilla
  Ventures/GSR/Sui Foundation, clients Etherscan/Revoke.cash/Arculus),
  confirmé complémentaire à GoPlus (réputation d'ADRESSE -- Exposure/Threat
  Risk/Sanctioned -- vs sécurité de CONTRAT côté GoPlus, aucun chevauchement
  aujourd'hui) -- piste pour un futur `/walletscore`, jamais `safety_screen`.
  Tarif API exact et couverture Base non positivement confirmés (portail
  dev 403, 401 sans clé) -- banqué, pas activé. **Nouvelle branche : Arkham
  Intelligence** (entity labels réels -- exchanges/funds/whales/individus),
  trouvée en creusant un angle mort réel de `smart_money.py` : l'exclusion
  actuelle des wallets "équipe/vesting/LP" ne repose que sur le flag brut
  `is_contract` + une liste DEX à la main -- un EOA d'équipe/whale connu
  n'est pas détecté. Base confirmée supportée, rate limits documentés (20
  req/s) -- mais produit payant (149-999$/mois), banqué comme piste
  sérieuse, pas urgent (mérite un "go" seulement si le sourcing actuel
  s'avère insuffisant en pratique). **Bonus : blocage Dune du tour précédent
  résolu** (clé corrigée en cours de session côté MCP) -- test live réussi
  sur `dex.trades`/`prices.usd`/`tokens.transfers` (WETH Base), coût réel
  0,161 crédit/2500 mensuels, les trois tables confirmées vivantes et
  fiables. **Réserve qualité de donnée trouvée** : `amount_usd` est `null`
  sur certaines lignes `dex.trades` issues d'agrégateurs (`0x API`) -- à
  gérer explicitement (jamais supposer une valeur) si `services/dune.py`
  est étendu. Aucun compte créé, aucune clé achetée/activée, aucune
  frontière approchée. Détail complet :
  `docs/aria-learning-inbox/2026-07-15-radar-webacy-approfondi-arkham-entity-labels.md`
  + `docs/dune-integration-plan.md` §7bis.
- **15/07 (nuit, suite) — VPS Secondaire : deuxième source de découverte de
  tokens Base via Dune, `build_recent_base_pairs_query()`, MERGÉE.** Piste
  initiale abandonnée après vérification en direct : "/v1/dex/pairs/{chain}"
  (§3.1 du plan) confirmé INEXISTANT (404 sur toute variante d'URL, y
  compris avec auth -- contrairement à l'Execute SQL API, réelle, qui
  répond 401 sans clé valide). Repli sur §3.2 : réutilise STRICTEMENT
  `services/dune.py` déjà mergé, même patron que
  `build_early_buyer_multiple_query` -- `token_launch` (premier trade Base
  jamais vu par token) filtré via HAVING sur l'agrégat, jamais via une date
  dans le WHERE (même piège déjà identifié et corrigé sur la 1ère requête,
  vérifié explicitement par un test dédié). `recent_volume`, elle, peut être
  bornée directement par `lookback_hours` dans son WHERE (safe : un token
  dont le lancement tombe dans la fenêtre a tous ses trades dans la fenêtre
  aussi). **Relecture cloud avant merge** : documenté un angle non couvert
  par Secondaire -- `SUM(amount_usd)` ignore silencieusement les lignes
  `null` (agrégateurs type `0x API`, trouvé par Research le même soir, cf.
  entrée ci-dessus) -- `volume_usd` est donc un PLANCHER, pas un total
  garanti (faux négatif de découverte possible, jamais un faux positif de
  sécurité) -- documenté en commentaire dans le code, pas corrigé (hors
  scope de cette passe, à traiter si significatif en usage réel). Portée
  EXACTE respectée : requête + tests SEULEMENT, aucun branchement
  `base_crawler.py`, aucun gate, aucune tâche heartbeat. 8 nouveaux tests
  (`TestBuildRecentBasePairsQuery`), suite complète verte (4997+ passed).
  **Rien branché au pipeline de sourcing** -- décision séparée après
  relecture, pas prise ce soir.
- **15/07 (nuit, suite) — VPS Principal : `/whoami` câblé + fuite `admin_ids`
  corrigée (#181), MERGÉ ET DÉPLOYÉ EN CODE (pas encore sur le VPS).**
  Handler orphelin (jamais enregistré via `add_handler`, seul hit du fichier
  = sa propre définition -- probable reliquat créé hors flux git normal, cf.
  hypothèse opérateur "peut-être Grok"). Décision : CÂBLÉ, pas supprimé --
  vérifié non-redondant (`/status` admin-only, `/start` visiteur ne montre
  ni ID ni instructions) -- seule voie pour qu'un visiteur non reconnu
  retrouve son ID Telegram. **Vrai bug de sécurité trouvé en investiguant,
  corrigé au passage** : la branche VISITEUR renvoyait `settings.admin_ids`
  (la vraie liste des IDs admin) à N'IMPORTE QUI tapant `/whoami` -- seule
  ligne de tout le fichier exposant cette liste hors d'une réponse déjà
  réservée à un admin confirmé. Un visiteur ne voit plus que son propre ID.
  Branche admin inchangée (déjà exposée par construction, aucune nouvelle
  fuite). Ajouté par la relecture cloud : entrée dans le menu `/` visible
  (`_register_bot_commands`, oubliée dans le lot initial). 6 nouveaux tests
  (dont une régression directe sur la fuite), suite complète verte (5013
  passed). **Code mergé sur main, pas encore déployé sur le VPS.**
- **15/07 (nuit, suite) — VPS Research : diligence skills.sh (Vercel).**
  Produit officiel Vercel (lancé 21/01/2026, CLI open-source MIT
  `vercel-labs/skills`, 91k+ installations, 87k+ skills indexées) -- pas
  un projet obscur. Test réel (curl direct, contourne le 403 anti-bot
  rencontré côté cloud) : l'API de recherche programmatique (600k+
  skills) exige un jeton Vercel OIDC scopé équipe/projet, pas une simple
  clé API -- ARIA ne tournant pas sur Vercel, contrainte d'infra réelle,
  pas juste un compte à créer. **Risque identifié** : aucun processus de
  revue de sécurité documenté pour les skills tierces avant installation
  -- vecteur d'injection à traiter avec la même prudence que l'auto-
  modification (lecture humaine intégrale avant toute adoption, jamais
  d'installation automatique). **Verdict : intéressant à connaître, pas
  une priorité d'intégration** -- outil méta (capacités de dev), pas une
  source de données on-chain. Branches banquées non creusées :
  `antfu/skills-cli`, SkillX.sh, SkillsMP, SkillHub, Claude Code
  Templates. Détail complet :
  `docs/aria-learning-inbox/2026-07-15-diligence-skills-sh-vercel-marketplace.md`.
- **15/07 (nuit, suite) — VPS Principal : vérification LIVE des deux
  requêtes Dune composées -- 1 vrai bug trouvé, documenté (pas corrigé).**
  Schéma `dex.trades` confirmé colonne par colonne (`blockchain`/`taker`/
  `token_bought_address`/`amount_usd`/`block_time` -- tous exacts) ;
  adresses `varbinary` déjà restituées en hex `0x...` par l'API, aucun
  décodage à ajouter. `build_recent_base_pairs_query` : verdict positif
  sans réserve (109 lignes, volumes $478K-$88,9M plausibles, 2,932
  crédits). `build_early_buyer_multiple_query` : exécute sans erreur SQL
  mais **`peak_multiple` aberrant (~10^22x)** sur plusieurs lignes en tête
  -- cause identifiée : division sur `launch_price_usd` quasi-nul (dust
  trade probable sur le tout premier trade d'un token, `MIN()` sur un seul
  point de mesure sans plancher de montant). `EXECUTE_SQL_LIMIT_1` seul
  n'aurait pas attrapé ça (requête syntaxiquement valide) -- confirme la
  valeur du test réel avec paramètres + inspection des valeurs, pas
  seulement le schéma. **Ne pas utiliser cette requête en prod avant
  correction** (backlog #185, pistes déjà documentées : plancher de
  montant, médiane sur N premiers trades, ou plafond de multiple
  suspect). Coût total de la vérification : 10,269 crédits/2500 (0,4%).
  Détail complet : `docs/dune-integration-plan.md` §8.
- **15/07 (nuit, suite) — VPS Research : chemin GRATUIT trouvé pour le
  clustering Sybil au-delà du pairwise (la plus grosse limite documentée
  de `smart_money.py`).** Repère académique (Victor, FC2020, "Address
  Clustering Heuristics for Ethereum") : heuristique de dépôt/airdrop/
  autorisation spécifique au modèle de compte Ethereum (Bitcoin's multi-
  input ne s'applique pas) -- l'heuristique de dépôt seule clusterise
  17,9% des EOA actives, chiffre publié. Outil open-source vérifié réel
  (API GitHub, pas juste la doc) : `TrustaLabs/Airdrop-Sybil-
  Identification` (Python actif, GPL-3.0) -- Louvain+K-Core (`networkx`,
  BSD) puis K-means (`scikit-learn`, BSD) réimplémentable SANS dépendre
  du code GPL. Méthodologie Arbitrum Foundation (doc seule, 271★) valide
  le même principe funder/sweep à l'échelle d'un vrai airdrop
  multi-milliardaire. Papier ML 2025 (LightGBM) plus précis mais hors de
  portée immédiate (code non confirmé). **Recommandation** : construire
  le graphe funder/sweep sur l'historique déjà collecté par ARIA
  (Blockscout), Louvain pour isoler les communautés, K-means pour le
  raffinement -- gratuit, Arkham/Webacy restent des options payantes de
  secours, pas un point de départ obligé. Rien codé ce soir (diligence
  seulement). Détail complet :
  `docs/aria-learning-inbox/2026-07-15-radar-sybil-clustering-entite-gratuit.md`.
- **15/07 (nuit, suite) — #185 corrigé : plancher anti-dust sur
  `build_early_buyer_multiple_query`, vérifié en direct (VPS Principal).**
  `amount_usd >= min_trade_usd` (défaut 1.0) ajouté dans `token_peak` ET
  `token_launch_price` (le bug pouvait toucher n'importe quel côté de la
  division). Volontairement PAS un plafond arbitraire sur `peak_multiple`
  (aurait masqué le symptôme). Vérifié en direct, mêmes paramètres qu'hier :
  `peak_multiple` max passe de ~10^22x à ~1,65×10^7x (15 ordres de
  grandeur). **Nuance honnête** : le résultat reste élevé après correction
  -- possiblement en partie un phénomène réel (bonding curves Base type
  Clanker, prix de départ authentiquement quasi nul), pas seulement un
  résidu du bug -- distinguer précisément les deux cas reste hors scope.
  4 nouveaux tests, suite complète verte (5017 passed).
- **15/07 (nuit, suite) — VPS Secondaire : nettoyage code mort, arbitrage
  câblage vs suppression sur `visitor.py` (même rigueur que #181).**
  `VISITOR_API_PREFIXES`/`is_visitor_api()` (allowlist de chemins "API
  visiteur") confirmées totalement orphelines -- décision SUPPRESSION,
  pas câblage : le vrai mécanisme actif de routage public/membre est
  `VANGUARD_PUBLIC_ROUTES`/`PUBLIC_PREFIXES` dans
  `auth/middleware.py::AccessCodeMiddleware._is_public` (plus précis,
  méthode+chemin, réellement consulté à chaque requête) -- câbler
  l'allowlist morte aurait créé une DEUXIÈME source de vérité pour cette
  classification, risque de divergence pire que l'absence actuelle.
  Aucun impact de sécurité actif trouvé (`access_code_enabled=False` par
  défaut rend tout ce gate no-op en configuration standard). 3 alias
  legacy supprimés en mécanique (auto-documentés comme superseded,
  0 référence hors leur propre définition) : `groq_factual_answer`/
  `resolve_factual_answer` (epistemic.py), `sync_entry_to_github`
  (truth_ledger/sync.py), `extract_twitter_from_identity`
  (privy_verify.py). Reste du Tier 1 (self_maintenance_context_for_brain,
  welcome_site_access, workflow_active, suppressed_journal_preview) et
  tout le Tier 3 banqués pour un futur balayage dédié. Suite complète
  vérifiée verte (5003 passed avant les deux merges ci-dessus).
- **15/07 (nuit, suite) — VPS Research : GraphSense vérifié négatif (code
  lu, pas supposition) -- pivot vers `labels.*`/`cex.addresses`/
  `addresses.stats` de Dune pour le clustering Sybil.** Lecture directe du
  code source `graphsense-spark` (pas la doc marketing) : grep
  `cluster|deposit|entity` sur les 6 fichiers du module Ethereum
  (`account/eth/*.scala`) -- zéro résultat. Le clustering réel
  (co-spend/multi-input) n'existe que côté `utxo.*`, spécifique Bitcoin --
  GraphSense n'implémente PAS les heuristiques Victor (FC2020) pour le
  modèle de compte. Piste fermée avec preuve. **Pivot testé en direct** :
  `addresses.stats.first_funded_by` (heuristique de financement partagé
  déjà calculée, confirmée sur une adresse Base réelle -- réserve trouvée :
  `is_smart_contract` se trompe sur les predeploys Base) ; `cex.addresses`
  (labels d'exchange réels sur Base : Binance/XT.com/Bithumb/Korbit/
  CoinDCX -- signal qu'ARIA n'a aujourd'hui aucunement, `smart_money.py`
  ne connaît que des adresses DEX manuelles) ; `labels.owner_addresses`
  (schéma riche `custody_owner`/`algorithm_name`, piste non creusée plus
  loin). Coût total ~1,2 crédit/2500. **Recommandation actualisée** :
  démarrer le chantier Sybil par une requête Dune sur ces tables plutôt
  que par une ré-implémentation Louvain/K-means from scratch -- le signal
  brut est déjà là, gratuit. Détail complet :
  `docs/aria-learning-inbox/2026-07-15-graphsense-verifie-negatif-dune-labels-pivot.md`.
- **15/07 (nuit, suite) — VPS Research : `algorithm_name` (Dune
  `labels.owner_addresses`) vérifié vide -- verdict négatif, symétrique à
  GraphSense.** Requête réelle : `algorithm_name` est `NULL` sur les 52,4
  millions de lignes de la table (5 groupes seulement en tout,
  `source` NULL/Forta/forta/Manual, jamais un nom d'algorithme réel) --
  aucun raccourci de clustering supplémentaire ici. Les deux raccourcis
  réels pour le chantier Sybil restent ceux du rapport précédent :
  `addresses.stats.first_funded_by` et `cex.addresses`. Découverte
  adjacente banquée : Forta Network (source réelle sur ~6900 lignes) --
  détection de menaces temps réel sur smart contracts, même famille que
  GoPlus, hors sujet Sybil direct. Coût 0,111 crédit. Détail complet :
  `docs/aria-learning-inbox/2026-07-15-labels-owner-addresses-algorithm-name-verdict-negatif.md`.
- **15/07 (nuit) — VPS Secondaire : Tier 1 du balayage code mort traité, 3
  câblages + 1 suppression, décision par cas (même rigueur que /whoami).**
  1. `self_maintenance_context_for_brain()` -- CÂBLÉ (vrai gap) : le
  classifieur regex strict qui intercepte les ordres opérateur (profil X/
  bannière/avatar) laisse passer les formulations non couvertes, qui
  rejoignent le flux LLM général sans le filet prévu -- câblé au même
  point d'insertion que les directives dans `build_verified_facts_block`
  (`grounding.py`), admin-only. 2. `welcome_site_access()` vs
  `welcome_site_return()` -- CÂBLÉ (vrai bug UX) : `privy_login` disait
  TOUJOURS "bienvenue de retour", même à la toute première connexion d'un
  nouveau membre -- `login_with_privy` réutilise le flag `existing`
  (`user_links`) déjà interrogé pour une autre vérification comme signal
  `is_new_member` (3e valeur du tuple retourné, propagée à la route). 3.
  `workflow_active()` -- SUPPRIMÉ (pas un bug) : le vrai garde-fou anti-
  double-démarrage est déjà en dur dans `handle_workflow_message`
  (`phase == IDLE.value`) -- pire, la sémantique de `workflow_active()`
  (SCHEDULED = "inactif") ne correspond PAS au comportement réel du
  dispatcher (bloque aussi pendant SCHEDULED), le câbler tel quel aurait
  changé un comportement plutôt que neutraliser un manque. 4.
  `suppressed_journal_preview()` -- CÂBLÉ (vrai bug, symétrique au cas 1) :
  le rappel vectoriel supprimé par l'arbitrage était déjà filtré avant
  injection LLM (`llm_context.py`), mais la couche journal ne l'était
  pas -- câblé en miroir exact du filtre vectoriel existant. **Bug de
  test trouvé et corrigé par la relecture cloud avant merge** (pas une
  régression de la logique livrée) : le nouveau test du filtre journal
  entrait en collision avec un mécanisme PRÉEXISTANT de
  `_assemble_context` (troncature à `max_chars`) -- quand le contenu
  total dépasse la limite, les sections prioritaires sont préservées
  dans un ORDRE FIXE (`priority_markers`), et "Journal récent" est LA
  DERNIÈRE du tuple -- donc la première sacrifiée si le total préservé
  dépasse encore la limite. Le test ne mockait pas les autres
  générateurs de contenu réel (valeurs/objectifs/état des capacités/
  réflexions), laissant le total varier et parfois dépasser 8000
  caractères, faisant disparaître la section journal AVANT même
  d'atteindre le filtre testé -- un faux négatif du test, pas un bug du
  correctif lui-même (vérifié par isolation complète : le filtre
  fonctionne correctement une fois le contenu total maîtrisé). Corrigé en
  mockant ces générateurs à vide dans le test, suite complète reverte
  (5020 passed).
- **15/07 (nuit) — #182 corrigé : correctif de vitesse scan wallet (VPS
  Principal), réponse à la question opérateur "1h entre 2 scans de 50
  tokens, il faut diviser ça par 2".** Diagnostic (cloud) : le
  wallet-scoring n'utilise `price_at()` (une seule bougie la plus
  proche d'un timestamp) mais `OHLCVClient.get_ohlcv` exigeait 20
  bougies (`_MIN_USEFUL_CANDLES`, pensé pour /vc et son besoin de
  support/résistance) avant d'accepter le palier journalier, sinon
  escalade vers 4H puis 1H -- jusqu'à 2 appels GeckoTerminal
  supplémentaires par token pour un token jeune/microcap n'ayant pas
  encore 20 bougies journalières (profil fréquent d'un wallet actif sur
  Base). Fix : nouveau paramètre `min_useful_candles` sur
  `OHLCVClient.get_ohlcv` (défaut inchangé, `_MIN_USEFUL_CANDLES`),
  transmis via `GeckoTerminalClient.get_ohlcv` (uniquement si fourni
  explicitement -- zéro nouveau kwarg pour les appelants existants),
  passé à `1` au point d'appel wallet-scoring dans `smart_money.py`.
  Zéro régression `/vc` (défaut inchangé partout, verrouillé par test
  dédié). 8 nouveaux tests, suite complète verte. **Rien déployé** --
  la lenteur observée en prod ce soir (captures opérateur, ~20-45min
  entre notifications de progression) reflète encore l'ANCIEN code ;
  amélioration attendue seulement après déploiement.

## Automatismes en place (à connaître dès le début de session — ne pas les défaire)
- **Environnement prêt tout seul** : `.claude/hooks/session-start.sh` (SessionStart, web) crée un venv Python 3.12 et installe `aria-core[dev]`. En web c'est **asynchrone** (barre de statut « 🔧 env NN% » → l'indicateur disparaît quand c'est prêt). Lancer les tests via ce venv : `packages/aria-core/.venv/bin/python -m pytest` (ou `pytest` une fois le PATH exporté). Ne pas recréer l'env à la main.
- **Garde-fou de cohérence** : `packages/aria-core/tests/test_coherence.py` tourne dans la **CI** et DOIT rester vert. Il impose : aucune IP/email dans les docs publiques ; honeypot actif (analyse VC **et** filtre d'entrée du pool) ; `paper_trade_cycle` câblé au heartbeat ; ACP gaté ; docs référencés existants ; blocs « faits établis » + « automatismes » présents ici ; **registre des actions externes** (`test_external_write_actions_registered_in_allowlist`, 10/07) — toute fonction de production qui écrit réellement à l'extérieur (GitHub/X/email) doit être déclarée dans `_EXTERNAL_WRITE_ALLOWLIST`, sinon la CI casse immédiatement (garde-fou mécanique anti-récidive après l'incident Cursor/worker-queue). **Si tu changes VOLONTAIREMENT un invariant, mets à jour ce test dans le MÊME commit** — c'est le contrat qui empêche la dérive entre sessions.
- **CI** : `.github/workflows/ci.yml` lance la surface VC + les capacités clés + le garde-fou de cohérence à chaque push touchant `packages/aria-core/**`.
- **Workflow Git** : développer sur la branche `claude/…`, PUIS **fusionner dans `main`** pour que les nouvelles sessions ET la prod héritent (une session neuve lit le `CLAUDE.md` de `main`). Rien n'est déployé sans `./vanguard/deploy.sh` sur le VPS.
- **Paper-trading 1M$** : tâche heartbeat `paper_trade_cycle` **gatée par `ARIA_PAPER_TRADING_ENABLED`** (OFF par défaut) ; l'activer démarre le run de preuve de 20 jours.
- **2FA** : site membres = MFA natif Privy (bouton d'enrôlement + Google, à activer dans le dashboard Privy). Opérateur = TOTP (`aria_core/admin_totp.py`) **opt-in via `ADMIN_TOTP_SECRET`** (OFF par défaut, aucun lock-out ; header `X-Admin-Totp` exigé en plus du secret admin quand activé ; verrou anti-force-brute par IP). Enrôlement : `python vanguard/operator/gen-admin-totp.py`.
- **Checkpoint auto de session (tous les 1000 messages, cadence relevée le 10/07 sur demande opérateur — était 20)** : hook `.claude/hooks/session-checkpoint.sh` (UserPromptSubmit) compte les messages dans `.claude/.msg-counter` (gitignoré) et, tous les 1000, injecte un rappel → l'assistant **propose de mettre à jour les fichiers de résumé** (HANDOFF, CLAUDE.md, `etat-systeme-cable.md`) pour garder `CLAUDE.md` alimenté et une nouvelle session prête. La barre de statut affiche « 📌 chk NN/1000 » pour le voir venir. Sauvegarde sur validation opérateur (jamais imposée). Ne pas défaire ce hook.
- **Backlog (liste `#` numérotée, TaskCreate/TaskUpdate) toujours alimentée (09/07, consigne opérateur explicite)** : garder en permanence **10 à 15 tâches pending/in_progress** dans la liste. Y penser souvent, pas seulement quand l'opérateur demande "ensuite ?" — dès qu'une session termine plusieurs tâches et fait descendre le compte sous ~10, proposer de nouvelles idées concrètes (jamais du remplissage vague) pour reconstituer la réserve. Les idées viennent de ce qui est observé en construisant (gaps trouvés en route, dette technique repérée, suites logiques d'une fonctionnalité livrée) — jamais inventées pour occuper l'espace.
- **Rappel de déploiement VPS (seuil de lignes non déployées)** : le même hook mesure les lignes changées sur `main` depuis le dernier déploiement (marqueur **suivi** `.claude/last-deployed-ref`) et, au-delà de **6000 lignes** (ajustable en tête du hook, relevé de 2500 le 15/07 sur demande opérateur), injecte un rappel → l'assistant affiche **UNE SEULE LIGNE** (« 🚀 Déploiement VPS conseillé — quota 6000 lignes atteint ») puis **CONTINUE normalement** (dépasser le seuil ne bloque rien). Les commandes de déploiement ne sont données **que sur demande** ("go"). Throttle : un rappel par nouvel état de `main`. Barre de statut : « 🚀 N l. à déployer ». **Quand l'opérateur confirme le déploiement, mettre `.claude/last-deployed-ref` = commit déployé (`git rev-parse main`) puis commit/push** — c'est ce qui remet le compteur à zéro. Ne pas défaire ce hook.
- **Accès réseau Claude Code (environnement cloud, 09/07, réaffirmé 10/07)** : liste blanche de domaines personnalisés (Custom domains), configurée UNIQUEMENT via les paramètres de l'environnement sur claude.ai — jamais depuis une session. **Réflexe systématique : dès qu'un accès API/domaine manque pour vérifier un fait en direct, DEMANDER à l'opérateur (« peux-tu ajouter tel domaine ? ») au lieu de conclure « inaccessible », deviner depuis le code seul, ou renvoyer la vérification au VPS par défaut** — consigne opérateur explicite, répétée. Un ajout prend effet **immédiatement, sans redémarrage de session** (vérifié 09/07 avec `*.virtuals.io`, `x.com`/`twitter.com`, `*.shekel.xyz` ; revérifié 10/07 avec `api.virtuals.io` + `www.clanker.world`, effectif en quelques secondes). Préférer un wildcard (`*.exemple.io`) à un sous-domaine unique quand plusieurs sous-domaines du même service sont probables (évite les allers-retours).
- **`/compact` proactif dès 60% de contexte (11/07, consigne opérateur explicite, vaut pour toute session — VPS Principal, VPS Secondaire, VPS Research, session cloud).** Dès que le contexte de la conversation dépasse ~60%, demander un `/compact` avant de continuer plutôt que d'attendre la limite. Objectif : éviter la dérive/perte de fil sur les sessions longues (celle-ci a déjà nécessité plusieurs compactages ce segment). Pas de mécanisme technique pour l'auto-mesurer précisément — rester attentif à la longueur de la conversation et proposer proactivement plutôt qu'attendre un signal système.

## Capacités (à jour 07/07)
- **Données** : DexScreener (prix/liq/vol), GeckoTerminal (OHLCV), Blockscout (contrat, holders, is_contract), CoinGecko (market cap, FDV, catégories). Moteur TA (RSI/fibo/divergences/EMA/MACD tous câblés dans le pipeline de scan réel — `skills/indicators.py` livré ET câblé dans `acp_onchain_scan.py`/`vc_analysis.py` le 10/07 même segment, voir journal « Nuit 8 » ci-dessus).
- **LLM** : **enabled:true en prod** (health VPS confirmé). *(L'ancien « dormant » est périmé.)*
- **Garde-fous wallet** : kill-switch fail-closed, resolve_spend via clic Telegram réel + anti double-clic. Exécution financière de-facto non câblée sur le VPS (provider off).
- **Anti-scam dynamique (nuit 07/07)** : `services/goplus.py` (GoPlus Security, gratuit) — honeypot, taxes réelles achat/vente, owner caché, reprise de propriété. Câblé data-gated (`include_honeypot`) dans le scan + barrières dures `safety_screen`, actif sur l'analyse VC. Complète le scan ABI Blockscout (statique) par du comportement.
- **Analyse de masse / tri (nuit 07/07)** : `skills/candidate_ranking.py` classe le pool screené (score composite transparent : sécurité + liquidité + concentration + verdict) → « Top candidats » dans le digest opérateur ; `draw_top` opt-in pour bâtir le track-record sur le haut du panier.
- **Paper-trading 1 M$ mode trading (nuit 07/07)** : `paper_trader.py` — portefeuille FICTIF appliquant les VRAIS rapports (achats/ventes simulés, alertes clairement fictives, marque au marché, P&L). Preuve sur ~20 jours avant argent réel. Tâche heartbeat `paper_trade_cycle` **gatée par `ARIA_PAPER_TRADING_ENABLED`** (OFF par défaut). Aucun argent réel, aucune signature.
- **ACP abandonné (confirmé)** : routage conversationnel ACP désactivé par défaut (`ARIA_ACP_ENABLED` off, `brain.detect_intent`) → la conversation libre Telegram tombe sur le LLM. Provider d'exécution ACP toujours off (CLI absent du conteneur). **Préservé en seam dormant (rien supprimé) ; checklist de réveil zéro-temps-perdu : `docs/acp-reactivation.md`** (flags env + signer local + rebuild).
- **X** : publication `@Aria_ZHC` opérationnelle (testée opérateur), gatée `arm_campaign`. **TikTok** (12/07, #34) : client réel posé (`gateway/tiktok.py`, patron dôme), gate `ARIA_TIKTOK_PUBLISH_ENABLED` OFF, **aucun compte TikTok créé à ce jour — décision opérateur explicite (12/07) : le créer seulement une fois qu'il y a une vraie valeur à proposer dessus**, pas avant. Ne pas pousser à l'activation tant que ce moment n'est pas venu. `aria_core.x_profile` = module non livré (imports gardés).
- **Showcase PR — relai humain (08/07)** : `skills/showcase_pr_watcher.py` répond en auto SEULEMENT sur un feu vert net (merge/LGTM sans négation/question/technique) ; sinon poste un court **relai public taguant l'opérateur (@GoldenFarFR)** + ping Telegram privé avec brouillon (ARIA n'invente ni ne tranche rien). Signature de transparence sur toute réponse (« Response generated by ARIA (an autonomous AI owned by GoldenFarFR) »), zéro em-dash. Commande opérateur `/github repair` (admin-gated) **édite** un commentaire déjà posté. Invariant verrouillé (`test_coherence`). Bug corrigé : commande `/github` n'était **jamais enregistrée** (muette) → branchée.
- **Base — financement (08/07)** : différenciateur = **track-record prouvé onchain** (preuve avant promesse). Briques : **seam x402** (`services/x402.py`, paiement agentique Base, gaté OFF, aucune dépense autonome, dôme) · **ancrage onchain** (`onchain/anchor.py` prépare la racine Merkle du track-record → **signature LOCALE**, serveur SANS clé ; contrat `contracts/AriaLedger.sol` + runbook `contracts/DEPLOY.md`, gaté `ARIA_ONCHAIN_ANCHOR_ENABLED`) · **dossier de candidature** `docs/base-funding-dossier.md` (Batches/Ecosystem Fund, plan d'emploi des fonds). Formulaire Ecosystem Fund : champs équipe/expérience remplis avec l'opérateur (parcours réel — 2 ans investisseur crypto, aucune expérience de gestion d'équipe classique, assumé comme différenciateur du modèle AI-operated holding).
- **Cockpit IA/humain (#21) — EN LIGNE (08/07 soir)** : `vanguard/src/pages/CockpitPage.tsx` + `CockpitPulsePanel`/`CockpitGate`/`CockpitDossierPanel`, câblés sur `GET /api/pulse` (public) et `GET /api/aria/dossier/{contract}` (gaté opérateur). Déployé (backend + vitrine) et validé visuellement par l'opérateur en prod.
- **Rapport `/vc` : PDF sécurisé multilingue (08/07 soir)** : `skills/vc_report_pdf.py` (reportlab, chiffrement pypdf permissions impression seule) + `skills/vc_i18n.py` (`report_strings` FR/EN) + choix de langue interactif (boutons Telegram) avant l'analyse LLM. Email = teaser court + PDF joint (jamais la thèse complète en clair dans le corps).
- **Rehearsal Sepolia autonome (08/07 soir) — EN LIGNE (08/07 nuit)** : `onchain/sepolia_autonomous.py` — décide ET exécute SEULE sur Base Sepolia (testnet, aucune valeur réelle), sans clic Telegram, exception bornée documentée dans les Règles absolues. Triple gate (`ARIA_SEPOLIA_WALLET_ENABLED` + `ARIA_SEPOLIA_AUTONOMOUS_ENABLED` + ancrage configuré), aucun actif par défaut ; chain_id verrouillé 84532 ; kill-switch = `/stop` Telegram existant (`outgoing_pause`), pas de mécanisme parallèle ; chemin structurellement séparé de `wallet_guard` (verrouillé par `test_coherence`). Sizing par critère de Kelly (demi-Kelly, plafonné, sur la vraie calibration `vc_predictions`) sur un capital de répétition fictif. Télémétrie complète par cycle (latence/hésitation, erreurs, coupe-circuit auto après 4 échecs consécutifs, auto-guérison au cycle suivant) — chiffres agrégés publics sur le cockpit (`GET /api/aria/sepolia-status`). But : que Sepolia soit le test le plus dur, pour que le mainnet (qui garde la validation humaine) soit ensuite simple. **Déployé et vérifié 08/07 nuit** : wallet dédié financé (testnet, faucet), statut public confirmé propre (`enabled:true`, aucune erreur). Reste en `skipped_no_ledger` tant que `AriaLedger.sol` n'est pas déployé sur Sepolia (étape distincte, pas encore faite).
- **Swap de test Sepolia (09/07) — CODÉ, PAS ENCORE ARMÉ** : décision opérateur explicite « swap réel sur Sepolia, actif de test » — `sepolia_wallet.send_test_swap_transaction` (wrap WETH → approve → `exactInputSingle` Uniswap V3, trois transactions réellement signées) + gate additif `ARIA_SEPOLIA_SWAP_ENABLED` (au-dessus des 3 gates existants), plafond dur `MAX_TEST_SWAP_WEI` (~0.002 ETH), montant fixe `TEST_SWAP_AMOUNT_WEI` jamais dimensionné par Kelly. Câblé dans `run_autonomous_cycle` : tentative de swap indépendante de l'ancrage de décision sur BUY — échec de l'un n'efface jamais le succès de l'autre. Porte sur une paire de TEST configurée (`ARIA_SEPOLIA_SWAP_ROUTER`/`ARIA_SEPOLIA_SWAP_TOKEN_OUT`), **jamais** le token candidat réellement analysé (inexistant sur Base Sepolia — chaîne différente de Base mainnet, aucun contrat en commun). **Bloquant avant activation** : routeur/token de sortie non vérifiés on-chain — cette session cloud n'a pas d'accès RPC sortant (bloqué par le proxy réseau de l'environnement, testé et confirmé). Vérification à faire depuis le VPS (accès réseau réel) : confirmer qu'un routeur Uniswap V3 (ou équivalent) a du bytecode déployé sur Base Sepolia et qu'une pool WETH/X a une liquidité réelle non nulle, avant de renseigner les env vars et d'armer le gate.
- **Relay chat opérateur/Claude/ARIA (08/07 nuit) — EN LIGNE** : `relay_chat.py`, réutilise le canal Telegram EXISTANT d'ARIA (pas de second bot). Deux routes gatées par un accès dédié étroit (`GET/POST /api/aria/relay/*`), distinct du secret admin. Vérifié en réel : lecture de l'historique Telegram réussie. **Limite d'architecture** : une session Claude Code en environnement cloud/web n'a pas d'accès réseau sortant vers le VPS (politique de l'environnement, non contournable) — la lecture/écriture autonome du relay nécessite Claude Code **en local** (desktop). Depuis une session cloud, le pont reste utilisable mais manuel (l'opérateur relaie via `curl`).
- **Exam pédagogique (08/07 nuit) — EN LIGNE** : `exam.py`, gaté `ARIA_EXAM_ENABLED`, statut public `GET /api/aria/exam-status`.
- **Conversation relay ARIA <-> Claude Code (08/07 nuit) — EN LIGNE, CONFIRMÉ EN PROD** : `relay_conversation.py`, cycle heartbeat `relay_conversation_cycle` (15 min), `ARIA_RELAY_AUTOREPLY_ENABLED=true` actif sur le VPS. ARIA répond dans sa propre voix (LLM, sans préfixe) uniquement quand le DERNIER message du relay vient de "claude" — auto-limitant, pas de boucle infinie. Prompt système explicite : c'est Claude Code, pas l'opérateur, aucune action/compétence ne peut être déclenchée depuis cet échange. Plafond quotidien (`MAX_AUTOREPLIES_PER_DAY = 40`), respecte le kill-switch `/stop` existant. **Premier échange bot-à-bot réel vérifié** (capture Telegram) : ARIA a répondu de façon autonome à un message de Claude, sans intervention opérateur.
- **Claude Code installé DIRECTEMENT sur le VPS (08/07 nuit)** : `/opt/aria` (Node.js 20 + `npm install -g @anthropic-ai/claude-code`) — résout la limite d'une session cloud (pas d'accès réseau sortant vers le VPS, politique d'environnement) et d'un clone local Windows (désynchronisation manuelle permanente). `/opt/aria` EST le clone toujours à jour (celui de `deploy.sh`), avec accès réseau normal et accès direct à `http://127.0.0.1:8000` (contourne nginx/le verrou Basic Auth du domaine public). C'est la session à privilégier pour toute interaction Telegram/relay en direct.
- **Corrigé (08/07 nuit) — hallucination sur son propre modèle LLM** : ARIA a affirmé en conversation réelle "je tourne sur Claude Opus 4.8" avec assurance — faux/invérifiable (son modèle standard est Grok via Virtuals/Spark ; Claude Opus 4.8 n'est utilisé qu'en mode "develop" interne). Aucun fait grounded ne couvrait ça. Corrigé : `grounding.py:grounded_llm_identity()` porte maintenant une ligne explicite — elle doit dire honnêtement qu'elle ne connaît pas avec certitude le modèle exact derrière une réponse donnée, plutôt que d'en inventer un.
- **Boîte de dépôt de connaissance (08/07 nuit) — EN LIGNE, déployée et activée (`ARIA_KNOWLEDGE_INBOX_ENABLED=true`)** : `docs/aria-learning-inbox/` (dépose des notes brutes) + `skills/knowledge_inbox.py`, cycle heartbeat `knowledge_inbox_cycle` (360 min), gaté `ARIA_KNOWLEDGE_INBOX_ENABLED` (off par défaut). Repère une note non traitée, PROPOSE (jamais n'impose) son intégration dans `knowledge/*.yaml`/`canonical_facts.yaml` via une ISSUE GitHub (label `aria-knowledge-proposal`) — jamais un commit ni une fusion autonome, revue humaine requise. Chaque note n'est proposée qu'une seule fois (mémorisé localement). Distinct de `CLAUDE.md` (qui reste réservé au briefing de Claude Code).
- **Revue de performance ARIA par Claude (09/07) — EN LIGNE, déployée et activée (`ARIA_CLAUDE_MENTOR_ENABLED=true`)** : `skills/claude_mentor.py`, heartbeat `claude_mentor_cycle` (60 min, throttle interne ~1x/jour), relais déjà actif. Corrige la première idée ("Claude bavarde avec ARIA sur Telegram") en un vrai retour d'entraînement ancré sur ses données de performance RÉELLEMENT mesurées (calibration `vc_predictions`, `paper_trader`, télémétrie `sepolia_autonomous` — fail-closed par source, jamais de donnée inventée). Appelle le vrai Claude Opus 4.8 déjà câblé en prod via la profondeur "develop" de Virtuals (`spark_config.DEFAULT_MODEL_DEVELOP`) — **aucun nouveau secret**, réutilise `aria_core.llm.chat_with_context`. Deux sorties : remarque dans le relais (ARIA répond en vrai) + proposition GitHub `aria-knowledge-proposal` si durable (même doctrine stricte que `knowledge_inbox.py` — jamais de commit/fusion autonome).
- **Adressage `@claude` dans le chat Telegram opérateur/ARIA (09/07)** : un chat à 3 identités visuelles distinctes est impossible avec un seul token de bot Telegram. Un message opérateur commençant par `@claude` n'active plus le pipeline LLM d'ARIA (accusé de réception seulement, `_handle_message` dans `telegram_bot.py`) — évite qu'ARIA réponde à la place de Claude. Le texte reste journalisé tel quel dans le relais.
- **Alertes proactives haute-conviction (09/07) — EN LIGNE, déployée et activée (`ARIA_HIGH_CONVICTION_ALERTS_ENABLED=true`)** : `skills/high_conviction_alerts.py`, heartbeat `high_conviction_alert_cycle` (60 min). Pousse une alerte Telegram dès que `candidate_ranking` (existant, rien dupliqué) fait remonter un candidat SAFE au-dessus du score composite (seuil 80/100) — signal de tri, jamais un ordre d'achat, renvoie vers `/vc <contrat>`. Un contrat alerté une seule fois (jamais de spam).
- **Overlay macro « Contexte marché » dans le rapport /vc — EN LIGNE, feu vert visuel opérateur confirmé (09/07)** : tâche #14 complète. Réutilise `btc_cycles.py` (`fetch_current_macro_phase`, déterministe, aucun LLM, cache 1h) → `VCResult.market_context` → section premium dans `vc_report.py` (même patron que ROI/TA). Géopolitique/réglementaire = seam volontairement vide (aucune source fiable branchée, à décider avec l'opérateur). Preview envoyé conformément à `architecture-extensibilite.md`, validé par l'opérateur (« le html est magnifique »). Code testé, mergé sur `main`.
- **Gestion de position paper-trading : stop suiveur + prise de profit échelonnée (09/07)** : `paper_trader.py` remplace la sortie binaire (100% à la cible OU à l'invalidation) par un stop suiveur (`TRAIL_STOP_PCT=15%`, ne se relâche jamais sous l'invalidation d'origine) + une prise de profit par tiers à +50/+100/+200% de gain (`reduce_position`, P&L partiel visible immédiatement). Migration de schéma à chaud non-destructive. Comportement de `run_paper_cycle` changé intentionnellement, tests mis à jour dans le même commit.
- **Vérifié code (09/07) — ARIA ne trade PAS sur Base Sepolia** : `sepolia_autonomous.py` relu ligne à ligne, aucun swap/DEX câblé (le testnet n'a pas de pool DEX indexé pour un token Base arbitraire). Le cycle décide, dimensionne en Kelly sur un capital fictif, puis ancre uniquement le hash de la décision sur `AriaLedger` — un test d'ingénierie logicielle et de discipline de sizing, jamais un trade réel.
- **Tâche #8 — autopsie pump/dump, moteur de connaissance 24/7 (09/07) — EN LIGNE, ACTIVÉE sur le VPS (`ARIA_PUMP_DUMP_AUTOPSY_ENABLED=true`).** `skills/pump_dump_autopsy.py`, heartbeat `pump_dump_autopsy_cycle` (3h), gaté `ARIA_PUMP_DUMP_AUTOPSY_ENABLED` (off par défaut). Le "collecte continue" existait déjà (`vc_crawl`/`vc_resolve`/`weekly_training`, câblés au heartbeat) — ce qui manquait : `weekly_training.resolve_due` clôture chaque pronostic sur un point-à-point entrée→échéance, ce qui masque un pump-puis-crash survenu ENTRE temps (entrée $1, pic à $4, retombé à $1.10 → le point-à-point dit "+10%" alors que le token a pris 4x et presque tout rendu). Ce module relit la vraie série OHLCV parcourue pendant la détention (`services/ohlcv`, réutilisé, rien dupliqué), détecte le pattern DÉTERMINISTIQUEMENT (`detect_pump_dump`, aucun LLM, seuils : pic ≥1.5x l'entrée puis retombée ≥40% depuis ce pic) et seulement si détecté, demande au LLM une autopsie courte (même profondeur `develop`/Opus 4.8 que `claude_mentor`, aucun nouveau secret). Deux sorties, même doctrine stricte que `knowledge_inbox`/`claude_mentor` : log local systématique + proposition d'issue GitHub (nouveau label `aria-playbook-proposal`, distinct de `aria-knowledge-proposal`) SEULEMENT si le LLM juge la leçon durable — jamais un commit ni une fusion autonome. C'est le socle des futurs "playbooks" : chaque issue acceptée par l'opérateur devient un motif réutilisable dans une future analyse. Ajout `vc_predictions.list_recently_closed` (lecture seule, réutilise la table existante).
- **Important — ne pas confondre `CLAUDE.md` et la connaissance d'ARIA** : `CLAUDE.md` est lu UNIQUEMENT par Claude Code (moi), jamais par ARIA elle-même. Ce qui façonne ce qu'ARIA sait/fait : `knowledge/*.yaml`, `truth_ledger/canonical_facts.yaml`, `knowledge/epistemic_core.yaml`, son code `skills/`. Grossir `CLAUDE.md` aide les futures sessions Claude Code, ça n'entraîne jamais ARIA — garder ce fichier compact (résumé + pointeurs), mettre les infos destinées à ARIA dans ses propres fichiers de connaissance.
- **Nettoyage dexpulse/Aria Market (09/07) — la partie "fausse affirmation" est FAITE, la partie "vrai produit" reste EN ATTENTE d'un nom.** Déclenché par un vrai incident : ARIA a répondu en Telegram "ajoute des paires dans Aria Market" — le bug venait de `holding.py`/`persona.md`/`epistemic_core.yaml` (une croyance donnée "certaine" à 100%)/`canonical_facts.yaml`/`content/faq.yaml`/`grounding.py`/`repertoire_skill.py` (auto-seed d'une fausse filiale) et une dizaine d'autres fichiers narratifs qui affirmaient "Aria Market est la filiale phare actuelle" — TOUS corrigés et alignés sur le seul fait déjà exact ailleurs : aucune filiale live, ARIA opère la holding directement. `repertoire_skill.execute_develop_repertoire` ne re-sème plus la fausse filiale. **Découverte en cours de route** : `vanguard/product-frontend/` (espace membre en iframe, servi en LIVE à la racine du backend via `vanguard/Dockerfile` — PAS du code mort, vérifié) reste réellement nommé "Aria Market" dans son UI (~15 endroits) + quelques labels backend (`auth.py:site_name`, `websocket.py` message de connexion). **Volontairement non touché** : l'opérateur n'a pas encore de nom de remplacement ("ARIA c'est l'IA, les futurs produits auront leur propre nom") — ne jamais mettre "ARIA" en placeholder ici, attendre une vraie décision de nom avant de renommer ces fichiers-là. Code mort confirmé et retiré au passage : `ProductFrame`/`MemberWelcome`/`ProductLaunchHint`/`ProductLaunchLink`/`resolve-product-session.ts` (jamais montés dans `vanguard/src`, remplacés par `/cockpit` + `/reports`). `docs/` (`VISION.md`, `AGENTS.md`, `ECOSYSTEM-REPOS.md`) pas encore traités — même bucket "en attente du nom".
- **Tâche #10 — mode bonding-phase Virtuals (09/07) — CORRIGÉ.** Le gap identifié le 08/07 (tokens Virtuals en phase de bonding recevant un AVOID/EXTREME mal fondé, faute de paire DexScreener — normal pour cette catégorie, pas un défaut) est fixé à la racine : `services/virtuals.py` avait déjà le client Strapi (détection pré-bonding) mais n'était utilisé que par `base_crawler` (découverte), jamais par le scan d'analyse. Ajout `VirtualsClient.fetch_by_address` (l'URL existait, jamais câblée à un fetch) + `acp_onchain_scan._resolve_bonding_phase` (best-effort, appelé UNIQUEMENT quand aucune paire DEX n'existe) + branche dédiée dans `_score_and_verdict` : un token en bonding confirmé n'est plus par défaut CAUTION/DANGER — le score se base sur la progression réelle vers la graduation (`virtualRaised`/42 000) et le nombre de holders, jamais une confiance inventée (progression indisponible → étiqueté comme tel, pas masqué). Comportement non-bonding strictement inchangé (testé). Le bruit X reste non consulté par `/vc` à la demande (radar X = découverte asynchrone séparée, toujours pas branché ici) — hors scope de ce correctif.
- **Tâche #9 — analyse technique/graphique "obligatoire" (09/07) — clarifiée et fermée sur le fond.** Vérifié avant de coder : `include_ta=True` était déjà systématique à chaque scan, et le LLM était déjà instruit d'ancrer entrée/invalidation/cible sur les niveaux OHLCV réels **quand ils existent**. Le vrai trou : quand ils n'existent PAS (aucune série OHLCV), aucune instruction n'encadrait le LLM — silence total, risque de prix inventé sans le dire. Décision opérateur déléguée ("choisis à ma place") sur la question business (le graphique détaillé + PNG reste-t-il premium-only ?) : **gate premium conservé tel quel** (différenciateur produit voulu, `vc_report.py`), correctif concentré sur le prompt LLM (`_build_untrusted_context`), donc actif sur TOUS les tiers puisque la thèse (`cible`/`invalidation`) est commune. Deux branches ajoutées : (1) token en bonding Virtuals sans OHLCV → explique pourquoi (progression réelle vers graduation, holders) au lieu du message générique ; (2) aucune donnée OHLCV et pas en bonding → instruction explicite de rester qualitatif, jamais un prix chiffré non soutenu. Régression testée : le grounding existant (TA réellement disponible) reste inchangé.
- **Recherche web Tavily + fix chemin opérateur (09/07 nuit 2) — EN LIGNE, testé en conditions réelles (détail complet : `docs/HANDOFF-2026-07-09-nuit2.md`).** `services/tavily.py` (patron dôme) derrière `web_verify.fetch_web_snippets`, opt-in (`ARIA_WEB_SEARCH_PROVIDER=tavily`, `TAVILY_API_KEY` en env). **Bug critique corrigé** : le chemin qui déclenche la recherche web (`brain.py::_general_response` → `resolve_calibrated_answer`) était gaté `public` uniquement — l'opérateur (admin) n'atteignait JAMAIS le web, contraire au principe « l'admin a le plein potentiel ». Corrigé : les questions d'actu de l'opérateur (`is_live_info_question`) passent par le chemin web aussi. Vérifié en réel (question Nvidia → vraies news + sources + crédit Tavily consommé). `aria_web` dans le health check + ligne `Web:` dans `/status` pour vérifier l'activation d'un coup d'œil.
- **Profil X @Aria_ZHC — seam livré (09/07 nuit 2).** `aria_core/x_profile.py` (était un seam vide documenté). `sync_x_profile()` aligne bio/nom/site sur la narrative existante, n'applique que sur écart réel. Commande `/x profile sync|preview|force` active (admin). Tâche heartbeat quotidienne encore gardée par `ARIA_X_PROFILE_SYNC_ENABLED` — **pas encore activée**.
- **Nettoyage narratif ACP/app-factory/$50-mois mort (09/07 nuit 2) — même famille que Aria Market.** Déclenché par une proposition ARIA en Telegram de lancer "marketplace ACP + lien Stripe payant" — vérifié qu'aucune action ne s'exécute (juste du texte), mais le PROMPT sous-jacent (`proactive.py`) et tout le module `entrepreneur_skill.py` poussaient encore cette stratégie abandonnée. **Bug public trouvé** : `community_feedback.py` répondait à de vrais visiteurs X avec ce narratif mort. Toutes les surfaces réalignées sur le pacte réel (`docs/protocole-argent-reel.md`) : pas de produit payant actif, priorité = prouver le track-record. Bug DEXPulse séparé trouvé et corrigé au passage (`knowledge/x_insight_relevance.py` affirmait encore "opératrice de DEXPulse", manqué par le grand nettoyage précédent).
- **Menu Telegram réduit au kill-switch (09/07 nuit 2, choix opérateur).** L'opérateur n'utilise jamais les slash-commandes. `/stop`/`/resume` seuls visibles dans le menu "/" ; les 14 autres commandes restent enregistrées et fonctionnelles si tapées (vérifié qu'aucune n'était supprimable sans casser un garde-fou ou un flux de clôture du track-record).
- **Pacte argent réel en deux étapes séquentielles (09/07 nuit 2, décision opérateur).** `docs/protocole-argent-reel.md` §3 : VC réel (85%) débloqué en premier sur le track-record paper ; trading réel (15%) débloqué SEULEMENT ensuite, une fois le VC réel ayant lui-même rejoué le barème des 8 cases sur son propre track-record réel.

## Moteur de légitimité (session 07/07 — drapeau brut → jugement de contexte, au cas par cas)
- `skills/mint_authority.py` + `knowledge/launchpads.yaml` : un mint n'est dangereux que si un DEV le contrôle (renounced / launchpad Virtuals-Flaunch-Clanker-Zora / contract / eoa / unknown). Normes par launchpad (Virtuals team ~15-20% = normal).
- `skills/dev_wallet.py` : builder engagé vs farmer (détient/achète/vend pour financer vs extraire/all-in, proportionnel à l'équipe).
- `skills/liquidity_depth.py` : ratio liquidité/mcap (100k → 30-40k mini), neutralisé sur courbe de bonding.
- `recalibration.py` : transparence exigée → escalade opérateur si token prometteur mais opaque.
- `skills/safety_screen.py` : `has_mint` basé ABI (fonctions appelables), plus la sous-chaîne source (faux positif `_mint` éliminé). Burn par motif (zéros+dead). `hard_fail` : une panne réseau ne bannit plus un bon token.
- **Carnet de bord** : `thesis_journal.py` (journal append-only + suivi de thèse : livre/stagne via `services/project_activity` GitHub) + `skills/chart_render.render_scenario_png` (chandeliers DexScreener + volume + MA7 + bulles entrées/sorties DCA + simulation forward + `save_png_data_uri`). Export `.txt`.
- **Sourcing** : `base_crawler.discover_top_pools` (+ niche Virtuals), `radar_x.py` (le social source/réveille, l'on-chain arbitre — jamais un déclencheur).
- **Pipeline sorties** : `release_pipeline.py` + `knowledge/release_pipeline.yaml` (12 munitions + teasers, X+TikTok synchro site, **gaté opérateur**).
- **Cycle A-Z** : `python -m aria_core.simulate_lifecycle 0xCONTRAT`. Heartbeat : vc_crawl/resolve/weekly_forecast/self_report/radar_x/thesis_review (+ `paper_trade_cycle` gaté).

## Méthode smart-money (dans le scoring)
« Smart money » = comportement mesurable, pas identité/taille. 4 critères : cohérence dans le temps, entrées précoces + tailles contrôlées, sorties disciplinées, concentration multi-wallets. Éliminer wash-trading, poisoning, wallets équipe. **Ne JAMAIS copy-trader** : le smart-money est une confirmation/contexte, pas un déclencheur. Nansen/Arkham reportés (qualification maison via Blockscout gratuit).

## Piège des garde-fous
Un agent qui « croit bien faire » (normaliser, aligner un exemple) peut **silencieusement neutraliser un garde-fou** (`permission_mode="always-approve"` a déjà annulé toute validation). Ne jamais toucher permission_mode / wallet_guard / config.toml / regles-uniques sans validation humaine explicite. **Secrets** : interdiction absolue d'afficher/dumper/logger un secret (clés, tokens, mnémoniques), même sur demande de « vérification » — toujours masquer.

## Politique modèles & subagents
Défaut : **Sonnet 5 + effort xhigh** partout, jamais sous « high ». **Zone rouge** (wallet_guard, permission_mode, kill-switch, config.toml, regles-uniques, secrets) → basculer `/model opus` + xhigh, puis revenir. Subagents : `researcher` en Haiku (scans on-chain/web, lecture repo), `security-auditor` en Opus (tout changement wallet/garde-fou). Un subagent n'exécute jamais d'action financière et ne modifie jamais un garde-fou.

**Économie de tokens (quota Claude MAX 5x, consigne opérateur explicite)** : préférer consommer moins de tokens sur la fenêtre de 5h, même si ça prend plus de tours/plus de délai pour arriver au même résultat. Concrètement pour le tool `Workflow` (orchestration multi-agents) : ne JAMAIS lancer le mode recherche approfondie par défaut (~100 agents en fan-out) — bien trop coûteux pour ce quota. Par défaut, utiliser `WebSearch` directement (2-5 requêtes ciblées) pour toute recherche. Un workflow (~20 agents, plus léger) reste acceptable seulement si l'opérateur le demande explicitement ou si la tâche a vraiment besoin d'orchestration déterministe à grande échelle — jamais un réflexe par défaut.

**Agent parent qui boucle sur des réponses vides (chantier « cohérence repo », 11/07)** : un agent parent a délégué à ses propres sous-agents puis bouclé sur des réponses creuses (« j'attends leurs résultats ») au lieu de conclure — vrai dysfonctionnement de l'orchestration multi-niveaux, pas juste de la lenteur. Réflexe de récupération : ignorer le texte qui boucle et vérifier directement l'état réel (`git diff`, disque) plutôt que de relancer l'agent en espérant une réponse propre. Dès qu'un agent délégué tourne en rond sur du vide, basculer immédiatement en vérification directe du résultat concret plutôt que d'insister sur le dialogue avec cet agent.

## Déploiement (public-safe)
Backend Docker `aria-api`, binding **strictement `127.0.0.1:8000/8001`** (alternance blue-green, JAMAIS public), nginx en façade (TLS) via un upstream dédié (`/etc/nginx/conf.d/aria-api-upstream.conf`, hors dépôt). Data bind-mount `/opt/aria-data`. `vanguard/deploy.sh` (build + health check). **Rollback quasi instantané (#154, 13/07)** : blue-green par alternance de port — le nouveau conteneur est lancé et vérifié PENDANT que l'ancien tourne encore ; l'ancien n'est supprimé qu'après confirmation du trafic réel à travers nginx. Un health-check cassé n'entraîne plus AUCUN downtime (l'ancien reste servi). Complété par `willfarrell/autoheal` (sidecar, redémarre un conteneur `unhealthy` — panne transitoire, pas un rollback de version) + un disjoncteur maison (`vanguard/scripts/autoheal-circuit-breaker.sh`, plafonne à 3 redémarrages/10 min avant de mettre autoheal en pause avec log clair). Détail complet : `docs/deploy-rollback-blue-green.md`. Vitrine : `vanguard/deploy-vitrine.sh` (même gap corrigé côté statique, #157, 13/07 — `.old` conservé jusqu'à vérification à double critère : heuristique de contenu + marqueur de build exact `build-info.txt`, avec retry ~10s post-reload nginx ; restauration + contenu cassé conservé dans `.failed` en cas d'échec). **Accès VPS, IP et infra : privés, dans `aria-ops`.** Sécu prioritaire : SSH clé-only + fail2ban + firewall (l'IP a fuité dans l'historique public → durcir SSH est le vrai correctif).

## Astuce : push GitHub quand `git push` échoue
Si le proxy git de l'environnement meurt (`fatal: could not read Username`), pousser via l'API GitHub (`mcp__github__push_files`) contourne le proxy. Puis VPS : `git pull && ./vanguard/deploy.sh`.

## Lecture requise (le cerveau détaillé)
`docs/etat-systeme-cable.md` (état câblé, faits établis) · `docs/architecture-extensibilite.md` (d'abord) · `docs/strategie-aria-investissement.md` · `docs/protocole-argent-reel.md` · `docs/roadmap-campagne.md` · `docs/playbook-editorial-aria.md` · le HANDOFF le plus récent `docs/HANDOFF-*.md`.

## Format de réponse
Court, clair, sans remplissage, sans exposer le raisonnement interne. Jamais le mot « Verdict » comme label. À chaque fin de tâche, proposer un prochain pas (dans le respect de la validation explicite). Commits : `Co-Authored-By: Claude <noreply@anthropic.com>` ; jamais d'identifiant de modèle dans commit/PR/artefact ; pas de PR sans demande explicite.

**Dispatch VPS (session cloud « commandement », 11/07, complété 12/07) — règle permanente, ne jamais oublier.** Toute consigne destinée à un VPS (Principal/Secondaire/Research) doit TOUJOURS être formatée : en-tête coloré hors bloc (🟠 **Pour VPS Principal :** / 🔵 **Pour VPS Secondaire :** / 🟣 **Pour VPS Research :**) suivi d'un bloc de code (\`\`\`) contenant le texte exact à coller — jamais en texte normal, même pour une simple confirmation ou un "vas-y". Le bloc de code déclenche le bouton copier natif du chat ; sans lui l'opérateur doit sélectionner le texte à la main. Se relire avant d'envoyer tout message qui mentionne une prochaine étape pour un VPS. Incident vécu (11/07) : plusieurs consignes envoyées en texte simple, l'opérateur a dû relancer manuellement, VPS Research est resté à l'arrêt en attendant un dispatch jamais réellement formaté/envoyé. **Trois rappels obligatoires dans CHAQUE bloc dispatché (décision opérateur explicite, 12/07 ; 3e ajouté 13/07 après un deuxième incident du même type)** : (1) auto-identification — le VPS doit commencer son prochain rapport par `[VPS Principal]`/`[VPS Secondaire]`/`[VPS Research]` (oublié une fois par Research le 12/07) ; (2) autorité de commit — seule la session cloud commit/pousse sur `main`, le VPS prépare et pousse uniquement sur une branche temporaire dédiée (cf. entrée "Autorité de commit centralisée" ci-dessus) ; (3) **push exclusivement via `scripts/safe-push.sh <ARIA|aria-ops> <nom-de-branche>`, jamais `git push origin ...` à la main** — le script (livré 13/07) vérifie lui-même que le remote local correspond bien au dépôt visé avant de pousser (refus bloquant et visible sinon) et pousse toujours vers une URL explicite, jamais l'alias `origin`. Exemple à coller dans le dispatch : `bash scripts/safe-push.sh ARIA claude/mon-sujet-temp`. Un alias `origin` mal configuré rendait un push "réussi" totalement silencieux sur le mauvais dépôt (vécu le 12/07 : VPS Research sur `aria-ops` au lieu d'`ARIA`) — le script rend cette classe d'erreur impossible plutôt que de compter sur la mémoire d'un agent pressé. Ces trois rappels vont dans le bloc de code lui-même (pas seulement en préambule hors bloc), pour survivre au copier-coller tel quel.

**Précision importante (13/07, deuxième incident distinct du premier, pas la même cause)** : un rapport Research annonçait une note `docs/aria-learning-inbox/` poussée avec succès -- introuvable côté commandement dans un premier temps, mais PAS un mensonge ni un remote cassé : le remote `origin` de cette session Research pointait correctement vers `aria-ops` (son dépôt de travail habituel, validé), et le commit y était réellement présent (confirmé via `git ls-remote`). Le vrai problème : `docs/aria-learning-inbox/` est un chemin qui vit dans **ARIA**, pas dans `aria-ops` -- une consigne qui demande d'écrire dans ce dossier doit donc TOUJOURS préciser explicitement `ARIA` comme dépôt cible dans le dispatch, jamais supposer que le remote par défaut d'une session VPS correspond au bon dépôt pour CE livrable précis. Contenu récupéré manuellement (ajout de `aria-ops` à cette session, lecture directe du commit, recopié proprement dans `ARIA` avec auteur `Claude <noreply@anthropic.com>` -- jamais l'email réel de l'opérateur qui apparaissait dans le commit original côté aria-ops). Réflexe à appliquer désormais : pour tout dispatch qui produit un livrable à un chemin donné, nommer explicitement le dépôt cible de ce chemin, ne jamais le laisser implicite.

**Ligne d'objectif en fin de réponse (décision opérateur explicite, 12/07)** : à chaque réponse qui dispatche une tâche à un VPS, la TOUTE DERNIÈRE ligne (après le/les bloc(s) de dispatch) doit énoncer explicitement et brièvement l'objectif de la tâche envoyée — un aperçu rapide, sans avoir à lire tout le bloc de code, pour que l'opérateur sache d'un coup d'œil ce qui est en cours sur chaque VPS.

**Mode Plan avant exécution sur chaque VPS (décision opérateur explicite, 12/07)** : avant d'envoyer une nouvelle tâche à un VPS, l'opérateur bascule la session cible en mode **"Plan"** (`Shift+Tab` pour faire défiler les modes de permission). Le dispatch doit alors demander explicitement d'élaborer un plan sans exécuter (« élabore un plan, n'exécute rien »). Le VPS renvoie son plan à l'opérateur, qui le relaie au commandement (session cloud) pour relecture avant tout « go ». Une fois le plan validé, l'opérateur repasse la session en mode "Auto" et donne le feu vert pour exécuter. Objectif : appliquer systématiquement aux VPS la méthode déjà écrite plus haut (Analyser → Proposer un plan → attendre "go" → Implémenter), qui jusque-là n'était pas formalisée pour les sessions VPS spécifiquement.

**La relecture d'un plan VPS doit être une vraie relecture critique, pas un tampon (décision opérateur explicite, 14/07).** Recevoir un plan en mode Plan ne sert à rien si le commandement se contente de confirmer qu'il est cohérent avec ce qui a déjà été discuté — l'objectif est d'avoir le MEILLEUR plan à chaque fois, pas juste un plan validé. Avant de donner le « go », chercher activement de vrais trous techniques (pas seulement la cohérence globale) : effets de bord d'une généralisation (ex. 14/07 — généraliser un contrôle anti-wash-trading d'UN token à TOUS les tokens d'un wallet cassait silencieusement l'exclusion du pool/routeur DEX, qui n'existait que pour une seule adresse ; sans correction, la plupart des traders actifs normaux auraient été disqualifiés à tort, puisque la majorité des swaps repassent par 1-2 contrats de routeur). Si un trou est trouvé après qu'un plan a déjà reçu un premier feu vert, le dire et le corriger avant l'implémentation plutôt que de laisser filer — un plan déjà « approuvé » n'est pas figé tant que le code n'est pas écrit.

Tu es dans un projet persistant.
