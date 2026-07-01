# Setup autre PC — aria-local-sync + Grok Build

> Guide pour remettre un PC GoldenFar / ARIA au même niveau que la machine source (PCDESS9).  
> Repo : `GoldenFarFR/aria-local-sync` (privé)

---

## Ce que ce repo contient

| Dossier | Contenu |
|---------|---------|
| `sync/vault/goldenfar-vault.gfv` | **Toutes les clés** (Render, X, Telegram, Groq, GitHub, Stripe…) — fichier **chiffré** |
| `sync/ide/` | Règles Cursor + Grok |
| `sync/metier/ddc/` | Fichiers Excel DDC |
| `sync/aria-data/` | Mémoire ARIA locale (souvent vide — la prod vit sur Render) |

**Chiffrement du `.gfv`** : rotation **quotidienne** (clé dérivée de la date). Les 2 PC partagent le **même secret maître** (Bitwarden) — pas le mot de passe du jour à la main.  
Ne jamais commiter les secrets dans Git (fichiers `.vault-*` sont dans `.gitignore`).

---

## Les 2 PC sont-ils liés ?

Oui, via **3 canaux** :

| Canal | Rôle | Qui initie |
|-------|------|------------|
| **GitHub** `aria-local-sync` | Coffre `.gfv`, règles IDE, Excel DDC | PCDESS9 pousse ; l'autre PC tire |
| **Bitwarden** (secrets locaux) | Secret maître + TOTP + lien API ARIA — **identiques** sur les 2 PC | Copie manuelle une fois |
| **Telegram** (pont TOTP) | ARIA demande le code ; tu réponds sur ton téléphone | Les 2 PC (même compte) |
| **Syncthing** (optionnel) | Coffre live `%LOCALAPPDATA%\GoldenFar\vault` | Les 2 PC |

**PC source actuel** : `PCDESS9` (rotation auto chaque nuit 03h00 + TOTP actif).  
**Cet autre PC** : suit ce guide — ne régénère **pas** de nouveaux secrets (sinon les 2 PC ne seraient plus liés).

---

## Prérequis Windows

Installer si absent :

```powershell
winget install Git.Git
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
winget install Syncthing.Syncthing   # optionnel — sync live du coffre
```

Comptes : GitHub (2FA), Bitwarden (passphrase coffre).

---

## Étape 0 — Changement de PC immédiat

Si tu viens de quitter **PCDESS9** : lis d'abord **[CHANGEMENT-PC-MAINTENANT.md](CHANGEMENT-PC-MAINTENANT.md)** (ordre strict + dépannage).

---

## Étape 1 — Cloner ce repo

```powershell
mkdir "$env:USERPROFILE\projets" -ErrorAction SilentlyContinue
cd "$env:USERPROFILE\projets"
git clone https://github.com/GoldenFarFR/aria-local-sync.git
cd aria-local-sync
git pull
.\scripts\bootstrap-autre-pc.ps1   # enchaîne apply + new-pc + handoff (après secrets étape 2)
```

> `bootstrap-autre-pc.ps1` suppose les fichiers `.vault-*` déjà en place (étape 2).

---

## Étape 2 — Lier les secrets (Bitwarden → fichiers locaux)

Sur **PCDESS9**, les secrets sont déjà dans Bitwarden. Sur **cet autre PC**, recopie-les **sans** lancer `setup-daily-vault.ps1` ni `setup-totp-vault.ps1` (ces scripts **créent** de nouveaux secrets et casseraient le lien).

### 2.1 Secret maître (rotation quotidienne)

Entrée Bitwarden : **`goldenfar-vault-master`**

```powershell
cd "$env:USERPROFILE\projets\aria-local-sync"
# Coller le secret maître (une ligne, base64) depuis Bitwarden :
Set-Content -Path ".vault-master-secret" -Value "<SECRET_BITWARDEN>" -Encoding UTF8 -NoNewline
```

Alternative : variable utilisateur Windows `GOLDENFAR_VAULT_MASTER` (même valeur).

> La clé du jour est **calculée automatiquement** — tu n'as pas à la noter.  
> `apply-local.ps1` essaie aujourd'hui / hier / avant-hier (décalage fuseau).

### 2.2 Google Authenticator (TOTP)

Entrée Bitwarden : **`goldenfar-vault-totp`** (clé base32, ex. `JUNCFNIPZWM67NDAEEZU`)

**Sur le téléphone** (même compte que PCDESS9) :

1. Google Authenticator → **+** → **Saisir une clé de configuration**
2. Nom : `GoldenFar Vault`
3. Clé : celle de Bitwarden `goldenfar-vault-totp`
4. Type : **Basée sur le temps**

**Sur ce PC** :

```powershell
Set-Content -Path ".vault-totp-secret" -Value "<CLE_TOTP_BITWARDEN>" -Encoding UTF8 -NoNewline
```

Alternative : variable `GOLDENFAR_VAULT_TOTP_SECRET`.

Désormais `collect-local` et `apply-local` demandent le **code à 6 chiffres** (comme sur PCDESS9).

### 2.3 Lien API ARIA (pont Telegram)

Entrée Bitwarden : **`goldenfar-admin-api`** (`ADMIN_API_SECRET`)

Permet au PC de parler à l'API Render (`/api/aria/totp/...`) pour le **pont Telegram** : ARIA t'envoie la demande, tu réponds avec les 6 chiffres.

**Après** `apply-local` (étape 4), vérifie que le coffre contient la valeur :

```powershell
Select-String -Path "$env:LOCALAPPDATA\GoldenFar\vault\production.env" -Pattern "^ADMIN_API_SECRET="
```

Si vide, ajoute la ligne depuis Bitwarden :

```powershell
$vault = "$env:LOCALAPPDATA\GoldenFar\vault\production.env"
# Ouvre le fichier et mets : ADMIN_API_SECRET=<valeur Bitwarden goldenfar-admin-api>
notepad $vault
```

### 2.4 Vérifier les fichiers locaux

```powershell
Test-Path "$env:USERPROFILE\projets\aria-local-sync\.vault-master-secret"   # True
Test-Path "$env:USERPROFILE\projets\aria-local-sync\.vault-totp-secret"     # True
```

### Entrées Bitwarden (récap)

| Entrée Bitwarden | Fichier local | Rôle |
|------------------|---------------|------|
| `goldenfar-vault-master` | `aria-local-sync\.vault-master-secret` | Rotation quotidienne `.gfv` |
| `goldenfar-vault-totp` | `aria-local-sync\.vault-totp-secret` | Google Authenticator |
| `goldenfar-admin-api` | `vault\production.env` → `ADMIN_API_SECRET` | Pont Telegram ARIA |

---

## Étape 3 — Cloner `aria-vanguard` (avant apply)

`apply-local` a besoin du script de chiffrement dans `aria-vanguard\operator`.

```powershell
cd "$env:USERPROFILE\projets"
git clone https://github.com/GoldenFarFR/aria-vanguard.git
```

---

## Étape 4 — Restaurer coffre + IDE + métier

### Option A — Pont Telegram (recommandé, Cursor / Grok)

ARIA t'écrit sur Telegram ; tu réponds avec les 6 chiffres (pas de saisie dans le terminal).

```powershell
cd "$env:USERPROFILE\projets\aria-local-sync"
.\scripts\apply-local.ps1 -ViaAria
```

### Option B — Saisie manuelle

```powershell
.\scripts\apply-local.ps1
# ou : .\scripts\apply-local.ps1 -TotpCode 123456
```

- Déchiffre le `.gfv` avec la clé du jour (secret maître).
- Restaure le coffre dans `%LOCALAPPDATA%\GoldenFar\vault`.
- Lance `sync-local.ps1` si `aria-vanguard` est cloné.

Puis vérifie `ADMIN_API_SECRET` (étape 2.3).

**Ne pas** utiliser l'ancien mot de passe statique `goldenfar-vault-sync`.

---

## Étape 5 — Cloner tous les repos + skills (`new-pc`)

```powershell
cd "$env:USERPROFILE\projets\aria-vanguard\operator"
.\new-pc.ps1
```

Clone automatiquement : `collegue-memoire`, `aria-skills`, `aria-sandbox`, `aria-vanguard`, et relance `apply-local` si besoin.

Si `aria-vanguard` n’est pas encore là :

```powershell
cd "$env:USERPROFILE\projets"
git clone https://github.com/GoldenFarFR/aria-vanguard.git
cd aria-vanguard\operator
.\new-pc.ps1
```

---

## Étape 6 — Sessions Grok multi-PC (handoff)

A chaque **fin** de session utile sur un PC :

```powershell
cd $env:USERPROFILE\projets\aria-local-sync\scripts
.\collect-session.ps1
cd ..\..\collegue-memoire
git add sessions/
git commit -m "session: $env:COMPUTERNAME"
git push
```

A chaque **debut** de session sur l'autre PC :

```powershell
.\session-handoff.ps1          # TOTP Git 12h si session expiree (-SkipGitGate urgence)
.\open-checklist.ps1           # checklist visuelle (optionnel)
```

L'assistant lit ensuite `HANDOFF.md` (delta depuis l'autre PC) + `COLLEGUE.md`.  
Detail : `collegue-memoire/sessions/README.md`.

**Audit sécurité** : intégré à `session-handoff` (`audit-github-security.ps1` + alerte Telegram + issue GitHub si critical).

**Après 1ᵉʳ handoff** : ajoute le nom de ta machine dans `security/github-trust.yaml` → `known_machines:` puis `git push`.

---

## Étape 7 — Test pont Telegram (recommandé)

```powershell
cd "$env:USERPROFILE\projets\aria-local-sync\scripts"
.\simulate-interactive.ps1
```

1. ARIA envoie **🔐 Code GoldenFar Vault requis** sur Telegram  
2. Tu réponds avec les **6 chiffres** (Google Authenticator)  
3. Attendu : `[ARIA] Code recu via Telegram` puis `[TOTP] OK`

Test sécurité complet (optionnel) : `.\test-vault-security.ps1`

---

## Étape 8 — Grok Build

### 8.1 Installer les skills ARIA

```powershell
cd "$env:USERPROFILE\projets\aria-skills"
.\scripts\install.ps1
```

### 8.2 Règles Cursor (obligatoire)

```powershell
$dst = "$env:USERPROFILE\.cursor\rules"
mkdir $dst -Force
Copy-Item "$env:USERPROFILE\projets\collegue-memoire\.cursor\rules\*.md" $dst -Force
```

Les règles sont aussi dans `sync/ide/` — `apply-local` les a déjà copiées vers `%USERPROFILE%\.cursor\rules` et `%USERPROFILE%\.grok\rules`.

### 8.3 Grok Build — premier lancement

1. Ouvrir **Grok Build** (ou Cursor avec Grok).
2. Workspace recommandé : `%USERPROFILE%\projets\aria-vanguard` ou `aria-sandbox`.
3. Au premier message, l’assistant lit automatiquement :
   - `collegue-memoire\COLLEGUE.md` (après `git pull`)
   - `VISION.md` à la racine du repo ouvert
4. **Reconnecter Grok** : auth propre à chaque PC (`%USERPROFILE%\.grok\auth.json` — pas dans ce repo).

### 8.4 Vérifier les skills actifs

Dans Grok Build, les skills SSOT viennent de `aria-skills` :

- `vision-enforcer`
- `journal-de-bord`
- `operator-runbook`
- `marketing-decision-framework`

Commande utile : `/operator-runbook` avant tout deploy.

---

## Étape 9 — Vérification finale

```powershell
cd "$env:USERPROFILE\projets\aria-vanguard\operator"
.\check-aria-status.ps1
```

Attendu : **OK — aucun problème critique**.  
Secrets locaux = Render, health `https://test-1-nwf2.onrender.com/api/health` OK.

Dev local (optionnel) :

```powershell
cd "$env:USERPROFILE\projets\aria-sandbox"
.\scripts\setup-local.ps1
cd ..\aria-vanguard\operator
.\sync-local.ps1
```

---

## Syncthing (optionnel, recommandé)

Si Syncthing tourne sur les 2 PC, le coffre se met à jour **en live** sans repasser par `collect-local` / `git push`.

1. `winget install Syncthing.Syncthing`
2. Sur le PC source : `aria-vanguard\operator\setup-syncthing-vault.ps1`
3. Sur ce PC : accepter le dossier `goldenfar-vault` → chemin `%LOCALAPPDATA%\GoldenFar\vault`

Guide complet : `aria-vanguard\operator\MULTI-PC-VAULT.md`

---

## Quand le PC source change quelque chose

Sur **PCDESS9** (ou machine à jour) :

```powershell
cd "$env:USERPROFILE\projets\aria-local-sync"
.\scripts\collect-local.ps1
git add -A
git status    # pas de .env en clair
git commit -m "sync: collect <nom-machine>"
git push
```

Sur **cet autre PC** :

```powershell
cd "$env:USERPROFILE\projets\aria-local-sync"
git pull
.\scripts\apply-local.ps1 -ViaAria    # ou saisie manuelle Authenticator
```

### Rôles des 2 PC (après liaison)

| Action | PCDESS9 (source) | Autre PC |
|--------|------------------|----------|
| Modifier une clé API | Oui — puis `collect-local` + `git push` | Non (sauf urgence) |
| Rotation nocturne `.gfv` | Oui — tâche `GoldenFar-DailyVaultRotation` 03h00 | Non — lit le `.gfv` du jour via `git pull` |
| Dev / Grok / check status | Oui | Oui |
| `setup-daily-vault.ps1` | Déjà fait (ne pas relancer) | **Ne pas lancer** — copier le secret maître |
| `setup-totp-vault.ps1` | Déjà fait (ne pas relancer) | **Ne pas lancer** — copier le secret TOTP |

Si **cet autre PC** modifie le coffre et doit pousser :

```powershell
cd "$env:USERPROFILE\projets\aria-local-sync\scripts"
.\collect-local.ps1 -SkipMetier -SkipIde -ViaAria
cd ..
git add -A
git commit -m "sync: collect $env:COMPUTERNAME"
git push
```

---

## Ce qui n’est PAS dans ce repo

| Élément | Où |
|---------|-----|
| Code source | Repos GitHub (`aria-sandbox`, `aria-vanguard`, …) |
| Mémoire ARIA prod (`aria.db`, tweets publiés) | Render — pas sur disque local |
| Auth Grok | Reconnexion manuelle |
| `kikou`, `methode-travail` | Clones Git séparés si besoin |

---

## Sécurité — détail complet

Voir **[SECURITE-CLES.md](SECURITE-CLES.md)** — rotation quotidienne, TOTP, sync auto, surveillance hebdo.

**Sur cet autre PC** : secrets déjà créés sur PCDESS9 → **copie Bitwarden** (étape 2), pas de `setup-*` qui régénère.

Surveillance clés (les 2 PC peuvent l'installer) :

```powershell
cd "$env:USERPROFILE\projets\aria-vanguard\operator"
.\setup-key-health-task.ps1
```

---

## Dépannage rapide

| Problème | Action |
|----------|--------|
| Impossible de déchiffrer `.gfv` | Vérifier `.vault-master-secret` = Bitwarden `goldenfar-vault-master` ; `git pull` pour le `.gfv` du jour |
| Code Authenticator refusé | Même clé TOTP que PCDESS9 dans `.vault-totp-secret` ; horloge téléphone à l'heure |
| Pont Telegram : pas de message | `check-aria-status.ps1` ; bot Render actif ; `TELEGRAM_ADMIN_IDS` |
| `ADMIN_API_SECRET absent` | Bitwarden `goldenfar-admin-api` → `production.env` puis `sync-local.ps1` |
| `401` sur `/api/aria/totp` | `ADMIN_API_SECRET` incorrect ou vide dans `production.env` |
| Demande TOTP `expired` | Répondre sous 2 min sur Telegram ; relancer `-ViaAria` |
| `Mode: mot de passe statique` | Fichier `.vault-master-secret` absent ou mauvais chemin |
| `apply-local` : crypto introuvable | `git clone aria-vanguard` puis relancer |
| X / Telegram non connectés | `.\sync-local.ps1` puis `.\check-aria-status.ps1` |
| Deploy Render après modif secrets | `.\sync-render.ps1` (redeploy inclus) |
| 2 PC plus liés | Quelqu'un a relancé `setup-daily-vault` ou `setup-totp-vault` sur l'autre PC — recopier les secrets PCDESS9 |
| Inventaire sans copier | `.\scripts\inventory.ps1` |

---

## Checklist une page (autre PC)

- [ ] Prérequis : Git, Python, Node (Syncthing optionnel)
- [ ] `git clone aria-local-sync` + `git pull`
- [ ] Bitwarden `goldenfar-vault-master` → `.vault-master-secret`
- [ ] Bitwarden `goldenfar-vault-totp` → `.vault-totp-secret` + Google Authenticator
- [ ] `git clone aria-vanguard` (avant apply)
- [ ] `apply-local.ps1 -ViaAria` (Telegram OK)
- [ ] Bitwarden `goldenfar-admin-api` → `ADMIN_API_SECRET` dans `production.env`
- [ ] `new-pc.ps1`
- [ ] `bootstrap-autre-pc.ps1` ou étapes manuelles 2–6
- [ ] `session-handoff.ps1` au premier lancement
- [ ] `known_machines` dans `security/github-trust.yaml` (nom du 2ᵉ PC)
- [ ] `simulate-interactive.ps1` → `[TOTP] OK`
- [ ] Fin de session : `collect-session.ps1` + `push-session-manifest.ps1 -ViaAria`
- [ ] `aria-skills\scripts\install.ps1`
- [ ] Règles `.cursor\rules` copiées
- [ ] Grok reconnecté
- [ ] `check-aria-status.ps1` vert
- [ ] `collegue-memoire` : `git pull` + lire `COLLEGUE.md`
- [ ] (Optionnel) Syncthing — `MULTI-PC-VAULT.md`
- [ ] **Ne pas** relancer `setup-daily-vault.ps1` ni `setup-totp-vault.ps1`

### Commandes quotidiennes (autre PC)

```powershell
git -C $env:USERPROFILE\projets\aria-local-sync pull
cd $env:USERPROFILE\projets\aria-local-sync\scripts
.\apply-local.ps1 -ViaAria -SkipMetier -SkipIde   # apres changement sur PCDESS9
```

Variable optionnelle (toujours pont Telegram) :

```powershell
[Environment]::SetEnvironmentVariable("GOLDENFAR_VAULT_TOTP_VIA_ARIA", "1", "User")
```

*Dernière mise à jour : 2026-06-20 — PCDESS9 source, Phase 3b self-improve, audit IP, bootstrap 2ᵉ PC*