# HANDOFF — Opérations VPS (git, déploiement, worktrees, dispatch)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[ETAT ACTUEL] Sujet    : `git push origin <nom-de-branche>` pousse la branche locale de ce nom, pas HEAD
Date : 2026.07.12  /  Probleme : un commit fait sur `main` local suivi de `git push origin <autre-branche>` est parti vers cette autre branche au lieu de `main` — `origin/main` n'a jamais bougé, sans erreur ni avertissement visible.
Solution : Toujours pousser avec un refspec explicite (`git push origin main:main` ou `HEAD:main`), jamais `git push origin <nom>` seul ; revérifier après coup via `git fetch origin main && git show origin/main:<fichier>` — cf. historique git 12/07.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : `origin` d'une session VPS peut pointer vers le mauvais dépôt sans erreur visible
Date : 2026.07.12 vers 13  /  Probleme : une session VPS a rapporté un push "réussi" (`git ls-remote` positif) vers une branche qui n'existait pourtant pas sur le bon dépôt — son `origin` pointait en réalité vers un autre repo du même écosystème (toutes ses commandes git étaient cohérentes... avec le mauvais dépôt).
Solution : Vérifier `git remote -v` en cas de doute, ou faire confirmer par la session commandement via l'API GitHub (indépendante du proxy git local) ; tout dispatch qui cible un chemin précis (ex. `docs/aria-learning-inbox/`) doit nommer explicitement le dépôt cible, jamais le laisser implicite — cf. historique git 12-13/07.

------------------------------------------------------------

[DEPLOYE] Sujet    : Déploiement blue-green + autoheal (rollback quasi instantané)
Date : 2026.07.13  /  Probleme : un déploiement cassé (health-check en échec) causait un downtime le temps de corriger, aucun mécanisme de retour arrière rapide.
Solution : `deploy.sh` bascule en blue-green (alternance de port, nouveau conteneur health-checké pendant que l'ancien tourne encore, nginx ne bascule qu'après succès) + `willfarrell/autoheal` avec disjoncteur maison (plafond 3 redémarrages/10min) — vanguard/deploy.sh / vanguard/scripts/autoheal-circuit-breaker.sh (cf. historique git 13/07).

------------------------------------------------------------

[DEPLOYE] Sujet    : Vérification post-déploiement trop rapide après reload nginx
Date : 2026.07.13  /  Probleme : `deploy.sh`/`deploy-vitrine.sh` tiraient un curl immédiatement après `systemctl reload nginx` — reload pas instantané (workers mettent un court instant à tourner), donc échec systématique et rollback automatique malgré un déploiement sain.
Solution : Boucle `retry_until` (~10s de plafond) avant de conclure à un échec — deploy.sh / vanguard/deploy_vitrine_lib.sh (cf. historique git 13/07).

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Suppression de branche distante bloquée par le proxy de session, pas le classifieur
Date : 2026.07.13  /  Probleme : `git push origin --delete <branche>` sur une branche déjà fusionnée à l'identique échoue en HTTP 403 ("non autorisé par la politique de l'organisation") — action structurellement impossible depuis une session cloud, même sur du contenu sans risque.
Solution : Faire supprimer la branche par l'opérateur directement sur l'interface GitHub (icône corbeille) — cf. historique git 13/07.

------------------------------------------------------------

[CODE] Sujet    : Contention `.git/index.lock` à isolation par worktree concurrente
Date : 2026.07.13  /  Probleme : chaque worktree Claude Code a son propre index, mais certaines opérations git (refs, packing) touchent quand même le `.git/` partagé — à 5+ agents concurrents sur la même machine, contention intermittente sur `.git/index.lock`. Un `git commit` qui échoue sur ce lock, suivi d'un nettoyage automatique de worktree (non-interactif `-p`, cas des sessions VPS), peut détruire un travail non commité de façon permanente.
Solution : Committer tôt et souvent dans chaque worktree — aucun correctif officiel confirmé livré côté Claude Code à cette date (ticket amont `anthropics/claude-code#55724`) — cf. historique git 13/07.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Tâche cron programmée ne se déclenche pas si la session VPS reste active
Date : 2026.07.12  /  Probleme : un job de vérification programmé n'a jamais tourné — la session VPS était restée active sans interruption sur un autre travail, et ce type de tâche ne se déclenche qu'en session inactive.
Solution : Vérifier manuellement en cas de doute plutôt que de compter sur le déclenchement automatique d'une session qui pourrait rester active — cf. historique git 12/07.

------------------------------------------------------------

[DEPLOYE] Sujet    : Nouveaux modules absents de la liste curatée de tests en CI
Date : 2026.07.08  /  Probleme : 9 modules livrés la même nuit (relay_chat, relay_conversation, knowledge_inbox, sepolia_wallet, sepolia_autonomous, exam, btc_cycles, code_proposal, skill_projects) avaient chacun leur fichier de test mais n'étaient pas listés dans .github/workflows/ci.yml — une régression sur l'un d'eux serait passée inaperçue.
Solution : les 9 fichiers de test ajoutés à la liste curatée de la CI — .github/workflows/ci.yml (cf. historique git 08/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Disque VPS saturé par le cache Docker jamais purgé
Date : 2026.07.09→11  /  Probleme : deploy.sh ne nettoyait jamais les images/cache de build après un déploiement, disque monté à 79,8% (images 35GB + build cache 31,7GB, 90% récupérable) — nettoyage manuel one-shot 80%→11% le 10/07, cause racine non corrigée à ce moment-là.
Solution : docker image prune -f + docker builder prune -f ajoutés à la fin de deploy.sh, exécutés UNIQUEMENT après confirmation du health check réussi (jamais en cas d'échec/rollback) — vanguard/deploy.sh (cf. historique git 11/07)

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : VPS dispose d'un accès SSH écriture aux 7 repos GoldenFarFR
Date : 2026.07.11  /  Probleme : une session Claude Code sur le VPS ne pouvait travailler que sur le repo courant, dépendait d'un poste Windows local pour les autres repos de l'écosystème (ARIA, aria-ops, aria-core, template-grok-cursor, aria-acp-showcase, acp-cli-demos, GoldenFarFR).
Solution : 7 deploy keys SSH dédiées (une par repo, aucune partagée, toutes en écriture) configurées sur le VPS — détail complet et alias ~/.ssh/config dans aria-ops/runbooks/vps-github-access.md (repo privé)
