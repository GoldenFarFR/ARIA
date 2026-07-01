# Runbook operateur ARIA / GoldenFar

> **SSOT humain** — les agents (Cursor, Grok, ARIA) lisent la version machine dans
> `aria-sandbox/packages/aria-core/src/aria_core/knowledge/operator_pitfalls.yaml`.
> Modifier les deux quand une nouvelle erreur est corrigee.

## Audit en 30 secondes

```powershell
cd projets\aria-vanguard\operator
.\check-aria-status.ps1
```

## Build local + deploy (politique 2026-06)

**Regle :** valider en local ; **1 seul** redeploy Render quand le lot est pret (~2 min quota/build).

```powershell
.\build-local.ps1              # pip + import + npm (apres chaque changement code)
.\deploy-render.ps1 -Reason "pin aria-core + fix X"   # build local + sync + redeploy + health
```

| Script | Quand |
|--------|--------|
| `build-local.ps1` | Toujours apres modif backend/frontend/pin |
| `deploy-render.ps1 -Reason` | Agent decide : fix prod, pin aria-core, secret critique |
| `sync-render.ps1 -SkipRedeploy` | Vars Render seulement (quota epuise) |
| `sync-render.ps1` | Eviter en rafale — preferer deploy-render |

GitHub Actions : CI sur **PR + manuel** seulement (plus sur push main). Budget 0 $ = OK.

## Apres toute modif de secret

```powershell
.\deploy-render.ps1 -Reason "rotation secret X"
.\sync-vanguard.ps1    # static holding — redeploy AUTO
.\sync-local.ps1       # dev local = secrets prod fusionnes
```

## Nouveau PC ou nouveau compte GitHub

1. `.\new-pc.ps1` ou cloner repos (voir `COLLEGUE.md` § Analyse installation)
2. Coffre : `%LOCALAPPDATA%\GoldenFar\vault` (Syncthing ou `import-vault-encrypted.ps1`)
3. `winget install Syncthing.Syncthing` + `.\setup-syncthing-vault.ps1` (multi-PC)
4. `.\check-aria-status.ps1` — tout doit etre vert
5. Copier regles Cursor depuis `collegue-memoire\.cursor\rules\`

## Nouvel agent de codage (Cursor, Grok, autre)

L'agent **doit** au debut de chaque session :

1. `git pull` sur `collegue-memoire` → lire `COLLEGUE.md`
2. Lire `operator_pitfalls.yaml` (ou skill `/operator-runbook`)
3. Ne jamais annoncer deploy/commit sans preuve skill ou health

## Pièges qui ont coute du temps (2026-06)

| Id | Symptome | Cause | Fix |
|----|----------|-------|-----|
| env-sync-no-redeploy | `/x status` = non alors que Render OK | Process pas redemarre | `deploy-render.ps1` (redeploy) |
| render-pipeline-minutes | build_failed <1s | Quota pipeline epuise | `build-local` + attendre reset / Starter |
| x-keys-never-in-vault | Tweet X visible mais API non | Cles jamais dans coffre | 4 OAuth + sync + `/x status` |
| comms-posted-false-positive | « Publie sur X » sans tweet | `posted:true` sur brouillon | URL status obligatoire |
| llm-fake-deploy | « Site deploye » invente | LLM sans skill | `holding_site` + `technical_claims` |
| local-bot-stale-env | Fausses reponses Telegram | `.env` local vide | `sync-local.ps1` |

## Quand ARIA code elle-meme

- Ecriture libre : `aria-sandbox`, `aria-token-base`
- Ecriture prod : `aria-vanguard`, `dexpulse` — via skill GitHub ou operateur
- Apres changement `aria-core` : `bump-aria-core-pin.ps1` + redeploy dexpulse
- Ajouter une lecon : editer `operator_pitfalls.yaml` + proposition `/learn` a l'operateur

## Telegram

- `/x status` — X API
- `/github status` — GitHub
- « runbook » ou « nouveau pc » — memoire pièges