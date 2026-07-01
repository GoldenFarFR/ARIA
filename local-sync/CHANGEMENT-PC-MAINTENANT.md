# Changement de PC — maintenant (PCDESS9 → autre machine)

> **PC source** : `PCDESS9` — session du 2026-06-20.  
> **Prod ARIA** : `https://test-1-nwf2.onrender.com/api/health` — Phase 3/3b deployée.

## Tu ne fais rien (sauf Bitwarden la 1ʳᵉ fois)

1. Installe **Grok Build** ou **Cursor** + connecte ton compte.
2. Ouvre une session — **premier message** : ce que tu veux (ou même « bonjour »).
3. L’assistant exécute **tout seul** : `session-handoff.ps1` → clone GitHub → lit `HANDOFF.md` → bootstrap si nouveau PC.
4. **Seule exception** : si Grok te demande les **2 secrets Bitwarden** (fichiers `.vault-*`) — copie depuis Bitwarden, une fois.

Tu n’as **pas** besoin de dire « lis le github et met toi à jour » — c’est automatique (règle always-on).

---

## Sur PCDESS9 avant de partir (si pas déjà fait)

```powershell
cd $env:USERPROFILE\projets\aria-local-sync\scripts
.\collect-local.ps1 -ViaAria          # pousse le coffre .gfv du jour
cd ..
git add -A
git status                            # aucun .vault-* ni .env en clair
git commit -m "sync: depart PCDESS9 $(Get-Date -Format yyyy-MM-dd)"
git push

.\collect-session.ps1
.\push-session-manifest.ps1 -ViaAria
```

---

## Sur l'autre PC — ordre strict (30 min)

### 1. Prérequis

```powershell
winget install Git.Git Python.Python.3.12 OpenJS.NodeJS.LTS
```

### 2. Cloner aria-local-sync

```powershell
mkdir $env:USERPROFILE\projets -Force
git clone https://github.com/GoldenFarFR/aria-local-sync.git $env:USERPROFILE\projets\aria-local-sync
cd $env:USERPROFILE\projets\aria-local-sync
git pull
```

### 3. Secrets Bitwarden (AVANT bootstrap — copier, ne pas régénérer)

| Bitwarden | Fichier local |
|-----------|---------------|
| `goldenfar-vault-master` | `aria-local-sync\.vault-master-secret` |
| `goldenfar-vault-totp` | `aria-local-sync\.vault-totp-secret` + Google Authenticator (même compte que PCDESS9) |
| `goldenfar-admin-api` | sera dans `vault\production.env` après `apply-local` (étape 4) |

```powershell
cd $env:USERPROFILE\projets\aria-local-sync
# Coller depuis Bitwarden (une ligne chacun) :
Set-Content .vault-master-secret "<goldenfar-vault-master>" -Encoding UTF8 -NoNewline
Set-Content .vault-totp-secret "<goldenfar-vault-totp>" -Encoding UTF8 -NoNewline
Test-Path .vault-master-secret, .vault-totp-secret   # True, True
```

**Ne pas** lancer `setup-daily-vault.ps1` ni `setup-totp-vault.ps1` sur le 2ᵉ PC.

### 4. Bootstrap automatique

```powershell
cd $env:USERPROFILE\projets\aria-local-sync\scripts
.\bootstrap-autre-pc.ps1
```

Enchaîne : `apply-local -ViaAria` → `new-pc.ps1` → skills → `session-handoff` → rappels finaux.

### 5. Premier message Grok / Cursor

L'assistant exécute automatiquement `session-handoff.ps1` et lit :

- `collegue-memoire\sessions\HANDOFF.md`
- `collegue-memoire\COLLEGUE.md`
- `collegue-memoire\SESSION-START.md`

Checklist visuelle : `collegue-memoire\SESSION-CHECKLIST.html` ou `.\open-checklist.ps1`

### 6. Vérification obligatoire

```powershell
cd $env:USERPROFILE\projets\aria-vanguard\operator
.\check-aria-status.ps1
cd $env:USERPROFILE\projets\aria-local-sync\scripts
.\simulate-interactive.ps1
```

Attendu : health OK + `[TOTP] OK`.

### 7. Ajouter cette machine à l'audit sécurité

Après le 1ᵉʳ `session-handoff` réussi, éditer :

`aria-local-sync\security\github-trust.yaml` → ajouter le nom de la machine dans `known_machines:`

Puis commit + push `aria-local-sync`.

---

## Commandes quotidiennes (2ᵉ PC)

```powershell
cd $env:USERPROFILE\projets\aria-local-sync\scripts
.\session-handoff.ps1                 # début session (TOTP Git 12h si expiré)
# ... travail ...
.\collect-session.ps1
.\push-session-manifest.ps1 -ViaAria  # fin session
```

Après modif secrets sur PCDESS9 :

```powershell
git -C $env:USERPROFILE\projets\aria-local-sync pull
.\apply-local.ps1 -ViaAria -SkipMetier -SkipIde
```

---

## Dépannage express

| Problème | Fix |
|----------|-----|
| `Assert-TotpGate` introuvable | `git pull aria-local-sync` puis relancer |
| Session Git TOTP bloquée | `.\session-handoff.ps1 -SkipGitGate` (urgence) |
| `.gfv` ne déchiffre pas | `git pull` + vérifier `.vault-master-secret` Bitwarden |
| Health OK mais vieux build | `operator\sync-render.ps1` sur PC avec coffre à jour |
| SSL `api.ariavanguardzhc.com` | Utiliser `test-1-nwf2.onrender.com` (DNS holding à corriger) |

---

## Guide complet

[SETUP-AUTRE-PC.md](SETUP-AUTRE-PC.md) — détail TOTP, Syncthing, Grok, skills.