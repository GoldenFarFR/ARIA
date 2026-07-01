# Sécurité des clés — ce qui est possible (et ce qui ne l'est pas)

## Rotation quotidienne du chiffrement (recommandé)

**Objectif :** si un attaquant vole `goldenfar-vault.gfv` sur GitHub, le fichier est **rechiffré chaque nuit** avec une **nouvelle clé** (dérivée de la date).

| Élément | Change chaque jour ? |
|---------|----------------------|
| Mot de passe de chiffrement du `.gfv` sur GitHub | **Oui** (automatique) |
| Secret maitre (Bitwarden) | Non — tu le gardes une fois |
| Clés API Render / X / GitHub | Non — sauf si tu les regeneres |

### Activation (une fois par PC)

```powershell
cd %USERPROFILE%\projets\aria-local-sync\scripts
.\setup-daily-vault.ps1
# Copier le SECRET MAITRE dans Bitwarden (goldenfar-vault-master)
.\rotate-daily-vault.ps1
.\setup-daily-vault-task.ps1
```

Sur l'**autre PC** : meme secret maitre (Bitwarden) → copier dans  
`%USERPROFILE%\projets\aria-local-sync\.vault-master-secret`  
ou variable `GOLDENFAR_VAULT_MASTER`.  
**Ne pas** relancer `setup-daily-vault.ps1` sur l'autre PC (ca creerait un secret different).  
Guide pas a pas : **[SETUP-AUTRE-PC.md](SETUP-AUTRE-PC.md)** (etapes 2 et 3 + TOTP).

`apply-local.ps1` essaie automatiquement la cle d'**aujourd'hui**, **hier** et **avant-hier** (fuseaux horaires).

### Scenario attaque (1 semaine)

1. Lundi : attaquant vole le `.gfv` + tente de le casser.
2. Mardi 03:00 : nouveau `.gfv` sur GitHub, **autre cle**.
3. La copie de lundi ne sert plus pour le fichier actuel.
4. Sans le **secret maitre**, chaque jour = une nouvelle enigme de chiffrement.

---

## Google Authenticator ≠ rotation des clés API

| Google Authenticator | Clés Render / X / GitHub |
|---------------------|---------------------------|
| Code **6 chiffres** qui change **toutes les 30 s** | Clé **fixe** jusqu'à ce que **tu** la régénères |
| Prouve que **c'est toi** qui te connectes | Autorise **l'API** à agir pour ARIA |

Les clés API **ne peuvent pas** tourner seules comme un TOTP — ce n'est pas le même mécanisme.

## Ce que nous avons mis en place

### 1. Coffre chiffré sur GitHub (`.gfv`)

- Fichier illisible sans mot de passe long (`GOLDENFAR_VAULT_SYNC_PASS`).
- Repo privé + chiffrement AES.

### 2. TOTP (Google Authenticator)

**Quand tu es demandé ?** Uniquement si **tu** lances à la main :

- `collect-local.ps1` (export vers GitHub)
- `apply-local.ps1` (restaure sur l'autre PC)

**Pas de prompt** pour : `test-vault-security.ps1`, rotation nocturne 03h00 (`rotate-daily-vault.ps1` — PC de confiance).

Le code Authenticator change toutes les **30 secondes** (pas chaque jour).  
La rotation **quotidienne** = autre mécanisme (clé dérivée de la date + secret maître).

### Session Git TOTP (12 h)

Au **debut de session Grok** (`session-handoff.ps1`) :

1. Si pas de session valide → **code Google Authenticator** (ou Telegram `-ViaAria`)
2. Session **12 h** : `git pull` / `git push` via scripts GoldenFar
3. Taches auto **03h00** / `watch-vault-sync` : exemptees (`SkipGitGate`)

Push fin de session : `push-session-manifest.ps1 -ViaAria`

### Audit GitHub (chaque session)

`audit-github-security.ps1` analyse les commits locaux (auteurs, vault hors horaire, secrets dans l'historique).  
Resultat : `SESSION-START.md`, checklist HTML, `%LOCALAPPDATA%\GoldenFar\github-audit-latest.json`

Regles : `security/github-trust.yaml`

**Alerte Telegram** : uniquement **critical origine/IP** (`ip_changed_vault`, `unknown_machine_vault`, `github_foreign_actor`, `vault_untrusted_origin`).  
Chaque PC enregistre son IP publique (`report-machine-ip.ps1` + `sessions/<machine>/ip-latest.json`).  
Anti-spam 6 h. Canal : API `/api/aria/operator/notify` ou `TELEGRAM_BOT_TOKEN`.

### Pont Telegram (ARIA)

Si le terminal n'est pas interactif (Cursor/Grok), ARIA demande le code sur **Telegram** :

```powershell
.\scripts\apply-local.ps1 -ViaAria
.\scripts\collect-local.ps1 -SkipMetier -SkipIde -ViaAria
.\scripts\simulate-interactive.ps1
```

Prerequis : `ADMIN_API_SECRET` dans le coffre (Bitwarden `goldenfar-admin-api`).  
Guide autre PC : **[SETUP-AUTRE-PC.md](SETUP-AUTRE-PC.md)**.

Flux : script → API Render → message Telegram → tu reponds 6 chiffres → ARIA transmet au PC.

Double verrou **avant** export/import manuel du coffre :

```powershell
cd %USERPROFILE%\projets\aria-local-sync\scripts
.\setup-totp-vault.ps1
```

- Ajoute le secret dans **Google Authenticator** (compte « GoldenFar Vault »).
- Ensuite `collect-local` et `apply-local` demandent le **code à 6 chiffres** en plus du mot de passe chiffré.

→ Même avec le `.gfv` + mot de passe, il faut **ton téléphone** (TOTP).

### 3. Sync auto quand le coffre change

Sur le **PC source** (avec mot de passe en variable Windows) :

```powershell
# Une fois : variable utilisateur (Paramètres Windows > Variables d'environnement)
# GOLDENFAR_VAULT_SYNC_PASS = <mot de passe Bitwarden>

cd %USERPROFILE%\projets\aria-local-sync\scripts
.\watch-vault-sync.ps1
```

Quand tu modifies `production.env` ou une clé → nouveau `.gfv` → `git push` automatique.

### 4. Surveillance hebdomadaire des clés

```powershell
cd %USERPROFILE%\projets\aria-vanguard\operator
.\setup-key-health-task.ps1
```

Chaque lundi : vérifie Render vs coffre vs `/api/health`. Si une clé est morte ou désynchronisée, tu le vois dans l'historique de la tâche planifiée.

## Rotation manuelle (quand une clé expire)

| Service | Où régénérer | Puis |
|---------|--------------|------|
| Render | dashboard.render.com | Mettre à jour `vault\keys\render.api-key` |
| X API | developer.x.com | Mettre à jour les 4 clés dans `production.env` |
| GitHub PAT | github.com/settings/tokens | `GITHUB_TOKEN` dans `production.env` |
| Groq | console.groq.com | `LLM_API_KEY` |

Après chaque changement :

```powershell
cd aria-vanguard\operator
.\sync-render.ps1          # prod Render
.\sync-local.ps1           # dev local
cd ..\..\aria-local-sync\scripts
.\collect-local.ps1        # met a jour .gfv sur GitHub
```

## Résumé

| Besoin | Solution |
|--------|----------|
| Empêcher lecture des clés sur GitHub | `.gfv` chiffré + repo privé |
| Comme Authenticator (2e facteur) | `setup-totp-vault.ps1` |
| Actualiser l'autre PC quand tu changes une clé | `watch-vault-sync.ps1` ou `collect-local` + `git push` |
| Détecter une clé morte | `setup-key-health-task.ps1` |
| Clés qui tournent seules toutes les 30 s | **Impossible** (pas le modèle des API) |