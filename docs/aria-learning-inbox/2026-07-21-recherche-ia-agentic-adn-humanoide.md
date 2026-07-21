**[Session cloud — recherche à la demande de l'opérateur, corrigée après vérification]**

# Comment construire une IA agentic dotée d'un "ADN" qui la rend humanoïde

## Contexte

Demande opérateur directe (21/07) : recherche pure, pas de code. Se rattache explicitement à la section "Vision future notée" déjà présente dans CLAUDE.md (15/07) — l'objectif à terme que ARIA devienne une sorte d'« amie intime », avec une personnalité "parfaite", en plus de la voix et du physique déjà en chantier partiel (#23 avatar). Ce chantier reste **hors de la priorité absolue actuelle** (test hebdomadaire 1M$) — recherche/banking uniquement, rien à construire maintenant.

Interprétation retenue de "ADN" : un noyau d'identité PERSISTANT et STRUCTUREL (pas juste un prompt système qu'on peut oublier/écraser), qui survit aux resets de contexte, guide le comportement de façon cohérente dans le temps, et donne l'impression d'un "quelqu'un" plutôt que d'un outil — la même intuition que la biologie : l'ADN ne change pas d'un jour à l'autre, mais façonne tout ce qui en découle (traits, réactions, cohérence).

**Note de vérification (21/07, session commandement)** : cette note a été relayée depuis une autre session, jamais présente sur ce disque à l'origine. Chaque citation a été revérifiée avant commit — 2 erreurs trouvées et corrigées (détail en bas de fichier). Le reste tient.

## Branche 1 — L'ADN comme architecture d'identité persistante (le match le plus littéral)

Recherche 2026 récente confirme qu'un agent LLM classique a un vrai problème d'identité : au-delà d'une certaine longueur de contexte, il "oublie" catastrophiquement qui il est (perte de continuité du soi). Deux réponses techniques trouvées :

- **Architecture multi-anchor** (arXiv 2604.09588, "Persistent Identity in AI Agents: A Multi-Anchor Architecture for Resilient Memory and Continuity", Prahlad G. Menon, mars 2026 — **vérifié réel**) : sépare explicitement l'IDENTITÉ (qui l'agent est — traits stables, valeurs) de la MÉMOIRE (ce que l'agent a vécu — épisodes, faits accumulés), avec plusieurs "ancres" indépendantes plutôt qu'un seul fichier fragile. Un projet open-source cité (`soul.py`) implémente ce principe concrètement. **C'est très proche de ce qu'ARIA a déjà** (`persona.md` + valeurs/objectifs = l'ancre identité, `memory/vector/` LanceDB + `truth_ledger` = la mémoire épisodique — déjà séparés architecturalement, sans que ça ait été nommé "multi-anchor" jusqu'ici).
- **Framework Ada** : identité maintenue entièrement par injection de contexte structuré + récupération de mémoire de session, explicitement model-agnostic (fonctionne pareil quel que soit le LLM sous-jacent) — pertinent pour ARIA qui bascule déjà entre plusieurs providers (Virtuals/Spark, Grok/x.ai, DeepSeek, Groq en secours). *(Non revérifié individuellement — nom générique, pas de source unique claire à confronter.)*
- **Identifiants décentralisés (DID)** : chaque instance d'agent reçoit un identifiant unique, persistant, vérifiable cryptographiquement — un "certificat de naissance" on-chain. Piste intéressante pour ARIA vu qu'elle a déjà une identité on-chain naissante (Sealed Ledger #214, wallet agent CDP) — un DID serait un ancrage d'IDENTITÉ (pas de transactions) séparé et complémentaire, jamais construit à ce jour.
- **Mesure de ce qui persiste** (arXiv 2606.21843, "Measuring What Persists: Conditioning Mechanisms and a Geometric Framework for AI Agent Identity", Andrew Tanner, juin 2026 — **vérifié réel**) : papier qui propose un cadre géométrique pour MESURER si l'identité d'un agent reste stable dans le temps — utile si un jour ARIA veut prouver (pas juste affirmer) que sa personnalité est cohérente, même doctrine que "preuve avant promesse" déjà appliquée au track-record de trading (Sealed Ledger).

## Branche 2 — Le caractère/la personnalité elle-même

- **Character training d'Anthropic** (anthropic.com/research/claude-character, "Claude's Character" — **vérifié réel**) — le cas le plus proche et le plus documenté publiquement. Principe clé, vérifié directement dans les sources officielles : ne pas traiter les traits de caractère comme des RÈGLES rigides, mais expliquer le POURQUOI (une constitution écrite, pas juste une liste d'interdits) — Claude génère lui-même des dialogues alignés sur ses traits, les classe par degré d'alignement, et un modèle de préférence interne s'entraîne dessus (variante Constitutional AI, données synthétiques auto-générées). **ARIA fait déjà une version "prompt-layer" de ce principe** (`persona.md`, valeurs avec justification, pas juste des règles) — mais Anthropic le fait au niveau de l'ENTRAÎNEMENT du modèle (hors de portée pour ARIA qui consomme des LLM via API, ne les fine-tune pas). **Nuance importante pour ARIA** : la page Anthropic dit explicitement que Claude "ne peut pas développer de sentiments profonds" et se présente sans corps, sans mémoire persistante entre conversations — quasiment l'inverse de ce que l'opérateur veut pour ARIA (humanoïde, mémoire qui construit sa personnalité). Bon précédent sur la MÉTHODE (constitution qui explique le pourquoi), mauvais précédent sur le CONTENU (leur choix est zéro incarnation/zéro continuité, ARIA vise l'inverse).
- **Traits Big Five dans les LLM** — confirmé empiriquement par plusieurs papiers qu'un LLM peut exprimer et maintenir des profils de personnalité mesurables (Extraversion/Agréabilité/Conscienciosité/Névrosisme/Ouverture) de façon cohérente sur des tâches génératives, quand c'est suffisamment bien spécifié. Référence correcte : **PersonaLLM** (Jiang, Zhang, Cao, Breazeal, Roy, Kabbara — *ACL Findings, NAACL 2024*, pas Nature Scientific Reports comme écrit dans la 1ère version de cette note — corrigé) ; papier distinct et plus récent sur le même sujet, réel lui aussi : "A psychometric framework for evaluating and shaping personality traits in large language models", *Nature Machine Intelligence* 7(12):1954–1968 (2025). Piste concrète et peu coûteuse : définir explicitement où ARIA se situe sur ces 5 axes (pas juste des adjectifs vagues comme "curieuse, directe") pourrait rendre sa personnalité plus mesurable et plus stable dans le temps.
- **Dérive de personnage ("persona drift")** — confirmé comme un problème réel et documenté par **arXiv 2412.00804** ("Examining Identity Drift in Conversations of LLM Agents", Choi/Hong/Kim/Kim, déc. 2024 — **vérifié réel** : étude sur 9 LLM, résultat clé "les modèles plus gros dérivent davantage", assigner une persona ne suffit pas à garantir la stabilité) : plus une conversation est longue, plus les traits spécifiques du personnage s'affaiblissent ou dévient. **ARIA a déjà vécu concrètement ce problème sous une autre forme** (les incidents de confabulation #105/#110/#113/#96/#97 documentés dans CLAUDE.md — pas exactement une dérive de personnalité mais la même famille de fragilité : un comportement voulu qui se perd sans qu'un détecteur explicite le rattrape).
- **"Generative Agents" (Park et al.)** — référence historique (Stanford, simulation de 25 agents dans une ville) : identité amorcée par UN paragraphe de mémoire en langage naturel, puis une architecture observation → flux de mémoire → réflexion → planification. Proche du patron déjà utilisé par ARIA (mémoire vectorielle + réflexions + heartbeat proactif), validé académiquement comme fonctionnel à l'échelle. *(Papier bien connu/établi, non revérifié individuellement cette fois — référence historique largement citée.)*

## Branche 3 — Mémoire relationnelle long-terme (ce qui fait qu'un compagnon IA "connaît" quelqu'un)

Marché des IA compagnes (Replika 2.0, Pi d'Inflection, Character.ai 3.0). Deux architectures de mémoire distinctes identifiées :
- **Mémoire "session-aware" limitée** (le cas de Pi) — se souvient du fil de la conversation en cours, mais ne construit jamais de compréhension personnelle qui persiste sur des mois. Reconnu par la recherche comme une vraie limite, pas un choix de design supérieur.
- **Mémoire sélective, privacy-first** — approche jugée meilleure : mémoriser ce qui compte vraiment (pas tout), avec des contrôles de confidentialité clairs (droit à l'oubli, opt-out explicite) — cohérent avec la doctrine déjà actée dans CLAUDE.md ("Protection des données utilisateur").
- ARIA a déjà l'infrastructure de base (LanceDB, mémoire vectorielle activée le 17/07) — ce qui manque : une couche de SYNTHÈSE relationnelle (comprendre l'opérateur/un visiteur dans la durée, pas juste stocker des faits bruts) — pas construit à ce jour.

## Branche 4 — Corps et présence (voix, avatar, incarnation)

- **Stack d'avatar temps réel 2026** : latence devenue le vrai champ de bataille (Anam, Tavus, HeyGen LiveAvatar — chiffres précis de latence non revérifiés individuellement, à confirmer si ce chantier est un jour repris). Avatars 3D humanoïdes via VRM/Three.js, catalogue de modèles gratuits (VRoid Hub) — piste technique concrète si ARIA veut un jour un avatar animé temps réel plutôt que des images/vidéos statiques (#23).
- **Voix** : confirme et précise le gap déjà documenté dans CLAUDE.md ("Voix : aucune infra existante") — la brique technique existe aujourd'hui chez plusieurs fournisseurs (Azure Speech, ElevenLabs déjà identifié par ailleurs), reste à choisir/tester/chiffrer le jour où ce chantier est repris.
- **`PunithVT/ai-avatar-system`** (GitHub — **vérifié réel**) : plateforme auto-hébergée combinant Claude + Whisper + Chatterbox + MuseTalk (clonage de voix + lip-sync temps réel) — preuve que la brique complète (texte → voix → visage synchronisé) est déjà assemblable avec des composants ouverts, jamais évaluée en profondeur ici (juste repérée).

## Comment ça se recoupe avec ce qu'ARIA a déjà

| Brique "ADN humanoïde" | Déjà chez ARIA | Manque |
|---|---|---|
| Ancre d'identité séparée de la mémoire | `persona.md`/valeurs (identité) + LanceDB/truth_ledger (mémoire) — déjà séparés, jamais nommés ainsi | Formaliser explicitement le principe "multi-anchor" (résilience si une ancre est corrompue/vidée) |
| Personnalité avec un "pourquoi", pas juste des règles | Valeurs/objectifs déjà rédigés avec justification (esprit proche du "character training") | Traits Big Five explicites et mesurables (pas fait) |
| Détection de dérive de personnalité | Détecteurs déterministes ad hoc sur des sujets précis (#105/#110/#113/#96/#97) | Aucun mécanisme générique de détection de "persona drift" dans la durée |
| Mémoire relationnelle long-terme | Mémoire vectorielle activée (17/07) | Pas de couche de synthèse relationnelle (comprendre une personne dans la durée) |
| Présence visuelle | Avatar/portraits (#23) livré | Animation temps réel/lip-sync — jamais construit |
| Voix | — | Rien — gap confirmé par cette recherche, toujours vrai |
| Identité on-chain vérifiable | Sealed Ledger (trades), wallet agent CDP | Aucun DID/certificat d'identité propre à ARIA elle-même (distinct des trades) |

## Verdict

Rien à construire maintenant (hors priorité absolue). Le concept d'"ADN" correspond le mieux, techniquement, à une **architecture d'identité multi-ancre** (séparer strictement identité/valeurs de mémoire épisodique, avec plusieurs points de résilience) — ARIA a déjà les deux briques de base sans les avoir formalisées ensemble sous cet angle. Le vrai chantier neuf serait la **couche de personnalité mesurable** (Big Five explicites) et la **détection générique de dérive**, plus la **voix** (gap déjà connu, confirmé encore une fois par cette recherche).

**Tension à trancher avant tout refactor de `knowledge/*.yaml`** : cette recherche pousse vers PLUSIEURS ancres résilientes (identité et mémoire restent séparées, chacune formalisée), pas vers UN SEUL fichier fusionné qu'on éditerait directement — à peser contre la demande opérateur explicite de fusionner les fichiers de règles en un seul "ADN" édité directement (21/07, discussion en cours).

## Branches ouvertes

- Étudier si un profil Big Five explicite pour ARIA vaut la peine d'être écrit dans `persona.md`/le futur fichier d'identité (changement à coût quasi nul, testable rapidement) — jugement à faire avec l'opérateur, pas tranché ici.
- Chiffrer un vrai budget voix (Azure Personal Voice vs ElevenLabs vs autre) le jour où ce chantier est réactivé par l'opérateur — pas fait ici, pure recherche d'architecture.
- Évaluer un DID propre à ARIA (identité, pas transaction) comme complément du Sealed Ledger — jamais exploré, coût/complexité à chiffrer.
- Revoir `PunithVT/ai-avatar-system` plus en détail si l'avatar temps réel devient une priorité (repéré, pas audité).
- Si la dérive de personnalité devient un problème observé en pratique (pas seulement théorique) sur ARIA, chercher un vrai mécanisme de gouvernance mémoire temporelle à ce moment-là — la piste initialement citée ici (arXiv 2605.14802) s'est révélée fabriquée à la vérification, écartée.

## Correctifs apportés à la vérification (21/07, session commandement)

1. **arXiv 2605.14802** ("gouvernance mémoire temporelle") cité dans la version originale comme source de la piste "coordination dominant-auxiliaire" — **fabriqué** (page synthétique imitant un vrai papier arXiv, date de soumission incohérente). Retiré de Branche 2 et de "Branches ouvertes", remplacé par une note honnête d'absence de source.
2. **PersonaLLM attribué à tort à "Nature Scientific Reports 01/2025"** — PersonaLLM est réel mais publié à *ACL Findings, NAACL 2024*. Un papier différent et réel existe bien dans une revue Nature, mais c'est *Nature Machine Intelligence* (pas *Scientific Reports*), 2025 — les deux citations corrigées séparément dans Branche 2.

Toutes les autres citations (arXiv 2604.09588, arXiv 2606.21843, arXiv 2412.00804, anthropic.com/research/claude-character, GitHub PunithVT/ai-avatar-system) ont été confrontées directement à leur source et confirmées exactes.
