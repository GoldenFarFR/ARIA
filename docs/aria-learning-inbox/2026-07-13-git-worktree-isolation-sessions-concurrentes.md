# [VPS Research] Git worktree — isoler des sessions Claude Code concurrentes sur la même machine

## Contexte et constat de départ

Deux accidents de commit croisé ce soir (13/07) attribués au partage d'un
même working tree entre sessions concurrentes. Veille sur les bonnes
pratiques `git worktree` pour éviter ça : convention de nommage,
nettoyage des worktrees abandonnés, pièges connus.

**Preuve concrète locale (fait vérifié, pas une supposition)** —
`git worktree list` sur le dépôt ARIA montre, au moment de cette veille,
**cinq worktrees actifs simultanément sur cette machine** :

```
/opt/aria                    4ada6c05 [claude/videos-marketing-temp]
/opt/aria-research            134fd9cb (detached HEAD)
/opt/aria-secondaire          804aa041 [claude/144-unbound-local-fix]
.../scratchpad/aria-diligence  c63057d1 [claude/diligence-flaunch-zora-temp]
.../scratchpad/aria-rollback   b5e5e263 [claude/veille-rollback-auto-temp]
```

Ceci confirme directement le contexte de l'incident : `/opt/aria`,
`/opt/aria-research` et `/opt/aria-secondaire` sont des worktrees
persistants nommés de façon ad hoc (pas de convention visible liant nom de
dossier ↔ branche ↔ tâche), placés en frères de `/opt/repos/` sans
regroupement ni marquage `.gitignore`, sans lien apparent avec un
mécanisme de nettoyage. C'est exactement le terrain favorable aux
collisions décrites plus bas.

---

## Convention de nommage par tâche

Consensus des sources 2026 : nommer le dossier du worktree par
**préfixe-dépôt + branche/tâche**, gardé en frère du checkout principal
(`../monprojet-feat-auth`, `../monprojet-fix-db-perf`) — un coup d'œil
suffit à savoir quelle tâche correspond à quel dossier. Recommandation
directement applicable ici : au lieu de `aria-research` / `aria-secondaire`
(qui ne disent rien de la tâche en cours), adopter un schéma du type
`aria-<slug-tâche>` où le nom du dossier reprend le suffixe de la branche
(cohérent avec la convention déjà en place dans cette veille elle-même :
`aria-diligence`, `aria-rollback`).

**Convention officielle Claude Code (CLI)** : `claude --worktree <nom>`
crée automatiquement le worktree sous `.claude/worktrees/<nom>/` à la
racine du dépôt, sur une nouvelle branche `worktree-<nom>` — un
regroupement propre et prévisible, à préférer à des dossiers frères
dispersés (`/opt/aria-research`, etc.) quand la CLI officielle est
utilisable. Recommandation associée : ajouter `.claude/worktrees/` au
`.gitignore` du dépôt (absent aujourd'hui — vérifié, aucune mention de
"worktree" dans `.gitignore`) pour que le contenu des worktrees
n'apparaisse jamais comme fichiers non suivis dans le checkout principal.

---

## Nettoyage automatique des worktrees abandonnés

**Comportement natif Claude Code (documentation officielle)** :
- Sans changement non commité, sans fichier non suivi, sans nouveau
  commit : le worktree et sa branche sont supprimés automatiquement à la
  sortie de session.
- S'il y a des changements non commités, des fichiers non suivis ou des
  commits : Claude demande de garder ou supprimer — garder préserve le
  dossier et la branche pour y revenir plus tard.
- **Sessions non-interactives (`-p`)** : **pas de nettoyage automatique**
  (pas d'invite de sortie) — suppression manuelle via `git worktree
  remove` nécessaire. C'est le cas probable des trois worktrees persistants
  trouvés ci-dessus (`/opt/aria-research`, `/opt/aria-secondaire`, et même
  `/opt/aria` lui-même n'est pas sur `main`) — sessions VPS lancées en
  mode non-interactif, jamais nettoyées faute d'invite de sortie.
- Verrouillage actif : pendant qu'un agent tourne, Claude Code exécute
  `git worktree lock` sur son worktree pour empêcher qu'un nettoyage
  concurrent le supprime pendant qu'il travaille — levé à la fin de
  l'agent.
- Un balayage automatique (gouverné par le réglage `cleanupPeriodDays`)
  supprime les worktrees créés pour des subagents/sessions en arrière-plan
  une fois dépassé ce délai, **seulement s'ils n'ont ni changement non
  commité, ni fichier non suivi, ni commit non poussé** — les worktrees
  créés via `--worktree` directement ne sont, eux, jamais balayés
  automatiquement.

**Nettoyage manuel générique (indépendant de Claude Code)** :
`git worktree prune` scanne et supprime les entrées orphelines (worktree
supprimé par un `rm -rf` externe sans passer par `git worktree remove`) ;
`git worktree add` déclenche déjà un prune léger automatiquement. Un
worktree est considéré "stale" si sa branche de suivi distante n'existe
plus (après `git fetch --prune`, `git rev-parse --verify
refs/remotes/origin/<branche>` échoue) — c'est le signal exploitable pour
un script de nettoyage périodique côté VPS, puisque les branches
`claude/*-temp` fusionnées et supprimées côté GitHub laissent alors un
worktree local orphelin détectable par ce critère.

---

## Pièges connus

**Verrouillage de branche** : impossible de checkout la même branche dans
deux worktrees à la fois (`fatal: 'main' is already checked out at
'<chemin>'`) — protection native, pas un vrai risque si chaque session
utilise bien une branche `claude/*-temp` dédiée (déjà la convention en
place ici).

**Contention de lock sur `.git/` partagé — cause la plus probable des
"accidents de commit croisé" décrits** : les worktrees ont chacun leur
propre index, mais certaines opérations (mise à jour de refs, packing
d'objets, création d'objets) touchent quand même le `.git/` partagé du
dépôt principal. Des `git add`/`git commit` concurrents entre worktrees
créent une contention transitoire sur `.git/index.lock` — le perdant de
la course reçoit `fatal: Unable to create '.git/index.lock': File
exists`. Documenté et confirmé sur un ticket GitHub `anthropics/claude-code`
(#55724, fermé comme doublon d'un ticket existant — donc suivi en amont,
pas encore livré comme correctif au moment de cette veille) : à 5 agents
concurrents, échecs intermittents ; à 13 agents testés, 8 échecs sur
lock ; à 10+ agents, échec quasi certain sur au moins un.

**Le vrai danger n'est pas l'erreur, c'est le mode d'échec silencieux
associé** : quand un `git commit` échoue sur ce lock, l'agent peut
s'arrêter **sans avoir commité**, et si un nettoyage automatique de
worktree suit (cf. section précédente), **le worktree entier est
supprimé, détruisant le travail non commité de façon permanente** —
correctifs proposés dans le ticket (retry avec backoff exponentiel,
préserver le worktree si `git status --porcelain` montre des changements
avant tout nettoyage, jitter aléatoire à la création) mais non confirmés
livrés au moment de cette recherche — donc une session VPS ne doit pas
compter dessus tel quel : mieux vaut committer tôt et souvent dans chaque
worktree, plutôt que d'accumuler du travail non commité en espérant que
le nettoyage soit assez prudent.

**Isolation partielle, pas totale** : les worktrees isolent le code
(fichiers, index, branche), mais pas les autres formes d'état partagé — un
`node_modules` symlinké entre worktrees peut se corrompre sous installs
concurrents (chaque worktree doit avoir le sien, jamais un lien partagé) ;
de même, un serveur MCP connecté par deux sessions peut faire fuiter de
l'état entre elles — un serveur MCP par worktree est le pattern sûr.

**Submodules (non applicable ici, vérifié)** : les submodules ne
s'initialisent pas automatiquement dans un nouveau worktree — `git
submodule update --init --recursive` est nécessaire manuellement à chaque
création. `git submodule status` sur ARIA ne retourne rien : **le dépôt
n'a aucun submodule**, ce piège ne s'applique pas ici.

---

## Recommandation concrète

**Signal vert** — les bonnes pratiques existent, sont documentées à jour
(y compris officiellement par Claude Code), et le mécanisme natif
(verrouillage pendant l'exécution, invite de nettoyage en session
interactive) couvre déjà une bonne partie du risque. Deux actions
concrètes directement actionnables sur ce VPS, sans dépendance nouvelle :

1. **Nommer par tâche, pas par rôle vague** : renommer la convention des
   futurs worktrees persistants (`aria-research`, `aria-secondaire` →
   quelque chose comme `aria-<branche-slug>`), et ajouter
   `.claude/worktrees/` au `.gitignore` si des worktrees CLI officiels
   commencent à être utilisés à cet endroit.
2. **Committer tôt/souvent dans chaque worktree** plutôt que de laisser
   du travail non commité s'accumuler, tant que le correctif de
   préservation-avant-nettoyage (ticket #55724) n'est pas confirmé livré —
   c'est la meilleure protection disponible aujourd'hui contre la
   destruction silencieuse de travail en cas de lock contention.
3. **Auditer périodiquement `git worktree list`** (déjà fait pour cette
   veille) pour repérer les worktrees orphelins dont la branche distante
   a disparu (`git fetch --prune` puis vérifier
   `refs/remotes/origin/<branche>`), et les nettoyer avec `git worktree
   remove` — un simple alias/script cron suffit, pas d'outil tiers requis.

## Sources

- [Run parallel sessions with worktrees — Claude Code Docs (officiel)](https://code.claude.com/docs/en/worktrees)
- [Agent isolation: worktree — parallel agents lose work due to git lock contention + auto-cleanup · Issue #55724 · anthropics/claude-code](https://github.com/anthropics/claude-code/issues/55724)
- [Parallel Agentic Development With Git Worktrees: A Practical Playbook — MindStudio](https://www.mindstudio.ai/blog/parallel-agentic-development-git-worktrees)
- [Git Worktree Conflicts with Multiple AI Agents: Diagnosis and Fixes — Termdock](https://www.termdock.com/en/blog/git-worktree-conflicts-ai-agents)
- [git worktree prune — GitWorktree.org](https://www.gitworktree.org/tutorial/prune)
- [Bulk cleaning stale git worktrees and branches — brtkwr.com](https://brtkwr.com/posts/2026-03-06-bulk-cleaning-stale-git-worktrees/)
- [git-worktree — Git official documentation](https://git-scm.com/docs/git-worktree)
- Preuve locale vérifiée : `git worktree list` sur `/opt/repos/ARIA`,
  `git submodule status` (aucun résultat), `.gitignore` (aucune mention
  "worktree") — 2026-07-13

## Frontières confirmées respectées

Aucun code touché, aucune modification de `.gitignore` ni des worktrees
existants (`/opt/aria`, `/opt/aria-research`, `/opt/aria-secondaire` non
touchés). Recherche et références uniquement. Décision d'adoption de la
convention de nommage / nettoyage laissée au commandement.
