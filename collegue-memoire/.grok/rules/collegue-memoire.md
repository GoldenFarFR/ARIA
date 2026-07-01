# Mandatory — Mémoire collègue (monorepo ARIA)

Avant toute tâche :

1. Lire `%ARIA_REPO_ROOT%\collegue-memoire\COLLEGUE.md`  
   (défaut `ARIA_REPO_ROOT` = `%USERPROFILE%\GitHub-Repos\ARIA`)
2. `git pull` sur `GoldenFarFR/ARIA` avant lecture si le clone existe
3. Handoff : `%ARIA_REPO_ROOT%\local-sync\scripts\session-handoff.ps1`

Après session utile : mettre à jour `COLLEGUE.md`, commit + push `GoldenFarFR/ARIA`.

Si clone absent (une fois) :
```powershell
git clone https://github.com/GoldenFarFR/ARIA.git "%USERPROFILE%\GitHub-Repos\ARIA"
[System.Environment]::SetEnvironmentVariable("ARIA_REPO_ROOT", "%USERPROFILE%\GitHub-Repos\ARIA", "User")
```