# Checklist session — reference visuelle

> **Fichier live (local, regenere)** : `%USERPROFILE%\projets\collegue-memoire\SESSION-CHECKLIST.html`  
> Ouvrir : `aria-local-sync\scripts\open-checklist.ps1`

## Legende

| Badge | Qui |
|-------|-----|
| **Script** | `session-handoff.ps1` au demarrage |
| **Grok** | Skill `session-handoff` always-on — sans que tu demandes |
| **Toi** | Setup une fois par PC (`SETUP-AUTRE-PC.md`) |

## Debut de session (chaque fois)

1. Verifier `collegue-memoire` clone
2. `git pull` repos GoldenFar
3. Comparer manifestes autre PC (`sessions/<machine>/latest.json`)
4. Generer `SESSION-START.md`
5. **TOTP Google** si session Git expiree (valide **12 h**)
6. **Audit GitHub** (commits suspects)
7. Grok lit `HANDOFF.md`, `COLLEGUE.md`, fin `JOURNAL.md`, `VISION.md`
8. Grok resume le delta autre PC + alertes audit

## Fin de session utile

1. `collect-session.ps1`
2. `git commit` + `push` `sessions/` dans collegue-memoire
3. MAJ `COLLEGUE.md` si decision metier
4. Append `JOURNAL.md` (journal-de-bord)

## Raccourci

```powershell
cd %USERPROFILE%\projets\aria-local-sync\scripts
.\open-checklist.ps1
```