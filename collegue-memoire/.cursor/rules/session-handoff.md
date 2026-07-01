---
description: Debut de session — handoff auto (Sylvain ne dit rien)
alwaysApply: true
---

# Session handoff (multi-PC) — automatique

**Sylvain ne lance rien** au demarrage — pas besoin de « lis le github ».

**Avant toute reponse** :

1. Executer `projets\aria-local-sync\scripts\session-handoff.ps1`  
   (clone repos + bootstrap nouveau PC via `ensure-pc-ready.ps1` si besoin)
2. Lire `collegue-memoire\sessions\HANDOFF.md` + `SESSION-START.md` + `boot-status.json`
3. Lire `sessions\CONSOMMATION-GROK.md` — mode concis (moins de tokens)
4. Lire `COLLEGUE.md` + fin de `JOURNAL.md`
5. Lire `sessions\ARIA-WORKER.md` — traiter tous les `[pending]` (ouvrier ARIA)
6. Nouveau PC : secrets Bitwarden seulement si absents, sinon `bootstrap-autre-pc.ps1`
7. Resumer delta autre PC en 3-5 lignes puis repondre (concis)

Fin de session : `collect-session.ps1` + `push-session-manifest.ps1 -ViaAria`

Ne jamais demander a Sylvain de rappeler le handoff.