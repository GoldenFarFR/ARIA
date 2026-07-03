# Leçons en attente — validation Sylvain

> Généré par ARIA-Critique (Letta-2). Valider puis ship via `apply-pending-lessons.ps1`.

## Workflow

1. `run-letta2-critique.ps1` — génère des leçons ci-dessous (auto en fin `collect-session`)
2. `apply-pending-lessons.ps1 -List` ou KART `/apply-lessons list`
3. `apply-pending-lessons.ps1 -Approve 1` ou `/apply-lessons approve 1` — **validation humaine obligatoire**
4. `apply-pending-lessons.ps1 -ApplyApproved` — ship vers aria-core (auto en fin `collect-session` si leçons `approved`)

Cibles **Ship core** : `reflection` · `pitfall` · `COLLEGUE` · `skill_route` · `defer`

---

### Leçon — Anti-Ollama fallback (exemple)
- **Constat** : Ollama répond hors-sujet après Groq 429 sur requêtes ACP
- **Tu as fait X** : fallback Ollama par défaut dans ouvrier_runner
- **Mieux** : Groq seul + message quota ; Ollama uniquement si `ARIA_OUVRIER_FALLBACK=ollama`
- **Ship core** : reflection
- **Statut** : done