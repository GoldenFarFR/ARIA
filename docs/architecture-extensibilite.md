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
| ✅ **Radar X / social** (#7) | service + orchestrateur faits | `services/x_social.py` (écoute/sanitisation) + `radar_x.run_radar` → absorbeur on-chain ; tâche heartbeat `vc_radar_x` | Voûte 4 livrée. Le social SOURCE/RÉVEILLE, l'on-chain ARBITRE. Jamais un déclencheur de trade. |
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
