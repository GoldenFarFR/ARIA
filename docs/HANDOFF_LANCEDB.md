# HANDOFF — LanceDB (mémoire vectorielle sémantique)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.**

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

## Ce que c'est (rappel court, l'opérateur ne connaissait pas ce composant avant le 14/08)

Une base de données vectorielle embarquée (fichiers `.lance` sur disque, pas de serveur à part
— bibliothèque `lancedb`), utilisée comme mémoire sémantique d'ARIA : chaque souvenir (texte)
est stocké avec son "embedding" (384 nombres qui capturent son sens, modèle local
`BAAI/bge-small-en-v1.5` via `fastembed`, ONNX, **calculé sur le CPU du conteneur `aria-api`
lui-même, sur le VPS — zéro appel réseau, zéro token consommé**). La recherche se fait par
similarité sémantique (le sens de la question), pas par mot-clé exact. Table unique
`aria_cognitive_vectors`, colonnes `id`/`vector`/`text`/`entry_type`/`metadata_json`, 5 types
définis : `insight`/`lesson`/`reflection`/`decision`/`conviction_research`.

Distinct d'`aria-brain` (chapitres narratifs écrits par ARIA sur un repo GitHub privé séparé,
gate `ARIA_BRAIN_ENABLED` OFF) — les deux mécanismes ne se recouvrent pas.

## Index des sections de ce fichier
- Checklist bonnes pratiques (issue d'une recherche web sourcée, 14/08)
- Historique détaillé (entrées datées)

## Checklist bonnes pratiques (recherche web sourcée, 14/08, avant tout futur chantier LanceDB)

**Indexation** : pas d'index vectoriel (IVF_PQ/HNSW) avant plusieurs milliers de lignes — la
doc officielle recommande d'attendre l'approche des ~100K lignes (scan brut largement assez
rapide en dessous). Si un jour un index est créé : IVF_PQ plutôt que HNSW, car nos requêtes
combineront toujours vecteur + filtre `entry_type` (HNSW dégrade sous filtrage fréquent).
Source : docs.lancedb.com/indexing/vector-index, docs.lancedb.com/performance.

**Maintenance** : la version open-source (celle utilisée ici) n'a **aucune** maintenance
automatique en tâche de fond (compaction/nettoyage de versions — réservé à l'offre Enterprise
payante). Sans `optimize()`/`cleanup_old_versions()` planifié périodiquement, les fragments et
vieilles versions s'accumulent indéfiniment (237 transactions constatées pour 85 lignes actives
au 14/08). `compact_files()` seul ne libère pas d'espace disque tant que
`cleanup_old_versions()` n'a pas tourné — et cette dernière action est irréversible (perd le
"time travel" vers les anciennes versions). Un futur watchdog dédié (même famille que
`memory-watch`/`log-health-watch`) est le bon pattern, pas encore construit.
Source : docs.lancedb.com/indexing/reindexing, github.com/lance-format/lance/discussions/5036.

**Anti-pattern déjà pratiqué par ARIA** : écrire une entrée à la fois en boucle (ce que fait
`conviction_research.py` à chaque recherche) est un piège documenté ("small-file problem") —
chaque écriture crée un petit fragment, et chaque fragment est reparcouru à chaque écriture/
lecture suivante. Pas grave à l'échelle actuelle, à surveiller si le volume d'écriture augmente.
Source : docs.lancedb.com/performance.

**Schéma** : sortir les champs souvent filtrés (`contract`, `chain`, `source_id`, date) du blob
`metadata_json` vers de vraies colonnes typées — la doc LanceDB recommande un index scalaire
sur toute colonne utilisée en filtre, impossible sur un champ enterré dans du JSON.
Source : docs.lancedb.com/search/filtering, blog LanceDB "Lance JSON Support".

**TTL / rétention** : pas de TTL natif. Pattern recommandé : colonne timestamp + `delete()`
périodique par prédicat + `cleanup_old_versions()` pour libérer l'espace réellement (le
`delete()` seul est un soft-delete récupérable par time-travel jusqu'au nettoyage).
Source : Medium "You can now delete rows in Lance and LanceDB" (Will Jones).

**Écritures concurrentes** : limites documentées sur les écritures concurrentes locales — un
writer peut échouer après un nombre limité de tentatives (contrôle de concurrence optimiste),
et le multiprocessing via `fork` est explicitement déconseillé. Point de vigilance réel pour
ARIA : plusieurs sessions Claude Code ET le heartbeat de prod ont déjà écrit dans le même
dossier `/opt/aria-data/vector` à des moments différents (jamais confirmé simultanément, mais
jamais non plus explicitement garanti sérialisé). À vérifier avant d'ouvrir de nouvelles
sources d'écriture concurrentes.
Source : docs.lancedb.com/faq/faq-oss, issues GitHub lancedb/lancedb #213/#647/#1077.

**Architecture mémoire d'agent (pas juste LanceDB en tant que tel)** :
- Table unique filtrée par type = bon choix architectural à cette échelle (confirmé par
  Pinecone et Atlan) — ne pas fragmenter en tables séparées par type.
- Pattern Reflexion/ExpeL : séparer "règles obligatoires" (erreurs identifiées, injectées
  systématiquement — équivalent `lesson`) de "guidance optionnelle" (retrouvée par similarité
  seulement — équivalent `insight`/`conviction_research`). Au-delà de ~3 règles obligatoires
  par requête, le bruit dégrade la performance — rester sélectif, pas exhaustif.
  Source : sderosiaux.substack.com "Paper Cuts #2 — Agents that remember".
- Pattern bi-temporel (Zep/Graphiti) : pour une recherche VC qui se périme, invalider
  explicitement l'ancienne entrée plutôt que la laisser coexister sans distinction avec la
  nouvelle. Pas encore implémenté — `conviction_research.py` empile aujourd'hui sans jamais
  invalider. Source : arXiv 2501.13956.
- Seuils de déduplication sémantique concrets (Mem0) : fusion à 0,85 de similarité cosinus,
  dédup stricte à 0,9 — gain mesuré +22% précision / -60% stockage. Pas encore de mécanisme de
  dédup dans ARIA. Source : mem0.ai/blog/long-term-memory-ai-agents.

**Risque de sécurité distinct, non encore audité** : OWASP a formalisé en 2026 le "Memory and
Context Poisoning" (catégorie ASI06 de l'Agentic AI Top 10) comme **distinct** du prompt
injection classique déjà audité côté pipeline financier (#277, `docs/HANDOFF_SECURITE.md`) —
temporellement découplé (contenu empoisonné écrit à l'instant T, exploité potentiellement des
mois plus tard). Attaques réelles chiffrées : MINJA (>95% de succès d'injection via de simples
requêtes normales, aucun privilège requis), AgentPoison (≥80% de succès à <0,1% de taux
d'empoisonnement du corpus — statistiquement indétectable par le volume). Défenses
recommandées : traçabilité de provenance (qui/quand/source à chaque écriture), TTL bornant la
fenêtre d'exploitation, score de confiance composite (pas un accept/reject binaire), journal
d'audit sur chaque write/read/update/delete. **État réel ARIA au 14/08** : `contains_injection_marker()`
existe déjà (garde structurel), mais pas de traçabilité de provenance systématique ni de journal
d'audit dédié constatés dans `lancedb_store.py` — à construire avant d'ouvrir de nouvelles
sources d'écriture non maîtrisées (web, X, etc.).
Source : forcepoint.com/blog/x-labs/persistent-memory-poisoning-ai-agents, vectorize.io/articles/ai-memory-poisoning, mem0.ai/blog/ai-memory-security-best-practices.

## Historique détaillé (entrées datées)

[DEPLOYE] Sujet    : Aucun garde anti-injection sur l'écriture en mémoire vectorielle (memory poisoning) (déplacé ici 24/08, était mal classé dans `docs/HANDOFF_SECURITE.md` -- complémentaire à #166 ci-dessous : celui-ci détecte l'injection dans le CONTENU écrit, #166 trace la PROVENANCE/TTL de l'écriture elle-même)
Date : 2026.07.18 / Probleme : audit du chemin d'écriture LanceDB a trouvé deux trous - cybercentry_insight.py écrivait directement sans aucun triage (0 appelant en prod mais aucune garantie pour demain) ; le triage Groq existant (x_insight_relevance.py) vérifiait pertinence et véracité mais jamais l'injection de prompt spécifiquement.
Solution : lancedb_store.contains_injection_marker() - garde regex FR+EN posée à la couche de PERSISTANCE elle-même (store()), protège tout appelant présent ET futur sans les modifier individuellement ; x_insight_relevance.py gagne un 5e critère INJECTION dans le même prompt Groq (aucun nouvel appel LLM), prime sur PERTINENT/FAIT si détecté - lancedb_store.py / x_insight_relevance.py (cf. historique git 18/07, #206)

------------------------------------------------------------

[CODE] Sujet : 2e source d'écriture LanceDB -- calibration_ledger comme type "lesson" (#169)
Date : 2026.08.14 / Probleme : un seul appelant écrivait réellement dans LanceDB depuis sa
création (`conviction_research.py`), les 4 autres types du schéma (`insight`/`lesson`/
`reflection`/`decision`) n'avaient aucun vrai producteur. Cartographie complète menée avant tout
code (agent Explore, tous les appelants + tous les candidats plausibles du dépôt) : le meilleur
candidat n'était pas le plus gros en volume mais celui qui PERD réellement de la donnée
aujourd'hui -- `knowledge/calibration_ledger.py::record_calibration()` (corrections humaines
`/calibrate` sur des affirmations d'ARIA) écrit dans un ring buffer JSON plafonné à 300 entrées,
les plus anciennes étant écrasées silencieusement sans laisser de trace.
Solution : `skills/calibrate_skill.py::execute_calibrate` appelle désormais
`lancedb_store.store("lesson", ...)` juste après `record_calibration()`, best-effort (même
pattern que `conviction_research.py`, `store()` ne lève jamais -- catch interne déjà en place).
Texte indexé : `[verdict] affirmation (source: ...)`. Métadonnées : `topic="epistemic"` (aligné
avec le `topic` déjà utilisé pour `triaged_add_knowledge`/`append_memory` juste en dessous dans
le même appelant), `confidence` = la même probabilité (`p_true`, 0.95/0.05/0.5) déjà calculée
pour le calcul du score de Brier, `source_id=f"calibration-{cal['id']}"` pour un futur
`find_exact` par id. Aucun autre appelant du repo touché -- rayon d'impact minimal, choix
délibéré (le second candidat identifié, `thesis_journal.py`, reste ouvert pour une prochaine
itération si l'angle "cœur métier crypto" prime plutôt que "arrêter une perte de données réelle").
4 tests dans `test_calibrate_skill.py` (fichier neuf -- aucun test n'existait pour ce skill
avant ce chantier) : type d'entrée + contenu, confidence alignée sur le verdict, aucun appel
LanceDB si la commande est malformée (pas de `|`).
`packages/aria-core/src/aria_core/skills/calibrate_skill.py`,
`packages/aria-core/tests/test_calibrate_skill.py`.

------------------------------------------------------------

[CODE] Sujet : colonnes typées contract/chain (#170) + fix bug de troncature d'id
Date : 2026.08.14 / Probleme : `metadata_json` restait un blob opaque — tout filtre exact
(`conviction_research.py`'s cache par contrat+chaîne) passait par une recherche sémantique
vectorielle PUIS un filtre Python sur préfixe de `source_id` parsé depuis le blob — fragile
(la recherche vectorielle peut manquer/réordonner un résultat avant même que le filtre ne
s'applique, car elle classe par distance d'embedding, pas par exactitude). En creusant, bug
distinct trouvé (latent, jamais matérialisé) : `doc_id = str(source_id)[:36]` tronquait
naïvement un `source_id` long (ex. `conviction-research-{chain}-{contract}-{date}`, ~79
caractères pour une adresse EVM typique) AVANT d'atteindre son suffixe de date — deux
recherches du même contrat à des dates différentes auraient collisionné sur le même id tronqué,
et `merge_insert("id").when_matched_update_all()` aurait silencieusement écrasé l'historique au
lieu de créer une nouvelle entrée datée, contredisant l'intention documentée "append-only,
jamais un UPDATE". Vérifié empiriquement sur les 81 lignes réelles `conviction_research` avant
correctif : 0 collision (le bug n'avait encore jamais eu l'occasion de se déclencher — aucun
contrat encore re-recherché à une date différente).
Solution : (1) `contract`/`chain` promus en colonnes pyarrow typées (`_table_schema()`),
migration idempotente `_migrate_typed_columns()` (même doctrine que `_migrate_provenance_columns`
de #166) ; `store()` les peuple depuis `metadata` en plus du blob JSON existant (non-cassant,
l'ancien lecteur du blob continue de fonctionner). (2) `doc_id` désormais un hash SHA256 tronqué
à 36 caractères de l'intégralité du `source_id` (au lieu d'une troncature naïve du texte) —
préserve l'idempotence même-jour (même `source_id` → même hash) tout en distinguant correctement
deux dates différentes. (3) Nouvelle fonction `lancedb_store.find_exact(entry_type, *, contract,
chain, limit)` — un scan filtré pur (`table.search(query=None).where(...)`, confirmé contre la
lib réelle installée : "If None then the select/where/limit clauses are applied to filter the
table"), zéro similarité vectorielle, triée `written_at` décroissant. `conviction_research.py`'s
`_find_cached_research`/`get_research_history` migrés vers cette fonction (le filtrage par date
via le suffixe de `source_id` reste inchangé, toujours présent dans `metadata`). Validation des
valeurs (`_EXACT_MATCH_VALUE_RE`) contre l'injection dans la clause `where`, même doctrine que
`_ENTRY_TYPE_RE` déjà en place pour `search()`.
Tests : 8 nouveaux (`test_lancedb_store.py` — colonnes typées peuplées/vides, hash évite la
collision de troncature (reproduit le bug réel avant fix), hash stable pour idempotence,
`find_exact` filtre/trie/rejette une valeur invalide/vide si désactivé) + migration des ~11
mocks existants de `test_conviction_research.py` de `lancedb_store.search` vers `find_exact`
(même comportement testé, nouvelle surface d'appel). Suite complète relancée.
`packages/aria-core/src/aria_core/memory/vector/lancedb_client.py`,
`packages/aria-core/src/aria_core/memory/vector/lancedb_store.py`,
`packages/aria-core/src/aria_core/conviction_research.py`,
`packages/aria-core/tests/test_lancedb_store.py`,
`packages/aria-core/tests/test_conviction_research.py`.

------------------------------------------------------------

[DEPLOYE] Sujet : `get_table()` mort en prod depuis le déploiement #166 lui-même — schéma migré jamais atteint
Date : 2026.08.14 / Probleme : incident auto-infligé, découvert en creusant #170 (colonnes typées) —
`create_table(name, schema=_table_schema(), exist_ok=True)` ne "ignore pas silencieusement" un
schéma différent pour une table déjà existante, contrairement à l'hypothèse écrite dans l'entrée
#166 ci-dessous (jamais vérifiée empiriquement sur ce cas précis — seul `add_columns()` isolé
l'avait été). Dès que `_table_schema()` a gagné `written_at`/`written_by` (#166), CHAQUE appel de
`get_table()` en prod levait `Schema Error: Provided schema does not match existing table schema`
(la vraie table sur disque, 85 lignes, avait encore l'ancien schéma à 5 colonnes) — avalée par le
`except Exception` large, donc `is_available()` retournait `False` en permanence, sans erreur
visible au-delà d'un simple warning log. Conséquence réelle : LanceDB entièrement mort (lecture ET
écriture) depuis le déploiement du 14/08 vers 13h13 — y compris le cache `conviction_research` que
le fix Docker du même jour venait tout juste de réparer. Vérifié en conditions réelles serveur
(`bootstrap.configure()` rejoué, jamais un `docker exec` isolé) : `vector_store_status()` bloqué
sur `available=False` sur chaque process frais avant le fix.
Solution : `open_table()` ne compare jamais de schéma — tenté en premier pour une table déjà
existante, `create_table()` seulement en repli si elle n'existe vraiment pas encore. 1 nouveau test
de régression reproduisant exactement le scénario réel (table pré-existante à l'ancien schéma,
`get_table()` appelé avec le nouveau schéma à 7 colonnes) — `test_get_table_migrates_pre_existing_table_with_old_schema`.
Suite complète verte : 10207 passed. Déployé et confirmé en conditions réelles serveur le jour même
(commit `c352ba26`) : `available=True`, schéma migré (7 colonnes), `collection_count=85` — aucune
perte de données, la table était intacte sur disque tout du long, seul l'accès était cassé.
`packages/aria-core/src/aria_core/memory/vector/lancedb_client.py`,
`packages/aria-core/tests/test_lancedb_store.py`.

------------------------------------------------------------

[DEPLOYE] Sujet : retrait de `verify_and_remember_wallet` (#168) — code mort, redondant avec GoPlus AML
Date : 2026.08.14 / Probleme : `skills/cybercentry_insight.py` (premier vrai appelant historique de
LanceDB, #199, 17/07) n'était en fait appelé par AUCUN code de production — seuls ses propres
tests l'invoquaient. Question opérateur explicite avant tout retrait : Cybercentry apporte-t-il
une vraie valeur, vu que GoPlus est déjà utilisé ? Vérifié dans le code réel (pas supposé) :
`services/goplus.py::get_address_security` (Malicious Address API/AML, GRATUIT) couvre déjà le
même terrain (sanctions/comportement frauduleux d'une adresse) et est déjà câblé dans
`smart_money.py:2373` (`financed_by_malicious`). Test empirique demandé par l'opérateur avant
tranche finale : appel x402 réel sur Cybercentry wallet-verification (2 adresses, dont une
sanctionnée OFAC connue publiquement) — les 2 tentatives ont échoué ("toujours 402 après paiement
(règlement refusé)"). Vérifié qu'aucun argent n'a réellement été perdu : historique on-chain réel
du wallet (Blockscout, indépendant des logs internes) confirmé sans transfert USDC sortant
correspondant à l'heure du test — le protocole x402 a bien annulé le paiement suite au refus de
règlement. Prix écarté comme cause (vérifié dans la réponse 402 brute du serveur :
`maxAmountRequired=20000` = 0,02$, identique à ce qu'ARIA paie). Légitimité du fournisseur
elle-même vérifiée avant tranche (portail officiel `centry.cybercentry.co.uk`, sur demande
opérateur explicite) : service réel avec traction on-chain vérifiable (351 vérifications livrées,
55 wallets payants distincts, 72,56$ USDC réglés au total), certifications Cyber Essentials +
IASME Cyber Assurance Level One, affiliation OWASP — pas une arnaque, juste redondant et
actuellement peu fiable (son autre endpoint token-verification était déjà cassé depuis juillet).
Solution : `skills/cybercentry_insight.py` + `services/cybercentry.py` + leurs 2 fichiers de test
retirés (code mort, jamais appelé en prod, zéro changement de comportement). Référence orpheline
nettoyée dans `services/api_registry.py` (entrée du registre `/api` qui aurait affiché un
provider fantôme) et `skills/market_alerts.py` (commentaire). Suite complète verte : 10206
passed. `packages/aria-core/src/aria_core/services/api_registry.py`,
`skills/market_alerts.py`.

------------------------------------------------------------

[DEPLOYE] Sujet : watchdog de maintenance hebdomadaire (#167) — purge TTL + compaction LanceDB
Date : 2026.08.14 / Probleme : `purge_expired_entries()` (#166 ci-dessous) était prêt mais
jamais appelé automatiquement ; et LanceDB open-source (la version utilisée ici) n'a **aucune**
maintenance en tâche de fond — sans un appel périodique à `optimize()`, les fragments et
vieilles versions internes s'accumulent indéfiniment (déjà 237 transactions pour 85 lignes
actives constatées le 14/08).
Solution : nouvelle fonction `run_vector_maintenance()` (`lancedb_store.py`) — enchaîne
`purge_expired_entries()` puis `table.optimize(cleanup_older_than=timedelta(days=30))`,
fail-safe (un `optimize()` cassé ne fait jamais perdre le résultat du purge). Fenêtre de
rétention des versions internes délibérément généreuse (30 jours, pas le défaut LanceDB de 7)
pour les premières semaines de ce mécanisme tout neuf — à resserrer une fois quelques passages
hebdomadaires propres observés, même doctrine que les autres cadences d'observation accélérée
du projet (appliquée ici à la fenêtre de rétention, pas à la fréquence du cycle lui-même — le
cycle reste hebdomadaire, le volume actuel ne justifie pas plus fréquent). Câblé au heartbeat
(`vector_memory_maintenance_cycle`, `interval_minutes=10080`), gate dédié
`ARIA_VECTOR_MEMORY_MAINTENANCE_ENABLED`, OFF par défaut, standalone (pas de double-gate paper-
trading, ce mécanisme ne touche jamais le pipeline momentum). API `table.optimize()` vérifiée
directement dans le SDK installé avant d'écrire le code (`cleanup_older_than: timedelta = 7
jours par défaut`, `delete_unverified: bool`), jamais supposée. 6 nouveaux tests
(`test_lancedb_store.py` + nouveau `test_heartbeat_vector_memory_maintenance_cycle.py`). Suite
complète aria-core verte. `packages/aria-core/src/aria_core/memory/vector/lancedb_store.py`,
`heartbeat.py`, `packages/aria-core/tests/test_lancedb_store.py`,
`test_heartbeat_vector_memory_maintenance_cycle.py` (nouveau), `docs/HANDOFF_LANCEDB.md`
(ce fichier). Déployé en prod le 14/08 groupé avec #166 (bascule blue-green confirmée, commit
`279bdcad7e88`, health-check nginx réel OK, heartbeat actif). Gate
`ARIA_VECTOR_MEMORY_MAINTENANCE_ENABLED` toujours OFF par défaut — code en prod, cycle pas
encore armé, activation à décider séparément.

------------------------------------------------------------

[DEPLOYE] Sujet : défenses memory-poisoning (#166) — provenance structurelle, TTL câblé, audit d'écriture
Date : 2026.08.14 / Probleme : audit sécurité (mandat permanent CLAUDE.md sur les faiblesses
spécifiques IA + risque OWASP ASI06 documenté dans la checklist plus haut) a trouvé 3 trous
concrets, vérifiés dans le code : (1) `retention_days` déclaré dans `schema.yaml` pour chaque
entry_type mais jamais lu/appliqué par aucun code — un TTL fantôme ; (2) `source` obligatoire
pour seulement 2 des 5 types, aucun champ structurel qui/quand a écrit une entrée — la table
n'avait même pas de colonne timestamp ; (3) aucune trace persistante des tentatives d'écriture
rejetées (injection détectée, validation échouée) — seulement des `logger.warning` éphémères.
Solution : (1) deux colonnes structurelles `written_at`/`written_by` ajoutées au schéma —
`written_by` dérivé du VRAI call stack (`inspect.stack()[1]`), jamais un paramètre que
l'appelant pourrait falsifier ; migration idempotente de la table déjà peuplée (85 lignes
réelles) via `add_columns()`, validée empiriquement sur une copie avant tout code (LanceDB
`create_table(exist_ok=True)` ignore silencieusement un schéma différent pour une table
existante — seul `add_columns()` fonctionne, et un second appel sur une colonne déjà présente
lève `ValueError`, d'où le check `schema.names` avant migration). (2) `purge_expired_entries()`
exploite enfin `retention_days` — supprime par entry_type au-delà de sa fenêtre, ne touche
JAMAIS une entrée sans `written_at` (fail-safe : âge réel inconnu = jamais supprimée par
hypothèse), pas encore appelée automatiquement (le futur watchdog #167 fera l'appel
périodique). (3) nouveau module `memory/vector/audit.py` (même pattern `system_issues.py` :
`aiosqlite`, `aria_db_path()`, jamais bloquant) — `log_write_attempt()` trace CHAQUE tentative,
acceptée ou rejetée, avec motif ; le flag désactivé n'est jamais loggé (pas une "tentative
repoussée", juste le mécanisme éteint). Score de confiance composite délibérément PAS construit
maintenant (#169, toutes les sources actuelles sont déjà des pipelines internes fiables — n'a
de sens réel qu'au moment d'ouvrir une source moins fiable). Tests écrits en conditions réelles
(extra `[vector]` installé dans le venv de dev pour la première fois — les 15 tests
`test_lancedb_store.py` existants, tous SKIPPED jusqu'ici, tournent enfin réellement), 21
nouveaux tests (`test_lancedb_store.py` + nouveau `test_lancedb_audit.py`). Suite complète
aria-core verte : 10210 passed, 0 failed. `packages/aria-core/src/aria_core/memory/vector/
lancedb_client.py`, `lancedb_store.py`, `audit.py` (nouveau), `packages/aria-core/tests/
test_lancedb_store.py`, `test_lancedb_audit.py` (nouveau). Déployé en prod le 14/08 groupé avec
#167 (bascule blue-green confirmée, commit `279bdcad7e88`, health-check nginx réel OK).

------------------------------------------------------------

[DEPLOYE] Sujet : extra `[vector]` jamais installé dans le Dockerfile de prod — mécanisme mort depuis sa création
Date : 2026.08.14 / Probleme : `lancedb`/`fastembed`/`pyarrow` (paquet `[vector]` de
`aria-core`) n'ont **jamais** figuré dans `vanguard/Dockerfile` (confirmé par `git log -p` sur
tout l'historique du fichier), alors que 3 vrais appelants étaient déjà câblés dans le code
depuis le 17/07 (`conviction_research.py` cache la diligence VC, `memory/vector/ingest.py`
indexe la connaissance approuvée, `skills/cybercentry_insight.py` mémorise une vérification
wallet). Le mécanisme tournait donc à vide en prod depuis sa création — `is_available()`/
`is_vector_enabled()` degradent silencieusement en no-op (jamais une exception), donc rien n'a
jamais surfacé le trou. Découvert sur demande opérateur explicite ("vérifie l'état réel en
prod... je sais pas ce qu'il y a").
Solution : extra `[vector]` ajouté à l'installation pip du Dockerfile. Versions déjà pinnées
dans `requirements-lock.txt` (`lancedb==0.36.0`/`fastembed==0.8.0`/`pyarrow==25.0.0` — déjà
présentes malgré le paquet jamais installé, probablement issues d'une régénération de lock
antérieure), une seule dépendance transitive manquante ajoutée (`abnf==2.7.0`). Déployé en prod
le 14/08 (bascule blue-green confirmée, commit `db5b1bd9b1b8`) — **`ARIA_VECTOR_MEMORY=true`
ajouté manuellement au `.env` par l'opérateur, requis en plus du paquet lui-même** (le flag
`aria_vector_memory` est `False` par défaut dans `app/config.py`, jamais activé automatiquement
par la seule présence du paquet). Confirmé en conditions réelles serveur (pas un test isolé,
cf. piège méthodologique documenté dans `docs/HANDOFF_VPS_OPS.md` — un `docker exec` isolé ne
reflète jamais `aria_core.runtime` sans rejouer `bootstrap.configure()`) :
`vector_store_status()` retourne `enabled=True, available=True, installed=True,
collection_count=85` — les 85 entrées déjà présentes sur le disque sont bien retrouvées, le
montage de `/opt/aria-data/vector` fonctionne de bout en bout. `vanguard/Dockerfile`,
`vanguard/backend/requirements-lock.txt` (bd5dd9e3, db5b1bd9).

------------------------------------------------------------

[ETAT ACTUEL] Sujet : 85 entrées déjà présentes sur le disque VPS, écrites hors du conteneur de prod
Date : 2026.08.14 / Probleme : `/opt/aria-data/vector/aria_cognitive_vectors.lance` existait déjà
sur le VPS AVANT le fix ci-dessus (237 transactions internes, 12/07 → 13/08, 1,9 Mo). Comme
`[vector]` n'a jamais été dans le Dockerfile, le conteneur `aria-api` n'a **jamais** pu écrire
ça — ces données proviennent forcément de sessions Claude Code antérieures qui ont testé le
mécanisme directement sur le VPS (venv local avec `lancedb` installé à la main, pointant sur le
vrai `DATA_DIR` de prod), jamais du vrai cycle heartbeat automatisé.
Solution (constat, pas un fix) : audité en lecture seule (copie du dossier avant toute
inspection, jamais touché l'original). Contenu réel : 81 `conviction_research` (diligences VC
datées, ex. SOGNI/ZRO, juillet 2026), 3 `lesson` (doctrine opérateur : protocole hebdo 1M$,
diligence launchpad tokenisation), 1 `insight` (une vérification Cybercentry, 17/07).
Conséquence concrète : le cache de `conviction_research` (censé éviter de re-rechercher un
contrat déjà diligenté récemment) **n'a jamais fonctionné en continu en production** — chaque
cycle heartbeat réel a dû repartir de zéro jusqu'ici, gaspillant des appels LLM/web à chaque
fois qu'un contrat était déjà analysé récemment. Corrigé par le fix Docker ci-dessus, à
confirmer une fois déployé.

------------------------------------------------------------

[ETAT ACTUEL] Sujet : chaîne de lecture déjà branchée sur le cœur conversationnel d'ARIA
Date : 2026.08.14 / Probleme : avant de juger si LanceDB peut apporter une vraie valeur (demande
opérateur explicite : "pas un outil boulet"), vérifié si le côté LECTURE est réellement invoqué
dans un flux actif, pas juste câblé en théorie comme `cybercentry_insight.verify_and_remember_wallet`
(trouvé mort — jamais appelé nulle part dans le repo, ni heartbeat ni ailleurs, malgré son
commentaire "the first real caller").
Solution (constat) : `build_llm_context` (`memory/llm_context.py`, via `fetch_vector_recall`)
est appelé depuis `brain.py` — le module central des conversations Telegram/vitrine d'ARIA.
Donc une fois le paquet actif, chaque réponse d'ARIA peut potentiellement être enrichie par un
rappel sémantique pertinent, sans rien construire de neuf côté lecture. La vraie limite
aujourd'hui n'est pas le câblage (déjà là) mais le volume/la diversité du contenu (85 entrées,
81 quasi-identiques en forme) — aucune trace de résultats de trades réels, de leçons d'incidents
(le corpus `docs/HANDOFF_*.md` existe déjà, jamais indexé), ou d'erreurs de jugement identifiées
après coup. `verify_and_remember_wallet` reste orphelin, décision à prendre (brancher à un vrai
déclencheur ou retirer) — pas encore fait.

------------------------------------------------------------

[ETAT ACTUEL] Sujet : verrous anti-gaspillage LLM et cache LanceDB — pas de collision, déjà fail-safe
Date : 2026.08.14 / Probleme : question opérateur explicite — les garde-fous anti-gaspillage de
tokens LLM déjà en place (budgets, caches TTL existants) pourraient-ils bloquer LanceDB, ou
inversement le cache LanceDB pourrait-il devenir un nouveau blocus mal calibré ?
Solution (constat, vérifié dans le code, pas supposé) : `lancedb_store.py` ne contient aucun
check de budget/throttle/quota — cohérent avec le fait que l'embedding est calculé localement
(CPU du conteneur, `fastembed`/ONNX), zéro appel réseau, zéro token consommé. Les verrous
existants (budgets LLM Anthropic/OpenRouter, `ARIA_VC_CACHE_TTL=300s` sur le résultat complet
d'analyse VC) ne peuvent structurellement pas bloquer LanceDB. Dans l'autre sens : `search()`
a un `try/except Exception` qui retourne `[]` en cas d'échec (jamais une exception qui remonte
et bloque le pipeline appelant) — déjà fail-safe par construction. Les deux caches VC (5 min sur
le résultat complet, 7 jours sur la recherche de diligence via LanceDB) opèrent à des échelles
et sur des objets différents, pas de concurrence constatée.

## Prochaines étapes possibles (pas encore décidées/construites)
- #166+#167 déployés en prod le 14/08 (commit `279bdcad7e88`) — reste à décider si/quand armer
  `ARIA_VECTOR_MEMORY_MAINTENANCE_ENABLED`.
- Décider du sort de `verify_and_remember_wallet` (#168 — brancher à un vrai déclencheur ou
  retirer).
- Élargir les sources d'écriture au-delà du pipeline VC (#169 — résultats de trades réels,
  corpus HANDOFF) — maintenant débloqué côté sécurité (provenance + audit + TTL en place),
  mais le score de confiance composite reste à construire AU MOMENT d'ouvrir une source moins
  fiable que les pipelines internes actuels.
- Colonnes typées (#170 — `contract`/`chain`/`source_id`/date) à la place du blob
  `metadata_json`.
- Piste de recherche banquée (14/08, hors ce chantier) : `xai-org/x-algorithm` (repo réel,
  vérifié — X open-source de son feed "For You"), en particulier le composant `user-cred-v2`
  (scoring de crédibilité de compte) pourrait informer `x_insight_relevance.py`/`smart_money.py`
  côté détection anti-manipulation. Garde-fou explicite opérateur : rester strictement défensif
  (jamais growth hacking), prudence sur ce qui devient public si ça débouche sur du code — cf.
  mémoire `feedback_dont_close_third_party_data_access`.
