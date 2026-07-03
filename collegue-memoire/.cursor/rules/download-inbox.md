---
description: Boîte download/ — traiter dépôts Sylvain à chaque session
alwaysApply: true
---

# Download inbox (ARIA)

**Chemin :** `%ARIA_REPO_ROOT%\download\`

## Début de session (après ARIA-WORKER)

1. `download\triage-inbox.ps1` — s'il y a des fichiers en attente, **traiter avant** la demande courante
2. Lire `download\INBOX-STATE.json` pour éviter re-traitement
3. Triage par type → action (voir `download\README.md`)
4. Fichiers traités → `download\processed\` · invalides → `download\rejected\`
5. Mettre à jour `INBOX-STATE.json` + append `JOURNAL.md`

## Rejets automatiques

- `.url` avec `blob:` (Messenger, navigateur) — demander réexport fichier réel
- Fichiers vides ou corrompus

## Ne pas committer

Le contenu déposé est gitignoré ; seuls README, INBOX-STATE, scripts sont versionnés.