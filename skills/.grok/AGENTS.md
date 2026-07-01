# aria-skills — agent instructions

**Every task:** read `VISION.md` at repo root, then `dexpulse/VISION.md` for ecosystem SSOT.

## This repo

- Add or edit skills only under `.grok/skills/<name>/`.
- Follow Grok skill format: YAML frontmatter (`name`, `description`) + actionable markdown body.
- Use `_template/SKILL.md` when creating a new skill.
- Do not commit secrets, API keys, or user-specific paths.

## Conventions

- Skill names: `[a-z0-9-]`, 2–64 chars.
- Prefer references/ and scripts/ over bloated SKILL.md bodies.
- Match tone and structure of existing skills in this repo.