# Mandatory — Journal de bord (always on)

Skill SSOT : `skills/.grok/skills/journal-de-bord/SKILL.md`

Après **chaque action significative** (fichier créé/modifié/supprimé, commit, push) :

1. Append une ligne dans `%ARIA_REPO_ROOT%\collegue-memoire\JOURNAL.md`
2. Format : `HHhMM — <verbe> <cible>` (heure locale, français)
3. Script : `skills/.grok/skills/journal-de-bord/scripts/append.ps1`

Distinct du § Journal de `COLLEGUE.md` (décisions métier).

Fin de session utile : commit + push `GoldenFarFR/ARIA`.