---
name: session-handoff
description: >
  Debut et fin de session Grok/Cursor multi-PC : handoff automatique via GitHub.
  Toujours actif — premiere action de session, derniere action utile.
  Triggers : session, handoff, autre PC, demarrage, lis le github, met toi a jour,
  /session-handoff.
metadata:
  short-description: "Auto handoff multi-PC au demarrage Grok"
  always-on: true
---

# Session handoff (multi-PC) — 100 % automatique

## Regle d or

**Sylvain ne lance rien et ne dit rien** au demarrage (pas meme « lis le github »).
Toi (Grok/Cursor) tu executes le handoff **avant la premiere phrase de reponse**.

## Premiere action — CHAQUE session

Executer soi-meme, dans l'ordre :

1. **Shell** : `%USERPROFILE%\GitHub-Repos\aria-ops\local-sync\scripts\session-handoff.ps1`  
   (ou `%ARIA_REPO_ROOT%\scripts\session-handoff.ps1` redirect)  
   Si `aria-ops` absent : cloner `GoldenFarFR/aria-ops` (prive) a cote de `ARIA`.
   **TOTP** : si `[TOTP_REQUIRED]` ou session Git expiree → **demander a Sylvain dans le chat** les 6 chiffres Google Authenticator (GoldenFar Vault), puis relancer avec `-TotpCode` (pas Telegram/ARIA).
2. **Lire** (obligatoire) :
   - `%ARIA_OPS_ROOT%\collegue-memoire\sessions\HANDOFF.md` (SSOT GitHub)
   - `%ARIA_OPS_ROOT%\collegue-memoire\SESSION-START.md` (genere local)
   - `%ARIA_OPS_ROOT%\collegue-memoire\sessions\<MACHINE>\boot-status.json` (nouveau PC ?)
   - `sessions/CONSOMMATION-GROK.md` (mode concis — moins de tokens)
   - `COLLEGUE.md` + fin de `JOURNAL.md`
3. **Nouveau PC** (`boot-status.json` → `is_new_pc: true`) :
   - Si `has_vault_secrets: false` : demander a Sylvain **une seule fois** les 2 lignes Bitwarden (master + TOTP) — rien d autre.
   - Si secrets OK : `bootstrap-autre-pc.ps1 -SkipHandoff` puis `check-aria-status.ps1`
   - Ajouter la machine dans `aria-local-sync\security\github-trust.yaml` → `known_machines`
4. **PC connu** : appliquer handoff (`git pull`, `apply-local.ps1 -TotpCode` si coffre change sur l autre PC).
5. Resumer en 3-5 lignes (delta autre PC) **puis** repondre a la demande.
6. Checklist : `SESSION-CHECKLIST.html` ou `open-checklist.ps1`

Ne jamais dire « lance session-handoff » a Sylvain.

## Phrases declencheurs (meme effet)

- « lis le github et met toi a jour »
- « autre PC » / « nouveau PC »
- debut de session sans phrase

Toujours executer le handoff — la phrase humaine est optionnelle.

## Derniere action — fin de session utile

1. `collect-session.ps1`
2. `push-session-manifest.ps1` (jamais `-ViaAria` — TOTP Telegram desactive). `-TotpCode` si session Git expiree.
3. `COLLEGUE.md` si decision metier

## Fichier SSOT

`GoldenFarFR/collegue-memoire` → `sessions/HANDOFF.md`