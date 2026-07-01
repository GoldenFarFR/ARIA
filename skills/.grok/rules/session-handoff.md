# Mandatory — session handoff multi-PC (always on)

Skill SSOT : `aria-skills/.grok/skills/session-handoff/SKILL.md`

**Sylvain ne dit rien au demarrage** — handoff 100 % agent.

**Avant toute reponse** :

1. `projets\aria-local-sync\scripts\session-handoff.ps1` (ensure-pc-ready inclus)
2. Lire `HANDOFF.md` + `SESSION-START.md` + `boot-status.json` + `COLLEGUE.md` + `JOURNAL.md`
3. Nouveau PC : Bitwarden x2 si manquant, sinon bootstrap auto
4. Resumer delta puis repondre

Triggers optionnels : « lis le github », « met toi a jour », « autre PC ».

Fin session : `collect-session.ps1` + `push-session-manifest.ps1`.