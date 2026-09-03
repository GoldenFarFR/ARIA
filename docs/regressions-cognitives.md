# Régressions cognitives

> Registre des **classes** d'erreurs de raisonnement, pas des incidents. Un incident se corrige une
> fois ; une classe revient jusqu'à ce qu'un test l'en empêche. Compagnon de
> `docs/cerveau-epistemique-sessions.md` §12 (« historique des erreurs = mémoire cognitive »).

Format imposé, et le champ qui compte le plus est **« pourquoi le raisonnement semblait correct »** :
c'est lui qui se répétera, pas l'erreur elle-même.

    ERREUR → MODE DE DÉFAILLANCE → TEST ADVERSARIAL → MÉCANISME PRÉVENTIF → RÉGRESSION PERMANENTE

Tous les cas ci-dessous ont été observés réellement sur ce projet, la plupart le 2026-09-03 en une
seule matinée. Aucun n'est théorique.

---

## COGNITIVE-001 — Pagination trap

**Erreur.** `gh api .../dependabot/alerts --jq 'length'` a rendu `30`, présenté comme « 30 alertes
ouvertes ». Il y en avait **zéro** : les 30 étaient l'historique complet (29 corrigées, 2 écartées),
et le compte était en plus tronqué par la pagination par défaut — 30 affichés sur 31 réels.

**Pourquoi le raisonnement semblait correct.** La commande a réussi, le chiffre est sorti d'une API
officielle, et rien dans la réponse ne signalait un filtre manquant. Un résultat propre venant d'un
instrument incomplet reste propre à l'œil.

**Comment l'attaque l'a cassé.** Redemander avec `state=open&per_page=100` : `0`.

**Signal précoce.** Toute API qui pagine ou qui filtre par défaut. Un `length` sans filtre explicite
est une mesure de ce que l'API a bien voulu rendre, pas de ce qui existe.

**Mécanisme préventif.** Le filtre d'état est obligatoire et documenté dans
`security_posture_collect.collect_dependabot`; le commentaire cite l'incident.

**Applicable à.** Toute source paginée : GitHub, DexScreener, RPC, SQL avec `LIMIT`.

---

## COGNITIVE-002 — PATH shadow-version

**Erreur.** Un watchdog a rapporté la CLI en `2.1.222` alors que la version **servie** était
`2.1.258`. Son propre `PATH` (`/usr/local/bin:/usr/bin:/bin`, sans nvm) résolvait la copie système
périmée.

**Pourquoi le raisonnement semblait correct.** `claude --version` est la commande canonique. Elle a
répondu. L'instrument mesurait sincèrement — mais pas le même objet que celui dont on parlait.

**Comment l'attaque l'a cassé.** Lire `/proc/<pid>/exe` du processus réellement servi.

**Signal précoce.** Tout script qui force son `PATH` puis interroge un binaire par son nom court.

**Mécanisme préventif.** Le collecteur mesure les trois versions (système, nvm, servie) et signale
explicitement la coexistence de deux générations.

**Applicable à.** Python, node, pip, tout outil installable à plusieurs endroits.

---

## COGNITIVE-003 — Deleted mapping (le faux succès du `pip upgrade`)

**Erreur.** `pip install --upgrade` a corrigé quatre paquets, le scan est repassé au vert, et le
processus de production a continué d'exécuter **24 mappages supprimés** — dont
`aiohttp/_http_parser.so`, celui qui portait la vulnérabilité.

**Pourquoi le raisonnement semblait correct.** L'upgrade a réussi, `pip check` était propre, le
re-scan rendait 0 vulnérabilité. Trois mesures concordantes, toutes exactes, toutes portant sur le
disque — aucune sur la mémoire.

**Comment l'attaque l'a cassé.** `grep '(deleted)' /proc/<pid>/maps` : 24 entrées.

**Signal précoce.** Toute mise à jour appliquée sous un processus déjà lancé. La mention `(deleted)`
dans `/proc/<pid>/maps` est la signature.

**Mécanisme préventif.** La chaîne de preuve exigée avant de dire « corrigé » : version disque →
inode disque → même inode mappé dans le **nouveau** pid → zéro mappage supprimé → modules rechargés
→ re-scan.

**Applicable à.** Bibliothèques natives, images Docker, configuration lue au démarrage, secrets
rechargés.

---

## COGNITIVE-011 — L'instrument accuse à tort l'implémentation

**Erreur.** Un test vérifiant que le garde-fou ne fait jamais de `git checkout` a échoué. L'unique
occurrence était dans **le commentaire qui explique pourquoi le garde-fou s'en abstient**.

**Pourquoi le raisonnement semblait correct.** Le test cherchait exactement la bonne propriété, sur
le bon fichier, et rendait un résultat déterministe et reproductible. Il était simplement incapable
de distinguer du code exécuté d'un texte documentaire.

**Comment l'attaque l'a cassé.** Localiser l'occurrence : elle était dans une ligne commençant par
`#`.

**Signal précoce.** Tout test qui affirme une propriété du **comportement** en la cherchant par
recherche textuelle. La documentation d'une interdiction ressemble à sa violation.

**Mécanisme préventif.** Le test ne matche que les lignes exécutées (`not line.lstrip().
startswith("#")`), et le commentaire du test conserve la trace du faux positif.

**Réflexe imposé.** Un test qui échoue ne prouve pas que l'implémentation est fausse. Attaquer
l'instrument avant de modifier le code — la tentation inverse (changer le code pour satisfaire le
test) aurait supprimé un commentaire utile pour rien.

**Applicable à.** Tout test statique, tout linter, tout audit par `grep`, toute règle appliquée à du
texte plutôt qu'à un comportement.

---

## COGNITIVE-012 — L'instrument est correct, son générateur ne l'est pas

**Erreur.** Le cron de promotion a écrit huit entrées longues directement dans `CLAUDE.md`, qui a
franchi son plafond CI et bloqué le push de trois sessions. Le script ne contenait aucun bug : il
lance une session Claude, et c'est **le prompt** qui ordonnait « ajoute un nouveau bullet numéroté au
backlog dans CLAUDE.md ». L'architecture documentée disait l'inverse depuis des semaines.

**Pourquoi le raisonnement semblait correct.** Chaque couche faisait exactement ce qu'on lui
demandait. Le script exécutait son prompt fidèlement ; la session obéissait à son instruction ; le
test de taille faisait son travail. La contradiction ne vivait dans aucune couche — elle vivait
**entre** l'architecture déclarée et l'instruction qui la contredisait.

**Comment l'attaque l'a cassé.** Remonter d'un niveau : symptôme → fichier → script → **prompt** →
architecture. Corriger le fichier aurait laissé le cron recommencer le lendemain.

**Signal précoce.** Toute automatisation pilotée par un prompt figé pendant que l'architecture
évolue. Le prompt est du code qui vieillit sans que personne ne le relise.

**Mécanisme préventif.** Prompt corrigé (routage vers `docs/`, index borné à 100 caractères),
**plus** un garde-fou déterministe qui mesure avant et après — parce qu'une instruction dans un prompt
n'est pas une garantie. Dépassement ⇒ `system_issue` critique + `exit 2`, jamais une CI rouge refilée
en silence. Onze tests dans `test_promotion_budget_guard.py`.

**Réflexe imposé.** Quand une automatisation viole une règle documentée, chercher qui lui a **dit**
de le faire avant de conclure à un bug. Et ne jamais réparer uniquement la dernière couche.

**Applicable à.** Tout cron pilotant une session, tout prompt versionné, toute règle qui n'existe que
dans un document.

---

## Comment s'en servir

Avant une mission, si son contexte ressemble à l'un de ces cas, l'attaque correspondante se déclenche
en priorité — c'est ce que §12 du cerveau appelle une régression cognitive : la classe ne revient
pas, parce qu'un test l'attend.

Trois traits communs aux cinq cas, et ils tiennent en une phrase : **l'instrument était sincère, la
mesure était exacte, et la conclusion était fausse quand même.** À chaque fois, ce qui manquait
n'était pas de la rigueur mais la question « qu'est-ce que mon instrument regarde réellement ? ».
