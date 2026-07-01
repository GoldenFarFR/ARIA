# aria-skills

Grok / Cursor skills for the **Aria / GoldenFar** ecosystem — reusable agent workflows, not app code.

Aligned with [Aria VISION](https://github.com/GoldenFarFR/aria-vanguard/blob/main/VISION.md): skills are a distribution moat (Grok, MCP, plugins).

**Repo map (SSOT):** [aria-vanguard/docs/ECOSYSTEM-REPOS.md](https://github.com/GoldenFarFR/aria-vanguard/blob/main/docs/ECOSYSTEM-REPOS.md) — où vit chaque repo GoldenFar.

**GitHub : repo privé** — visible pour les collaborateurs autorisés, pas ouvrable en public (pas de clone/browse sans accès).

## Structure

```
aria-skills/
└── .grok/skills/
    └── <skill-name>/
        ├── SKILL.md          # required — agent instructions + YAML frontmatter
        ├── scripts/          # optional — helper scripts
        └── references/       # optional — docs the agent can read
```

Each skill folder name = lowercase, hyphens, 2–64 chars (e.g. `dexpulse-analyze`, `aria-marketing`).

## Skills included

| Skill | Scope | Role |
|-------|-------|------|
| `vision-enforcer` | **Always on** | Read `VISION.md` before any change; founder mode; moat & autonomie Aria |
| `journal-de-bord` | **Always on** | Horodate chaque action IDE (fichier, repo, commit) dans `collegue-memoire/JOURNAL.md` |
| `marketing-decision-framework` | Marketing / growth | 6-point framework before any post, narrative, or com strategy |
| `graft-mini-games` | Sites holding / vitrine | Mini-jeux sur accueil — poster fixe + preview animée au survol (`/graft-mini-games`) |

## Add a skill

1. Create `.grok/skills/<name>/SKILL.md` (see `_template/SKILL.md`).
2. Commit and push.
3. **Project scope**: open this repo in Grok — skills load from `.grok/skills/`.
4. **User scope** (all projects): run `scripts/install.ps1` to link into `~/.grok/skills/`.

## Install (Windows)

```powershell
.\scripts\install.ps1
```

Links every skill under `.grok/skills/` into `%USERPROFILE%\.grok\skills\` (junction, no copy).

## Related repos

| Repo | Role |
|------|------|
| [aria-vanguard](https://github.com/GoldenFarFR/aria-vanguard) | Holding + API + DEXPulse app |
| [aria-sandbox](https://github.com/GoldenFarFR/aria-sandbox) | Aria brain |