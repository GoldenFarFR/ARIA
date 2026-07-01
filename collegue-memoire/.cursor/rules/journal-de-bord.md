---
description: Journal de bord Aria — horodate chaque action fichier/repo
alwaysApply: true
---

# Journal de bord (Cursor)

Après **chaque action significative** (fichier créé/modifié/supprimé, commit, push) :

1. Append dans `%USERPROFILE%\projets\collegue-memoire\JOURNAL.md`
2. Format : `HHhMM — <verbe> <cible>` (heure locale)
3. Script : `projets\aria-skills\.grok\skills\journal-de-bord\scripts\append.ps1`

**Consulter le journal** : ouvrir `JOURNAL.md`, ou demander « montre le journal de bord ».

Distinct du § Journal de `COLLEGUE.md` (décisions métier).