---
name: journal-de-bord
description: >
  Journal de bord des actions Aria (IDE) : horodatage + fichier/repo modifié.
  Toujours actif — après chaque action significative, append une ligne au journal.
  Triggers : journal, journal de bord, log activité, historique actions, /journal-de-bord.
metadata:
  short-description: "Horodate file/repo actions in JOURNAL.md"
  always-on: true
---

# Journal de bord Aria

Trace **ce que l'assistant a fait** pendant la session — pas les décisions métier (voir `COLLEGUE.md` § Journal).

## Où écrire

| Contexte | Fichier |
|----------|---------|
| **Toujours** (multi-PC) | `%USERPROFILE%\projets\collegue-memoire\JOURNAL.md` |
| **Repo Git ouvert** (optionnel, en plus) | `<racine-repo>/JOURNAL.md` |

Si `collegue-memoire` est absent : rappeler le clone une fois, puis continuer sans journal global.

## Quand logger

Après **chaque** action significative (pas besoin d'attendre la fin de session) :

- création / modification / suppression de fichier
- commit ou push git
- clone ou init repo
- installation skill / règle
- commande shell ayant changé l'état du disque

Ne pas logger : lectures seules, grep, réponses purement conversationnelles.

## Format d'une ligne

```
HHhMM — <verbe> <cible courte>
```

Exemples :

```
14h32 — Ajout du fichier projets/ddc/ddc_calculateur.py
14h35 — Modification de projets/collegue-memoire/COLLEGUE.md
14h40 — Commit + push repo collegue-memoire (journal de bord)
15h02 — Suppression de Downloads/ancien_script.py
```

Verbes : **Ajout**, **Modification**, **Suppression**, **Commit**, **Push**, **Clone**, **Installation**.

## Structure du fichier

Un fichier unique, sections par jour :

```markdown
# Journal de bord Aria

## 2026-06-19

14h32 — Ajout du fichier ...
14h35 — Modification de ...
```

- Nouveau jour → ajouter `## YYYY-MM-DD` puis les lignes.
- Même jour → append sous la section existante (ne pas dupliquer l'en-tête de jour).

## Comment append

Préférer le script (horodatage fiable, encodage UTF-8) :

```powershell
& "%USERPROFILE%\projets\aria-skills\.grok\skills\journal-de-bord\scripts\append.ps1" -Message "Modification de path/to/file.py"
```

Sinon : lire le fichier, trouver ou créer la section du jour, ajouter la ligne avec l'heure locale `HHhMM`.

## Fin de session

1. Vérifier que toutes les actions de la session sont loguées.
2. Si `collegue-memoire` modifié : `git commit` + `git push` (règle collègue existante).
3. Ne pas résumer le journal dans la réponse utilisateur sauf demande explicite (`/journal-de-bord`, « montre le journal »).

## Lecture

Sur demande : lire `JOURNAL.md`, afficher les N dernières lignes ou la section du jour.