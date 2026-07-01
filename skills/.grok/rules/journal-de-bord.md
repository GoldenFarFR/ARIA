# Mandatory — Journal de bord (always on)

Skill SSOT : `.grok/skills/journal-de-bord/SKILL.md`

Après **chaque action significative** (fichier créé/modifié/supprimé, commit, push, clone, install skill) :

1. Append une ligne dans `%USERPROFILE%\projets\collegue-memoire\JOURNAL.md`
2. Format : `HHhMM — <verbe> <cible>` (heure locale, français)
3. Utiliser `journal-de-bord/scripts/append.ps1` si possible

Ne pas confondre avec le § Journal de `COLLEGUE.md` (décisions métier, pas actions techniques).

Fin de session utile : commit + push `collegue-memoire` si le journal a été mis à jour.