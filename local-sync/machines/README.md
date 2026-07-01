# Inventaire machines GoldenFar

Chaque PC a un dossier `machines/<COMPUTERNAME>/` :

| Fichier | Rôle |
|---------|------|
| `inventory.json` | Dernier `collect-local` (coffre, clones, warnings) |
| `ip-latest.json` | IP publique (`report-machine-ip.ps1`) |
| `*.json` | Manifestes session (`collect-session`) |

Après bootstrap sur un **nouveau PC** :

1. Lancer `bootstrap-autre-pc.ps1`
2. Ajouter le nom machine dans `security/github-trust.yaml` → `known_machines`
3. `collect-local.ps1` + `git push`