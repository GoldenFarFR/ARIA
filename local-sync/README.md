# aria-local-sync

État local GoldenFar / ARIA **hors repos code** — synchronisé via GitHub privé entre tes PC.

## Ce repo contient

| Dossier | Contenu |
|---------|---------|
| `sync/aria-data/` | Mémoire ARIA (`aria.db`, compose intel, ledgers X, directives…) |
| `sync/ide/` | Règles Cursor + Grok (sans secrets) |
| `sync/metier/` | Réservé (vide — métier hors repo ARIA) |
| `machines/` | Inventaire par PC (ce qui a été trouvé / manquant) |

## Cles et secrets (coffre chiffre)

`collect-local.ps1` exporte **tout le coffre** en `sync/vault/goldenfar-vault.gfv` (AES — pas en clair).

Sur l'autre PC, `apply-local.ps1` restaure le coffre + lance `sync-local.ps1` → `backend/.env`.

**Sécurité** : rotation quotidienne du chiffrement + TOTP + pont Telegram ARIA.  
Voir [SECURITE-CLES.md](SECURITE-CLES.md) et [SETUP-AUTRE-PC.md](SETUP-AUTRE-PC.md).

Bitwarden : `goldenfar-vault-master`, `goldenfar-vault-totp`, `goldenfar-admin-api`.

Alternative live : **Syncthing** (voir `aria-vanguard\operator\MULTI-PC-VAULT.md`).

## PC source (celui qui a les données)

```powershell
cd %USERPROFILE%\projets\aria-local-sync
.\scripts\collect-local.ps1
git add -A
git status   # vérifier : aucun .env ni clé
git commit -m "sync: collect depuis $env:COMPUTERNAME"
git push
```

## PC destination (autre machine)

**Guide complet : [SETUP-AUTRE-PC.md](SETUP-AUTRE-PC.md)** — Grok Build, Cursor, checklist.

```powershell
git clone https://github.com/GoldenFarFR/aria-local-sync.git "%USERPROFILE%\projets\aria-local-sync"
# Suivre SETUP-AUTRE-PC.md (secrets Bitwarden, puis) :
.\scripts\apply-local.ps1 -ViaAria
cd ..\aria-vanguard\operator
.\new-pc.ps1
.\scripts\simulate-interactive.ps1   # test Telegram
.\check-aria-status.ps1
```

## Changement de PC

**Guide express** : [CHANGEMENT-PC-MAINTENANT.md](CHANGEMENT-PC-MAINTENANT.md)  
**Guide complet** : [SETUP-AUTRE-PC.md](SETUP-AUTRE-PC.md)  
**Bootstrap auto** : `scripts\bootstrap-autre-pc.ps1`

## Scripts

| Script | Rôle |
|--------|------|
| `ensure-pc-ready.ps1` | Clone repos manquants + règles IDE + détecte nouveau PC (appelé par handoff) |
| `bootstrap-autre-pc.ps1` | **2ᵉ PC** : apply + new-pc + skills (appelé par ensure-pc-ready si secrets OK) |
| `collect-local.ps1` | Export coffre chiffré → `sync/` (`-ViaAria` = code via Telegram) |
| `apply-local.ps1` | Restaure coffre + IDE + métier (`-ViaAria`) |
| `session-handoff.ps1` | Début session : pull repos + audit + `HANDOFF.md` |
| `collect-session.ps1` | Fin session : manifeste Grok → `collegue-memoire/sessions/` |
| `push-session-manifest.ps1` | Push `sessions/` avec gate Git TOTP 12h |
| `git-operator-session.ps1` | Session Git 12h (TOTP une fois) |
| `audit-github-security.ps1` | Audit origine/IP/secrets (session-handoff) |
| `send-audit-alert.ps1` | Telegram si critical + issue GitHub auto |
| `file-self-improve-gap.ps1` | Issue/PR ARIA self-improve (API ou GitHub) |
| `report-machine-ip.ps1` | Enregistre IP publique machine |
| `write-session-checklist.ps1` | Génère `SESSION-CHECKLIST.html` |
| `open-checklist.ps1` | Ouvre la checklist navigateur |
| `simulate-interactive.ps1` | Test complet sécurité + pont Telegram |
| `test-vault-security.ps1` | Simulation attaque (14 scénarios) |
| `test-totp-live.ps1` | Test TOTP en conditions réelles |
| `inventory.ps1` | Affiche l'état sans copier |
| `notify-aria-telegram.ps1` | Message admin via API ARIA |
| `totp-gate.ps1` / `totp-aria-bridge.ps1` | TOTP + pont Telegram |
| `rotate-daily-vault.ps1` | Rotation nocturne `.gfv` (03h00) |
| `watch-vault-sync.ps1` | Surveillance sync coffre |

## DATA_DIR local par défaut

`%USERPROFILE%\projets\aria-vanguard\backend\data`

Override : variable `ARIA_LOCAL_DATA_DIR` ou `DATA_DIR` dans `local.env` (coffre).