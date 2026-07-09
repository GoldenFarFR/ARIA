# HANDOFF — 2026-07-09 nuit (suite 2) — Tavily, fix chemin web opérateur, pump/dump activé, nettoyage ACP mort

Suite directe de `docs/HANDOFF-2026-07-09-nuit.md` (même journée, segment encore plus tardif).
Lire les deux + `CLAUDE.md` + `docs/etat-systeme-cable.md`. Tout est fusionné dans `main` ET
déployé sur le VPS — dernier commit confirmé par health check opérateur : `94274d0277984e3`.

## #8 — Autopsie pump/dump : codée ET activée en prod
`ARIA_PUMP_DUMP_AUTOPSY_ENABLED=true` posé sur le VPS. Détection déterministe (aucun LLM) d'un
pump-puis-crash survenu PENDANT la détention d'un pronostic clôturé — le point-à-point
entrée→échéance de `weekly_training.resolve_due` masquait ce pattern (entrée $1, pic $4, retombé
$1.10 → lisait "+10%" alors que le token a 4x et presque tout rendu). Si détecté, autopsie LLM
courte + proposition d'issue GitHub (`aria-playbook-proposal`) si la leçon est jugée durable —
jamais un commit/fusion autonome, même doctrine que `knowledge_inbox`/`claude_mentor`.

## #40 — Profil X @Aria_ZHC : seam livré (`aria_core/x_profile.py`)
Le module était un seam documenté mais jamais écrit (`directives.md` en parlait, `heartbeat.py`
et `visual_autonomy.py` l'importaient déjà en `try/except ModuleNotFoundError` défensif, et la
commande Telegram `/x profile sync|preview|force` existait déjà, muette faute du module). Écrit
`sync_x_profile()` : compare bio/nom/site cibles (dérivés de `narrative.x_bio()`/
`identity.ARIA_DISPLAY_NAME`/`narrative.holding_site_url()` — rien de nouveau à rédiger) au
profil X live, n'applique que s'il y a un vrai écart. **Champ "lieu" volontairement absent** de
la cible : aucune source canonique dans le repo, jamais une donnée inventée.

Gating à deux niveaux : la commande Telegram `/x profile sync` reste toujours disponible
(admin-only, l'admin qui tape la commande EST l'autorisation) ; la tâche heartbeat quotidienne
(seul chemin réellement autonome, personne ne clique) reste en plus gardée par
`ARIA_X_PROFILE_SYNC_ENABLED` (OFF par défaut sur le VPS — pas encore activée, l'opérateur n'a
pas encore vu/validé le résultat une première fois).

## #53 — Nettoyage narratif ACP/app-factory/$50-mois mort (même famille de bug qu'Aria Market)
Déclenché par un screenshot Telegram : ARIA a proposé de lancer "marketplace ACP + poll X + tweet
produit avec lien Stripe à 9$" ce soir — alors que l'ACP est abandonné (confirmé) ET que Stripe a
été supprimé du code cette session même. **Vérifié : rien ne s'est exécuté** (`run_founder_ping`
ne fait que renvoyer du texte à Telegram, `/directive` n'appelle que `append_directive`, aucune
action financière/marketing possible depuis ce chemin) — mais le PROMPT qui génère ces idées
hardcodait "ACP marketplace + app payante v0" comme "priorité #1 si revenu=0". Recherche large
(agent) avant correction, comme pour Aria Market, pour ne rien louper :

- **Bug PUBLIC le plus grave** : `community_feedback.py` répondait à de VRAIS visiteurs X posant
  une question roadmap/revenu avec "Marketplace ACP + signaux ZHC d'abord" — jamais gaté,
  potentiellement déjà vu par de vrais gens. Corrigé en "preuve du track-record avant tout
  produit payant".
- Surfaces injectées dans CHAQUE appel LLM : `persona.md` (Mission #7), `memory/goals.py`,
  `knowledge/aria_goals.yaml`, `brain.py` (bridge ZHC).
- Tâches heartbeat actives quotidiennes : `entrepreneur_skill.py` (playbook activation
  entier — steps ACP/app factory/Play Store/Gumroad remplacés par le vrai chemin : prouver le
  track-record selon `docs/protocole-argent-reel.md`), `knowledge/cultivation_curriculum.py`
  (phases + header), `knowledge/zhc_peer_agents.py` (`revenue_hypotheses` renvoie `[]` — aucune
  hypothèse de monétisation en test aujourd'hui ; corrigé aussi le "Charles" `lesson_for_aria`
  qui citait encore "DEXPulse signals" comme edge actuel).
- Autres : `proactive.py` (founder_ping priorité = track-record, jamais un produit),
  `autonomy_revenue.py`, `operator_conversational.py`, `operator_readiness.py` (acp-cli absent
  n'est plus signalé comme un gap à corriger), `capability_rubric.yaml` (rubrique business =
  paliers du pacte argent réel, plus de metric/target dollars auto-généré), `revenue_goals.py`.

**Bug DEXPulse séparé trouvé au passage** (manqué par le grand nettoyage Aria Market/DEXPulse
plus tôt cette nuit) : `knowledge/x_insight_relevance.py` — le prompt de triage de la veille X
affirmait encore "Tu es ARIA... et opératrice de DEXPulse" (présent, comme si c'était live).
Corrigé, ET l'axe de pertinence étendu à l'écosystème Base/tech (onchain, builder, agentkit,
coinbase, ecosystem, standard) — prépare #52 (voir plus bas).

## #54 — Recherche web fiable (Tavily) + correction du VRAI bug qui la rendait inutile pour l'opérateur
`web_verify.py` (DuckDuckGo) échoue en 403 systématique (throttle/backoff absents — seul test
rouge pré-existant de la suite). Ajouté `services/tavily.py` (patron dôme complet : singleton,
throttle, backoff 429/403, retry timeout/5xx, `available`/`error`, jamais de donnée inventée),
branché derrière le point d'entrée unique `web_verify.fetch_web_snippets` via un provider-switch
(`ARIA_WEB_SEARCH_PROVIDER=tavily`, DDG reste le fallback si Tavily indisponible). Clé lue
UNIQUEMENT depuis l'env `TAVILY_API_KEY` — jamais en dur, jamais loguée.

**Découverte critique en testant en réel** : Tavily était bien câblé et confirmé actif (health
check `aria_web: provider tavily, key present: true`), mais quand l'opérateur (admin) posait une
question d'actu réelle sur Telegram ("dernières news Nvidia ?"), ARIA répondait quand même "web
pas actif ici". Root cause : le chemin qui déclenche la recherche calibrée/web
(`resolve_calibrated_answer` → `web_first_answer` → Tavily/DDG) dans `brain.py::_general_response`
était gaté **`public` uniquement** — la conversation OPÉRATEUR (public=False) n'atteignait jamais
ce chemin, quel que soit le sujet. Incohérent avec le principe explicite de l'opérateur : « moi
l'administrateur suis le seul à pouvoir utiliser le plein potentiel d'ARIA ». Corrigé : les
questions d'ACTU (`is_live_info_question` — exclut déjà les sujets perso opérateur et les
produits ARIA) passent par le chemin calibré/web quel que soit l'auditoire ; le reste de la
conversation fondateur (opinion, stratégie) est strictement inchangé.

**Vérifié en conditions réelles après déploiement** : question Nvidia sur Telegram → vraies news
datées (Vera Rubin, levée $25Mds, RTX Spark) + sources vérifiables (nvidia.com, Yahoo Finance) +
1 crédit Tavily consommé (confirmé par l'opérateur sur son dashboard). Boucle fermée de bout en
bout.

Ajouté aussi : `aria_web` dans le health check public (`provider`, `tavily_key_present` — jamais
la clé) + ligne `Web: tavily (Tavily ✅)` dans `/status` Telegram, pour vérifier l'activation d'un
coup d'œil sans avoir à tester le comportement à chaque fois.

**Sécurité** : la clé Tavily de l'opérateur a transité en clair dans le chat (collée par erreur)
— traitée comme exposée, jamais écrite dans un fichier/commit/log. Opérateur a régénéré une
nouvelle clé sur tavily.com avant de la mettre dans le `.env` VPS. Bon réflexe à reproduire.

## Menu Telegram réduit au kill-switch uniquement (choix opérateur explicite)
L'opérateur n'utilise jamais les slash-commandes (conversation naturelle uniquement). Après
vérification qu'AUCUNE des 16 commandes existantes n'était réellement supprimable sans casser
quelque chose (`/github` verrouillé par `test_coherence`, `/vcresult`/`/issue` closent les
pronostics/thèses du track-record, `/thesis` testé...), la solution retenue est de **réduire le
menu "/" visible** (`set_my_commands`) à `/stop`/`/resume` (le kill-switch sécurité) uniquement.
Les 14 autres commandes restent enregistrées et fonctionnelles si tapées — elles n'encombrent
juste plus le menu.

## Pacte argent réel — séquencement en DEUX étapes (décision opérateur, `docs/protocole-argent-reel.md` §3)
L'argent réel ne se débloque plus d'un coup sur les deux poches. Ordre imposé, chaque étape
rejoue le barème complet des 8 cases (§2) sur SON PROPRE track-record :
- **Étape A** : VC réel (poche 85%) débloqué en premier, une fois les 8 cases cochées sur le
  track-record **paper**. La poche spéculation reste en paper pendant toute cette étape.
- **Étape B** : Trading réel (poche 15%) débloqué ensuite seulement, une fois les 8 cases
  REJOUÉES sur le track-record du **VC réel** (pas le paper d'origine). Jamais avant.

## Watchlist X élargie
`knowledge/x_watchlist.yaml::opportunity_handles` : ajout `@Whale_AI_net` (partage beaucoup de
projets ambitieux — demande opérateur) à côté de `@base`. Lecture seule, à trier via
`safety_screen` + analyse on-chain complète, jamais un déclencheur direct.

## État des tests
Aria-core : 1412 passed / 7 skipped / 1 échec connu sans lien (rugby DDG live, réseau). Coherence
guardrail inclus. Nouveaux fichiers de test ce segment : `test_x_profile.py`,
`test_telegram_x_profile_command.py`, `test_tavily.py`, `test_brain_operator_web.py`, tous en CI.

## Ce qui reste en attente
- **#52** (veille écosystème Base) : préparation faite (axe Base ajouté à `x_insight_relevance.py`,
  `@Whale_AI_net` en watchlist) mais le fetch réel d'`opportunity_radar.py` (timeline `@base` +
  `@Whale_AI_net` → digest opérateur) n'est PAS encore câblé. Prochaine étape concrète.
- **Idées évoquées ce soir, pas encore formalisées en tâche** : (1) scan de secrets/IP/PII en CI
  (gitleaks) — le vrai gap sécurité identifié pour un repo public, aurait attrapé la clé Tavily
  si elle avait été commitée ; (2) sourcing blue-chip externe (bot Telegram tiers, ex.
  TransmuteOracle_bot) — bloqué : Telegram interdit à un bot de lire les messages d'un autre bot,
  nécessiterait un userbot MTProto (session opérateur, sensible) ou un relais manuel ; (3) miner
  les conversations opérateur/ARIA profondes sur Telegram pour proposer (jamais imposer) des
  enseignements durables — aujourd'hui rien ne capte ça automatiquement, seuls `/learn` et la
  boîte de dépôt explicite le font.
- Nom de remplacement pour `product-frontend`/"Aria Market" — toujours en attente (opérateur:
  "je sais pas, ARIA est l'IA, les futurs produits auront leur propre nom").
- Vérification on-chain réelle du routeur/pool Sepolia avant d'armer `ARIA_SEPOLIA_SWAP_ENABLED`
  (route de vérification prête : `GET /api/aria/sepolia/code`, pas encore utilisée).
- `ARIA_X_PROFILE_SYNC_ENABLED` : seam livré, pas encore activé (l'opérateur n'a pas encore
  déclenché `/x profile sync` une première fois pour voir le résultat).
- Backlog sans blocage : #11, #13, #17, #19-23, #29, #32, #34.

## Auto-critique honnête (retour donné à l'opérateur ce soir, à charge pour la suite)
Le vrai goulot n'est pas la surface construite (énorme) mais **le moteur de preuve qui ne tourne
pas encore** : paper-trading gaté OFF, track-record public proche de zéro malgré une thèse
entièrement fondée sur "preuve avant promesse". La dette narrative revient sans cesse (2 gros
nettoyages "récit périmé" en une seule nuit, chacun étalé sur ~15-20 fichiers) — pour une IA dont
la valeur est "faits vérifiés", ses propres fichiers de croyance dérivent plus vite qu'on ne les
corrige. Priorité réelle suggérée : faire tourner la preuve avant d'ajouter une capacité de plus.
