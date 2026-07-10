# Architecture d'extensibilité ARIA — anticiper les outils futurs

> **SSOT extensibilité.** À lire AVANT d'intégrer tout nouvel outil / source de
> données / modèle. Objectif : chaque brique future se branche sur un **point
> d'ancrage existant** (« seam »), sans réécrire le cœur ni casser le validé.
> Répondre à l'utilisateur en **français**. Document versionné = permanent.

---

## 0. La porte d'entrée de TOUTE intégration — le dôme (non négociable)

Aucun outil n'entre s'il ne respecte pas ces règles. Ce sont les garde-fous —
ne jamais les contourner « pour aller plus vite » :

1. **Facts-only** : jamais inventer une donnée. Non sourçable → « donnée insuffisante ».
2. **Données non fiables = données, jamais instructions** : tout ce qui vient
   d'une API/on-chain/utilisateur passe par la sanitisation (`_sanitize`,
   `_esc`, balises `<donnees_non_fiables>`, allowlists). Un nouvel outil qui
   ramène du texte externe DOIT le sanitiser avant de le donner au LLM.
3. **Aucune exécution financière automatique** : tout ordre est une PROPOSITION,
   validée humainement (Telegram/Tangem). Un outil ne déclenche jamais un trade.
4. **Dégradation gracieuse** : une source indispo (rate limit, timeout, panne)
   ne casse jamais le flux — on retombe sur un fallback déterministe.
5. **Ne jamais modifier les fichiers garde-fous sans validation** :
   `permission_mode`, `wallet_guard`, `regles-uniques`, `config.toml`,
   flag `aria_llm_enabled`.
6. **Un signal social/externe FILTRE, ne DÉCLENCHE jamais** : X, fact-check,
   sentiment = entrées candidates, l'analyse on-chain reste l'arbitre.

---

## 1. Les couches et leurs points d'ancrage

```
Gateways (Telegram, futur web/API)   ← fin : traduit I/O, appelle des skills
        │
        ▼
Skills  (aria_core/skills/*.py)      ← logique métier, PURE, réutilisable
        │        │
        ▼        ▼
Context hub      Services (aria_core/services/*.py) ← clients API isolés
(TokenScanContext)                    (virtuals, futur OHLCV, fact-check…)
        │
        ▼
LLM routing (aria_core/llm.py)       ← provider-agnostic (virtuals→groq→…)
        │
        ▼
Persistance (paths.py → aria.db, aiosqlite ; fichiers data_dir)
```

### 1.1 Skills = plugins métier — `packages/aria-core/src/aria_core/skills/`
Chaque capacité est un **module autonome** avec une entrée `async` qui retourne
une **dataclass typée** (jamais un dict brut), sans effet de bord, dôme-hardened.
Exemples existants : `vc_analysis`, `vc_judge` (proof engine), `ta_levels`,
`chart_render`, `vc_report`, `vc_i18n`, `vc_prefs`, `acp_onchain_scan`.
> **Recette d'un nouvel outil** : nouveau fichier `skills/<nom>.py`, fonction
> `async def <verbe>(...) -> <Dataclass>`, fallback déterministe, tests offline
> mockés. Le gateway l'appelle, il ne connaît pas Telegram.

### 1.2 Services = clients de sources externes — `aria_core/services/`
Un service = un client d'API isolé (ex. `services/virtuals.py`). Contrat commun :
`httpx` avec timeout, parsing en dataclass, **dégradation gracieuse** (retourne
`None`/objet vide, jamais une exception qui remonte). Tout OHLCV, fact-check,
social passe par un service dédié — **jamais d'appel réseau en dur dans un skill**.

### 1.3 Le hub d'intégration : `TokenScanContext` (`acp_onchain_scan.scan_base_token`)
**C'est LE point d'ancrage central.** `scan_base_token` agrège les faits on-chain
et les expose via des drapeaux additifs : `include_smart_money`,
`include_fundamentals`. **Chaque nouvelle source de données s'ajoute ici** comme
un `include_<x>` → le contexte porte le champ → l'analyse ET le juge le
consomment sur les MÊMES faits.
> Futurs : `include_ta` (OHLCV/niveaux), `include_social` (X radar),
> `include_factcheck` (Facticity/ArAIstotle), `include_virtuals` (pré-bonding).
> Toujours **additif et data-gated** : sans la donnée, le comportement est
> identique à aujourd'hui.

### 1.4 Routage LLM — `aria_core/llm.py`
`_resolve_routes` construit une chaîne de fallback provider-agnostic
(virtuals → groq → …). Un nouveau modèle/fournisseur se branche ici, pas dans
les skills. `chat_with_context(user, system, …)` est l'unique surface d'appel.
La directive de langue (`vc_i18n.llm_language_directive`) montre le motif pour
moduler un prompt sans dupliquer le système.

### 1.5 Gateways — garder les handlers FINS
`gateway/telegram_bot.py` (polling/handlers) + `vanguard/.../telegram_route.py`
(webhook). Un handler doit **traduire l'I/O puis appeler un skill** — pas
contenir la logique métier. Ainsi un **futur gateway web/API** réutilise les
mêmes skills sans copier-coller. (Cf. `_vc_analyze_and_reply` extrait pour
isoler le cœur du transport.)

### 1.6 Persistance — `aria_core/paths.py`
`data_dir()` / `aria_db_path()` centralisent l'emplacement (piloté par
`DATA_DIR`, monté en volume sur le VPS). Motif clé/valeur SQLite (`aria_setting`
via `vc_prefs`) et tables d'ajout pur (`vc_prediction`). Tout nouvel état
persistant réutilise ce chemin — jamais de fichier écrit ailleurs.

### 1.7 i18n — `aria_core/skills/vc_i18n.py` + `aria_core/locale.py`
Tout texte destiné à l'utilisateur passe par la couche i18n (FR défaut, EN
additif). Un nouvel outil expose ses libellés ici, jamais en dur dans le code.

### 1.8 Rendu rapport — `vc_report.py`
Sections **additives et data-gated** : rendues seulement si la donnée existe,
sinon rapport identique. **Toute nouvelle section (TA, macro) suit ce motif +
un preview validé par l'utilisateur avant prod** (le visuel actuel est validé,
ne pas le modifier sans feu vert).

---

## 2. Catalogue des outils futurs — où chacun se branche

| Outil / brique | Type | Point d'ancrage | Notes |
|---|---|---|---|
| **Cache court (TTL)** perf | wrapper | autour de `scan_base_token` / `analyze_vc`, clé = contrat+TTL | Machine sur-dimensionnée → le cache est le vrai levier vitesse. |
| **Logs de timing** observabilité | instrumentation | dans skills + `llm.py` | Mesure scan vs LLM → pilote la perf. Prérequis pour de vrais chiffres. |
| **Pipeline OHLCV** (#5, #9) | service | `services/ohlcv.py` (GeckoTerminal gratuit + Alchemy) → `include_ta` | Dépendance commune de la projection ROI, des niveaux TA et du graphique. |
| **TA + graphique dans rapport** (#9) | skills prêts | `ta_levels` + `chart_render` → section data-gated de `vc_report` | Moteurs déjà construits ; câblage additif + preview. |
| ✅ **Projection ROI par comparables** (#5) | skill fait | `skills/roi_comparables.py` (jalons `knowledge/roi_comparables.yaml`) → champ data-gated de `vc_report` (premium) | Voûte 3 livrée. Placement historique tangible, JAMAIS une cible/promesse. Nourri par les fondamentaux du scan (market cap/FDV). |
| **Virtuals pré-bonding** (#10) | service prêt | `services/virtuals.py` → `include_virtuals` + mode d'analyse dédié | Testable en live seulement sur VPS (réseau bloqué en cloud). |
| **Arena Virtuals — classement public** (#60, Phase 0) | service dormant | `services/virtuals_arena.py` (`GET /api/leaderboard`, zéro clé) | Lecture seule, testé live. Volontairement PAS branché (pas de heartbeat/Telegram/wallet) tant que la persistance du edge des top agents n'est pas observée dans la durée. |
| **Signal BTC pour agents tiers** (#60, seam Shekel/Custom Data Endpoint) | endpoint public | `GET /api/aria/arena-signal/btc` → `skills/arena_signal.py` (réutilise `btc_cycles` + `entry_signals.rsi_series`, aucun doublon de client) | Nourrit un agent tiers (ex. Shekel) avec les VRAIES analyses BTC d'ARIA plutôt qu'un prompt seul. Aucune écriture, aucune auth (contrat Shekel), aucune PII. Champ manquant = donnée indisponible, jamais inventée. L'exécution du trade reste hors `aria-core` (wallet_guard ne s'applique pas ici, doctrine inchangée). |
| **Historique BTC long** (#62) | service | `services/blockchain_info.py` (`charts/market-price`, zéro clé, 2009→aujourd'hui) → `btc_cycles.fetch_btc_history` | Remplace CoinGecko pour l'historique >365j (leur tier gratuit le refuse désormais, confirmé en direct). CoinGecko reste utilisé pour le RSI récent (`arena_signal.py`) — deux clients distincts, jamais confondus. |
| **Signal macro Polymarket** (#59) | service dormant | `services/polymarket.py` (`fetch_top_event_by_tag`, zéro clé, testé live sur `fed-rates`) | Probabilité implicite d'événements macro réels (ex. décision de taux Fed) — complète `btc_cycles` (cycle de halving) sans le remplacer. Volontairement PAS branché dans le rapport `/vc` (déjà validé visuellement) : ajouter une section à un rapport premium approuvé est une décision produit de l'opérateur, pas un choix technique unilatéral. |
| ✅ **EMA/MACD réels** (#68, câblé le 10/07) | skill câblé | `skills/indicators.py` (`ema_series`/`macd_series`) → `acp_onchain_scan.TokenScanContext.ta_ema_*`/`ta_macd_*` → `vc_analysis._build_untrusted_context` (LLM voit EMA12/26, MACD/signal/histogramme) | Décision opérateur (10/07) : câblé dans le pipeline de scan réel, même garde `include_ta` que le TA existant. N'affiche rien de nouveau dans le rapport HTML (`vc_report.py` inchangé) — informe le raisonnement du LLM, pas encore une nouvelle section visuelle. |
| ✅ **Setup « golden pocket + divergence RSI »** (#69, câblé le 10/07) | skill câblé | `skills/entry_signals.py` (`detect_entry`) → `ctx.ta_golden_pocket_signal` → `vc_analysis._build_untrusted_context` (ligne dédiée si `present=True`, silence sinon) | Ne recoupe pas `ta_levels.suggest_entry_zone` (générique, toujours renvoyé) : signal plus rare et qualitatif, complémentaire. Câblé dans le contexte LLM seulement (même périmètre qu'EMA/MACD ci-dessus) — pas de nouvelle section HTML pour l'instant. |
| ✅ **Radar X / social** (#7) | service + orchestrateur faits | `services/x_social.py` (écoute/sanitisation) + `radar_x.run_radar` → absorbeur on-chain ; tâche heartbeat `vc_radar_x` | Voûte 4 livrée. Le social SOURCE/RÉVEILLE, l'on-chain ARBITRE. Jamais un déclencheur de trade. |
| **Scorecard « feu vert argent réel »** (#70, livré le 10/07) | skill + commande admin | `skills/real_money_readiness.py` → `/feuvert` Telegram (admin-only) | Mesure objectivement les 8 cases pré-engagées de `docs/protocole-argent-reel.md` depuis le vrai journal `vc_predictions` — jamais un jugement subjectif. 3 cases calculables aujourd'hui (échantillon+étalement, intégrité structurelle, robustesse anti-chance), le reste (benchmark hold-ETH, vérif a posteriori des AVOID, calibration du juge, feu vert avocat) reste honnêtement `unknown` tant que la donnée/l'action humaine manque — jamais transformé en `ok` par optimisme. Seam pour compléter plus tard : un client de prix ETH/USD aligné sur les fenêtres réelles d'entrée/sortie (benchmark) et un module type `pump_dump_autopsy` appliqué aux AVOID (vérif a posteriori). |
| **Bollinger Bands** (#71) | fonction pure dans `indicators.py` | `bollinger_bands(closes, period, num_std)` | Même patron qu'`ema_series`/`macd_series` (écart-type de population sur fenêtre glissante, `None` en chauffe). Utilisé par `market_sentiment.py` (position dans les bandes). |
| **Patterns de bougies** (#71) | skill prêt, non câblé | `skills/candlestick_patterns.py` (`detect_patterns` — doji/marubozu/hammer/shooting_star/engulfing, 171 lignes, testé) | Nécessite de vraies bougies OHLC (`services/ohlcv.py`, tokens Base) — PAS applicable au sentiment BTC/ETH (CoinGecko `market_chart` ne fournit que des closes). Même prudence que #68/#69 : pas branché dans `acp_onchain_scan.py` sans validation opérateur. |
| ✅ **Barres "échelle commune" des scénarios** (#11/#64, résolu le 10/07) | prompt LLM + rendu | `vc_analysis.py` (`cible_multiple`, jamais fabriqué) → `vc_report.py` (`_scenario_value_widths`, largeur partagée bull/base/bear) | Écart trouvé en relisant le code : la barre de PROBABILITÉ des scénarios était déjà à l'échelle (0-100% par carte), mais aucune barre ne comparait l'AMPLEUR des cibles entre elles (le texte `cible` est de la prose LLM libre, pas un nombre). Ajout d'un champ numérique optionnel dédié, jamais un parsing fragile de la prose. Omis si <2 scénarios chiffrés (dégradation douce). Thèse LLM enrichie au même commit (ancrage sur ≥2 signaux concrets exigé). |
| **Sentiment de marché continu** (#71, demande opérateur 10/07 — image Wall St Cheat Sheet) | skill + heartbeat + commande admin | `skills/market_sentiment.py` (`classify_sentiment`, RSI+Bollinger+momentum+retracement → 6 régimes + repli neutre) → tâche `market_sentiment_cycle` (60min, gate OFF `ARIA_MARKET_SENTIMENT_ENABLED`) → `/sentiment` Telegram (admin-only) | Aligne le vocabulaire du cheat sheet (disbelief→euphoria→...→depression) sur des chiffres réels, SANS prétendre distinguer les 13 émotions fines (aucune signature numérique ne sépare "colère" de "dépression") — 6 régimes défendables seulement. Paires principales de DÉPART : BTC + ETH (`PRINCIPAL_PAIRS`, extensible). Persistance `market_sentiment` (SQLite, `upsert_reading` écrase TOUJOURS — "sans expiration" : la fraîcheur dépend du heartbeat, jamais d'un TTL de lecture). Complète `btc_cycles.py` (halving, pluri-annuel) par une lecture court/moyen terme, ne le remplace pas. |
| ✅ **Sentiment de marché → décision LLM réelle** (#75, 10/07) | prompt LLM (pré-appel) | `vc_analysis._fetch_sentiment_readings` + `_build_untrusted_context(..., sentiment_readings)` → régime BTC/ETH ajouté au bloc `<donnees_non_fiables>` AVANT `chat_with_context` | Distinction architecturale importante trouvée en creusant : l'overlay macro halving (#14, `_attach_market_context`/`_attach_extras`) s'exécute APRÈS la réponse LLM — décoration de rapport, n'a jamais influencé le raisonnement. Demande opérateur explicite (« utile pour ARIA et toi, pas pour moi ») exigeait le chemin PRÉ-LLM, comme EMA/MACD/golden pocket (#74). Régime `donnees_insuffisantes` jamais affiché (silence, pas de bruit). Dégradation douce : erreur/DB absente/gate OFF → liste vide, jamais bloquant. |
| ⚠️ **Incident : délégation autonome "Cursor" retirée** (10/07) | sous-système entier supprimé/nettoyé | `aria_worker_queue.py` + `skills/community_worker_skill.py` supprimés ; `capability_gap.py` réduit à une notification Telegram locale (plus d'issue/PR/branche GitHub, plus de délégation) ; `brain.py`/`operator_readiness.py`/`operator_go_ahead.py`/`community_feedback.py`/`health_watch.py` nettoyés de tout appel à ce cluster | Un sous-système committé le 05/07 **par Cursor lui-même** (co-auteur `cursoragent@cursor.com`), jamais documenté ici malgré la doctrine "Cursor/Grok abandonnés", pouvait déléguer du code à un tiers sans validation opérateur — déclenchable par heartbeat (6h/15min), mots du quotidien Telegram ("go", "vas-y", "nettoie le répertoire") ou un formulaire public du site. Preuve qu'il avait déjà agi pour de vrai : issue #1 + PR #2 (03/07), jamais traitées. Garde-fou mécanique ajouté : `test_coherence.py::test_external_write_actions_registered_in_allowlist` — toute nouvelle fonction d'écriture externe (GitHub/X/email) non déclarée dans une allowlist explicite fait échouer la CI. |
| **Centre de commandement — dashboard** (#72, 10/07) | endpoints publics + panneaux React | `/api/aria/market-cycle`, `/api/aria/sentiment` (nouveaux), `/api/aria/track-record` étendu (`calibration`, `by_strategy`) → `CockpitCalibrationPanel`/`CockpitCyclePanel`/`CockpitSentimentPanel`/`CockpitMethodologyPanel` sur `/cockpit` | Réponse à « qu'est-ce qui prouve qu'ARIA est câblée pour gérer un portefeuille ? » : agrège calibration réelle, cycle macro, sentiment continu et l'explication du pipeline (sourcing → sécurité → quantitatif → LLM → juge → track record) sur UNE page. Jamais un contrat candidat exposé (agrégats seulement, même doctrine que `/track-record` existant — l'alpha reste réservée). Vérifié : TS compile, 3 endpoints testés en direct (dont un vrai appel réseau BTC réussi), 4 nouveaux tests backend + 52 existants verts. Rendu visuel non vérifié en navigateur ce segment (PrivyProvider bloque le boot local sans vrai App ID — un contournement a été correctement refusé par le classifieur de sécurité), à valider par l'opérateur au prochain déploiement. |
| **Mineur de conversations opérateur/ARIA** (#57) | skill + heartbeat | `skills/telegram_conversation_miner.py` → tâche `telegram_miner_cycle` (60min, throttle ~1x/jour) | Relit `relay_chat.py` (rien dupliqué), propose un enseignement durable via ISSUE GitHub (label `aria-knowledge-proposal`, même doctrine que `knowledge_inbox`/`claude_mentor` — jamais commit/fusion autonome). Garde-fou dédié : bloque toute publication si le titre/corps ressemble à un secret (`_looks_like_secret`) — une création d'issue ne passe pas par le scan `detect-secrets` de la CI, contrairement à un push. Gate OFF par défaut (`ARIA_TELEGRAM_MINER_ENABLED`). |
| **Fact-check (Facticity / ArAIstotle)** | service | `services/factcheck.py` → soit `include_factcheck`, soit **2e dimension de juge** | Motif identique à `vc_judge` : audit indépendant, pluggable. Ne pas toucher au token $FACY. |
| **Moteur de connaissance 24/7** (#8) | worker VPS | tâche de fond réutilisant les MÊMES skills (scan/analyse) → écrit dans un knowledge store | Exige que les skills restent purs et appelables hors Telegram (ils le sont). Surveiller la RAM (~1,8 Gio). |
| **Overlay macro / géopolitique** (#14) | données + section | source macro → section data-gated de `vc_report` | Additif, non-régression testée, preview avant prod. Facts-only. |
| **Gateway web / API clients** | gateway | nouveau dossier gateway réutilisant les skills | Ne dupliquer aucune logique métier : tout est déjà dans les skills. |
| **Conformité (Clerk / Solvr, avocat)** | process | hors code — dossier `docs/conformite-*` | Aucun encaissement avant validation juridique. |

---

## 3. Recette pour intégrer un outil (checklist)

1. **Source externe ?** → un `service` isolé (httpx + timeout + dégradation).
2. **Logique métier ?** → un `skill` pur retournant une dataclass, testé offline.
3. **Enrichit l'analyse ?** → ajouter un `include_<x>` à `TokenScanContext`,
   **additif et data-gated** (sans la donnée = comportement inchangé).
4. **Texte externe ?** → sanitisation dôme AVANT le LLM.
5. **Texte utilisateur ?** → via la couche i18n (FR/EN).
6. **Persistant ?** → `data_dir()` / `aria.db`, jamais ailleurs.
7. **Nouveau modèle/provider ?** → dans `llm.py` (`_resolve_routes`), pas ailleurs.
8. **Tests offline** (mocks) + ajout à la **CI** (`.github/workflows/ci.yml`).
9. **Rendu rapport** → section optionnelle + **preview validé** avant prod.
10. **Jamais d'exécution financière ; validation humaine.**

---

## 4. Anti-patterns (à ne PAS faire)

- ❌ Appel réseau en dur dans un skill ou un handler (→ service).
- ❌ Logique métier dans un handler Telegram (→ skill, pour réutilisation multi-gateway).
- ❌ Donnée externe passée au LLM sans sanitisation.
- ❌ Section rapport rendue même sans donnée (casse le visuel validé).
- ❌ Nouveau fichier de données hors `data_dir()`.
- ❌ Texte utilisateur en dur (contourne l'i18n).
- ❌ Un signal social/fact-check qui déclenche une action au lieu de filtrer.
- ❌ Bloquer le flux sur une panne de source (toujours un fallback).

---

## 5. Le mot d'ordre : ANTICIPATION

> **Plus on anticipe l'architecture, moins l'amélioration future sera complexe.**
> Chaque « seam » posé aujourd'hui (un `include_<x>`, un service isolé, un
> handler fin) transforme une future intégration lourde en un simple branchement.
> C'est un intérêt composé : le coût d'ajout du 10ᵉ outil dépend des seams posés
> pour le 1ᵉʳ. On code donc **pour le brancheur de demain**, pas seulement pour
> la fonctionnalité d'aujourd'hui.

Règle pratique : avant d'écrire une fonctionnalité, se demander **« quel sera le
prochain outil de la même famille, et est-ce qu'il se branchera sans réécriture ? »**
Si non → poser le seam maintenant (même vide), documenter ici.

---

## 6. La vitrine = surface de première classe (page d'accueil client)

Le **site web (`vanguard/`) est la première impression d'un client** de la gamme
luxe. Il doit être **exceptionnel** — au niveau du produit qu'il vend. Ce n'est
pas un « à-côté » : c'est une surface produit à part entière, soumise aux mêmes
exigences d'anticipation.

Points d'ancrage à préserver pour qu'elle évolue sans dette :
- **Contenu piloté par données**, pas codé en dur (track-record, preuves,
  éditions de rapport) → un futur CMS/feed se branche sans refonte.
- **Durcissement déjà posé** : `robots.txt`, `sitemap.xml`, en-têtes de sécurité
  (`vanguard/public/_headers`). Étendre, ne pas refaire.
- **Cohérence de marque** (palette premium navy/or + standard rose) partagée
  entre la vitrine ET les rapports email — une seule source de style à terme.
- **Modèle d'accès** à trancher tôt (mur Privy / public) — cf. audit anti-bot :
  ça conditionne toute l'architecture front/produit.
- **Performance perçue** : la vitrine doit être instantanée (statique/CDN),
  contrairement à l'analyse (profonde par nature). Deux exigences distinctes.

> À traiter comme un chantier dédié (lié à #13 positionnement/GTM) : viser un
> niveau « exceptionnel » cohérent avec le positionnement 500 $/mois.

---

## 7. ADR — Faut-il un framework d'agents (CrewAI, etc.) ?

**Décision (2026-07) : non pour l'instant. Pas un interdit éternel — un critère.**

Le réflexe « ARIA grossit → prenons CrewAI » confond deux pannes distinctes :

1. **Panne de VOLUME/charge** (trop de requêtes, tokens, latence). Un framework
   d'agents **aggrave** (agents qui délibèrent = plus de tokens/latence). Vraie
   réponse : **cache, file d'attente, parallélisme, infra**.
2. **Panne de COMPLEXITÉ d'orchestration** (décider dynamiquement quels outils
   appeler, branches). Là un orchestrateur *peut* aider — mais le choix n'est
   jamais « CrewAI ou rien » : orchestrateur **maison léger** vs LangGraph
   (graphes explicites, déterministes) vs CrewAI (autonomie large). On penche
   vers **le plus contrôlable**.

**Pourquoi pas CrewAI aujourd'hui :** il retire le **contrôle** qui EST le moat
d'ARIA (décision prouvée, auditable). Il masque le prompt réel (risque
anti-injection), alourdit les dépendances, et son autonomie contredit le dôme
(facts-only, zéro exécution auto). Or le pattern multi-agent utile (analyste +
juge adverse) est **déjà** implémenté proprement et sous contrôle total ; un
nouvel outil = une **dimension de juge** ou un `include_<x>` de plus.

**Le critère PERMANENT (ce qui est gravé, pas l'outil) :** toute orchestration
ajoutée doit (a) **préserver le contrôle et l'auditabilité**, (b) **laisser le
dôme envelopper le tout** (jamais un plugin qu'on espère respecté), (c) ne pas
masquer le texte envoyé au LLM. Un framework qui coche ces cases est discutable
le jour venu ; sinon, non.

**Quand rouvrir la question :** signal clair que l'orchestration devient
ingérable en code maison (et non un simple problème de charge). On réévaluera
alors avec le critère ci-dessus — en privilégiant un loop maison gardé ou un
graphe explicite avant l'autonomie large.
