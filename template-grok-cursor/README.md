# template-grok-cursor

Template GitHub pour démarrer chaque nouveau projet **Aria-ready** avec **Grok Build** et **Cursor** — réponses toujours en **français**.

## Règles incluses (always-on)

| Fichier | Rôle |
|---------|------|
| `.cursor/rules/francais.md` | Langue française |
| `.cursor/rules/vision.md` | Alignement vision Aria (SSOT `aria-vanguard`) |
| `.cursor/rules/conventions.md` | Style de code |
| `.cursor/rules/skills-on-demand.md` | Skills chargées uniquement sur demande |
| `.grok/rules/vision-enforcer.md` | Garde-fou vision côté Grok |

## Contenu

```
.
├── .cursor/rules/          # Règles Cursor (copiées dans chaque nouveau repo)
├── .grok/
│   ├── rules/              # vision-enforcer, skills-on-demand
│   ├── references/
│   │   ├── site-fonctionnel.md    # Blueprint site holding lambda
│   │   ├── ecosystem-repos.md     # Carte GoldenFar 2026
│   │   ├── aria-backend-patterns.md
│   │   └── scaffold/              # Site Vite/React + Privy à copier
│   └── skills/
│       ├── install-skill/         # Audit avant ajout skill au template
│       └── spawn-holding-site/    # Création site holding (à la demande)
├── scripts/
│   └── link-aria-skills.ps1       # Lier aria-skills sur le PC
├── VISION.md               # Stub → aria-vanguard/VISION.md
├── AGENTS.md
└── README.md
```

## Écosystème GoldenFar / Aria (2026)

**Vision SSOT :** [aria-vanguard/VISION.md](https://github.com/GoldenFarFR/aria-vanguard/blob/main/VISION.md)  
**Carte repos :** [aria-vanguard/docs/ECOSYSTEM-REPOS.md](https://github.com/GoldenFarFR/aria-vanguard/blob/main/docs/ECOSYSTEM-REPOS.md)  
**Référence locale :** [`.grok/references/ecosystem-repos.md`](.grok/references/ecosystem-repos.md)

| Repo | Rôle |
|------|------|
| `aria-vanguard` | Holding + API ARIA + DEXPulse + scripts `operator/` |
| `aria-sandbox` | Cerveau `aria-core` (runtime pip) |
| `aria-skills` | Skills Grok/Cursor métier (SSOT) |
| `template-grok-cursor` | Ce template |
| `collegue-memoire` | Mémoire opérateur (multi-PC) |
| ~~`dexpulse`~~ | *Déprécié* — migré dans `aria-vanguard` |

Secrets : coffre `%LOCALAPPDATA%\GoldenFar\vault` — **jamais** dans le repo.

## Skills du template

| Skill | Invocation | Rôle |
|-------|------------|------|
| `spawn-holding-site` | `/spawn-holding-site <nom>` | Site lambda Privy + Render |
| `install-skill` | `/install-skill` | Auditer une skill avant ajout au template |

Toutes ont `disable-model-invocation: true` — pas de chargement automatique. Voir `.grok/rules/skills-on-demand.md`.

**Skills métier** (marketing, vision, deploy, mini-jeux) : repo [aria-skills](https://github.com/GoldenFarFR/aria-skills) — ne pas les dupliquer ici.

## Nouveau projet (à chaque fois)

1. GitHub → [template-grok-cursor](https://github.com/GoldenFarFR/template-grok-cursor) → **Use this template** → **Create a new repository**
2. Cloner le nouveau dépôt dans `%USERPROFILE%\projets\`
3. Ouvrir dans Cursor — règles FR + vision déjà actives
4. Site holding ? → `/spawn-holding-site <nom>` (lit `site-fonctionnel.md` + scaffold)

## Setup PC opérateur (une fois)

```powershell
# Skills métier Aria (vision-enforcer, marketing, operator-runbook, …)
cd $env:USERPROFILE\projets\template-grok-cursor
.\scripts\link-aria-skills.ps1

# Écosystème complet (optionnel)
cd $env:USERPROFILE\projets\aria-vanguard\operator
.\new-pc.ps1
```

## Configuration globale IDE (optionnel)

| Fichier template | Copier vers |
|------------------|-------------|
| `.cursor/rules/francais.md` | `~/.cursor/rules/francais.md` |
| `.grok/AGENTS.md` | `~/.grok/AGENTS.md` |

## Blueprint site lambda

[`.grok/references/site-fonctionnel.md`](.grok/references/site-fonctionnel.md) — accueil + Privy + Render.  
Exemples enrichis : `aria-vanguard`, `kikou`.

Deploy : `aria-vanguard/operator/setup-holding-render.ps1 -SiteName <nom> -UpdateCors`

## Ajouter un skill au template

1. Envoyer la source à Grok dans ce dépôt
2. Lancer `/install-skill` (audit sécurité, doublons, tests)
3. Checklist : `.grok/skills/install-skill/references/checklist.md`

## Licence

Libre d'utilisation — adaptez ce template à vos besoins.