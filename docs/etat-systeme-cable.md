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
- ~~`aria_core.x_profile` : module non livré~~ **LIVRÉ le 09/07 nuit 2** (`sync_x_profile()`) —
  voir section dédiée plus bas. Commande Telegram active ; tâche heartbeat quotidienne encore
  gardée par `ARIA_X_PROFILE_SYNC_ENABLED` (pas encore activée).
- `opportunity_radar.py::mine_threads`/`extract_opportunities` : le scoring existe (langage
  d'opportunité + accroches techniques Base), mais le FETCH réel d'une timeline X (`@base`,
  `@Whale_AI_net`) n'est pas câblé — c'est la pièce qui manque pour #52.

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
- **ARIA NE TRADE PAS sur Base Sepolia — vérifié code le 09/07** (`sepolia_autonomous.py`
  relu ligne à ligne) : sur signal BUY, aucun swap/DEX n'est appelé — il n'existe même pas
  de client DEX/router pour Sepolia dans `onchain/` (seuls fichiers Sepolia du dôme :
  `sepolia_wallet.py`, `sepolia_autonomous.py`, `sepolia_rehearsal.py`, `anchor.py`,
  `wallet_guard.py`). Le testnet n'a pas de pool DEX indexé pour un token Base arbitraire
  (documenté dans le module lui-même). Ce que fait réellement le cycle : elle décide (LLM +
  données réelles), dimensionne en Kelly sur un capital **fictif** (`REHEARSAL_NOTIONAL_USD`,
  10 000 $ de répétition), puis **ancre onchain le hash de sa décision** (signature réelle,
  gas réel, nonce réel) sur `AriaLedger` — un test d'ingénierie logicielle et de discipline
  de sizing, jamais une exécution de trade, exactement comme voulu par l'opérateur (« un
  test d'ingénierie logicielle, pas une validation de stratégie de trading »). Aucun ETH ne
  change de mains sur un swap, aujourd'hui ni demain sur ce module tel que conçu.
- **Relay chat (chat à 3)** — module `relay_chat.py` (table SQLite dédiée, token
  `ARIA_RELAY_ACCESS_TOKEN` séparé du secret admin, `send_relay_reply`/`send_aria_relay_reply`
  pour poster réellement dans le Telegram existant) : `ARIA_RELAY_ACCESS_TOKEN` actif, `GET /api/aria/relay/recent` +
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
- **Deux chemins Sepolia distincts, ne pas confondre** : `sepolia_autonomous.py` (ci-dessus,
  décide ET exécute seule, structurellement séparé de `wallet_guard`) est différent de
  `onchain/sepolia_rehearsal.py`, qui lui passe par `wallet_guard.escalate_spend` (clic
  Telegram Oui/Non classique) pour l'ancrage — un second chemin Sepolia, human-confirmed
  celui-là, testnet uniquement lui aussi. Les deux sont corrects et gatés séparément ; le
  premier n'emprunte jamais le second, verrouillé par `test_coherence`.
- **Corrigé (08/07 nuit) — hallucination sur son propre modèle LLM** : ARIA a affirmé "je
  tourne sur Claude Opus 4.8" en conversation réelle, sans fondement (son modèle standard est
  Grok via Virtuals/Spark ; Claude Opus 4.8 n'est utilisé qu'en mode "develop" interne, jamais
  annoncé comme tel). `grounding.py:grounded_llm_identity()` porte maintenant une ligne
  explicite lui interdisant d'affirmer un nom de modèle précis sans certitude.
- **Boîte de dépôt de connaissance — EN LIGNE, déployée et activée** : `docs/aria-learning-inbox/`
  + `skills/knowledge_inbox.py`, heartbeat `knowledge_inbox_cycle` (360 min),
  `ARIA_KNOWLEDGE_INBOX_ENABLED=true` sur le VPS. Lit une note non traitée, PROPOSE (jamais
  n'impose) son intégration dans `knowledge/*.yaml`/`canonical_facts.yaml` via une issue
  GitHub (`aria-knowledge-proposal`) — jamais un commit ni une fusion autonome. Une note n'est
  proposée qu'une seule fois (mémorisé localement). `CLAUDE.md` reste réservé au briefing de
  Claude Code, jamais à la connaissance d'ARIA elle-même.
- **Claude Code tourne aussi DIRECTEMENT sur le VPS (08/07 nuit)** : installé dans `/opt/aria`
  (Node.js 20 + `npm install -g @anthropic-ai/claude-code`). Accès réseau normal + accès
  direct `http://127.0.0.1:8000` (pas de nginx, pas de verrou Basic Auth) — c'est la session
  à privilégier pour toute interaction relay/Telegram en direct, plutôt qu'une session cloud
  (bloquée réseau) ou un clone local Windows (désynchronisation manuelle).
- **Revue de performance ARIA par Claude — EN LIGNE, déployée et activée (09/07)** :
  `skills/claude_mentor.py` + heartbeat `claude_mentor_cycle` (60 min, throttle interne
  ~1x/jour). `ARIA_CLAUDE_MENTOR_ENABLED=true` sur le VPS, relais déjà actif
  (`ARIA_RELAY_ACCESS_TOKEN`). Corrige la conception initiale ("Claude bavarde avec ARIA")
  vers un vrai retour d'entraînement : lit ses données de performance RÉELLEMENT mesurées
  (`vc_predictions.metrics()`, `paper_trader.portfolio_summary()`,
  `sepolia_autonomous.autonomous_status()` — fail-closed par source, jamais de valeur
  inventée), appelle le vrai **Claude Opus 4.8** déjà câblé en prod via la profondeur
  "develop" de la passerelle Virtuals (`spark_config.DEFAULT_MODEL_DEVELOP`) — **aucun
  nouveau secret, aucun processus externe**, réutilise le client LLM existant
  (`aria_core.llm.chat_with_context`). Deux sorties : (1) remarque postée dans le relais
  Telegram existant → ARIA y répond en vrai via `relay_conversation_cycle` (feedback
  visible et immédiat) ; (2) si le constat est jugé durable, proposition d'ISSUE GitHub
  (même label `aria-knowledge-proposal` que `knowledge_inbox.py`, même doctrine stricte —
  jamais un commit ni une fusion autonome, revue humaine systématique). Zéro chat libre
  sans ancrage factuel : si aucune donnée de perf n'existe encore (`insufficient_data`),
  le cycle ne coûte rien et n'appelle pas le LLM.
- **Alertes proactives haute-conviction — EN LIGNE, déployée et activée (09/07)** :
  `skills/high_conviction_alerts.py`, heartbeat `high_conviction_alert_cycle` (60 min),
  `ARIA_HIGH_CONVICTION_ALERTS_ENABLED=true` sur le VPS. Pousse une alerte Telegram
  dès que `candidate_ranking` (déjà existant, rien dupliqué) fait remonter un candidat
  `SAFE` au-dessus du score composite (seuil 80/100) — signal de tri, jamais un ordre
  d'achat, renvoie vers `/vc <contrat>` pour l'analyse complète. Un contrat n'est alerté
  qu'une seule fois (mémorisé localement, jamais de spam sur le même candidat). Respecte
  le kill-switch (`/stop`).
- **Overlay macro « Contexte marché » dans le rapport /vc — EN LIGNE, feu vert visuel opérateur confirmé (09/07)** :
  tâche #14. Réutilise `btc_cycles.py` (rien dupliqué) : nouvelle fonction pure
  `current_phase_summary()` (dernier segment du cycle Bitcoin en cours) + `fetch_current_macro_phase()`
  (async, cache 1h en mémoire, dégradation douce sur une source qui échoue). **Aucun appel LLM** — chiffres
  déterministes uniquement, zéro coût/latence ajoutés à chaque rapport. Câblé dans `vc_analysis.py`
  (`VCResult.market_context`, nouveau champ data-gated) via `_attach_extras` (regroupe TA+ROI+macro,
  chacun indépendant). Rendu dans `vc_report.py` (`_market_context_block_html`, même patron visuel que
  ROI/TA, section premium uniquement). i18n FR/EN dans `vc_i18n.py`. **Géopolitique/réglementaire reste
  un seam volontairement VIDE** — aucune source fiable branchée, jamais de donnée inventée pour combler
  la case (à décider avec l'opérateur : quelle source ? coût ? avant de coder). Conformément à
  `architecture-extensibilite.md` (« Toute nouvelle section suit ce motif + un preview validé par
  l'utilisateur avant prod »), un rapport d'exemple a été envoyé à l'opérateur, qui a validé le
  visuel (« le html est magnifique », 09/07) — **tâche #14 complète**. Code testé et
  mergé sur `main` (tests ajoutés/étendus dans `test_btc_cycles.py`, `test_vc_analysis.py`,
  `test_vc_cache.py`, `test_vc_report.py`, suite complète verte). Piège évité : `test_vc_analysis.py`/`test_vc_cache.py`
  déclarent explicitement « aucun appel réseau réel » — une fixture autouse coupe
  `fetch_current_macro_phase` par défaut dans ces deux fichiers pour ne jamais régresser cet invariant.
- **Gestion de position paper-trading : stop suiveur + prise de profit échelonnée (09/07)** :
  `paper_trader.py` — remplace la sortie binaire (100 % à la cible OU à l'invalidation, tâche
  #38) par une gestion qui protège les gains acquis sans couper le potentiel restant.
  **Stop suiveur** (`TRAIL_STOP_PCT=15%`) : se resserre avec le plus haut atteint depuis
  l'entrée (`high_water_price`), ne se relâche JAMAIS en dessous de l'invalidation d'origine
  (`active_stop = max(trailing_stop, invalidation_price)`) — avant toute hausse significative,
  c'est encore l'invalidation d'origine qui protège. **Prise de profit échelonnée**
  (`TP_STAGES = (+50%, +100%, +200%)`, `TP_STAGE_FRACTION = 1/3`) : vend un tiers de la
  quantité INITIALE à chaque palier de gain franchi (`reduce_position`, nouvelle fonction —
  P&L partiel accumulé dans `realized_pnl_partial`, visible immédiatement dans
  `cash_available`/`portfolio_summary` sans attendre la clôture complète) ; le dernier palier
  clôture le reliquat (`close_position`, jamais de position résiduelle qui traîne). Migration
  de schéma à chaud (`ALTER TABLE ADD COLUMN`, même patron que `vc_predictions.py`) —
  non-destructive sur une DB déjà peuplée. Comportement CHANGÉ intentionnellement pour
  `run_paper_cycle` (tests mis à jour dans le même commit, cf. doctrine `test_coherence`) :
  une position qui dépasse l'ancienne "cible" ne se ferme plus à 100% d'un coup, elle se
  réduit par paliers. Aucun impact sur le reste (dossier par contrat, cockpit) — lecture
  seule des positions, champs supplémentaires transparents.
- **Adressage `@claude` dans le chat Telegram opérateur/ARIA (09/07)** : un vrai chat à 3
  identités visuellement distinctes (avatars séparés) est **impossible avec un seul token de
  bot Telegram** — Telegram n'autorise qu'une identité par bot. Palliatif déjà en place :
  toute réponse de Claude est préfixée `🤖 Claude — ` (jamais ARIA), et ARIA ne préfixe
  jamais sa propre voix — on distingue donc toujours qui parle à la lecture. Nouveau : un
  message opérateur commençant par `@claude` (insensible à la casse, `_handle_message` dans
  `telegram_bot.py`) n'active PLUS le pipeline LLM d'ARIA — elle répond juste un court accusé
  de réception ("transmis à Claude") au lieu de répondre à sa place. Le texte complet reste
  journalisé tel quel dans le relais (`process_webhook_update`, inchangé). **Limite non
  résolue** : rien ne réveille automatiquement une session Claude quand `@claude` arrive —
  une réponse réelle exige soit que l'opérateur ouvre la session Claude Code résidente sur
  le VPS (`/opt/aria`, `claude`) au moment voulu, soit un futur cron VPS invoquant Claude en
  mode headless (pas encore construit, implique coût/risque de boucle à cadrer avec
  l'opérateur avant de le coder).

## Recherche web fiable + fix chemin opérateur (09/07 nuit 2) — EN LIGNE, testé en conditions réelles
- **Tavily** en opt-in derrière le point d'entrée unique `web_verify.fetch_web_snippets`
  (provider-switch `ARIA_WEB_SEARCH_PROVIDER=tavily`, DDG reste le fallback). Client
  `services/tavily.py`, patron dôme complet (throttle, backoff 429/403, `available`/`error`).
  Clé `TAVILY_API_KEY` en env uniquement. **Actif en prod, confirmé** : health check
  `aria_web: {provider: tavily, tavily_key_present: true}`, `/status` Telegram affiche
  `Web: tavily (Tavily ✅)`.
- **Bug critique trouvé et corrigé** : le chemin `resolve_calibrated_answer` (qui déclenche
  Tavily/DDG) dans `brain.py::_general_response` était gaté `public` uniquement — la
  conversation OPÉRATEUR ne l'atteignait JAMAIS, même sur une question d'actu explicite. Corrigé :
  `is_live_info_question(route)` ouvre le chemin calibré/web pour l'opérateur aussi (le reste de
  sa conversation fondateur est inchangé). **Vérifié en réel** : question Nvidia sur Telegram →
  vraies news datées + sources + 1 crédit Tavily consommé (confirmé opérateur).
- Déclenchement toujours **à la demande uniquement** (`should_use_web_verify`/
  `is_live_info_question`), jamais un crawl de fond.

## Profil X @Aria_ZHC — seam livré (09/07 nuit 2)
`aria_core/x_profile.py` (était un seam vide documenté, imports gardés en `try/except` dans
`heartbeat.py`/`visual_autonomy.py`, commande Telegram `/x profile sync|preview|force` déjà
câblée mais muette). `sync_x_profile()` compare bio/nom/site à la narrative existante
(`narrative.x_bio()`, `identity.ARIA_DISPLAY_NAME`, `narrative.holding_site_url()`), n'applique
que s'il y a un écart. Champ "lieu" volontairement absent (aucune source canonique).
Commande Telegram toujours disponible (admin-only). Tâche heartbeat quotidienne gardée en plus
par `ARIA_X_PROFILE_SYNC_ENABLED` — **pas encore activée** sur le VPS.

## Menu Telegram réduit au kill-switch (09/07 nuit 2 — choix opérateur)
`set_my_commands` n'expose plus que `/stop`/`/resume`. Les 14 autres commandes (dont `/vc`,
`/scan`, `/track`, `/watchlist`, `/github`...) restent enregistrées et fonctionnelles si tapées
— retirées du menu visible seulement (l'opérateur passe par la conversation naturelle).

## Autopsie pump/dump (#8) — ACTIVÉE en prod (09/07 nuit 2)
`ARIA_PUMP_DUMP_AUTOPSY_ENABLED=true` sur le VPS. `skills/pump_dump_autopsy.py` re-analyse la
vraie série OHLCV de chaque pronostic VC clos récemment (le point-à-point entrée→échéance de
`weekly_training.resolve_due` masque un pump-puis-crash intermédiaire). Détection 100%
déterministe (aucun LLM), autopsie LLM courte seulement si détecté, proposition d'issue GitHub
(`aria-playbook-proposal`) si jugé durable — jamais un commit/fusion autonome.

## Nettoyage narratif ACP/app-factory/$50-mois mort (09/07 nuit 2) — même famille que Aria Market
L'ACP est abandonné et Stripe retiré, mais plusieurs surfaces poussaient encore ARIA à proposer
"marketplace ACP + app payante v0 + Play Store" comme stratégie de revenu #1 (déclenché par un
`founder_ping` proposant ce plan un soir où revenu=0, ce qui est toujours le cas aujourd'hui).
**Bug public le plus grave trouvé** : `community_feedback.py` répondait à de vrais visiteurs X
avec ce narratif mort, jamais gaté. Toutes les surfaces (persona, goals, heartbeat entrepreneur,
cultivation curriculum, capability rubric...) réalignées sur le pacte réel
(`docs/protocole-argent-reel.md`) : pas de produit payant actif aujourd'hui, la priorité est de
prouver le track-record. `revenue_hypotheses()` renvoie `[]` (aucune hypothèse de monétisation
en test actuellement, honnête plutôt que de lister des options mortes).

## Pacte argent réel — deux étapes séquentielles (09/07 nuit 2)
`docs/protocole-argent-reel.md` §3 : l'argent réel se débloque poche par poche, jamais les deux
en même temps. VC réel (85%) débloqué en premier sur le track-record paper ; trading réel (15%)
débloqué ENSUITE seulement, une fois le VC réel ayant lui-même rejoué le barème des 8 cases sur
son propre track-record réel (pas le paper d'origine).

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

## Filtre web : `public` réellement propagé de bout en bout (09/07 nuit 3) — CORRIGÉ
`should_use_web_verify` ne dépend plus du réglage global `is_public_mode()` (toujours `True` en
prod, donc aveugle au fait que c'est l'opérateur ou un visiteur qui parle). Le paramètre `public`
est maintenant réellement transmis sur toute la chaîne : `brain.py` → `resolve_calibrated_answer`
(`epistemic.py`) → `enhance_calibrated_answer` (`epistemic_pipeline.py`) → `web_enhance_calibrated`
/`web_first_answer` (`web_verify.py`). Concrètement : une question d'actualité posée par
l'opérateur atteint la recherche web comme pour un visiteur public — c'était cassé avant ce fix
(root cause de l'hallucination auto-rapportée par ARIA sur une question autoréflexive).

## `heartbeat.py` : une tâche cassée ne fait plus planter tout le cycle (09/07 nuit 3)
`_sync_x_curiosity_enabled()` isole maintenant chaque vérification de gate dans son propre
try/except (fail-closed : `task.enabled = False` + log, jamais une exception qui remonte et coupe
le heartbeat entier). Root cause du "No module named 'ariacore.xprofile'" auto-rapporté par ARIA.

## CI — scan de secrets sur tout le repo (#55, 09/07 nuit 3) — LIVRÉ, PAS encore un déploiement VPS
`.github/workflows/secrets-scan.yml` (detect-secrets, baseline-diff, tourne sur tout push/PR sans
filtre de chemin) — c'est un job CI, aucun composant runtime, rien à déployer sur le VPS pour ça.
`.secrets.baseline` = état audité au 09/07, à régénérer et ré-auditer si un vrai nouveau secret
légitime apparaît (procédure dans le commentaire du workflow).

## INCIDENT — clé privée réelle du wallet Virtuals exposée puis retirée du code (09/07 nuit 3) — RÉSOLU
`skills/development/connect.ts` contenait une clé privée + adresse en dur, confirmée par
l'opérateur comme le VRAI wallet actif de l'agent Virtuals "Aria Vanguard ZHC" (mainnet, pas un
exemple testnet malgré la référence `baseSepolia` dans le fichier). Code corrigé (lecture depuis
`process.env`). **Rotation de clé côté plateforme Virtuals confirmée terminée par l'opérateur
(09/07)** : nouvelle clé ajoutée en section "Signers", vérifiée active, PUIS ancienne clé (celle
qui a fuité) supprimée — bon ordre respecté (jamais l'inverse, pour ne pas perdre l'accès). Détail
complet : `docs/HANDOFF-2026-07-09-nuit3.md`.

## Endpoint `arena-signal/btc` (#60) — EN LIGNE sur `api.ariavanguardzhc.com`, PAS sur la vitrine
`GET /api/aria/arena-signal/btc` vit sur le sous-domaine backend (`api.ariavanguardzhc.com`,
`proxy_pass` nginx vers `localhost:8000`, **aucun Basic Auth**) — PAS sur `ariavanguardzhc.com`
(vitrine statique, verrouillée par Basic Auth sur tout, `/api/*` y retombe sur `index.html` faute
de règle de proxy). Ne jamais tester cet endpoint sur le mauvais domaine. Alimente Shekel (agent
tiers Arène Virtuals) et tout futur consommateur externe. Cycle macro complet à nouveau alimenté
(voir section CoinGecko/Blockchain.com ci-dessous) — RSI et cycle renvoient de vraies valeurs.

## CoinGecko — tier gratuit limité à 365 jours d'historique (09/07 nuit 7) — CORRIGÉ (Blockchain.com)
Changement de politique CoinGecko confirmé en direct (`error_code 10012`, HTTP 401 même avec une
clé Demo valide) : impossible de requêter des données de plus de 365 jours sur le tier gratuit,
peu importe la taille de la fenêtre. Cassait potentiellement en silence l'overlay macro du rapport
`/vc` (tâche #14, déjà en prod) puisque `btc_cycles` demande l'historique depuis 2015.
**Corrigé** : `btc_cycles.fetch_btc_history` bascule sur `services/blockchain_info.py`
(`charts/market-price`, société établie, gratuit, sans clé, ~1600 points quotidiens 2009→aujourd'hui) —
segmentation des 3 cycles vérifiée en direct, réelle et complète. CoinGecko reste utilisé pour le
RSI récent de `arena_signal.py` (fenêtre 90 jours, largement dans la limite des 365 jours) — **deux
clients distincts, jamais interchangeables** (piège vécu : les confondre casse le typage silencieusement,
verrouillé par test dédié). Clé `COINGECKO_DEMO_API_KEY` dans `vanguard/backend/.env` (PAS
`/opt/aria/.env` racine — piège vécu, deux fichiers `.env` différents selon le composant). Détail
complet : `docs/HANDOFF-2026-07-09-nuit7.md`.

## Mineur de conversations opérateur/ARIA (#57) — LIVRÉ, gate OFF (09/07 nuit 7)
`skills/telegram_conversation_miner.py`, tâche heartbeat `telegram_miner_cycle` (60min, throttle
~1x/jour), gate `ARIA_TELEGRAM_MINER_ENABLED` (off par défaut). Relit `relay_chat.py` (rien
dupliqué), propose un enseignement durable via issue GitHub — jamais un commit/fusion autonome,
même doctrine que `knowledge_inbox`/`claude_mentor`. Garde-fou spécifique : bloque toute
publication si le titre/corps ressemble à un secret (une création d'issue ne passe pas par le
scan `detect-secrets` de la CI, contrairement à un push).
