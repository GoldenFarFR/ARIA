# Réactivation ACP — checklist « zéro temps perdu »

> ACP est **abandonné comme ligne de revenus** (marché service en sommeil), mais **rien n'a
> été supprimé** : tout vit en **seam dormant**, gaté par des variables d'env. Ce doc est le
> SSOT pour **rallumer l'ACP en quelques minutes** le jour où Virtuals répare le 500 (ou si
> le marché ACP redevient actif). Anticipation : le brancheur de demain ne redécouvre rien.

## Déclencheur
Virtuals corrige la cause racine du 500 (signer non autorisé) OU le marché ACP service
redevient exploitable. Signal concret : `acp browse` / `acp job list` renvoient des données
au lieu d'un 500.

## Ce qui est PRÉSERVÉ (aucun rebuild de logique nécessaire)
- **Tâches heartbeat** (dans `heartbeat.py`, gatées OFF) : `acp_provider_poll`,
  `acp_market_scan`, `acp_email_watch`. Elles s'auto-activent quand `is_acp_available()` est
  vrai + flags posés.
- **Routage conversationnel** : `brain.py` (`detect_intent`), gaté par `ARIA_ACP_ENABLED`.
- **Config agent** : `knowledge/acp_config.yaml` (agent_id `019f0522-…`, offering).
- **Skills** : `skills/acp_*.py` (provider, client, offering, workflow, market intelligence).
- **Showcase PR** : `skills/showcase_pr_watcher.py` — **indépendant de l'ACP**, déjà en mode
  relai sûr (full-auto feu vert + passage de relai humain + signature). Rien à toucher.

## Étapes de réveil (dans l'ordre)
1. **Local** (machine avec acp-cli + clé de signer — jamais le serveur) :
   - `npm i -g @virtuals-protocol/acp-cli` (Node ≥ 18) si absent.
   - `acp configure` (connexion navigateur).
   - `acp agent add-signer --agent-id 019f0522-b57b-7e8e-a70a-aab2070e070e` → approuver dans
     le navigateur → `acp agent signer-status …` doit afficher **completed**.
   - Vérifier : `acp browse` renvoie des données (plus de 500).
2. **Flags env** (`.env` de l'hôte qui poll les jobs) :
   - `ARIA_ACP_PROVIDER_ENABLED=true`
   - `ARIA_ACP_EVENTS_FILE=<chemin du flux d'événements acp>` (ex. Windows :
     `%LOCALAPPDATA%\GoldenFar\acp-events.jsonl`)
   - Optionnel : `ARIA_ACP_ENABLED=1` (routage conversationnel ACP dans le chat).
3. **Disponibilité CLI** : `is_acp_available()` doit voir l'acp-cli depuis le contexte qui
   exécute le poll. La **signature reste locale** (clé jamais sur le serveur) — le poll et la
   signature peuvent devoir tourner côté machine locale, pas dans le conteneur VPS. À trancher
   au moment du réveil (runner local vs conteneur), c'est la seule vraie décision d'archi.
4. **Rebuild + deploy** : modifier ARIA = rebuild l'image Docker (`./vanguard/deploy.sh`).
   Un simple flag dans `.env` sans redémarrage ne suffit pas.

## Garde-fous MAINTENUS au réveil (ne pas contourner)
- **Aucune exécution financière automatique** — validation humaine (Telegram) obligatoire.
- **Clé privée jamais sur le serveur** — signature acp-cli locale.
- **Rien d'outward autonome** sans gate opérateur.

## Vérif rapide de l'état courant
- `/status` Telegram et les skills `acp_client_skill` / `acp_provider_skill` affichent l'état
  des flags (`ARIA_ACP_PROVIDER_ENABLED` ON/OFF, CLI disponible ou non).
- `is_acp_available()` = présence de l'acp-cli ; tant qu'il est absent, les tâches ACP restent
  OFF quoi qu'il arrive (fail-safe).
