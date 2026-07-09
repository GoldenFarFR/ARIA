# HANDOFF — 2026-07-09 nuit (suite 6) — Onboarding local Windows, bug join confirmé sur 2 environnements, 1er trade test

Suite directe de `docs/HANDOFF-2026-07-09-nuit5.md` (même journée, segment encore plus
tardif). Lire les six HANDOFF du 09/07 + `CLAUDE.md` + `docs/etat-systeme-cable.md`.

**Toujours aucun commit aria-core** — tout ce segment s'est passé sur le PC Windows de
l'opérateur (PowerShell + Git Bash) et le VPS, en parallèle. Seule exception : ce
HANDOFF et la mise à jour `CLAUDE.md` associée.

## Onboarding reproduit sur une SECONDE machine (PC Windows local) — notes techniques réutilisables
L'opérateur a répété la config `acp-cli` sur son PC local (pas seulement le VPS), ce qui
a révélé des points Windows-spécifiques à connaître pour la prochaine fois :
- **`acp` (CLI globale, `npm i -g @virtuals-protocol/acp-cli`) fonctionne directement
  dans PowerShell** — pas besoin de bash pour ça, c'est un binaire Node normal.
- **`dgclaw.sh` (et tout script `.sh`) NE fonctionne PAS dans PowerShell** — Windows
  tente de l'ouvrir avec une appli associée au lieu de l'exécuter. Il faut une fenêtre
  **Git Bash** dédiée (installée avec Git for Windows). Le `bash` accessible depuis
  PowerShell pointe vers WSL (non installé ici), pas vers le bash de Git — bien ouvrir
  l'appli "Git Bash" séparément, ne pas taper `bash` depuis PowerShell.
- **Dans Git Bash/MinTTY, `Ctrl+V` ne colle pas** (insère un caractère de contrôle
  littéral) — utiliser **Ctrl+Inser (copier) / Maj+Inser (coller)**, ou le clic droit.
  Le mode "bracketed paste" de ce terminal corrompt aussi les commandes courtes collées
  juste après un prompt vide (`^[[200~cd ...` au lieu de `cd ...`) — taper les commandes
  courtes au clavier plutôt que les coller évite ce problème.
- **`dgclaw.sh` exige `acp-cli` cloné en dossier VOISIN de `dgclaw-skill`** (même niveau
  dans l'arborescence), pas imbriqué dedans — sinon `Error: acp-cli not found. Set
  ACP_CLI_DIR or clone it as a sibling directory`. Corrigé avec `mv dgclaw-skill/acp-cli .`
  depuis le dossier home.
- **`jq` manquant par défaut sur Windows** (contrairement à Ubuntu/VPS, où il était déjà
  présent) — `scripts/dgclaw.sh: line 164: jq: command not found`. Installé via
  `winget install jqlang.jq` (PowerShell) — **redémarrer la fenêtre Git Bash après**
  pour que le PATH mis à jour soit pris en compte.

## INCIDENT SÉCURITÉ MINEUR — reliquat de clé privée dans un NOM DE FICHIER, résolu
Deux fichiers trouvés dans `C:\Users\Studi\` (dossier home Windows de l'opérateur, HORS
du repo), avec un nom de la forme `connect.ts<CLE-PRIVEE-EC-REDIGEE>` — **[secret retiré
de ce document, jamais reproduit ici]**. Le préfixe du nom correspondait à la signature
base64/PKCS8-DER d'une clé privée EC, collée directement dans le NOM du fichier (pas
seulement un contenu caché) — pire qu'un contenu dans le fichier, car visible dans un
simple listing de dossier, sans même l'ouvrir. Probable reliquat de l'incident
`connect.ts` traité plus tôt le même jour (même nom de base), origine exacte non
confirmée (pas un comportement voulu d'un outil Virtuals officiel — aucune raison
légitime d'encoder une clé dans un nom de fichier). **Résolu** : fichiers supprimés via
un joker sur le début du nom (le nom exact retranscrit depuis une capture d'écran ne
correspondait pas caractère pour caractère, `rm` échouait sur le nom copié littéralement
— un motif générique a suffi). `rm` dans Git Bash supprime directement, sans passer par
la Corbeille Windows — confirmé effacé, rien à vider en plus.

## `dgclaw.sh join` — bug CONFIRMÉ indépendant de l'environnement (VPS ET Windows local)
Testé sur deux machines complètement différentes (Ubuntu VPS IONOS, Windows local via
Git Bash) avec la même configuration agent (Aria Vanguard ZHC) :
- **VPS** : reste bloqué indéfiniment sur `[PrivyAlchemy] Manual approval required...
  Reason: RPC request denied due to policy violation`, même après approbation confirmée
  côté opérateur (3 tentatives distinctes sur plusieurs dizaines de minutes, y compris
  un essai délibéré de 30 minutes d'attente).
- **Windows local** (une fois `jq` et le placement d'`acp-cli` corrigés) : échoue
  immédiatement et proprement avec `Error: Server error 500` sur la création du job
  `join_leaderboard` — pas de blocage, un vrai rejet serveur.
- **Conclusion définitive** : ce n'est ni le VPS, ni l'environnement Windows, ni notre
  configuration qui posent problème — **c'est le service `join_leaderboard` de Virtuals
  qui est cassé côté serveur**, sur les deux tentatives, deux machines. Cohérent avec le
  problème ouvert et non résolu sur `Virtual-Protocol/dgclaw-skill` (issue #12, "No
  agent found on ACP" malgré un agent indexé) — même famille de symptôme.
- **Décision actée** : ne plus retenter `join` en boucle. Pivot vers l'hypothèse
  documentée dans `SKILL.md` : l'éligibilité au classement ne nécessite qu'**au moins un
  trade placé dans la fenêtre de saison en cours** — pas explicitement le succès du
  `join` (qui sert surtout à obtenir `DGCLAW_API_KEY` pour poster sur le forum).

## Découverte technique complémentaire : minimum de commande Hyperliquid = 15$ notionnel
Distinct du minimum de DÉPÔT (5 USDC, déjà documenté nuit5) : **chaque ordre perp doit
avoir une valeur notionnelle d'au moins 15$**, sans quoi `acp trade --side long --token
BTC --size 0.0001 ...` échoue avec `Order notional ~$6.33 ... is below HL's $15
minimum`. Corrigé avec une taille plus grande (`--size 0.0003` ≈ 19$ notionnel à
~63 264$/BTC). Prévisualisation (`--dry-run`) confirmée propre : levier 2x, marge
requise ~9,49$ sur les 18,78$ disponibles, slippage 5% (dans la limite absolue de 10%).

## Test exécuté — a échoué aussi, même symptôme que `join`
Commande validée en aperçu (`--dry-run` propre), puis lancée pour de vrai :
```
acp trade --side long --token BTC --size 0.0003 --leverage 2 --slippage 5
```
**Résultat : `Error: Server error 500`** — même échec générique que `join_leaderboard`,
sur une opération différente (un vrai trade, pas juste l'inscription). Vérifié ensuite
via `acp trade hl-status` : **aucun fonds prélevé**, solde inchangé à 18.778095 USDC,
aucune position ouverte — l'échec a été propre, rien perdu.

**Interprétation retenue (incertaine, faute d'accès aux journaux serveur de Virtuals)** :
Hyperliquid lui-même fonctionne (274+ agents réels, milliers de trades vérifiés sur le
classement public) — c'est la couche proxy de Virtuals (`api.acp.virtuals.io`, utilisée
par `acp trade`) qui semble instable ce soir précisément (deux opérations différentes en
500 au même moment). Aucune page de statut public trouvée pour confirmer une panne
généralisée. **Décision : arrêter les tentatives pour ce soir, réessayer plus tard
(quelques heures ou le lendemain)** plutôt que d'insister sur une infra qui semble
instable maintenant.

## Ce qui reste en attente (priorité pour la prochaine session)
1. **Réessayer le trade ci-dessus** (`acp trade --side long --token BTC --size 0.0003
   --leverage 2 --slippage 5`) une fois un peu de temps passé — si ça passe, vérifier le
   classement public pour voir si Vanguard ZHC y apparaît sans `join` réussi.
2. Si le trade confirme l'apparition au classement : le pilote HL Perps est
   fonctionnellement lancé malgré `join` cassé — seule la fonctionnalité forum resterait
   indisponible (mineur). Documenter comme tel, mettre à jour le statut de la tâche #60.
3. Si `join` reste bloquant pour l'éligibilité réelle malgré tout : envisager de
   commenter sur l'issue GitHub #12 avec nos propres logs (deux environnements, deux
   modes d'échec) plutôt que de continuer à retenter.
4. Chantier Jetons d'agent (`bondv5-trader`, wrapper GAME custom) toujours pas commencé
   — 20$ USDC réservés dans le wallet, prêts.
5. Reporté depuis nuit3 (toujours valable) : JWT non vérifié dans
   `skills/core/memory/ACP VIRTUAL PROTOCOL/20260628_1139_source.md:211`.
6. Backlog sans blocage : #11, #17, #19-23, #29, #32, #34, #56, #57, #59.

## Auto-critique honnête
Bonne discipline ce segment : chaque nouvelle erreur a été corrigée sur preuve exacte
(message d'erreur lu et suivi à la lettre), jamais par supposition — et le fait de
tester sur DEUX environnements indépendants a permis de trancher définitivement une
question qui restait ouverte depuis nuit5 (bug plateforme vs. environnement), plutôt que
de rester dans le doute. Le seul vrai residu à surveiller : la découverte de la clé dans
un nom de fichier, hors du repo, sur la machine personnelle de l'opérateur — sa véritable
origine reste non expliquée, à garder en tête si un symptôme similaire réapparaît
ailleurs.
