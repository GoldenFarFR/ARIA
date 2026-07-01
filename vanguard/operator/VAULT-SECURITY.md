# Coffre local GoldenFar — sécurité secrets

> **Règle** : zéro secret dans `projets/` ni dans Git. Scripts dans `aria-vanguard/operator/` ; **valeurs** dans le coffre machine.

## Emplacement (ce PC)

| Élément | Chemin |
|---------|--------|
| Coffre | `%LOCALAPPDATA%\GoldenFar\vault` |
| Variable | `GOLDENFAR_VAULT` (profil utilisateur Windows) |
| Attributs | Dossier caché + ACL = ton utilisateur uniquement |

Contenu typique :

```
vault/
  production.env          # backend Render
  local.env               # dev local
  vanguard.env            # VITE_* holding
  keys/
    render.api-key        # rnd_...
    ionos.api-key         # prefix.secret
  stripe/
    recovery-codes.txt    # codes 2FA Stripe (jamais Git)
```

## Setup / migration

```powershell
cd projets\aria-vanguard\operator
.\setup-vault.ps1
.\migrate-to-vault.ps1
.\check-aria-status.ps1
```

**Plusieurs PC** : voir `MULTI-PC-VAULT.md` (Syncthing gratuit + Bitwarden + sauvegarde `.gfv`).

## GitHub — durcissement recommandé

### Déjà en place

- 8 repos GoldenFarFR en **privé**
- `.gitignore` sur les `.env` et clés locales

### À faire (priorité)

1. **2FA obligatoire** sur github.com → Settings → Password and authentication → Passkeys ou TOTP
2. **Révoquer / régénérer** toute clé collée en chat (IONOS, GitHub `gho_`, Stripe si exposé)
3. **Fine-grained PAT** pour `GITHUB_TOKEN` Render : accès lecture seule `aria-sandbox` + repos listés dans `GITHUB_READ_REPOS`, pas `*`
4. **Ne jamais** coller de secret dans Cursor / Telegram / mail
5. **`stripe/recovery-codes.txt`** : retiré de Git — régénérer les codes Stripe si le repo a déjà été poussé avec ce fichier

### Si tu passes en organisation GitHub

- Activer **Secret scanning** + **Push protection** sur tous les repos privés
- Rôles : toi = Owner, CI = token dédié minimal

### Token Render / Groq / X / Telegram

- Rotation annuelle ou après incident : voir `ROTATION.md`
- Render garde les vars en prod ; le coffre local = source de vérité pour éditer + `sync-render.ps1`

## Sauvegarde du coffre

- **BitLocker** sur le disque Windows (obligatoire portable)
- Sauvegarde chiffrée du dossier `vault` (7-Zip AES / VeraCrypt) sur support externe
- **Pas** OneDrive / Google Drive non chiffré pour `production.env`

## Ce que les repos contiennent encore

| Repo | Contenu |
|------|---------|
| `aria-vanguard/operator` | Scripts PowerShell, `*.example`, `site.config.json` — **pas** de secrets |
| Autres repos | Code, `.env.example` uniquement |