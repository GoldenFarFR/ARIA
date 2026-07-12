[VPS Research]

# Design "prêt à construire" — `memory/consolidation.py` (approfondissement de #28)

Suite de la passe 9 (#28, gap "sleep-time compute" identifié). Objectif de
cette note : rendre la piste directement dispatchable à un ouvrier
(Principal/Secondaire), pas juste une direction de recherche. Méthode :
comparaison de 3 systèmes existants (mem0, Zep, Letta/MemGPT) + analyse
réelle de la mécanique derrière la skill `consolidate-memory` disponible
dans cette session + inspection en lecture seule des données réelles ARIA
(`/opt/aria-data`) pour ancrer le design dans les volumes réels, pas des
suppositions.

---

## 1. Comment 4 systèmes existants gèrent la consolidation hors-ligne

### mem0 — décision LLM au niveau de chaque fait, ADD/UPDATE/NOOP/DELETE
Pour chaque candidat extrait, un appel LLM décide : ADD (nouveau), UPDATE
(fusionner avec un existant proche sémantiquement), NOOP (redondant,
ignorer), DELETE (obsolète). Optimisation coût 2026 documentée : passer en
extraction ADD-only en un seul appel et différer la résolution de conflit
à la lecture (ou en asynchrone) réduit les appels LLM en écriture de
60-70 % sans dégrader la qualité perçue.

### Zep/Graphiti — jamais de perte, seulement des intervalles de validité
Graph temporel bi-temporel (horodatage de l'événement + horodatage
d'ingestion). Chaque relation porte un intervalle de validité explicite.
**Rien n'est jamais supprimé** — une info remplacée voit sa période de
validité close, mais reste interrogeable pour l'historique. C'est
exactement le même principe que le `supersedes` déjà utilisé dans le
truth-ledger d'ARIA (vérifié : `truth-ledger/2026-07-11/155613-*.md` a un
champ `supersedes: [ff80b882-...]`, jamais de suppression physique).

### Letta/MemGPT — agent "sleep-time", tours dédiés hors conversation
Un agent dédié obtient des tours autonomes sans input utilisateur pour
réorganiser : consolider le stockage archival, réécrire un bloc mémoire
devenu confus, résumer une conversation récente en une note stable. La
gestion mémoire est asynchrone, séparée du chemin de réponse utilisateur
(contrairement à MemGPT original qui mélangeait tout dans un seul agent,
ralentissant les réponses).

### La skill `consolidate-memory` (lue en entier, mécanique réelle)
Pas de cadence fixe intégrée à la skill elle-même (cadence = externe,
déclenchement manuel ou planifié). Algorithme en 3 phases :
1. **Inventaire** — lister le répertoire mémoire, lire l'index, repérer les
   fichiers qui se recoupent, ceux qui semblent obsolètes, ceux qui sont
   trop maigres.
2. **Consolider** — séparer le *durable* (préférences, relations,
   workflows récurrents — à garder et affiner) du *daté* (projets
   spécifiques, deadlines, tâches ponctuelles — à retirer si la date est
   passée, ou à replier en un takeaway durable). Fusionner les recoupements
   en gardant le fichier le plus riche. Convertir les dates relatives en
   dates absolues. **Retirer ce qui est facile à retrouver ailleurs**,
   garder ce qui est difficile à re-dériver (préférences énoncées,
   contexte d'une décision).
3. **Ranger l'index** — le fichier index reste sous une limite de taille,
   une ligne par entrée.

**Comment elle évite de perdre une info correcte** : elle ne supprime
jamais le répertoire source pendant l'opération (les fichiers modifiés
restent dans le même dépôt/répertoire, donc récupérables via l'historique
git si le répertoire est versionné) et la règle "garder ce qui est dur à
re-dériver" fait qu'elle biaise systématiquement vers la rétention en cas
de doute plutôt que vers la suppression.

---

## 2. État réel des données ARIA aujourd'hui (vérifié, pas supposé)

Inspection en lecture seule sur `/opt/aria-data` (aucune écriture) :

- **`cognitive_knowledge`** (SQLite, `aria.db`) : **18 lignes, toutes
  `approved=1`**. Toutes créées via `upsert_knowledge_by_topic` (un topic =
  une ligne, idempotent, confidence=1.0) — c'est déjà une forme de
  consolidation par construction. **Aucune accumulation non approuvée
  aujourd'hui** — la table ne pose pas encore de problème de volume.
- **`memory_dir()`** (fichiers markdown `{catégorie}_{date}.md`) : **89
  fichiers, 680 Ko au total**, span du 2026-07-05 au 2026-07-12 (une
  semaine). ~15-20 catégories actives (avatar, chat, comms, entrepreneur,
  epistemic, exam, github, heartbeat, launchpad, market_sentiment,
  proactive, repertoire, vc, wallet, capability...), 2 à 8 fichiers datés
  par catégorie. Entrées très denses en information mais courtes
  (~450 octets pour 7 entrées dans l'échantillon inspecté).

**Correction par rapport à la passe 9** : j'avais mentionné "`journal.jsonl`"
par erreur — vérifié dans `memory/_legacy_journal.py` : le format réel est
un fichier markdown par catégorie/jour (`{category}_{date}.md`), pas un
jsonl unique. La cible réelle du gap est donc `memory_dir()`, pas un fichier
jsonl qui n'existe pas sous ce nom.

**Conséquence sur la priorité** : le volume actuel est petit (680 Ko/semaine,
~90 fichiers) — rien d'urgent en soi aujourd'hui. Mais la croissance est
mécanique et linéaire dans le temps sans plafond ni fusion — le bon moment
pour construire le mécanisme est avant que le volume devienne un problème
de coût/qualité, pas après.

---

## 3. Design proposé : `memory/consolidation.py`

### Garde-fou non négociable (à coder en premier, avant toute logique de fusion)

**Périmètre autorisé — UNIQUEMENT :**
- `memory_dir()` (fichiers markdown `{category}_{date}.md`)
- `cognitive_knowledge` **WHERE `approved = 0`** (aujourd'hui vide, mais le
  périmètre doit être verrouillé dès le départ pour rester correct quand
  cette table commencera à accumuler des entrées non approuvées)

**Interdit, verrouillé en dur (fail-closed si le code tente d'y toucher) :**
- Tout le `truth-ledger` (`truth_ledger_dir()`) — aucune entrée, verified
  ou non, ne doit jamais être lue en écriture par ce module. Le
  truth-ledger a déjà son propre mécanisme de succession
  (`status: verified` + `supersedes:`) géré ailleurs (`canonical_facts.yaml`
  → sync) — la consolidation mémoire ne doit jamais l'écraser ni le dupliquer.
- `cognitive_knowledge` **WHERE `approved = 1`** — déjà consolidé par
  construction via `upsert_knowledge_by_topic` (doctrine, confidence=1.0).
- `memory/values.py`, `memory/goals.py` — mémoire d'identité déjà curatée
  à la main, hors périmètre par nature (même logique que "durable, ne
  jamais toucher automatiquement" dans la skill `consolidate-memory`).

### Jamais de suppression physique — archive-then-rewrite

Inspiré de Zep (rien n'est jamais perdu, seulement des intervalles de
validité) et du principe dôme déjà en place ailleurs dans ARIA (dégradation
gracieuse, pas d'opération destructrice silencieuse) : avant toute
réécriture, écrire un instantané brut des entrées touchées dans
`memory_dir() / "archive" / f"consolidated_{date}.jsonl"` (une ligne par
entrée pré-consolidation, format `{category, date, content, source_file}`).
Cet instantané n'est **jamais lui-même consolidé ni élagué** — c'est le
filet de récupération : en cas d'erreur de fusion, l'opérateur peut
toujours retrouver l'entrée brute originale. Seuls les fichiers-sources
`memory_dir()/{category}_{date}.md` (hors archive) sont réécrits/élagués.

### Algorithme (par catégorie, pas par entrée — coût maîtrisé)

1. Pour chaque catégorie active (ex. `vc`, `heartbeat`, `epistemic`...),
   lire le fichier consolidé existant `memory_dir()/consolidated/{category}.md`
   (vide au premier passage) + les fichiers datés non encore consolidés
   depuis la dernière exécution.
2. Un seul appel LLM par catégorie (pas un appel par entrée individuelle —
   c'est le principal levier de coût, aligné sur le principe "single-pass"
   de mem0) avec un prompt structuré autour des 3 règles de la skill
   `consolidate-memory` : séparer durable/daté, fusionner les recoupements
   en gardant le contenu le plus riche, retirer ce qui est trivialement
   re-dérivable (ex. un statut heartbeat répété identique 10 fois → une
   ligne "stable depuis le X"), **jamais reformuler au point de perdre un
   fait précis** (nombre, adresse, decision — consigne explicite dans le
   prompt, miroir de la règle "clichés IA interdits" déjà câblée ailleurs
   dans ARIA, même famille de discipline : instruction, pas post-traitement
   destructif).
3. Écrire le résultat dans `consolidated/{category}.md`, archiver les
   entrées brutes traitées (étape précédente), marquer les fichiers datés
   source comme consolidés (ex. suffixe ou registre séparé, pas suppression).

### Cadence

Réutilise le registre existant `heartbeat.py` (`HeartbeatTask`), pas un
nouveau scheduler : `HeartbeatTask(id="memory_consolidation", interval_minutes=1440, enabled=False)`
— gated OFF par défaut, même discipline que les autres tâches sensibles du
fichier (ex. `zhc_watch`). Cadence quotidienne cohérente avec les données
réelles (fichiers déjà nommés par jour). Ajout d'un seuil de volume en
complément (ex. ne lance la consolidation d'une catégorie que si ≥3
nouvelles entrées depuis le dernier passage) pour éviter des appels LLM
quotidiens sur des catégories inactives — l'équivalent du "idle turns" de
Letta, adapté à un déclenchement par volume plutôt que par tour de
conversation vide.

### Coût LLM estimé (basé sur les volumes réels observés, pas une supposition)

~15-20 catégories actives, entrées très courtes (~450 octets / 7 entrées
≈ 65 octets/entrée ≈ ~20 tokens/entrée). Un appel de consolidation par
catégorie active un jour donné : contexte d'entrée dominé par le fichier
consolidé existant + quelques nouvelles entrées, de l'ordre de
**500 à 1500 tokens en entrée, 150 à 400 tokens en sortie** par catégorie.
Avec le seuil de volume (pas toutes les catégories actives chaque jour),
estimation réaliste : **10-15 appels/jour**, soit environ
**8 000-20 000 tokens d'entrée et 2 000-6 000 tokens de sortie par jour**
au total. **Route explicitement en `depth="brief"`** (pas `"develop"` —
rappel du constat déjà fait sur `llm_usage.py` : les appels `depth="develop"`
consomment 72,5 % des tokens d'entrée pour 28 % des appels ; une tâche de
housekeeping routinière ne justifie jamais ce tier). À ce volume et ce
tier, le coût quotidien reste marginal comparé aux dépenses `develop`
déjà en place — mesurable directement après déploiement via les champs
existants de `llm_usage.py` (`kind`, `depth`, `total_tokens`), pas besoin
de nouvelle instrumentation.

### Ce qui n'est PAS dans ce design (délibérément, pour rester dispatchable)

Pas de fusion sémantique inter-catégories (ex. relier une entrée `vc` à une
entrée `epistemic` sur le même token) — hors scope, complexité inutile
pour un premier jet. Pas de graphe temporel façon Zep — ARIA a déjà son
mécanisme de succession dans le truth-ledger pour les faits qui comptent
vraiment ; dupliquer cette mécanique dans `memory_dir()` serait une
architecture en plus à maintenir pour un gain marginal sur des logs
opérationnels courts-vécus.

---

## Verdict

Piste #28 passée de "gap identifié" à design concret : garde-fou explicite
et verrouillable en code dès la première ligne (périmètre `memory_dir()` +
`cognitive_knowledge WHERE approved=0` uniquement, jamais le truth-ledger
ni les entrées approuvées), mécanique archive-then-rewrite qui garantit
qu'aucune info ne peut être perdue silencieusement, cadence réutilisant le
registre `heartbeat.py` existant (gated OFF par défaut), coût estimé à
partir des volumes réels observés (pas une supposition) et calé sur le
tier `depth="brief"` déjà existant. Prêt à dispatcher.
