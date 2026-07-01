# COLLEGUE — mémoire Sylvain × assistant IA

> **Un seul fichier**, synchronisé via **GitHub** sur tous vos PC.  
> Monorepo : `GoldenFarFR/ARIA` (privé) — ce fichier vit dans `collegue-memoire/COLLEGUE.md`  
> Chemin local : `%ARIA_REPO_ROOT%\collegue-memoire\COLLEGUE.md` (défaut : `GitHub-Repos\ARIA`)

---

## Qui

- **Sylvain Rio** — ingénieur structure **Doursat**
- Sujets : **DDC** Robot, stabilité 3D, synthèses Excel, outils métier
- Langue : **français** toujours

---

## Comment on travaille

1. Besoin **métier** d'abord
2. Outil **prêt à l'emploi** (Excel + script générateur)
3. Tester (formules visibles, recalcul Excel)
4. **Avant chaque session** : `git pull` sur le repo **ARIA** puis lire ce fichier (règle automatique — pas de rappel de Sylvain)
5. Mettre à jour **ce fichier** + `git commit` + `git push` sur `GoldenFarFR/ARIA` après session utile

### Consommation Grok / Cursor (mode concis)

> SSOT : `sessions/CONSOMMATION-GROK.md` — lu automatiquement au handoff.

- Réponses **concises** : plan minimal + code + exécution (pas de blabla)
- Tâche grosse ou floue → **Plan Mode** avant de coder
- Contexte long → nouvelle session ou résumé d'état
- Prompts types dans `CONSOMMATION-GROK.md` (copier-coller)

---

## Préférences livrables

- Police **Aptos 11** ; en-têtes `FX` / `FY` / `FZ` / `Nom_cas` → **gras**
- Excel **natif** (MINIFS, MAXIFS)
- **2 tableaux côte à côte** : gauche **avec FY** (A–F), droite **sans FY** (I–M), **G–H = espace**
- Un seul tableau rempli selon l'export Robot
- Synthèse par **nœud** ; FZ > 0 soulèvement, FZ < 0 compression
- Pas de filtre nœud manuel, pas de colonne Famille, toggles non demandés

---

## Produits

### Calculateur DDC Excel

| Élément | Chemin |
|---------|--------|
| Excel | `Downloads\DDC - Calculateur v7b.xlsx` (local, par PC) |
| Générateur | `collegue-memoire/projets/ddc/ddc_calculateur.py` (dans le monorepo ARIA) |

**Règles DDC** : synthèse par famille ; mots-clés `Nom_cas` : `Vent G/D`, `Vent D/G`, `Neige`, `accidentel`, `Couverture`, `G :`.

---

## Nouveau PC oublié ?

Si le clone n'est pas fait, l'assistant **rappelle tout seul** au début de session :

```powershell
git clone https://github.com/GoldenFarFR/ARIA.git "%USERPROFILE%\GitHub-Repos\ARIA"
[System.Environment]::SetEnvironmentVariable("ARIA_REPO_ROOT", "%USERPROFILE%\GitHub-Repos\ARIA", "User")
# Cursor (optionnel, plus tard) :
copy "%USERPROFILE%\GitHub-Repos\ARIA\collegue-memoire\.cursor\rules\*.md" "%USERPROFILE%\.cursor\rules\"
# Grok :
copy "%USERPROFILE%\GitHub-Repos\ARIA\collegue-memoire\.grok\rules\*.md" "%USERPROFILE%\.grok\rules\"
cd "%USERPROFILE%\GitHub-Repos\ARIA\skills\scripts"
.\install.ps1
```

## Analyse installation GoldenFar / ARIA (multi-PC)

> **Règle assistant** : en début de session sur un PC inconnu ou après demande setup, **analyser et proposer d'installer** tout ce qui manque ci-dessous — sans attendre que Sylvain rappelle. Exécuter soi-même quand possible.

### 1. Système Windows

| Élément | Pourquoi | Install |
|---------|----------|---------|
| **Git** | clones, push | `winget install Git.Git` |
| **Python 3.11+** | aria-core, dexpulse, tests | `winget install Python.Python.3.12` |
| **Node.js LTS** | aria-vanguard (build) | `winget install OpenJS.NodeJS.LTS` |
| **BitLocker** | coffre + disque | Paramètres Windows |
| **gh** (optionnel) | repos GitHub CLI | `winget install GitHub.cli` |

### 2. IDE — Grok (Cursor optionnel plus tard)

| Élément | Chemin / action |
|---------|-----------------|
| **Monorepo ARIA** | clone + `git pull` + lire ce fichier |
| **Variable** | `ARIA_REPO_ROOT` → racine du clone (ex. `GitHub-Repos\ARIA`) |
| **Règles Grok** | `ARIA\collegue-memoire\.grok\rules\` → `%USERPROFILE%\.grok\rules\` |
| **Skills** | `ARIA\skills\scripts\install.ps1` |
| **Règles Cursor** (si utilisé) | `ARIA\collegue-memoire\.cursor\rules\` → `%USERPROFILE%\.cursor\rules\` |

### 3. Monorepo GitHub `GoldenFarFR/ARIA` (privé — un seul clone)

| Dossier | Rôle (ex-repo) |
|---------|----------------|
| `collegue-memoire/` | **Ce fichier** + HANDOFF + JOURNAL |
| `packages/aria-core/` | Cerveau Python ARIA |
| `vanguard/` | API, holding, DEXPulse, `operator/` |
| `local-sync/` | Sync multi-PC, coffre `.gfv` |
| `skills/` | Skills Grok/Cursor |
| `sandbox/`, `memory/`, `bot/` | Expériences et travail local |

Script opérateur : `vanguard\operator\new-pc.ps1` — handoff : `local-sync\scripts\session-handoff.ps1`

### 4. État local sync (`local-sync/`)

| Action | Commande |
|--------|----------|
| **PC source** (données à jour) | `%ARIA_REPO_ROOT%\local-sync\scripts\collect-local.ps1` puis `git push` sur ARIA |
| **Autre PC** | **Rien à lancer** — Grok fait `session-handoff.ps1` seul. Secours : `local-sync\CHANGEMENT-PC-MAINTENANT.md` |
| Inventaire sans copie | `local-sync\scripts\inventory.ps1` |

Contenu : `sync/aria-data/`, règles IDE, Excel DDC, **`sync/vault/goldenfar-vault.gfv`** (coffre chiffré = toutes les clés). Mot de passe identique sur les 2 PC (Bitwarden).

### 5. Coffre secrets (hors Git, hors `projets/`)

| Élément | Emplacement |
|---------|-------------|
| **Coffre** | `%LOCALAPPDATA%\GoldenFar\vault` |
| **Variable** | `GOLDENFAR_VAULT` (profil utilisateur) |
| **Contenu** | `production.env`, `vanguard.env`, `local.env`, `keys\render.api-key`, `keys\ionos.api-key`, `stripe\recovery-codes.txt` |

Setup : `vanguard\operator\setup-vault.ps1`  
Guide : `vanguard\operator\VAULT-SECURITY.md`

### 6. Multi-PC — sync des clés (gratuit)

| Priorité | Outil | Script |
|----------|-------|--------|
| **1 — Auto** | **Syncthing** | `setup-syncthing-vault.ps1` — dossier `goldenfar-vault` |
| **2 — Logins** | **Bitwarden** (gratuit) | Comptes GitHub, IONOS, Stripe, Render ; passphrase `.gfv` |
| **3 — Secours** | Sauvegarde `.gfv` chiffrée | `export-vault-encrypted.ps1` / `import-vault-encrypted.ps1` |

Guide : `vanguard\operator\MULTI-PC-VAULT.md`

### 7. Cloud prod (déjà en ligne — pas sur le PC)

| Service | URL / rôle |
|---------|------------|
| **Render** | Static `ariavanguardzhc.com` (aria-vanguard) ; API `api.` → **service holding** (plus de service nommé DEXPulse) |
| **IONOS** | DNS `api` → Render (clé API dans coffre) |
| **Stripe / Telegram / Groq / X** | Secrets dans coffre → `sync-render.ps1` pousse vers Render |

Clé API Render locale : `vault\keys\render.api-key` (`rnd_...`)

### 8. Dev local (optionnel)

```powershell
cd %ARIA_REPO_ROOT%\vanguard\operator
.\build-local.ps1
.\sync-local.ps1
```

### 9. Comptes & sécurité GitHub

- **2FA / passkey** obligatoire sur github.com
- PAT fine-grained pour `GITHUB_TOKEN` Render (droits minimaux)
- Politique : `vanguard\operator\REPO-SECURITY.md`
- Ne jamais coller de secret dans Cursor / chat

### 10. Vérification finale (obligatoire)

```powershell
cd %ARIA_REPO_ROOT%\vanguard\operator
.\check-aria-status.ps1
```

Attendu : secrets coffre = Render, health `https://api.ariavanguardzhc.com/api/health` OK.

### 11. Checklist assistant début de session

1. `git pull` sur ARIA → lire ce § + `JOURNAL.md`
2. Coffre présent ? Sinon Syncthing / import `.gfv`
3. `check-aria-status.ps1` — lister manques
4. Proposer et **exécuter** installs manquantes (winget, clone, scripts)
5. Après modif : journal `JOURNAL.md` + commit mémoire si décision nouvelle

## Journal

| Date | Décision |
|------|----------|
| 2026-06-18 | DDC v7b : 2 tableaux côte à côte, notes FZ dynamiques |
| 2026-06-18 | Mémoire = **ce fichier sur GitHub** `GoldenFarFR/collegue-memoire` (pas OneDrive) |
| 2026-06-18 | Si setup absent sur un PC → assistant rappelle le clone automatiquement |
| 2026-06-19 | **Journal de bord** actions Aria → `JOURNAL.md` (horodaté, skill `journal-de-bord`) |
| 2026-06-19 | **ARIA** : zéro mention pairs ZHC en conversation (code/canonical) ; veille interne OK, concurrence silencieuse |
| 2026-06-19 | **`/directive`** = mandat positif qui rend ARIA meilleure (décider, livrer, communiquer) — pas interdictions ni rappels de config |
| 2026-06-19 | **Indice ARIA** : 6 axes (codage, social, intelligence, fiabilité, autonomie, business), nv 0→1000 — `/qi`, `/level up <axe>` Telegram |
| 2026-06-19 | **Split architecture** : cerveau = `aria-sandbox/packages/aria-core` ; `dexpulse` = hôte deploy + plugins marché uniquement |
| 2026-06-19 | **Tests cerveau** : 79 tests dans `aria-core/tests` ; setup PC = `aria-sandbox/scripts/setup-local.ps1` ; CI `check_no_drift.py` |
| 2026-06-19 | **Split clos** : pip aria-core pin SHA ; `bump-aria-core-pin.ps1` ; tests dexpulse 28/28 ; scripts migrate/sync obsolètes |
| 2026-06-19 | **ARIA site holding** : « lancer le site holding » = audit GitHub réel ; garde anti faux commit/deploy LLM |
| 2026-06-19 | **Runbook opérateur** : `operator_pitfalls.yaml` (ARIA + tests) + `OPERATOR-RUNBOOK.md` + `new-pc.ps1` + skill/règle `operator-runbook` — plus jamais oublier sync/redeploy |
| 2026-06-19 | **Noyau épistémique Phase A** : `epistemic_core.yaml` — croyances calibrées P(vrai/faux) avant LLM libre ; complète FAQ, Truth Ledger, triage Groq X ; Phase B = vérif web ciblée |
| 2026-06-19 | **Assistant IA** : Sylvain apprécie quand l'assistant **prend ses responsabilités** — propose, implémente et déploie sans attendre ; autonomie alignée vision Aria |
| 2026-06-19 | **Épistémique Phase B déployée** : vérif web si incertain, journal calibration, gate anti-hallucination, triage mémoire, `/calibrate`, replay heartbeat, curriculum exposition |
| 2026-06-19 | **Repos GitHub verrouillés** : les 8 repos GoldenFarFR sont **privés** ; politique dans `dexpulse-secrets/REPO-SECURITY.md` ; secrets = coffre uniquement |
| 2026-06-19 | **Coffre local** : secrets dans `%LOCALAPPDATA%\GoldenFar\vault` — plus dans `dexpulse-secrets` ; multi-PC = Syncthing + Bitwarden + `.gfv` |
| 2026-06-19 | **Assistant** : en début de session, **analyser et installer** tout le stack § « Analyse installation » sans attendre — Sylvain multi-PC |
| 2026-06-19 | **Migration API terminée** : `aria-vanguard` héberge backend + product-frontend ; Render `aria-api` déploie depuis `GoldenFarFR/aria-vanguard` ; health commit `7749f83`. |
| 2026-06-19 | **SSOT docs** : `VISION.md` + `docs/ECOSYSTEM-REPOS.md` déplacés vers `aria-vanguard` ; repo `dexpulse` marqué DEPRECATED — archivable. |
| 2026-06-19 | **Repo `dexpulse` supprimé** sur GitHub (`gh repo delete`) — stack unique `aria-vanguard`. |
| 2026-06-19 | **Repo `dexpulse-secrets` supprimé** — scripts dans `aria-vanguard/operator/`, secrets restent dans le coffre local. |
| 2026-06-19 | **Fin DEXPulse hôte** : service Render DEXPulse supprimé **volontairement** ; toute l’infra importante migre vers **aria-vanguard** (API + vitrine). Repo `dexpulse` à retirer après migration code. |

| 2026-06-20 | **Mono-PC** : **PC-SYLVAIN** seul — plus d'autre machine (confirmé Sylvain) ; handoff = GitHub uniquement |
| 2026-06-20 | **Consommation Grok** : mode concis SSOT sessions/CONSOMMATION-GROK.md — plan minimal, exécution directe |
| 2026-06-20 | **Gem Crush Phase A** : boucle incrémentale (backlog YAML, Critic v1, dry-run, micro-ship 1–2 items) — pas de rafales massives |
| 2026-06-20 | **Recherche web ARIA** : Tavily rejeté — **DuckDuckGo seul**, cerveau 100 % gratuit (pas de clé API payante) |
| 2026-06-20 | **Repos GoldenFarFR** : 6 repos actifs, **tous privés** — jamais PUBLIC sauf urgence explicite |
| 2026-06-20 | **Gem Crush prod** : catalogue premium s'arrête à **v42** — v43+ à ajouter (présentation Candy : pre-level, étoiles, map, obstacles) |
| 2026-06-20 | **aria-core structure** : Phase A = doc-only (ARCHITECTURE.md) — **pas de déplacement fichiers Python** tant que Render non validé |
| 2026-06-20 | **Ouvrier ARIA** : file sessions/ARIA-WORKER.md — Cursor traite [pending] avant toute autre tâche |
| 2026-06-20 | **Mémoire aria-core Phase C** : Chroma local opt-in validé (`67b28c3a`) — **pause jusqu'au 2026-07-02** |
| 2026-06-20 | **Reprise 2026-07-02** : SSOT `sessions/REPRISE-2026-07-02.md` — assistant **demande** si lancer Phase D ou attendre ; **jamais auto-start** |
| 2026-06-20 | **Mémoire Phase D** : `llm_context.py` — injection vectorielle dans `build_llm_context` (local, flag off prod) — avance anticipée avant 07-02 |
| 2026-06-20 | **TOTP** : Telegram désactivé — code uniquement dans chat Grok/Cursor (`-TotpCode`), `GOLDENFAR_VAULT_TOTP_VIA_ARIA=0` |

### Reprise programmée (ARIA mémoire)

> **Phases A–D** livrées en local (2026-06-20). Prod inchangée — deploy groupé quand quota Render OK.

| Fait (A–D) | À faire (E→G) |
|------------|---------------|
| Doc, package `memory/`, Chroma opt-in, `llm_context.py` + injection vector | **E** values → **F** goals → **G** reflection |
| Prod : `aria_vector_memory=false`, pas de deploy (quota Render) | Tester vector local : `pip install -e ".[dev,vector]"` + flag true |

### Journal de bord (actions techniques)

Fichier : **`JOURNAL.md`** (même repo). Une ligne par action :

`14h32 — Ajout du fichier projets/ddc/ddc_calculateur.py`

| Outil | Activation |
|-------|------------|
| **Cursor** | Règle `.cursor/rules/journal-de-bord.md` — demander « montre le journal » |
| **Grok** | Skill `/journal-de-bord` + règle always-on |

Setup : `skills\scripts\install.ps1` puis copier `.grok\rules\journal-de-bord.md` depuis `collegue-memoire\`.

Distinct du tableau ci-dessus (décisions métier).

---

| 2026-07-01 | **Monorepo ARIA** : tout l'écosystème dans `GoldenFarFR/ARIA` ; `collegue-memoire/` = ce dossier ; `ARIA_REPO_ROOT` sur chaque PC |
| 2026-07-01 | **Gem Crush retiré** — supprimé du monorepo (skills, POC vanguard, heartbeat, QI juge) ; **pas de push GitHub** sans validation Sylvain |
| 2026-07-01 | **Mémoire Phases E–G** : values + goals + reflection injectés LLM (`d91c33e`→`a970bd7`) — **4 commits locaux** en attente push |

*Dernière mise à jour : 2026-07-01 (mémoire E–G complète)*