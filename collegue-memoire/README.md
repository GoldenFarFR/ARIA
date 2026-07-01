# collegue-memoire (dans le monorepo ARIA)

Mémoire Sylvain × assistant IA — **un fichier** `COLLEGUE.md` + journal + sessions handoff.

## Emplacement

| Élément | Chemin |
|---------|--------|
| Repo GitHub | `GoldenFarFR/ARIA` (privé) |
| Ce dossier | `%ARIA_REPO_ROOT%\collegue-memoire\` |
| Mémoire métier | `COLLEGUE.md` |
| Journal technique | `JOURNAL.md` |
| Handoff | `sessions/HANDOFF.md` |

## Nouveau PC

```powershell
git clone https://github.com/GoldenFarFR/ARIA.git "%USERPROFILE%\GitHub-Repos\ARIA"
[System.Environment]::SetEnvironmentVariable("ARIA_REPO_ROOT", "%USERPROFILE%\GitHub-Repos\ARIA", "User")
copy "%USERPROFILE%\GitHub-Repos\ARIA\collegue-memoire\.grok\rules\*.md" "%USERPROFILE%\.grok\rules\"
cd "%USERPROFILE%\GitHub-Repos\ARIA\skills\scripts"
.\install.ps1
```

## Session

```powershell
cd %ARIA_REPO_ROOT%\local-sync\scripts
.\session-handoff.ps1
```

Puis lire `COLLEGUE.md` + `JOURNAL.md`.