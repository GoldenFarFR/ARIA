# ARIA Letta v2.4 — guide opérateur

Point d'entrée unique pour l'orchestrateur multi-agents local (Letta 0.6.7 + Ollama).

## Prérequis

- Python 3.12 (`py -3.12`)
- Ollama avec modèles : `qwen2.5:14b`, `qwen2.5:32b`, `aria-qwen32b:latest`, `nomic-embed-text`
- Coffre GoldenFar : `%LOCALAPPDATA%\GoldenFar\vault\local.env`

## Installation (une fois)

```powershell
cd %ARIA_REPO_ROOT%\letta-orchestrator
.\install.ps1
.\create_agents.py   # si agents_config.json absent
```

## Usage quotidien

| Commande | Rôle |
|----------|------|
| `.\start-letta.ps1` | Démarre le serveur Letta (:8283) |
| `.\orchestrate.ps1 -Message "..."` | Routage auto (classification + cascade) |
| `.\orchestrate.ps1 -Niveau complexe -Message "..."` | Force le niveau |
| `.\smoke-complex.ps1 -Quick` | Validation 3 scénarios |
| `aria-letta status` | État serveur, agents, modèles (profil PS) |
| `aria-letta "message"` | Même chose via profil PowerShell |
| `aria` puis `/letta [simple\|moyen\|complexe] message` | Shell ARIA |

## Routage visible

Chaque requête affiche un bandeau `═══ ARIA ROUTING ═══` :

- **Niveau** : `simple` / `moyen` / `complexe` + source (`heuristique`, `qwen`, `forcé`)
- **Agent** : Scout → Grok → Core (cascade si échec)
- **Modèle** : lu depuis `models_config.json`
- **Durée** et **escalades**

Ligne machine : `ARIA_ROUTING_JSON={...}` (stderr) pour scripts / smoke tests.

## Fichiers clés

```
letta-orchestrator/
  orchestrate.py          # Routage + classification
  orchestrate.ps1         # Wrapper coffre GoldenFar
  aria_config.py          # Chemins monorepo, modèles
  create_agents.py        # Création agents Letta
  start-letta.ps1         # Serveur local
  smoke-complex.ps1       # Tests régression
  agents_config.json      # IDs agents (gitignored)
  models_config.json      # Modèles actifs
```

Intégration profil : `scripts/PowerShell/aria-letta-integration.ps1` (branché via `link-aria-profile.ps1`).

## ARIA-Ouvrier (copie conforme Cursor/Grok)

Même rôle que l'ouvrier Grok : handoff, ARIA-WORKER, download/, outils shell/fichiers, journal, build-local — **en local** (économie jetons Cursor).

### Installation (une fois)

```powershell
cd %ARIA_REPO_ROOT%\letta-orchestrator
.\start-letta.ps1
.\setup_ouvrier.py
```

### Usage

```powershell
# Langage naturel uniquement — pas de commandes à préciser
.\orchestrate-ouvrier.ps1 -Message "y'a des trucs dans download, occupe-toi en"
.\orchestrate-ouvrier.ps1 -Message "le CI passait pas sur le truth ledger, c'est réglé ?"
```

L'ouvrier **déduit** l'intention, **exécute** seul, et **demande confirmation** seulement s'il doute ou si c'est risqué.

| Composant | Rôle |
|-----------|------|
| `ouvrier_persona.md` | Règles ouvrier (SSOT) |
| `ouvrier_tool_sources.py` | 8 outils Letta (read/write, pwsh, handoff, journal…) |
| `orchestrate_ouvrier.py` | Bootstrap handoff + inbox puis agent |

**Limite honnête :** modèle local/cloud Letta < Grok Cursor sur grosses refactors multi-fichiers — même outils, intelligence variable.

## Mémoire vivante (Sprints 1–4)

Boucle auto-amélioration : **Groq exécute · aria-core retient · Letta archive · Letta-2 critique · apply ship**.

| Étape | Script | Quand |
|-------|--------|-------|
| Préflight mémoire ouvrier | `ouvrier_memory.py` | Chaque tour KART |
| Sync core → archival | `sync-core-to-letta.ps1` | Fin `collect-session` |
| Critique méta | `run-letta2-critique.ps1` | Fin `collect-session` |
| Apply leçons validées | `apply-pending-lessons.ps1 -ApplyApproved` | Fin `collect-session` (si `approved`) |

**KART :** `/apply-lessons list` · `/apply-lessons approve N` · `/apply-lessons apply N` · `/apply-lessons approved`

Cibles **Ship core** : `reflection` · `pitfall` · `COLLEGUE` · `skill_route` · `defer`

Vault local recommandé : `ARIA_OUVRIER_CLOUD=groq`, `ARIA_VECTOR_MEMORY=true`, `ARIA_MEMORY_ARBITRATOR=true`

## Dépannage

| Symptôme | Action |
|----------|--------|
| Port 8283 occupé | Normal si Letta déjà up — `aria-letta status` |
| Réponse vide | Vérifier Ollama ; relancer `create_agents.py` |
| Classification toujours `moyen` | Ollama injoignable pour Qwen classifier |
| Legacy tool-calling PS | `$env:ARIA_AGENT_LEGACY = "1"` |

## Legacy vs Letta

Par défaut `Invoke-AriaAgent` utilise Letta. Pour l'ancien orchestrateur PowerShell :

```powershell
$env:ARIA_AGENT_LEGACY = "1"
```