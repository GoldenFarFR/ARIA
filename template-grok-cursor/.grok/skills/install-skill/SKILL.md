---
name: install-skill
description: >
  Procédure d'audit, d'installation et de validation d'un skill Grok dans le
  template template-grok-cursor. Utiliser quand l'utilisateur envoie un lien ou
  un skill à intégrer au template, demande d'installer une skill, ou lance
  /install-skill. Couvre sécurité, qualité, doublons, tests et documentation.
disable-model-invocation: true
user-invocable: true
metadata:
  short-description: "Auditer et installer un skill dans le template Grok"
  author: template-grok-cursor
compatibility: Nécessite grok inspect, git, accès réseau pour sources externes
---

# Installation d'un skill dans le template

Procédure obligatoire avant d'ajouter un skill à `template-grok-cursor/.grok/skills/`.
Objectif : chaque projet créé depuis le template hérite de skills sûrs, utiles et non redondants.

## Entrées requises

Demander si manquant :

1. **Source** — URL GitHub, archive, ou chemin local
2. **Nom proposé** — identifiant du skill (kebab-case)
3. **Justification** — quel problème récurrent ce skill résout
4. **Scope** — toujours **projet/template** ici (pas `~/.grok/skills/` sauf exception documentée)

## Phase 1 — Collecte et inventaire

1. Télécharger ou lire la source complète (SKILL.md + `scripts/`, `references/`, dépendances).
2. Lister l'inventaire actuel :
   ```bash
   grok inspect
   ```
3. Lister les skills déjà dans le template :
   ```bash
   ls .grok/skills/
   ```
4. Noter la licence et l'auteur — refuser si licence incompatible ou source non vérifiable sans alternative.

## Phase 2 — Détection de doublons

Comparer le candidat avec :

| Zone | Emplacement |
|------|-------------|
| Template | `.grok/skills/*` |
| Skills globaux | `~/.grok/skills/*` |
| Skills bundled Grok | design, implement, review, check-work, create-skill, etc. |

**Doublon** = même workflow ou trigger (`description` / `when-to-use`) couvrant >70 % du cas d'usage.

Décision :

- **Rejeter** — doublon strict (ex. second skill « code review »)
- **Fusionner** — enrichir le skill existant plutôt qu'ajouter
- **Garder les deux** — périmètres clairement distincts ; documenter la différence dans le README du template

## Phase 3 — Audit sécurité (bloquant)

Ne pas installer si un critère bloquant échoue.

### SKILL.md

- [ ] Pas d'instruction à exfiltrer secrets (`.env`, clés API, `auth.json`)
- [ ] Pas de `curl | bash` ou téléchargement exécutable non vérifié
- [ ] Pas de suppression massive (`rm -rf`, format disque) sans garde-fou explicite
- [ ] Pas d'accès réseau vers domaines suspects ou non justifiés

### Scripts (`scripts/`)

- [ ] Lire chaque script avant intégration
- [ ] Pas d'obfuscation, d'encodage base64 suspect, ni d'eval dynamique
- [ ] Dépendances externes documentées et justifiées
- [ ] Chemins relatifs au projet — pas d'écriture hors repo sans raison

### Permissions implicites

- [ ] Le skill n'exige pas `--yolo` systématique pour fonctionner
- [ ] Les commandes destructives demandent confirmation utilisateur dans le corps du skill

Si doute : **rejeter** ou **durcir** le skill (ajouter garde-fous) avant merge.

## Phase 4 — Audit qualité et efficacité

### Format (obligatoire)

- [ ] Frontmatter YAML valide avec `name` et `description`
- [ ] `name` en kebab-case, 2–64 caractères, unique dans le template
- [ ] `description` précise : quoi + quand + mots-clés de déclenchement
- [ ] Corps : étapes numérotées, une action par étape
- [ ] Outils Grok nommés explicitement quand pertinent

### Efficacité

- [ ] Un skill = un workflow (pas de fourre-tout)
- [ ] Instructions actionnables — pas de prose vague
- [ ] Taille raisonnable (< ~500 lignes ; scinder si plus)
- [ ] `disable-model-invocation: true` sur toute skill template (sauf exception documentée)
- [ ] `description` précise avec « Utiliser UNIQUEMENT quand… » — pas d'invocation auto large

### Adaptation template

- [ ] Instructions compatibles « répondre en français » (AGENTS.md du template)
- [ ] Pas de référence à un repo ou une stack spécifique non présente dans le template
- [ ] Si le skill vient de l'anglais : traduire `description` et instructions clés en français

## Phase 5 — Installation

1. Créer le dossier :
   ```bash
   mkdir -p .grok/skills/<nom>/
   ```
2. Copier SKILL.md et fichiers annexes (scripts, references).
3. Normaliser le `name` du frontmatter = nom du dossier.
4. Ajouter `references/SOURCE.md` si skill tiers :
   ```markdown
   # Provenance
   - URL : ...
   - Auteur : ...
   - Licence : ...
   - Version / commit : ...
   - Date d'intégration : ...
   ```
5. Mettre à jour `README.md` du template — section **Skills inclus** (nom, rôle, date).
6. Ne pas modifier `~/.grok/skills/` sauf demande explicite de scope global.

## Phase 6 — Validation fonctionnelle

1. Vérifier la détection :
   ```bash
   grok inspect
   ```
   Le skill doit apparaître avec source `project` (ou `repo`).

2. Tester l'invocation :
   ```
   /<nom-du-skill>
   ```
   sur une tâche minimale représentative.

3. Simuler un nouveau projet (recommandé) :
   - créer un repo test depuis le template, ou copier `.grok/skills/` dans un dossier vide ;
   - confirmer que `grok inspect` liste le skill et que `/nom` répond correctement.

4. Vérifier qu'aucune règle globale conflictuelle ne masque le skill (même `name` en `~/.grok/skills/`).

## Phase 7 — Clôture et versionnement

1. Rédiger un **rapport court** pour l'utilisateur :

   | Critère | Résultat |
   |---------|----------|
   | Sécurité | OK / Corrigé / Rejeté |
   | Doublons | Aucun / Fusion / Coexistence justifiée |
   | Qualité | OK / Améliorations appliquées |
   | Test | OK / Échec |

2. Commit Git :
   ```
   feat(skills): ajouter <nom> — <une ligne de justification>
   ```

3. Si rejeté : expliquer pourquoi et proposer alternative (skill existant, création sur mesure via `/create-skill`).

## Critères de rejet rapide

Rejeter sans installer si :

- source opaque ou non reproductible
- doublon non justifié d'un skill bundled ou template existant
- scripts non auditables
- description trop vague pour déclenchement fiable
- skill nécessite des secrets ou infra absents du template sans doc d'installation

## Améliorations optionnelles (si le skill est accepté)

- Durcir les garde-fous sécurité dans le corps du skill
- Ajouter `metadata.short-description` en français
- Scinder en sous-skills si le workflow dépasse 2 responsabilités
- Ajouter un exemple d'usage dans `references/example.md`