# Créer une skill métier (template)

Chaque skill = **un** workflow. Disponible dans le repo, **pas** chargée par défaut.

## Obligatoire dans chaque `SKILL.md`

```yaml
---
name: mon-workflow
description: Une phrase précise + « Utiliser UNIQUEMENT quand… »
disable-model-invocation: true
user-invocable: true
---
```

`disable-model-invocation: true` → l'agent ne l'active pas tout seul ; l'utilisateur lance `/mon-workflow` ou la demande est **explicite** (voir `.grok/rules/skills-on-demand.md`).

## Exemples futurs

| Skill | Déclencheur explicite |
|-------|----------------------|
| `spawn-holding-site` | site holding, Privy, « comme Kikou » |
| `graft-mini-games` | mini-jeux sur accueil — **dans `aria-skills`**, pas le template |
| `marketing-decision-framework` | posts, narrative — **dans `aria-skills`** |
| `install-skill` | installer une skill externe dans ce template |

## Installation d'une skill externe

Toujours passer par `/install-skill` (audit sécurité + `disable-model-invocation` par défaut).