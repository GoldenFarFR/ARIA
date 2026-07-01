# Sessions Grok — manifestes multi-PC

> **Pas** les fichiers bruts `.grok/sessions` (trop lourds, ~Mo, format instable).  
> Seulement un **resume JSON par PC** apres chaque session utile.

## Contenu

```
sessions/

    latest.json          # dernier manifeste
    2026-06-20T125300.json
  AUTRE-PC/
    latest.json
```

Chaque manifeste contient : fichiers touches, repos, etat git, fin du JOURNAL.md.

## Fin de session (PC courant)

```powershell
cd %USERPROFILE%\projets\aria-local-sync\scripts
.\collect-session.ps1
cd ..\..\collegue-memoire
git add sessions/
git commit -m "session: <machine>"
git push
```

## Debut de session (n'importe quel PC)

```powershell
cd %USERPROFILE%\projets\aria-local-sync\scripts
.\session-handoff.ps1
```

Puis Grok lit **`sessions/HANDOFF.md`** (SSOT GitHub) + `COLLEGUE.md` + `JOURNAL.md`.

Le skill `session-handoff` (always-on dans aria-skills) execute tout ca **automatiquement** au demarrage.

## Checklist visuelle

| Fichier | Role |
|---------|------|
| `../SESSION-CHECKLIST.html` | Page HTML locale (coches vertes, setup PC, etat multi-PC) |
| `CHECKLIST-REFERENCE.md` | Reference texte (GitHub) |
| `aria-local-sync/scripts/open-checklist.ps1` | Ouvre la page dans le navigateur |

Regeneree a chaque `session-handoff.ps1`. Tu n'as rien a demander a Grok.

## Ce que fait session-handoff

1. `git pull` collegue-memoire, aria-local-sync, aria-vanguard, etc.
2. Compare `sessions/<autre-PC>/latest.json` vs ta derniere visite
3. `git pull` les repos modifies sur l'autre PC
4. Ecrit `HANDOFF.md` (delta + actions)

## Lourd ou pas ?

| Approche | Verdict |
|----------|---------|
| Sync tout `.grok/sessions` | Trop lourd — GitHub, secrets, images |
| Manifeste JSON + HANDOFF | OK — quelques Ko par session |
| JOURNAL.md seul (deja en place) | Minimum viable, moins de detail fichiers |