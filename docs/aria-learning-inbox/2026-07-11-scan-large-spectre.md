[VPS Research]

# Scan large spectre — pistes pour ARIA (2026-07-11)

Mission continue (recadrée par l'opérateur le 2026-07-11) : scanner large, toute
catégorie, sans angle fixé à l'avance. Méthode : WebSearch ciblé par piste,
pas de workflow fan-out. Jugement sur 2 critères seulement :
1. Légitimité (doc réelle, maintenu, adoption réelle — angle token/financement
   uniquement si l'outil LUI-MÊME est un projet crypto).
2. Connectable sans dupliquer l'existant — quel seam de
   `docs/architecture-extensibilite.md` (repo `GoldenFarFR/ARIA`), quel upside concret.

Aucune intégration n'est décidée ici — ce sont des pistes banquées pour arbitrage opérateur.

---

## ~~1. GoPlus Security API~~ — RETIRÉ (déjà intégré)

**Correction opérateur (2026-07-11) : déjà en production.** `services/goplus.py`
existe et est câblé dans `safety_screen`/`bonding_screen` et l'analyse `/vc`
depuis le 07/07. Ce n'était pas une piste nouvelle — vérifié après coup par
`curl` sur l'API GitHub (`contents/.../aria_core/services` liste bien
`goplus.py`). Confirme aussi au passage que `blockscout.py` existe **déjà**
comme service dédié (voir entrée 4 ci-dessous, également déjà intégrée).

**Leçon appliquée à partir de maintenant** : avant de proposer toute piste,
grep `packages/aria-core/src/aria_core/services/` et `.../skills/` (via l'API
GitHub `contents/`, le repo n'étant pas cloné en local sur ce VPS) pour
confirmer l'absence — pas seulement la mémoire de session ou la doc
d'architecture qui peut être en retard sur le code réel.

---

## 2. Instructor (567-labs/instructor) — extraction structurée depuis un LLM

**Légitimité.** Bibliothèque Python la plus utilisée pour l'extraction de
données structurées depuis un LLM : ~3M téléchargements/mois, 11k étoiles
GitHub, 100+ contributeurs, basée sur Pydantic, portée dans 6 langages.
Pas un projet crypto — angle financement non pertinent. Légitimité forte,
adoption massive et maintenue.

**Seam.** `aria_core/llm.py` — la doctrine actuelle impose déjà que chaque
skill retourne une **dataclass typée, jamais un dict brut**. Instructor
formalise exactement ce contrat *au niveau de l'appel LLM lui-même*
(validation Pydantic + retry automatique si le schéma ne matche pas), plutôt
que de parser/valider la sortie du LLM à la main dans chaque skill.

**Upside concret — confirmé, pas de doublon.** Vérifié directement dans le
code (`llm.py` et `skills/vc_analysis.py` récupérés via
raw.githubusercontent.com) : `chat_with_context` bâtit un payload
`chat/completions` brut (`messages`, `temperature`, `max_tokens`) sans
`response_format` ni `tool_choice` — **aucun function-calling/JSON-mode
natif n'est utilisé**, la réponse est retournée en `str` brut
(`data["choices"][0]["message"]["content"].strip()`). Et `vc_analysis.py`
ligne 448 fait bien du parsing manuel fragile : `json.loads(text[start:end+1])`
(slice sur les accolades trouvées dans le texte libre). C'est exactement le
point de fragilité qu'Instructor est fait pour durcir (validation Pydantic +
retry automatique si le schéma ne matche pas) — **pas de doublon, gain réel
identifié sur du code qui existe vraiment aujourd'hui**.
**Réserve d'implémentation** (pas un doute sur le fit, juste un préalable
technique) : Instructor s'appuie en général sur le tool/function-calling ou le
JSON-mode du provider ; à vérifier laquelle des routes actuelles (xai/grok,
openai, groq, openrouter, virtuals, ollama) le supporte réellement avant de
généraliser — possible qu'il faille un mode dégradé (retry + validation
manuelle Pydantic sans tool-calling) pour les providers qui ne l'exposent pas.

---

## 3. LLM Guard (protectai) — scanners input/output pour LLM

**Légitimité.** MIT, 3,1k étoiles GitHub, maintenu par Protect AI. 15 scanners
d'entrée + 20 scanners de sortie, dont un scanner dédié PromptInjection
(direct + indirect) et détection de texte invisible/jailbreak. Pas un projet
crypto. Légitimité correcte (moins massif qu'Instructor mais actif et
spécialisé sécurité LLM, catégorie où peu d'alternatives sont aussi complètes).

**Seam.** C'est exactement la fonction du **dôme** existant (règle 0.2 de
`architecture-extensibilite.md` : « données non fiables = données, jamais
instructions », `_sanitize`/`_esc`/balises `<donnees_non_fiables>`). LLM Guard
serait une **deuxième couche indépendante de détection**, pas un remplacement
— brancherait comme un scanner supplémentaire dans le pipeline de
sanitisation déjà existant (`services/x_social.py` pour le radar X, tout
`service` qui ramène du texte externe).

**Upside concret.** Détection de patterns de prompt injection connus
(recherche/benchmarks packagés) que la sanitisation maison ne couvre peut-être
pas explicitement — un filet de sécurité supplémentaire sur une surface
(X/social, contenu externe) déjà identifiée comme sensible dans la doctrine
ARIA. **Risque à peser** : dépendance Python supplémentaire sur un chemin
critique de sécurité — évaluer le coût de latence avant tout branchement.

---

## ~~4. Blockscout API~~ — RETIRÉ (confirmé déjà intégré)

**Doute levé, pas de doublon accidentel évité de justesse.** Le doute posé
dans la première passe ("impossible de savoir sans lire `services/`") a été
vérifié : `services/blockscout.py` existe bel et bien dans le repo
(`packages/aria-core/src/aria_core/services/`), aux côtés de `smart_money.py`
dédié. Bon réflexe d'avoir flagué le risque de doublon au lieu de proposer
sans vérifier — mais la vérification elle-même (grep du repo réel) aurait dû
être faite avant de banquer la piste, pas après coup sur remarque opérateur.
Retenu comme rappel de méthode, pas comme piste.

---

## 5. Dune Analytics API — requêtage SQL on-chain multi-chaînes

**Légitimité.** Standard de facto du secteur pour les requêtes SQL on-chain
(100+ chaînes, dashboards, API). Pas un projet crypto — angle financement non
pertinent. Légitimité forte, mais **Flipside Crypto (le concurrent
équivalent) a fermé son offre créateur en 2026** — signal que ce marché se
consolide, à surveiller pour la pérennité de Dune lui-même à moyen terme.

**Seam.** Alimenterait potentiellement `include_ta`/`include_fundamentals`
sur `TokenScanContext` pour des requêtes agrégées/historiques que GeckoTerminal
(gratuit, déjà utilisé) ne couvre pas.

**Upside concret** : faible priorité immédiate. Modèle de prix par crédits,
démarre autour de 75 $/mois — **contredit la doctrine actuelle** ("machine
sur-dimensionnée, le cache est le vrai levier vitesse", sources gratuites
privilégiées : GeckoTerminal, blockchain.info, Polymarket, Arena Virtuals,
toutes "zéro clé"). Banqué pour mémoire, pas recommandé à court terme sauf
besoin spécifique non couvrable par une source gratuite.

---

## Non retenu à ce stade (recherché, écarté)

- **Frameworks d'agents multi-agents génériques** (au-delà de CrewAI déjà
  tranché par l'ADR §7) : rien de nouveau trouvé dans ce scan qui change le
  critère permanent (contrôle/auditabilité/pas de prompt masqué) — pas
  reproposé sans signal de charge d'orchestration devenue ingérable.
- **Nansen/Arkham et équivalents payants** (Smart Money API, Deep Blue Alpha,
  CryptoPulse) : légitimes mais commerciaux et redondants avec Blockscout côté
  fonction (holders/whale tracking) — moins alignés avec la doctrine
  "zéro clé, gratuit d'abord" qu'ARIA applique déjà à ses autres services.

## Prochaine passe

Catégories pas encore couvertes dans ce scan (à faire en continu, pas en une
fois) : indexation vectorielle/RAG, observabilité LLM (tracing coûts/latence),
protocoles agent-à-agent au-delà d'ACP/x402 déjà en place, techniques
d'inférence (décodage spéculatif, routing multi-modèle par coût).

---
---

# Passe 2 (2026-07-11, même jour) — 4 catégories confirmées par l'opérateur

Discipline appliquée cette fois : grep du repo réel (`contents/` API GitHub sur
`aria_core/`, `aria_core/memory/`) **avant** d'écrire quoi que ce soit, pas
après. Ça a évité deux propositions mortes-nées (voir "écarté avant
publication" ci-dessous) — la leçon du tour précédent tient.

## 6. Indexation vectorielle / RAG — ÉCARTÉ AVANT PUBLICATION (déjà présent)

**Grep fait avant de proposer** (`contents/.../aria_core` puis
`.../aria_core/memory/vector`) : ARIA a déjà une façade mémoire complète —
`aria_core/memory/` avec `journal.py` (journal épisodique), `cognitive_sql.py`
(leçons approuvées SQLite), `arbitrator.py` (arbitre court/moyen/long terme +
résolution de conflits), `values.py`, `goals.py`, `reflection.py` — **et un
vectoriel embarqué** : `memory/vector/chroma_store.py` +
`chroma_client.py` (Chroma embedded, schema typé `insight`/`lesson`/
`reflection`/`decision`). Décrit comme "Phase C", **désactivé par défaut**
(`is_vector_enabled`, install opt-in `pip install -e ".[dev,vector]"`).

Aucune piste externe (LanceDB, Qdrant, sqlite-vec) n'est proposée ici — ce
serait un doublon pur. Seule information utile à remonter : **le vectoriel
existe mais est éteint** — si un besoin de recherche sémantique se fait
sentir, le premier réflexe est d'activer ce qui existe déjà, pas d'évaluer un
nouvel outil.

## 7. Observabilité LLM (Langfuse) — retenu, pas de doublon confirmé

**Légitimité.** Langfuse : MIT, self-hostable, standard de facto de la
catégorie observabilité LLM en 2026 (traces/spans/appels imbriqués pour
pipelines RAG et boucles d'agents). Pas un projet crypto.

**Vérifié avant de proposer** : `aria_core/llm_usage.py` existe déjà mais son
rôle est étroit — un **compteur de tokens en JSONL mensuel** (agrégation
input/output/calls par jour/provider via `ContextVar`), pas une trace
structurée par appel (pas de spans, pas de latence par étape, pas d'UI). Donc
**pas un doublon** : Langfuse couvrirait un manque réel (visualiser une
chaîne skill→LLM→retry/fallback avec latence par étape), `llm_usage.py`
couvrirait un besoin différent (coût agrégé) et resterait en place.

**Seam.** `aria_core/llm.py` (`_post_chat`, `chat_with_context`) — Langfuse
s'intègre typiquement par décorateur/wrapper autour de l'appel LLM, donc
addable sans toucher aux skills appelants.

**Upside concret.** Utile surtout pour du debugging de chaîne (pourquoi telle
route a basculé sur le fallback, où le temps est perdu) — pas urgent tant
qu'aucun incident de perf/fiabilité de chaîne LLM n'est identifié. Priorité
faible à moyenne, à activer si un problème de latence/fiabilité multi-route
devient difficile à diagnostiquer avec les logs actuels.

**Précision demandée par l'opérateur — modèle d'hébergement (vérifié).**
Langfuse est bien MIT et auto-hébergeable via Docker **sans coût de licence
ni limite d'événements** — donc conforme à la doctrine "gratuit d'abord" et
au contrôle des données (rien n'est envoyé à un SaaS tiers si auto-hébergé,
contrairement au cloud Langfuse payant à éviter par minimisation des
données). **Mais un vrai bémol technique à peser avant de la banquer comme
piste "sérieuse" sans réserve** : depuis la v3, Langfuse s'appuie sur
**ClickHouse** pour le stockage des traces — un service supplémentaire à
faire tourner. Le chiffre cité pour un déploiement "medium-scale"
(3-4k$/mois) suppose un cluster ClickHouse dédié + DevOps et **ne s'applique
probablement pas** au volume d'ARIA (bien en dessous de 500k-2M
événements/mois) ; à ce volume, un ClickHouse mono-nœud en Docker Compose
serait objectivement plus léger. Reste que c'est **un service de plus à
maintenir sur un VPS déjà noté comme contraint en RAM** (~1,8 Gio observé
pour une autre fonctionnalité dans `architecture-extensibilite.md`) — donc
**verdict : piste sérieuse et conforme à la doctrine données/coût, mais pas
gratuite en effort opérationnel** ; à peser contre la RAM disponible avant
d'arbitrer, pas contre un coût license/cloud (qui, lui, est bien nul en
auto-hébergé).

## 8. Protocoles agent-paiement au-delà de x402/ACP (AP2, MPP, TAP) — watch seulement, pas une proposition

**Légitimité.** AP2 (Google, mandats d'autorisation cryptographiques), MPP
(Stripe), TAP (Visa, identité d'agent dans les headers de paiement) — tous
réels, lancés/adoptés courant 2025-2026, pas des projets crypto isolés (AP2
et MPP viennent de Google/Stripe). x402 et ACP sont déjà en place dans ARIA
(`services/x402.py`, `skills/acp_*.py`, `services/virtuals.py`).

**Seam.** Aucun identifié avec un besoin concret aujourd'hui. Ces protocoles
couvrent l'**autorisation** (mandats vérifiables avant paiement) — un rôle
que la doctrine ARIA remplit déjà autrement, par validation humaine
Telegram/Tangem (le dôme, règle 0.3 : "aucune exécution financière
automatique"). Tant qu'ARIA n'interopère pas avec des agents tiers exigeant
un mandat d'autorisation formel (au-delà du endpoint `arena-signal/btc`
lecture seule pour Shekel), ce n'est pas connectable concrètement — **pas
banqué comme piste d'action, juste noté pour veille** si un futur besoin
d'interop avec paiement tiers émerge.

## 9. Routage multi-modèle par coût, style cascade (FrugalGPT) — technique, pas un outil

**Légitimité.** Ligne de recherche mature et publiée (FrugalGPT et
descendants, papier "Policy-Guided Stepwise Model Routing for Cost-Effective
Reasoning" 2026) : router chaque requête vers le modèle le moins cher
suffisant, escalader seulement si nécessaire. C'est une **technique**, pas un
projet — l'angle financement/token ne s'applique pas.

**Vérifié avant de proposer.** `llm.py::_resolve_routes` construit déjà une
chaîne primary→fallback, mais **le fallback ne se déclenche que sur erreur
HTTP/absence de clé** (`_fallback_route`), jamais sur un critère de
coût/difficulté de la tâche — donc pas de doublon, c'est un axe différent
(cascade par confiance/coût vs. simple bascule sur panne).

**Seam.** `_resolve_routes`/`chat_with_context` dans `llm.py`.

**Upside concret.** Gain de coût potentiel si certains appels (ex. formatage,
résumé court) n'ont pas besoin du modèle primaire premium.

**Vérification demandée par l'opérateur — faite.** Grep complet de `llm.py` sur
`depth` (5 occurrences) : le paramètre traverse `_post_chat` (l.176) et
`chat_with_context` (l.253), mais dans les deux cas **il n'est utilisé que
comme tag passé à `record_llm_usage(..., depth=depth)`** (l.219, 240, 303) —
c'est-à-dire uniquement pour catégoriser le log de coût après coup. **Il
n'apparaît nulle part dans `_resolve_routes`, `_route_for_provider`,
`_fallback_route` ni `_resolve_model`** — aucune de ces fonctions ne lit
`depth` pour choisir un modèle. **Confirmé : c'est un vrai seam vide**, pas
une fonctionnalité déjà là qui porterait un autre nom. Le paramètre est
littéralement le bon nom pour porter demain un signal de difficulté
(`depth="light"/"deep"` par ex.) jusqu'à `_resolve_model`/`_route_for_provider`
sans changer la signature d'aucun appelant existant — juste faire lire ce
champ, déjà propagé partout, par la logique de sélection de route au lieu de
seulement le logging. Seam confirmé : `llm.py`, précisément
`_resolve_model`/`_route_for_provider` (pas `chat_with_context`, qui ne fait
que relayer `depth` plus bas). Priorité moyenne, dépend du volume réel
d'appels "faciles" dans le pipeline (donnée non disponible dans cet audit).

---
---

# Passe 3 (2026-07-11, même jour)

## 10. Slither / Mythril (analyse statique de smart contracts) — évalué, écarté

**Légitimité.** Les deux sont réels et solides (Slither par Trail of Bits,
Mythril par ConsenSys/analyse symbolique) — pas des projets crypto, outils
d'infra classiques.

**Vérifié avant de proposer.** Aucun module `audit`/`slither`/`mythril`/
`contract_audit` dans `services/`ou `skills/` (listes déjà grep-ées lors des
passes précédentes) — pas un doublon technique. Mais la doctrine ARIA
existante (confirmée dans `architecture-extensibilite.md` et l'entrée GoPlus
déjà intégrée) est de **consommer un scoring de risque tiers déjà maintenu**
(GoPlus, potentiellement TokenSniffer) plutôt que d'auditer soi-même le
bytecode. Faire tourner Slither/Mythril impliquerait : compilateurs Solidity
multi-versions, exécution symbolique coûteuse en CPU/mémoire — sur un VPS
déjà noté comme sensible en RAM.

**Verdict** : légitime mais **écarté** — aucun seam clair (ARIA ne fait pas
d'audit de contrat, elle consomme du scoring), et le coût d'infra contredit
la doctrine "profondeur proportionnelle à l'enjeu" pour un besoin déjà
couvert autrement. Noté pour mémoire, pas banqué comme piste active.

## 11. Prompt caching natif multi-provider — retenu, mais c'est un changement de structure de prompt, pas une lib à ajouter

**Légitimité.** Fonctionnalité native des providers eux-mêmes (pas un outil
tiers) : Anthropic (cache_control, -90% sur les tokens en cache), OpenAI
(automatique au-delà de 1024 tokens de préfixe stable, -50%), Gemini (-90%).
Pas de projet crypto, angle non pertinent — et pas de bibliothèque à évaluer,
c'est une fonctionnalité d'API.

**Vérifié avant de proposer.** Grep de `llm.py` sur `cache` : **zéro
occurrence**. Le payload construit dans `_post_chat` (`model`, `messages`,
`temperature`, `max_tokens`) n'a aucun champ de cache. `PROVIDER_URLS` liste
`xai/grok, openai, groq, openrouter, virtuals, ollama` — **pas Anthropic**,
donc le mécanisme "cache_control" d'Anthropic ne s'applique pas ici. Pour
OpenAI, la mise en cache est automatique et gratuite en code (zéro
changement) **à condition que le préfixe du prompt soit stable** — donc le
vrai levier n'est pas "ajouter du caching" mais **vérifier que
`system_context` (mémoire injectée, cf. docstring `chat_with_context`) place
le contenu STABLE en premier et le contenu variable (données on-chain du
scan, timestamp, etc.) en dernier** dans la liste `messages`. Je n'ai pas
vérifié la construction exacte de `system_context` dans les skills
appelants (hors budget de cette passe) — **à faire avant de considérer ce
point actionnable**.

**Seam.** Pas un nouveau fichier — un ordonnancement à l'intérieur de la
construction du prompt dans les skills qui appellent `chat_with_context`
(`vc_analysis.py` en premier lieu). Zéro coût d'infra, zéro dépendance.

**Upside concret** : gain de coût potentiel sur le provider OpenAI (gratuit,
zéro changement de code hors réordonnancement du prompt) — mais le
routage primaire d'ARIA passe par `virtuals`/`groq` selon la config (pas
confirmé qu'OpenAI soit la route réellement utilisée en production) ; à
vérifier lequel des providers actifs supporte un caching équivalent avant de
prioriser ce chantier.

## 12. LLM-as-judge d'évaluation (DeepEval / Promptfoo) — CORRIGÉ, voir note dédiée

**Correction opérateur (2026-07-11) : mauvais diagnostic initial.** Ce que je
propose ci-dessous est resté ancré à l'angle "outil de test", alors que le
vrai blocage de la case "calibration" dans `real_money_readiness.py` est un
manque de **volume de pronostics forward clôturés**, pas un manque de test de
cohérence du juge — DeepEval/Promptfoo mesurent si le juge est cohérent, pas
s'il est juste contre la réalité. Analyse redirigée et approfondie dans
[2026-07-11-calibration-juge-sans-attendre-volume.md](2026-07-11-calibration-juge-sans-attendre-volume.md)
(backtesting historique + calibration bayésienne à petit échantillon —
DeepEval/Promptfoo y sont downgradés au rang de plomberie d'exécution
optionnelle, pas la solution). Entrée conservée ci-dessous pour l'historique
du raisonnement, mais **superseded** par la note dédiée.

**Légitimité.** DeepEval (Confident AI) et Promptfoo : open source, actifs,
adoption réelle en 2026, usage documenté en CI pre-deploy. Pas des projets
crypto.

**Vérifié avant de proposer.** Le répertoire `tests/` d'ARIA contient 209
fichiers, dont plusieurs liés au juge (`test_vc_judge.py`,
`test_qi_auto_judge.py`, `test_qi_self_judge_shadow.py`,
`test_recalibration.py`). Lecture de `test_vc_judge.py` : **`chat_with_context`
y est systématiquement remplacé par un `AsyncMock`** — ces tests vérifient la
logique de code autour du juge (clamp de score, sanitisation, allowlists)
avec des réponses LLM **fabriquées à la main**, jamais un vrai appel modèle.
Cohérent avec la doctrine "tests offline mockés" déjà documentée. **Donc
aucune évaluation de la qualité réelle du jugement d'un vrai modèle en
production n'existe aujourd'hui** — pas de doublon, c'est une couche
manquante distincte (CI qualité vs tests de code).

**Rapprochement avec un manque déjà documenté par ARIA elle-même** :
`skills/real_money_readiness.py` liste explicitement "calibration du juge"
comme une case **`unknown`** du scorecard "feu vert argent réel" — faute de
donnée/mécanisme pour la mesurer. DeepEval (GEval, métriques de calibration)
est exactement le type d'outil qui comblerait cette case précise.

**Seam.** Nouveau : un dossier d'eval (ex. `tests/evals/` ou équivalent),
séparé des tests unitaires mockés existants, tournant sur de vrais appels
`chat_with_context` (donc en CI optionnelle/nocturne, pas sur chaque commit,
pour ne pas consommer de vrais tokens à chaque push) — alimenterait
potentiellement `real_money_readiness.py` en donnée réelle pour la case
calibration.

**Upside concret** : comble un manque nommément identifié par ARIA
elle-même (`real_money_readiness` case "calibration du juge" = unknown),
pas une amélioration spéculative. Priorité à discuter avec l'opérateur au vu
de l'enjeu (c'est une des 8 cases pré-engagées avant argent réel).

---

## Prochaine passe

Catégories pas encore couvertes : sécurité (scanners de dépendances/SCA type
Trivy/OSV-Scanner sur les deps Python), techniques de retrieval
augmenté pour du texte long (au-delà de Chroma déjà présent — reranking,
hybrid search), standards d'identité d'agent (au-delà des mandats de
paiement déjà notés en veille passe 2).

---
---

# Passe 4 (2026-07-11, même jour)

## 13. SCA — scan de vulnérabilités des dépendances (pip-audit / OSV-Scanner) — retenu, gap réel

**Légitimité.** pip-audit : maintenu par PyPA/Trail of Bits (avec support
Google), lit `pyproject.toml`/venv, croise PyPI Advisory DB + OSV, `--fix`
automatique, sortie SARIF pour CI. OSV-Scanner : Google, multi-écosystème
(PyPI + npm, pertinent puisqu'ARIA a aussi un frontend). Pas des projets
crypto.

**Vérifié avant de proposer.** Les 4 workflows CI du repo
(`ci.yml`, `frontend-build.yml`, `secrets-scan.yml`, `security-sim.yml`) —
grep sur `pip-audit|osv|trivy|safety|bandit|dependency|vulnerab` : **zéro
résultat dans les trois pertinents**. `secrets-scan.yml` couvre la fuite de
secrets (detect-secrets, cohérent avec `.secrets.baseline` vu à la racine du
repo), `security-sim.yml` couvre vraisemblablement le dôme/anti-injection
(`test_coherence.py` et consorts, vus lors de l'audit ODEI). **Aucun des
trois ne vérifie les CVE connues dans les dépendances** (`httpx`, `web3`,
`fastapi`, `aiosqlite`, etc. — 15 deps directes rien que dans
`aria-core/pyproject.toml`, plus le frontend npm). Pas de doublon — angle
totalement absent aujourd'hui.

**Seam.** Nouveau job dans `ci.yml` (ou un 5ᵉ workflow dédié, même patron que
`secrets-scan.yml`) — `pip-audit` pour `packages/aria-core`, `osv-scanner`
en complément pour couvrir le frontend npm dans la même passe.

**Upside concret.** Comble un angle de sécurité totalement absent
aujourd'hui (CVE connues dans les deps, pas la logique métier) — coût
d'intégration trivial (une action GitHub, pas de nouvelle dépendance
runtime, gratuit). Priorité moyenne à haute : peu d'effort, risque réel non
couvert (chaîne d'approvisionnement).

## 14. Reranking / hybrid search (BM25 + vecteur + reranker) — banqué, priorité basse, prématuré

**Légitimité.** Écosystème mûr en 2026 (BM25 + Reciprocal Rank Fusion +
reranker cross-encoder type bge-reranker-v2-m3 ou Cohere Rerank) — gains
mesurés significatifs (jusqu'à +39% recall vs dense seul) sur des corpus
larges et ambigus. Composants open source auto-hébergeables à coût quasi
nul (BM25 + pgvector/Chroma + MiniLM reranker).

**Vérifié avant de proposer.** `memory/vector/ingest.py` : aucune trace de
`rerank`/`bm25`/`hybrid` — pas de doublon technique. **Mais le vrai
obstacle n'est pas un manque d'outil, c'est le manque de justification** :
le vectoriel Chroma d'ARIA est (a) désactivé par défaut (Phase C, opt-in),
(b) conçu pour un corpus **petit et typé** (`insight`/`lesson`/`reflection`/
`decision`), pas du texte libre volumineux et ambigu. Le gain de la
reranking/hybrid search se manifeste sur des corpus larges avec des
requêtes ambiguës — un profil qui ne correspond pas à l'usage actuel décrit.

**Seam** (si le besoin apparaît un jour) : `memory/vector/chroma_store.py` /
`ingest.py`.

**Upside concret** : quasiment nul aujourd'hui, à réévaluer seulement si (a)
le vectoriel est activé en production ET (b) le volume/l'ambiguïté des
requêtes sur la mémoire vectorielle devient un problème observé — pas avant.
Banqué pour mémoire, pas une piste active.

## 15. Standards d'identité d'agent (W3C DID/VC, MCP-I, TRAIL did:trail) — veille seulement

**Légitimité.** Développements réels et récents : Verifiable Credentials 2.0
est un standard W3C, DID v1.1 en Candidate Recommendation (mars 2026),
MCP-I donné à la DIF (mars 2026), `did:trail` — méthode DID spécifiquement
conçue pour les agents IA. Adoption encore limitée selon les sources
elles-mêmes ("adoption remains limited"). Pas des projets crypto isolés
(standards W3C/DIF, initiatives d'acteurs identité établis).

**Vérifié avant de proposer.** Aucune trace de `did:`/`agent-card`/
`verifiable` dans `acp_client_skill.py` (le skill qui gère l'interaction
d'ARIA avec le réseau ACP de Virtuals) — pas de doublon technique direct.
Mais ARIA a déjà une identité de facto dans l'écosystème Virtuals via ACP
(agent enregistré, "Aria Vanguard ZHC" présent sur `app.virtuals.io/acp`) —
la question n'est pas "ARIA a-t-elle une identité", c'est "a-t-elle besoin
d'une identité interopérable **hors** Virtuals".

**Seam.** Aucun identifié avec un besoin concret aujourd'hui — même
raisonnement que l'entrée 8 (AP2/MPP, passe 2) : ARIA n'interopère pas
aujourd'hui avec des agents/plateformes tiers qui exigeraient un DID/VC
formel (seul point d'exposition externe connu : l'endpoint lecture seule
`arena-signal/btc` pour Shekel, qui n'exige aucune authentification par
contrat déjà documenté). **Pas banqué comme piste d'action, veille
seulement** — à réévaluer si ARIA doit un jour prouver son identité à une
plateforme tierce hors Virtuals (adoption encore limitée de toute façon selon
les sources elles-mêmes, donc pas urgent).

## 16. Indexation on-chain temps réel (Goldsky / The Graph, subgraphs) — banqué, priorité basse

**Légitimité.** Standard du secteur pour indexer des événements on-chain
sur mesure via GraphQL. Goldsky : tarification simple (workers horaires +
volume d'entités), palier gratuit pour l'exploration. The Graph : palier
gratuit ~100k requêtes/mois, mais **le "Hosted Service" gratuit historique a
été déprécié en 2026** — l'offre gratuite restante est plus limitée
qu'avant. Pas des projets crypto au sens spéculatif (infra de requêtage), même
si adjacents à l'écosystème.

**Vérifié avant de proposer.** Services existants déjà listés
(`blockchain_info.py`, `blockscout.py`, `coingecko.py`, `ohlcv.py`,
`smart_money.py`) couvrent prix/holders/historique — **aucun ne fait de
requêtage d'événements on-chain sur mesure** (ex. suivi en temps réel
d'ajouts/retraits de liquidité sur un pool spécifique) — pas de doublon
direct, mais pas de besoin documenté non plus.

**Seam potentiel** (hypothétique) : `services/` (nouveau client) →
`launchpad_discovery.py`/`bonding_absorber.py` si la latence de détection de
nouveaux pools/graduations via polling des APIs actuelles s'avérait
insuffisante.

**Upside concret** : spéculatif — pas de preuve que la latence actuelle
(polling GeckoTerminal/Basescan/Blockscout) pose un problème réel. Coût non
nul au-delà du palier gratuit. Banqué pour mémoire seulement, à ne
reconsidérer que si un besoin de détection plus rapide est explicitement
identifié par l'opérateur.

---

---
---

# Passe 5 (2026-07-11, même jour)

## 17. Red-teaming/fuzzing LLM (Garak, PyRIT) — partiellement doublon, piste réduite à un complément ciblé

**Légitimité.** Garak (NVIDIA, 120+ probes jailbreak/prompt-injection/fuite
de données) et PyRIT (Microsoft, orchestration d'attaques multi-tours type
Crescendo/TAP) : les deux références open source maintenues de la
catégorie, complémentaires entre elles (Garak = balayage large, PyRIT =
campagnes programmables profondes). Pas des projets crypto.

**Vérifié avant de proposer — doublon substantiel trouvé.** `.github/workflows/
security-sim.yml` fait déjà tourner **quotidiennement** un red-team
"maison" en-process (`python -m security_sim.run --budget 8000`) : des
milliers de requêtes hostiles sur toutes les routes du backend, échec CI si
faille CRITICAL/HIGH (crash 5xx, fuite de secret/stacktrace, bypass auth).
Ce n'est PAS une lacune "aucun red-teaming n'existe" — ARIA a déjà un
système actif et planifié. Proposer Garak/PyRIT comme piste générale serait
un doublon de fonction.

**Ce qui reste un gap réel, plus étroit** : le harnais maison teste
vraisemblablement des attaques conçues en interne (portée inconnue depuis
cet audit — code de `security_sim` non lu), alors que Garak embarque un
**catalogue de 120+ probes issus de la recherche externe** (patterns de
jailbreak/fuite documentés par la communauté, pas seulement ce à quoi
l'équipe ARIA a pensé). Piste réduite en conséquence : pas "adopter
Garak/PyRIT comme système", mais **faire tourner Garak en job CI
complémentaire** (même cadence hebdomadaire/quotidienne que
`security-sim.yml`, job séparé) pour élargir la couverture de probes
connus, sans toucher au harnais maison existant.

**Seam.** Nouveau workflow GitHub Actions à côté de `security-sim.yml`
(même patron : `workflow_dispatch` + cron), ciblant l'API `api.odei.ai`-like
ou plutôt l'équivalent ARIA exposé (vanguard/backend) — nécessite de
vérifier au préalable la portée exacte de `security_sim.run` (fichier non
lu ici) avant d'arbitrer le niveau de chevauchement réel.

**Upside concret.** Couverture de probes externes documentés en plus de la
suite maison — priorité basse à moyenne, dépend de ce qui est déjà couvert
dans `security_sim` (à vérifier avant toute décision, pas supposé ici).

## 18. Compression de contexte / prompt (LLMLingua) — retenu, pas de doublon confirmé

**Légitimité.** Famille LLMLingua (Microsoft Research) : LLMLingua,
LongLLMLingua (query-aware, réordonnancement anti-biais de position),
LLMLingua-2 (compression par classification de tokens, distillée depuis
GPT-4, fidélité au contenu original mesurée). Gains documentés : jusqu'à
4x moins de tokens avec un gain de performance (pas juste neutre) sur
benchmark long-contexte. Pas un projet crypto — technique de recherche,
implémentations open source (Microsoft).

**Vérifié avant de proposer.** Grep de `llm.py` sur
`compress|truncate|summar|lingua` : seule occurrence, `max_tokens` — qui
borne la **sortie**, pas une compression de l'**entrée**. `vc_session_context.py`
fait autre chose (persistance TTL 4h du dernier rapport `/vc` pour le
suivi conversationnel Telegram — de la continuité, pas de la compression de
prompt). Aucun mécanisme de réduction du `system_context` (mémoire injectée,
potentiellement volumineuse si beaucoup de thèses/faits accumulés) avant
envoi au LLM. Pas de doublon.

**Seam.** `aria_core/llm.py::chat_with_context` — un pré-traitement
optionnel du `system_context` avant construction des `messages`, activable
seulement au-delà d'une taille seuil (data-gated, dégradation gracieuse :
sans compression, comportement inchangé).

**Upside concret.** Réduction de coût/latence sur les appels à contexte
long (`vc_analysis` avec beaucoup de thèses historiques via
`list_theses_for_token`, par exemple) — mais **valeur réelle non
confirmée sans mesurer d'abord la taille effective des prompts actuels**
(je n'ai pas cette donnée ici — `llm_usage.py` logue des tokens agrégés,
pas la distribution de taille de prompt par skill). Priorité conditionnelle
: pertinent seulement si un audit de `llm_usage` montre des prompts d'entrée
déjà larges ; sinon gain marginal.

## 19. Détection de wash trading / faux volume — problème réel confirmé, mais pas d'outil externe accessible ; DIY suggéré

**Légitimité du problème.** Bien documenté et chiffré (Chainalysis : 2,57
milliards $ de wash trading suspecté sur DEX en 2025 ; ~42% du volume NFT
serait du wash trading). Mais les outils trouvés ne sont **pas
concrètement accessibles à l'échelle d'ARIA** : Bubblemaps est un outil de
visualisation web, pas d'API publique documentée trouvée ; Chainalysis est
une offre enterprise/forensique, hors budget et hors doctrine "gratuit
d'abord". **Aucun outil externe abordable identifié** — angle légitimité
technique ok, mais rien à adopter tel quel.

**Vérifié avant de proposer.** `safety_screen.py` vérifie déjà la
concentration des holders (`top_holder_pct` vs `DEFAULT_MAX_TOP_HOLDER_PCT
= 30%`) ; `liquidity_depth.py` vérifie le ratio liquidité/market cap. **Ni
l'un ni l'autre ne vérifie l'authenticité du volume de trading**
(schémas d'aller-retour entre mêmes wallets, ratio buys/sells anormal) —
c'est un axe distinct, pas un doublon, et un vrai trou : un token peut
passer les deux checks existants (holders dispersés, liquidité correcte)
tout en ayant un volume largement artificiel.

**Proposition différente de d'habitude** : plutôt qu'un nouvel outil
externe, une **heuristique maison** sur des données déjà disponibles dans
le pipeline de scan (vu concrètement dans l'audit ODEI : `buys24h`/`sells24h`
déjà exposés par les agrégateurs de prix type Dexscreener/GeckoTerminal déjà
utilisés) — ex. ratio buys/sells anormalement proche de 1:1 avec un volume
élevé mais un nombre de holders qui ne bouge pas (déjà lisible via
`services/blockscout.py`/`smart_money.py` existants). Cohérent avec la
doctrine "profondeur proportionnelle à l'enjeu" : ne pas payer pour du
forensique enterprise quand un signal heuristique simple sur des données
déjà en main peut suffire comme premier filtre.

**Seam.** Nouvelle fonction déterministe dans `safety_screen.py` (même
patron que `assess_liquidity_depth` — pur, `data-gated`, aucun LLM), lue à
partir de champs déjà présents dans `TokenScanContext` si les agrégateurs
utilisés les exposent (à vérifier : quels champs remontent réellement de
`services/ohlcv.py`/`coingecko.py`/`blockscout.py` aujourd'hui — pas fait
dans cet audit).

**Upside concret.** Comble un vrai trou du screening actuel (authenticité
du volume, pas juste concentration/liquidité) sans nouvelle dépendance
externe ni coût — mais nécessite de vérifier d'abord si les données
buys/sells sont déjà remontées par les services existants avant d'écrire
la fonction, sans quoi ça retombe sur l'ajout d'un nouveau service.

---

## Prochaine passe (note passe 5)

Catégories restantes à couvrir (reportées, pas encore confirmées par
l'opérateur) : techniques de détection de collusion LLM-LLM (pertinent si un
jour ARIA interagit avec d'autres agents IA sur ACP), standards de
provenance de contenu (C2PA) pour les visuels générés (`chart_render.py`,
avatar), frameworks de simulation multi-agents pour tester des scénarios de
marché avant déploiement réel.

---
---

# Passe 6 (2026-07-11, même jour) — angle demandé : voix moins "IA générique"

Motif concret de l'opérateur : dans le test Groq de tout à l'heure, la
réponse "méthode d'analyse" était factuellement correcte mais sonnait comme
du remplissage IA type ("processus complexe qui nécessite une approche
multidisciplinaire") — exactement ce que la charte "zéro trace IA / voix
humaine" cible.

## 20. Le mécanisme existe déjà — mais câblé sur le mauvais périmètre (trouvaille principale)

**Vérifié avant de chercher quoi que ce soit à l'extérieur.** ARIA a déjà
`aria_core/x_voice.py` : un détecteur déterministe par regex des tics d'IA
qui se présente ("as an AI", "autonomous agent", "ZHC agent", "CAO", jargon
de stack), une fonction `human_voice_rules_for_llm(lang)` qui injecte des
règles de voix humaine dans le prompt LLM, et un nettoyage regex léger
post-génération (`strip_obvious_ai_phrases`) sans appel LLM. Il existe aussi
un playbook éditorial complet (`docs/playbook-editorial-aria.md`) avec un
vrai tableau d'exemples ✅/❌ de la voix ARIA (phrases concrètes, pas des
généralités).

**Mais** : `human_voice_rules_for_llm` n'est importé et injecté que dans
`comms_skill.py` (le pipeline de publication X) — **grep confirmé sur
`vc_analysis.py` et sur le fichier de la conversation générale
(`proactive_greeting.py`, dépôt `aria-ops`) : zéro occurrence**. Les 3
prompts testés tout à l'heure via Groq (`these_vc`, `methode_fr`,
`initiative`) ne passent par AUCUN de ces deux périmètres câblés (X) — ils
sortent donc "nus", sans la couche anti-IA-générique qui existe pourtant
déjà dans le code. **Ce n'est pas un problème d'outil manquant, c'est un
problème de câblage incomplet.**

**Seam.** Élargir l'injection de `human_voice_rules_for_llm` (ou une
variante) aux system prompts de `vc_analysis.py` et du handler de
conversation générale — même fonction existante, juste appelée à plus
d'endroits. Zéro nouvelle dépendance.

**Upside concret.** C'est la piste la plus rentable de toute cette passe :
aucun outil à évaluer, aucune légitimité à vérifier, juste étendre le
périmètre d'appel d'une fonction qui existe déjà et qui marche déjà (elle
est en prod sur X). Priorité haute.

## 21. Extension du détecteur — clichés de remplissage IA (pas seulement l'auto-référence)

**Vérifié avant de proposer.** `_AI_VOICE_RE` dans `x_voice.py` ne couvre
que les tics d'**auto-référence** ("as an AI", "autonomous agent"...) — pas
les clichés de **remplissage générique** ("processus complexe", "approche
multidisciplinaire", et en anglais delve/leverage/robust/tapestry/realm/
navigate/landscape/foster/elevate/intricate/meticulous — liste de 21 "focal
words" documentée par la recherche sur les sorties ChatGPT). C'est
exactement le problème signalé par l'opérateur sur la réponse Groq, et
`x_voice.py` ne le couvre pas aujourd'hui — pas un doublon, un axe
complémentaire distinct.

**Légitimité.** Deux références externes trouvées, toutes deux
open-source/déterministes, dans le même esprit que `x_voice.py` (règles
regex, pas de modèle) : un "AI Writing Linter" (gist public, ruleset
composable base + couche voix perso) et un plugin Claude Code équivalent
("AI Pattern Detector", score 0-100 + réécriture). Ce sont des références
d'inspiration pour la LISTE de motifs, pas des dépendances à installer —
étendre `_AI_VOICE_RE`/`strip_obvious_ai_phrases` avec une liste FR/EN plus
large est plus cohérent avec le style déjà en place que d'ajouter un
paquet externe pour un simple ruleset regex.

**Point de vigilance sur la catégorie.** Une partie de l'écosystème
"humanize AI text" trouvé (ex. `lynote-ai/humanize-text`, qui se présente
explicitement comme "bypass Turnitin, GPTZero, undetectable AI content")
vise à **tromper des détecteurs anti-IA** (plagiat académique, SEO
déceptif) — motif hors sujet et sans rapport avec le besoin réel d'ARIA
(qualité de prose légitime, pas évasion de détection). À ne pas utiliser
comme référence, même si superficiellement adjacent.

**Seam.** Extension de `x_voice.py` (`_AI_VOICE_RE`, `strip_obvious_ai_phrases`)
avec une liste de clichés élargie ; appelée aux mêmes points que l'entrée 20
une fois le câblage étendu.

**Upside concret.** Complète le filet existant sur l'axe qui manquait
précisément dans le cas signalé. Priorité haute, effort faible (extension
d'un module pur déjà en place, pas de nouvelle dépendance).

## 22. Few-shot avec la vraie voix ARIA déjà écrite (réutiliser, pas inventer)

**Légitimité de la technique.** Le few-shot prompting (2-5 exemples réels
avant l'instruction) est documenté comme le levier le plus fiable pour
casser la "voix par défaut" d'un LLM — plus efficace qu'une description
abstraite du ton voulu. Ce n'est pas un outil, une technique de prompting.

**Vérifié avant de proposer.** `docs/playbook-editorial-aria.md` contient
déjà un tableau d'exemples ✅/❌ concrets (pas des généralités — des phrases
réelles écrites et déjà validées en esprit par l'opérateur, ex. « Everyone's
buying $X. I looked at the contract. Top holder owns 76%... »). Ce matériau
existe déjà, n'est actuellement qu'un document de planification lu par des
humains — rien ne l'injecte en few-shot dans un prompt LLM aujourd'hui (pas
trouvé dans `x_voice.py` ni `comms_skill.py` : la fonction injecte des
RÈGLES écrites, jamais d'EXEMPLES de phrases).

**Seam.** Nouvelle fonction dans `x_voice.py` (ou un module dédié) qui
extrait 2-4 exemples ✅ du playbook et les ajoute au bloc système avant les
règles — même point d'injection que l'entrée 20/21 une fois élargi.

**Upside concret.** Réutilise du matériau déjà écrit et déjà approuvé en
esprit (pas de nouvel exemple à inventer/faire valider) — combine bien avec
les deux entrées précédentes (règles + détecteur + exemples réels = les
trois leviers documentés dans la même famille de technique). Priorité
haute, effort faible.

---

## Verdict de cette passe

Les trois pistes (20, 21, 22) sont interdépendantes et se banquent comme un
seul chantier cohérent : étendre le périmètre d'appel de `x_voice.py` (déjà
en prod sur X) au reste des surfaces LLM d'ARIA, élargir sa liste de motifs
au-delà de l'auto-référence, et ajouter des exemples réels en few-shot au
même point d'injection. Rien de nouveau à installer, rien à valider côté
légitimité externe — le travail est interne, sur du code qui existe déjà et
qui marche déjà sur un autre périmètre.

---

# Passe 7 (2026-07-11) — collusion LLM-LLM, provenance de contenu, frameworks multi-agents

**Vérifié avant de proposer.** `grep -ril` sur `packages/aria-core/src/aria_core/`
(hors `.venv`) pour "collusion", "c2pa", "provenance", "content credentials",
"multi-agent"/"multi_agent"/"crewai"/"autogen"/"langgraph" : aucun résultat
réel (seuls des faux positifs dans les dépendances vendorisées). Les trois
sujets sont bien absents du code actuel — pas de doublon à signaler.

## 23. Détection de collusion LLM-LLM (interprétabilité multi-agents)

**Légitimité.** Ligne de recherche active et sérieuse en 2026 : sondes
linéaires (linear probes) sur les activations, agrégées au niveau du
groupe d'agents pour détecter une collusion invisible agent par agent.
Benchmark dédié `NARCBench` (AUROC 1.00 in-distribution, 0.60–0.86 en
transfert zero-shot). Papier complémentaire "Audit the Whisper" sur la
collusion stéganographique (un juge LLM qui lit la conversation en clair
ne discrimine pas collusion vs contrôle ; l'accès aux activations internes
donne un signal complémentaire). Publications arXiv multiples, pas un
outil produit — donc pas de "légitimité projet" à évaluer, juste la
solidité de la littérature, qui est réelle.

**Connectable ? Non, pas directement aujourd'hui.** ARIA n'est pas un
système multi-agents au sens de ces papiers : un seul agent ARIA appelle
des providers LLM externes (`llm.py`, routage par `depth`), il n'y a pas
plusieurs agents ARIA qui négocient/collaborent entre eux dont il faudrait
auditer la collusion. Le seam le plus proche serait la coordination
inter-VPS (blocs colorés 🟣 entre VPS Research/Secondaire/Principal) —
mais c'est de la coordination humaine-arbitrée entre opérateurs de VPS,
pas des agents qui négocient de façon autonome et non supervisée. Pas de
upside concret identifiable aujourd'hui.

**Verdict : à surveiller (watch), pas de piste actionnable maintenant.**
Deviendrait pertinent uniquement si ARIA évoluait vers une architecture où
plusieurs instances ARIA négocient entre elles sans validation humaine
systématique à chaque échange — ce qui contredirait la doctrine du dôme
(validation humaine des propositions) déjà en place. Banqué comme
référence de méthode (linear probes sur activations) au cas où un besoin
d'audit de logs inter-agents apparaîtrait un jour.

## 24. C2PA / Content Credentials — provenance cryptographique de contenu généré par IA

**Légitimité — forte et concrète en 2026.** C2PA (Coalition for Content
Provenance and Authenticity) attache un manifeste signé (assertions
origine, éditions, outils utilisés, implication IA) à un contenu ; "Content
Credentials" est le nom grand public. Adoptants 2026 : OpenAI, Google,
Kakao, ElevenLabs, Nvidia. OpenAI+Google ont annoncé le 2026-05-19 un
modèle à double couche (C2PA + watermark SynthID) sur tout contenu généré
via ChatGPT/API OpenAI. **Driver réglementaire concret** : le règlement UE
sur l'IA (EU AI Act), Article 50, rend le marquage machine-readable du
contenu généré par IA **obligatoire à partir du 2026-08-02** (dans un peu
plus de 3 semaines).

**Vérifié avant de proposer.** Grep sur `x_publication_policy.py`,
`gateway/x_twitter.py`, `x_profile.py` pour toute mention de
disclosure/label IA/bot : aucun résultat. Lu `narrative.py::x_bio()` — la
bio publique du compte X (`@Aria_ZHC`) mentionne "CAO", le holding, le
bot Telegram, mais **aucune mention explicite "contenu généré par IA" ni
manifeste de provenance**. Confirmé : ARIA ne fait actuellement aucune
forme de disclosure/marquage IA sur ses posts X, ni manifeste C2PA.

**Connectable — seam identifié, mais question de conformité, pas juste
technique.** Seam le plus direct : pipeline de publication X
(`gateway/x_twitter.py`, en amont de l'appel API) où un texte de
disclosure pourrait être ajouté (ex. mention visible ou métadonnée), et/ou
`x_profile.py`/bio pour un statut de compte explicitement automatisé.
**Point de vigilance** : l'applicabilité de l'Article 50 dépend du
statut juridictionnel réel de l'opération (fournisseur/déployeur établi
dans l'UE ou visant le marché UE) — je n'ai pas cette information et ne
la déduis pas. C'est une décision de politique de contenu/conformité, pas
un bug à corriger — je ne propose pas de câblage sans confirmation
explicite sur (a) l'exposition juridictionnelle réelle et (b) la forme de
disclosure souhaitée (mention textuelle simple vs manifeste C2PA complet,
qui demande une infra de signature).

**Verdict : priorité moyenne-haute, à trancher par l'opérateur** — pas un
manque technique caché, mais un vrai gap de conformité si l'exposition UE
est réelle. Échéance concrète (2026-08-02) qui justifie de ne pas la
laisser traîner sans une décision explicite, même si la décision est "non
applicable, pas d'exposition UE".

## 25. Frameworks de simulation multi-agents pour trading crypto (TradingAgents, ElizaOS, FinRL)

**Légitimité.** `TradingAgents` (TauricResearch, Apache-2.0, v0.3.1 en
juillet 2026, agents de risque qui valident avant qu'un "portfolio
manager" approuve, routage vers un exchange simulé — jamais de courtage
live direct) ; `ElizaOS` (framework le plus déployé dans l'écosystème
crypto, plugins, multi-LLM) ; `FinRL` (RL pur, 15200+ stars). Les trois
sont réels, maintenus, largement adoptés — pas de red flag de légitimité.

**Connectable ? Non — rejet cohérent avec une décision déjà actée.**
L'ADR existant d'ARIA refuse explicitement l'adoption d'un framework
multi-agents générique (type CrewAI) tant que la complexité
d'orchestration reste gérable en interne — c'est exactement la catégorie
de ces trois outils. Les adopter dupliquerait une décision d'architecture
déjà prise, pas juste du code.

**Point notable (pas une piste, une validation).** Le design de
`TradingAgents` — agents de risque qui valident *avant* qu'une action soit
approuvée par un "portfolio manager" humain-configuré, jamais d'exécution
automatique directe — est structurellement la même doctrine que le dôme
d'ARIA (propositions financières toujours validées par un humain, jamais
d'exécution auto). Ce n'est pas un delta technique à récupérer, c'est une
confirmation externe indépendante que le choix de design actuel d'ARIA est
aligné avec ce qui se fait de mieux ailleurs sur ce point précis.

**Verdict : rejet, cohérent avec l'ADR existant. Rien à banquer comme
piste d'action** — juste une note de confirmation de design.

---

## Verdict de la passe 7

Sur les trois sujets : un "watch" sans action (collusion, pas de multi-agent
non supervisé chez ARIA aujourd'hui), un rejet cohérent avec l'ADR existant
(frameworks multi-agents génériques), et une vraie question de conformité à
trancher par l'opérateur avec une échéance concrète (C2PA / EU AI Act
Article 50, 2026-08-02) — pas un manque technique caché mais un choix de
politique de contenu qui mérite une décision explicite avant l'échéance.

---

# Passe 8 (2026-07-12) — account abstraction agent, inférence LLM vérifiable (TEE)

**Nouvelle règle process (gravée CLAUDE.md 704978e) appliquée à partir de
cette passe** : plus de commit/push main de ma part — préparation
(recherche + code si applicable) poussée sur une branche temporaire dédiée,
jamais fusionnée par moi.

**Vérifié avant de proposer.** Grep sur "x402" → **déjà intégré**
(`services/x402.py` existe, pas une piste). Grep sur "eas"/"attestation" →
`onchain/anchor.py` + `onchain/attestation.py` existent déjà : ancrage
Merkle-root custom du track-record sur Base (préparation seule, jamais de
clé serveur, gated OFF par défaut). Grep sur "4337"/"account abstraction"/
"paymaster" → un seul hit réel, dans `opportunity_radar.py:42` : un simple
mot-clé de liste de veille (radar textuel), **aucune implémentation** —
donc pas un doublon de fonctionnalité, juste un signal que le sujet est déjà
sur l'écran radar de l'opérateur.

## 26. Account abstraction (ERC-4337) pour le wallet Sepolia — session keys + spending limits on-chain

**Légitimité.** ERC-4337 est un standard mature et déployé en production
sur Base et tous les L2 majeurs. Écosystème SDK mûr en 2025-2026
(Biconomy, ZeroDev, Safe, Coinbase Smart Wallet). Le consensus 2026 pour
un agent autonome qui manipule de vrais fonds : préférer un smart account
ERC-4337 à une EOA, précisément parce que la politique de dépense (limite,
fenêtre de temps, liste blanche de contrats/fonctions) devient
**exécutoire au niveau du contrat**, pas seulement de confiance dans le
code applicatif.

**Vérifié avant de proposer.** Lu `onchain/sepolia_wallet.py` en entier
(en-tête) : c'est **explicitement documenté comme la SEULE exception** à la
règle "clé privée jamais sur le serveur" du dôme — une EOA classique
détient une clé sur le serveur et signe de vraies transactions (bornées à
Sepolia par un verrou `chain_id`, mais c'est un verrou applicatif, pas
un verrou porté par le compte lui-même). C'est exactement le point du
système où un compte à politique de dépense on-chain apporterait une
défense en profondeur réelle, sur un risque déjà identifié et documenté
comme tel par l'équipe elle-même — pas un risque que j'invente.

**Seam.** `onchain/sepolia_wallet.py` — remplacer/complémenter l'EOA par un
smart account (ex. Coinbase Smart Wallet, natif Base) avec session key
scopée (durée + montant + liste de fonctions autorisées, ex. seulement
`exactInputSingle` sur le routeur Uniswap V3 déjà utilisé). Le chain_id
lock existant resterait en plus, pas remplacé — défense en profondeur, pas
substitution.

**Upside concret.** Transforme un risque aujourd'hui contenu par de la
discipline de code (`chain_id` vérifié en amont, flag `ARIA_SEPOLIA_
SWAP_ENABLED`) en un risque contenu *aussi* par une garantie cryptographique
portée par le compte on-chain lui-même — pertinent avant d'envisager un
jour la même mécanique sur des fonds réels (mainnet), ce que le fichier
mentionne explicitement comme trajectoire possible. Priorité moyenne :
pas urgent tant que Sepolia (fonds sans valeur), mais **prérequis naturel
avant tout passage à des fonds réels** — à traiter bien avant cette
échéance-là, pas après.

## 27. Inférence LLM vérifiable par TEE (Phala / EigenAI / Automata)

**Légitimité.** Plusieurs implémentations en production en 2026 : Phala
Network (attestation à distance, déploiement conteneur, GPU TEE Nvidia
Confidential Computing), EigenAI (ré-exécution déterministe vérifiable via
EigenVerify, adossé à la sécurité économique d'EigenLayer), Automata,
Flashbots. Le compromis documenté : le zero-knowledge reste 10 000 à
100 000× plus lent que l'inférence native en 2026, donc les TEE sont le
choix pratique quand la latence compte — ce qui est le cas pour ARIA
(réponses temps réel Telegram/X).

**Connectable ? Faible aujourd'hui — mais pas nul.** `llm.py` route déjà
vers plusieurs providers avec fallback ; rien n'atteste aujourd'hui que le
modèle effectivement utilisé pour une réponse donnée est bien celui
annoncé (ex. prouver à un tiers qu'une analyse `/vc` vient réellement de
`claude-opus-4-8` et n'a pas été discrètement downgradée). C'est un besoin
réel de vérifiabilité, mais **seulement si un tiers externe doit un jour
faire confiance à une sortie ARIA sans faire confiance à l'opérateur** —
aujourd'hui, la confiance repose sur l'opérateur humain qui valide, pas sur
une preuve cryptographique de provenance du calcul. Change de nature si
ARIA devait un jour vendre des verdicts/analyses comme service à des tiers
sans supervision humaine par transaction (ex. API payante en autonomie).

**Seam (si un jour pertinent).** `llm.py` (point de routage unique, déjà le
bon seam architectural) — router optionnellement vers un provider TEE
(Phala Confidential AI) pour les appels dont la sortie doit être
vérifiable par un tiers, en gardant les providers actuels pour tout le
reste (additif, pas un remplacement).

**Verdict : watch, pas de piste actionnable maintenant.** Le besoin réel
(confiance d'un tiers dans une sortie LLM) n'existe pas encore dans
l'usage actuel d'ARIA (validation humaine systématique). Réévaluer si/quand
une surface autonome vendue à des tiers sans validation humaine par
transaction apparaît (ex. lié à l'entrée #24 côté conformité, ou à une
offre API type `acp_offering_skill.py`).

---

## Verdict de cette passe

Deux pistes, deux statuts différents : l'account abstraction (#26) est une
**vraie piste actionnable**, ancrée sur un risque déjà documenté par le
code lui-même (l'exception EOA du dôme) — priorité moyenne, prérequis avant
tout passage à des fonds réels. L'inférence vérifiable par TEE (#27) est un
**watch** — techniquement solide et mature en 2026, mais résout un problème
(confiance d'un tiers sans supervision humaine) qu'ARIA n'a pas encore.

---

# Passe 9 (2026-07-12) — mise à jour #26 (Coinbase Agentic Wallets) + consolidation mémoire hors-ligne

Retour au mode radar large-spectre par défaut après la parenthèse #79
(veille positionnement, note séparée du 2026-07-12). Point de départ
suggéré par l'opérateur : la roadmap "agent-native smart accounts" de
Base, en lien direct avec #26.

## 26bis. Mise à jour de #26 — Coinbase Agentic Wallets rend la piste concrète, pas juste standard générique

**Ce qui change depuis la passe 8.** #26 proposait ERC-4337 en général
(standard générique, implémentation à choisir). Recherche plus poussée :
**Coinbase Agentic Wallets** (GA le 2026-02-11, donc déjà en production
depuis 5 mois, pas une annonce) est l'implémentation concrète, directement
pertinente pour ARIA :
- Wallet MPC-backed pour agent, clé privée **jamais en dehors d'un TEE**
  (enclave matérielle isolée cryptographiquement, y compris de
  l'infrastructure de Coinbase elle-même) — élimine structurellement la
  classe de risque documentée dans `sepolia_wallet.py` ("SEULE exception à
  la règle clé privée jamais sur le serveur"), pas juste l'atténuer.
- Session keys **à portée d'action** (montant, adresses, fonctions
  autorisées) + plafonds par transaction, exécutoires au niveau TEE — le
  patron de "spending policy" exact décrit en #26, mais en produit fini,
  pas à construire.
- **Support natif x402** — déjà intégré côté ARIA (`services/x402.py`,
  confirmé passe 8). Pas une nouvelle dépendance à apprendre, un
  complément naturel à un mécanisme déjà en place.
- Installable en CLI (`npx awal`) ou serveur MCP compatible Claude — donc
  accessible aussi depuis l'environnement de développement Claude Code
  utilisé pour construire ARIA, pas seulement en production.

**Seam.** Toujours `onchain/sepolia_wallet.py` — mais le choix concret
proposé maintenant est "migrer vers Coinbase Agentic Wallets" plutôt que
"choisir un SDK ERC-4337 parmi plusieurs" (Biconomy/ZeroDev/Safe cités en
passe 8). Réduit le travail de sélection, le risque est déjà porté par un
acteur dont l'infra Base est déjà celle utilisée par ailleurs (Coinbase =
chaîne cible d'ARIA).

**Verdict : priorité inchangée (moyenne, prérequis avant fonds réels) mais
piste affinée** — de "standard à évaluer" à "produit précis à évaluer".

## 28. Consolidation mémoire hors-ligne ("sleep-time compute") — gap réel repéré après lecture du système existant

**Vérifié avant de proposer — découverte importante en cours de route.**
Avant de proposer quoi que ce soit, lu `memory/reflection.py` (Phase G —
journal + synthèse) et `memory/arbitrator.py` (Phase H — collecte
multi-couches + résolution de conflits) en détail. **ARIA a déjà un
système de réflexion/arbitrage mémoire sophistiqué**, largement
équivalent en esprit à ce que la littérature 2026 appelle memory
consolidation — donc pas une piste vierge. Mais en creusant précisément ce
que fait `arbitrator.py` (grep de tous les `def`, aucun `forget`/`decay`/
`prune`/`expir` trouvé) : c'est un système **d'arbitrage à la volée, par
requête** — il collecte des snippets depuis plusieurs sources (vérité,
cognitif, journal, réflexion, directives, valeurs, objectifs, vecteur,
conversation) et arbitre les conflits **au moment de construire le contexte
envoyé au LLM**. Il ne **réécrit jamais la mémoire stockée elle-même** —
`journal.jsonl`, la base cognitive, le store vectoriel s'accumulent sans
jamais être élagués, fusionnés ou réorganisés en arrière-plan.

**Légitimité de la technique manquante.** "Sleep-time compute" / memory
consolidation (terme neuroscientifique explicite, hippocampe/sommeil) :
processus asynchrone, hors ligne, qui relit les transcripts/mémoire
accumulée, fusionne les doublons, remplace les entrées obsolètes, applique
un oubli algorithmique borné, et réécrit une mémoire reconsolidée que les
sessions futures utilisent — décrit dans plusieurs papiers 2026 (SCM,
TiMem) et implémenté par Anthropic elle-même sur les fichiers mémoire de
Claude (le mécanisme derrière la skill `consolidate-memory` disponible
dans cette session, au passage — pas un hasard si la même idée existe des
deux côtés).

**Seam.** Nouveau module (ex. `memory/consolidation.py`), tâche planifiée
(pattern déjà en place ailleurs dans ARIA pour les tâches périodiques,
cf. `heartbeat.py`) qui relit `journal.jsonl` + la base cognitive
périodiquement, hors du chemin critique d'une requête utilisateur, et
produit une version élaguée/fusionnée — sans toucher à `arbitrator.py`
(qui resterait le mécanisme de sélection à la volée, complémentaire, pas
remplacé).

**Upside concret.** Deux problèmes concrets que l'absence de consolidation
cause déjà mécaniquement avec le temps : (1) coût — `arbitrator.py`
recollecte et rearbitre depuis la même masse de journal brut à chaque
requête, qui ne fait que croître ; (2) qualité — sans fusion des doublons
ni retrait des entrées obsolètes, le risque de contradictions entre
anciennes et nouvelles entrées augmente avec le volume (partiellement
mitigé aujourd'hui par `knowledge/contradiction.py`, qui détecte les
contradictions au moment de la réponse, mais ne nettoie jamais la source).
Priorité moyenne : pas un incident aujourd'hui, mais un problème qui
s'aggrave mécaniquement avec le volume — plus rentable à traiter tôt que
tard vu que `journal.jsonl` ne fait que croître.

---

## Verdict de cette passe

Une mise à jour concrète d'une piste déjà actionnable (#26 → produit
précis identifié, pas juste un standard) et une vraie découverte de gap
après lecture complète du système existant (#28) — le genre de piste que
la discipline grep-first est censée produire : elle a d'abord failli être
un doublon (le système de réflexion/arbitrage existe bel et bien), et ne
s'est révélée être un vrai gap qu'après avoir vérifié précisément le
périmètre de ce qui existe déjà.

---

# Passe 10 (2026-07-12) — authenticité des comptes X (bot/sybil), veille dépeg stablecoin

## 29. Détection bot/sybil des comptes X qui interagissent avec ARIA — gap réel, mais blast radius déjà borné

**Vérifié avant de proposer.** Lu `gateway/x_engagement.py` et
`community_feedback.py` en détail. Ce qui existe déjà : `_feedback_has_spam_signal`
(filtre regex sur le **contenu** — mots-clés scam, URLs externes non
autorisées) et `trusted_feedback_handles()` (allowlist minuscule et codée
en dur, un seul handle par défaut : l'opérateur `GoldenFarFR`, pas un
mécanisme de scoring). **Aucun des deux ne mesure l'authenticité du compte
lui-même** (âge, ratio abonnés/abonnements, régularité de publication,
patterns de coordination) — c'est un axe différent du filtre anti-spam
textuel déjà en place, pas un doublon.

**Légitimité de la catégorie.** Écosystème réel en 2026 : BotBlock
(30+ signaux, score 0-10), Botometer (score de vraisemblance bot 0-5),
TwtData (modèle ML entraîné sur 1M de comptes vérifiés humains/bots +
analyse approfondie en partenariat avec X AI). Point de vigilance
documenté par les sources elles-mêmes : les bots 2026 utilisent du texte
généré par IA, des comptes mûris/achetés, des patterns de publication
proches de l'humain — les détecteurs naïfs (dont `BotometerLite`) sont
explicitement signalés comme dépassés par ces bots "IA-boostés".

**Seam.** `gateway/x_engagement.py` (mentions → like/reply, cycle limité à
15/cycle) et `community_feedback.py` (apprentissage mémoire optionnel via
`X_MENTIONS_LEARN_ENABLED`).

**Pourquoi la priorité reste modérée, pas urgente.** Le blast radius est
déjà borné par construction : `MAX_MENTIONS_PER_CYCLE = 15`,
`X_ALLOW_LIKES`/`X_MENTIONS_LEARN_ENABLED` gatés, et l'apprentissage en
mémoire (le cas le plus sensible — un sybil qui empoisonnerait la mémoire
d'ARIA) est déjà opt-in et non actif par défaut. Le risque réel aujourd'hui
est plus "quelques réponses gaspillées à des bots" que "empoisonnement de
mémoire" — mais si `X_MENTIONS_LEARN_ENABLED` devait un jour être activé
en production, ce gap deviendrait bien plus important à combler d'abord.

**Verdict : banqué, priorité basse-moyenne** — vrai gap, pas un doublon,
mais pas urgent tant que l'apprentissage mémoire depuis X reste désactivé
par défaut. À réévaluer si `X_MENTIONS_LEARN_ENABLED` passe à `true` en
production.

## 30. Monitoring dépeg stablecoin temps réel — watch seulement, exposition non vivante aujourd'hui

**Légitimité.** Réel et actif en 2026 : Webacy (monitoring continu,
dashboards + API dédiées), CoinGecko (REST/WebSocket/webhook sur écarts de
prix), seuils typiques d'alerte 0,5-1 % d'écart. Événement récent cité par
les sources : dépeg sUSD (Synthetix) fin janvier 2026, suivi en direct
multi-chaînes.

**Vérifié avant de proposer.** Grep sur `USDC`/`USDT`/`stablecoin` :
présent dans `services/x402.py` (USDC natif Base, actif par défaut du
paiement x402) et `skills/acp_offering_skill.py` (tarification des
offerings ACP en USDC). Mais `services/x402.py` est **explicitement gaté
OFF par défaut** ("sans rien activer de vivant") — l'exposition réelle à
un dépeg USDC n'existe pas encore en production, c'est une exposition
préparée, pas vécue (même statut que l'ancrage on-chain de #26/#28 : prêt,
pas armé).

**Verdict : watch, pas de piste actionnable maintenant.** Le jour où x402
est armé en production (paiements USDC réels entrants/sortants), un
monitoring de dépeg deviendrait pertinent avant d'accepter des paiements
sans y penser — mais construire ça avant l'activation de x402 serait
prématuré (rien à protéger encore). Noté pour réévaluation au moment de
l'activation de `services/x402.py`, pas avant.

---

## Verdict de cette passe

Deux pistes de nature différente : un vrai gap sur l'authenticité des
comptes X (#29), banqué mais pas urgent car déjà borné par les gardes-fous
existants (rate limit, flags opt-in) ; et un watch pur sur le dépeg
stablecoin (#30), dont la légitimité technique est réelle mais dont
l'exposition ARIA n'existe pas encore tant que `x402` reste gaté OFF —
les deux illustrent le même principe déjà observé plusieurs fois dans ce
scan : une techno peut être solide sans être urgente si le risque qu'elle
couvre n'est pas encore vivant dans le déploiement actuel.

---

# Passe 11 (2026-07-12) — protection MEV pour les swaps réels (sepolia_wallet.py)

## 31. Protection MEV (sandwich/front-running) — watch, risque déjà atténué par l'architecture Base elle-même

**Vérifié avant de proposer.** Grep sur "mev"/"flashbot"/"private mempool"/
"frontrun"/"sandwich" : aucun résultat réel dans le code — territoire
neuf. Pertinent car `onchain/sepolia_wallet.py` exécute déjà de vrais
swaps signés (Uniswap V3, wrap→approve→exactInputSingle) en répétition
avant un éventuel passage à des fonds réels sur Base mainnet (cf. passe 8
#26) — un swap réel non protégé est exposé aux sandwich attacks classiques
sur beaucoup de chaînes.

**Recherche faite — nuance importante trouvée.** Flashbots Protect (la
solution la plus connue) **ne supporte PAS Base** — seulement Ethereum
mainnet/Sepolia/Holesky, extension aux L2 annoncée mais pas encore
livrée. Mais surtout : **Base a une protection structurelle par défaut**,
indépendante de tout outil tiers — architecture à séquenceur unique
centralisé, premier-arrivé-premier-servi, **pas de mempool public** au
sens où Ethereum L1 en a un. Confirmé par plusieurs sources indépendantes :
"les attaques de sandwich classiques sont quasiment impossibles" sur ce
type de L2 aujourd'hui — le volume de sandwich sur L2 est nettement
inférieur à celui de l'arbitrage atomique, à l'inverse d'Ethereum L1 où le
sandwich domine largement.

**Caveat documenté, pas ignoré** : cette protection est une **propriété
de la centralisation actuelle** du séquenceur — la décentralisation
prévue des séquenceurs L2 (feuille de route générale du secteur, pas
spécifique à Base) réintroduirait de la dynamique MEV classique à terme.
Ce n'est donc pas un "jamais nécessaire", c'est un "pas nécessaire tant
que l'architecture actuelle tient".

**Solution existante si besoin un jour** : dRPC propose une protection
MEV dédiée sur Base (endpoint RPC différent, obfuscation du mempool) —
mais **payante** ("premium feature, à partir de 1$ par unité"), pas
gratuite comme Flashbots Protect sur Ethereum L1.

**Verdict : watch, pas de piste actionnable maintenant.** Le risque que
cette catégorie couvre est déjà largement atténué par l'architecture Base
elle-même aujourd'hui, et `sepolia_wallet.py` reste de toute façon en
rehearsal testnet (aucun fond réel en jeu). À réévaluer à deux moments
précis : (1) si un passage à des fonds réels sur Base mainnet est décidé
(cf. #26/#26bis), et (2) si Base annonce une feuille de route de
décentralisation de son séquenceur qui changerait la donne — pas avant.

---

## Verdict de cette passe

Une seule piste, tranchée nettement : la protection MEV est une vraie
catégorie technique, mais le risque qu'elle adresse est déjà largement
neutralisé par une propriété structurelle de Base (séquenceur unique, pas
de mempool public) — pas une lacune à combler maintenant, un point de
vigilance à réévaluer à deux déclencheurs précis et identifiés (passage
mainnet réel, décentralisation du séquenceur Base).

---

# Passe 12 (2026-07-12) — surveillance continue des positions ouvertes (sub-quotidien, faible coût)

## Correction préalable (importante) à la note psychologie trading du 2026-07-12

Cette passe a révélé une inexactitude dans
`2026-07-12-psychologie-trading-recherche-verifiee.md` (trait 3, vitesse
de révision de thèse) : j'avais écrit "aucun mécanisme ne re-vérifie une
thèse ouverte" — **c'est faux**. `heartbeat.py::vc_thesis_review`
(quotidien, `enabled=True`) appelle
`weekly_training.py::run_thesis_review()`, qui relit chaque position
ouverte (prix via OHLCV + activité GitHub), consigne un checkpoint et
remonte une alerte si la thèse "stagne/casse". Le vrai gap n'est donc
**pas** l'absence totale de mécanisme, mais sa **granularité** : une seule
passe par jour (1440 min), pas de signal entre deux passages. Correction
notée ici plutôt que réécrite dans l'ancienne note, pour garder la trace
de l'erreur et de sa correction (transparence).

## 32. Surveillance continue à faible coût des positions ouvertes

**Vérifié avant de proposer.** Grep confirmé par l'opérateur et par moi :
rien de "watch continu" n'existe — seul `vc_thesis_review` (quotidien) et
deux tâches horaires désactivées (`high_conviction_alerts`,
`market_sentiment_cycle`, toutes deux `enabled=False`). Vrai axe à
explorer, pas un doublon.

**Webhooks vs polling — tranché par la recherche, pas par principe.**
Consensus clair : le polling coûte cher en appels API/latence pour un
gain souvent nul (rien n'a changé la plupart du temps) ; le pattern
recommandé en 2026 est **hybride** — webhook comme chemin rapide,
polling périodique comme filet de sécurité en cas de panne du webhook
("the webhook gives you speed, the poll ensures nothing is permanently
lost"). Ce pattern hybride est directement transposable : garder
`vc_thesis_review` quotidien tel quel comme filet, ajouter un chemin
webhook en plus pour la réactivité — pas un remplacement.

**Candidat concret identifié** : **Alchemy Notify — Address Activity
webhook**. Supporte Base, suit les transferts ETH/ERC20/ERC721/ERC1155
sur des adresses suivies (ex. wallet du dev, adresse du pool LP d'une
position ouverte), **palier gratuit disponible** (retry avec backoff
jusqu'à 10 min en cas d'échec de livraison sur le palier gratuit/PAYG).
Limites exactes du palier gratuit (nombre d'adresses suivies, quota
mensuel) non confirmées dans cette recherche — **à vérifier avant tout
engagement**, ne pas supposer "gratuit = illimité".

**Écarté après vérification** : Blockscout, déjà utilisé par ARIA
(`services/blockscout.py::BASE_URL = "https://base.blockscout.com/api/v2"`),
propose des alertes/webhooks **mais uniquement en self-hosting** — ARIA
consomme l'instance publique hébergée, pas une instance auto-hébergée.
Cohérent avec le pattern déjà en place chez ARIA (GoPlus, DexScreener,
GeckoTerminal — toujours des API tierces hébergées, jamais d'infra
auto-hébergée dans `services/`) : ajouter un self-hosted Blockscout
casserait cette cohérence pour un seul besoin, alors qu'Alchemy Notify
s'y intègre sans rien changer à ce principe.

**Seam identifié, avec une limite honnête.** ARIA a déjà une capacité de
réception de webhooks **en production** — `gateway/telegram_bot.py`
tourne en mode "webhook" (`_webhook_mode`), avec anti-rejeu déjà en place
(`process_webhook_update`, dédoublonnage par `update_id`). C'est le bon
patron à réutiliser (serveur HTTP déjà exposé, discipline anti-rejeu déjà
écrite) plutôt qu'inventer un nouveau récepteur. **Limite** : le fichier
exact où ce serveur HTTP est déclaré/route les endpoints n'a pas été
localisé dans ce checkout (`packages/aria-core` ne contient aucun
`FastAPI(`/`@app.` — le point d'entrée réel du conteneur `aria-api` vit
probablement hors de ce package) — je note le pattern à suivre, pas le
fichier exact à éditer, honnêteté plutôt que supposition.

**Alertes différentielles — confirmé comme la bonne pratique, pas une
idée isolée.** Ne notifier que sur un changement de signal clé (ex. sortie
de fonds du wallet dev, retrait de liquidité, chute du `security_score`)
plutôt qu'à chaque transfert — directement compatible avec la structure
déjà utilisée par `run_thesis_review` (verdict "stagne/casse"), il
suffirait d'appeler la même logique de jugement en réaction à un webhook
au lieu d'attendre le prochain passage quotidien.

**Coût réel** : palier gratuit Alchemy à valider précisément avant tout
chiffrage définitif ; en tout état de cause bien moins cher qu'un service
commercial de surveillance de portefeuille dédié (non cherché ici, hors
scope — la question posée portait sur une alternative low-cost, pas sur
une comparaison de services premium).

**Verdict : piste actionnable, priorité moyenne.** Un vrai gap de
granularité (quotidien → quasi temps réel), une solution concrète et
cohérente avec l'architecture existante (webhook + polling de secours,
pattern anti-rejeu déjà en prod pour Telegram), un chiffrage encore à
affiner (palier gratuit Alchemy) avant tout engagement.

---

## Verdict de cette passe

Une correction transparente d'une inexactitude de la passe précédente
(le mécanisme de revue de thèse existe, sa granularité est le vrai sujet)
et une piste actionnable bien sourcée : Alchemy Notify (webhook, palier
gratuit, support Base) en complément — pas en remplacement — de
`vc_thesis_review`, en réutilisant le patron webhook+anti-rejeu déjà en
production pour Telegram.
