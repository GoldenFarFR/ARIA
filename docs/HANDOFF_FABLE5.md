# HANDOFF — Fable 5 / Avocat du Diable (appel API, pièges déjà rencontrés)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

**Pourquoi ce fichier existe (14/08, demande opérateur explicite)** : les pièges API de Fable 5 étaient déjà tous documentés, mais noyés dans `docs/HANDOFF_AUTOMATISATION.md` (qui couvre aussi les watchdogs, le research loop, la promotion de backlog — rien à voir). Résultat : chaque nouvel usage de Fable 5 hors du chemin déjà câblé (`devils-advocate-review.sh`/`devils-advocate-precommit.sh`) redécouvrait les mêmes pièges un par un, par erreur réelle, à chaque fois — coût en tokens et en temps jugé inacceptable par l'opérateur. Ce fichier est le point de référence UNIQUE à lire AVANT tout nouvel appel API Fable 5 personnalisé.

## Checklist avant tout nouvel appel Fable 5 (lire AVANT d'écrire du code, pas après un échec)

1. **Réutiliser `scripts/devils-advocate-lib.sh`**, jamais un appel API réécrit à la main — le modèle/prix/format de payload y sont déjà corrects et testés. `devils_advocate_call()` accepte un system prompt optionnel en 4e argument (14/08) pour un usage hors code-review, sans dupliquer le format d'appel.
2. **Format du payload EXACT** : `thinking: {type: "adaptive"}` + `output_config: {effort: "..."}`. PAS le format extended-thinking classique Anthropic standard (`thinking: {type: "enabled", budget_tokens: N}`) — Fable 5 le rejette en HTTP 400. Découvert le 10/08, jamais deviné.
3. **`max_tokens` généreux** (96000 minimum déjà vérifié) — le raisonnement interne ("thinking") de Fable 5 est TOUJOURS actif et partage le MÊME budget `max_tokens` que la réponse visible. Un budget trop bas (calibré pour un autre modèle type Gemini) produit une réponse VIDE, facturée quand même.
4. **Timeout curl généreux** (`--max-time`, actuellement 300s dans la lib depuis le 14/08, était 120s avant). Un contenu volumineux (>10k caractères) combiné à `effort=high` peut dépasser 120s avant le premier octet de réponse — le symptôme est un `HTTP 000` (curl n'a reçu AUCUNE réponse, pas une erreur API) qui ressemble à s'y méprendre à un problème réseau alors que c'est juste un timeout trop court. Vécu en direct le 14/08 sur une review de backlog (~14k caractères, effort=high) : premier essai HTTP 000, deuxième essai après avoir porté le timeout à 300s → succès.
5. **Clé API `ANTHROPIC_API_KEY`** : toujours lue PAR LE SCRIPT LUI-MÊME depuis `vanguard/backend/.env` (`grep '^ANTHROPIC_API_KEY=' "$ENV_FILE"`), JAMAIS par une commande Bash de la session Claude Code elle-même — règle absolue "jamais toucher/afficher un .env via Bash", même en apparence sûr (cf. `docs/HANDOFF_SECURITE.md`).
6. **Condensation Haiku 4.5** (`devils_advocate_condense`) existe pour un diff/contenu dépassant `DEVILS_ADVOCATE_CONDENSE_THRESHOLD_CHARS` (60000 car.) — mais si l'opérateur demande explicitement le contenu INTÉGRAL sans pré-résumé (ex. 14/08, review de backlog où chaque nuance compte), ne PAS appeler cette étape, envoyer le texte brut tel quel.
7. **Suivi de coût** : toujours appeler `devils_advocate_cost()`/`devils_advocate_log_cost()` sur la réponse brute — jamais un appel Fable 5 non tracé.
8. **Extraction du texte final : filtrer par `type == "text"`, JAMAIS par index `content[0]`.** Le raisonnement interne ("thinking") est un bloc SÉPARÉ dans le tableau `content[]`, souvent en première position — `.content[0].text` retourne silencieusement `null` (pas une erreur, un piège classique). Pattern correct, déjà établi et à toujours réutiliser : `jq -r '[.content[]? | select(.type == "text") | .text] | join("\n\n")'` (voir `devils-advocate-review.sh`/`devils-advocate-precommit.sh`/`devils-advocate-lib.sh`).
9. **Sauvegarder la réponse brute AVANT tout parsing final.** Un appel Fable 5 coûte réel — si le parsing échoue en aval (cf. point 8), il ne faut jamais devoir repayer un appel identique juste pour relire un texte déjà reçu. Écrire `$RAW_RESPONSE` dans un fichier temporaire avant d'extraire le texte, ne le supprimer qu'après confirmation d'un résultat exploitable.
10. **Tester en conditions réelles avant de considérer un nouveau script "fini"** (process norm du projet, né de l'incident Blockscout) — un `bash -n` de syntaxe ne suffit jamais, il faut un vrai appel API réussi ET un texte final non vide avant de committer.

## Historique détaillé (repris/condensé depuis `docs/HANDOFF_AUTOMATISATION.md`, où le détail complet reste consultable)

[CODE] Sujet : Format de payload Fable 5 découvert par erreur HTTP 400 réelle
Date : 2026.08.10 / Probleme : premier appel direct à `api.anthropic.com/v1/messages` avec le format extended-thinking classique (`thinking.type: enabled` + `budget_tokens`, calqué sur OpenRouter) rejeté en HTTP 400 par l'API pour ce modèle.
Solution : Fable 5 exige `thinking.type: adaptive` + `output_config.effort` — corrigé après vérification live, jamais supposé. `scripts/devils-advocate-lib.sh`.

------------------------------------------------------------

[CODE] Sujet : max_tokens trop bas = réponse vide facturée quand même
Date : 2026.08.04 / Probleme : payload calibré pour Gemini (max_tokens ~4000) donnait une réponse VIDE sur un diff long/complexe avec Fable 5, car le "thinking" toujours actif consomme le même budget que la réponse visible — leçon déjà connue de `consult-gemini.sh` (03/08), pas réappliquée directement.
Solution : `max_tokens=96000` (laisse ~64000 tokens à la réponse visible), même marge que `consult-gemini.sh`. `scripts/devils-advocate-lib.sh`.

------------------------------------------------------------

[CODE] Sujet : timeout curl 120s trop court pour un appel personnalisé volumineux + effort=high
Date : 2026.08.14 / Probleme : nouveau script `devils-advocate-backlog-review.sh` (review d'une liste de ~70 idées de backlog, ~14000 caractères, effort=high demandé explicitement par l'opérateur pour un vrai approfondissement) — premier appel a échoué en `HTTP 000` après le timeout de 120s hérité de la review de diff standard, qui n'avait jamais été mis à l'épreuve avec un contenu aussi volumineux à effort élevé.
Solution : timeout porté de 120s à 300s dans `devils_advocate_call()` (constante partagée, bénéficie aussi aux 2 appelants existants sans changement de comportement en cas normal — juste plus de marge). `scripts/devils-advocate-lib.sh`.

------------------------------------------------------------

[CODE] Sujet : `.content[0].text` retourne `null` en silence — mauvais pattern d'extraction copié à la main
Date : 2026.08.14 / Probleme : même script `devils-advocate-backlog-review.sh`, second essai (après le fix du timeout ci-dessus) — appel API réussi (HTTP 200, coût réel facturé $0.888230), mais le fichier de sortie ne contenait que `null`. Cause : le script utilisait `jq -r '.content[0].text'` au lieu du pattern déjà établi dans `devils-advocate-review.sh`/`devils-advocate-precommit.sh`/`devils-advocate-lib.sh` (filtre par `type == "text"`, jamais par index) — le bloc de raisonnement ("thinking") occupe `content[0]` quand il est actif, `.text` y est absent. La réponse brute n'avait pas été sauvegardée avant ce parsing raté, donc le texte reçu (payé) était irrécupérable — deuxième appel nécessaire juste pour relire le même résultat.
Solution : `jq -r '[.content[]? | select(.type == "text") | .text] | join("\n\n")'` (pattern déjà correct ailleurs, jamais réutilisé ici avant ce fix). Ajout structurel : la réponse brute est désormais TOUJOURS écrite dans un fichier temporaire avant tout parsing, supprimé seulement après confirmation d'un texte exploitable — un futur bug de parsing ne fera plus jamais perdre un appel déjà payé. `scripts/devils-advocate-backlog-review.sh`.

------------------------------------------------------------

[CODE] Sujet : condensation Haiku — troncature silencieuse au-delà du seuil nominal
Date : 2026.08.10 / Probleme : un diff cumulé de 720341 caractères a fait échouer la condensation Haiku elle-même (HTTP 400, fenêtre 200k tokens dépassée), et le repli de secours tronquait au plafond fixe (45000 car.) — Fable 5 n'a vu que ~6% du diff réel, contredisant silencieusement la doctrine "jamais tronqué".
Solution : `devils_advocate_split_diff_by_file()` découpe par frontières de fichiers (jamais un fichier coupé en deux), condensation par tranche avec 1 retry, couverture réelle écrite dans un fichier temporaire (jamais une variable globale — piège de sous-shell distinct trouvé le même jour, une variable fixée dans une fonction appelée via `$(...)` ne remonte jamais à l'appelant). `scripts/devils-advocate-lib.sh`/`devils-advocate-review.sh`/`devils-advocate-precommit.sh`.

------------------------------------------------------------

[DEPLOYE] Sujet : migration OpenRouter → API Anthropic directe
Date : 2026.08.10 / Probleme : crédits OpenRouter épuisés en cours de route (HTTP 402), Avocat du Diable silencieusement mort pendant cette fenêtre — dépendance tierce jugée inutile pour un appel direct au même modèle.
Solution : appel direct `api.anthropic.com/v1/messages` (`x-api-key`, nom de modèle `claude-fable-5` SANS le préfixe `anthropic/` propre à OpenRouter). C'est cette migration qui a révélé le piège de format payload (entrée ci-dessus, même jour). `scripts/devils-advocate-lib.sh`.

## Détail complet et contexte plus large
`docs/HANDOFF_AUTOMATISATION.md` garde l'historique complet (gouvernance du modèle, comparaison Gemini/DeepSeek/Fable5, seuils de batching, coûts réels par appel) — ce fichier-ci est un résumé orienté "pièges API à ne jamais redécouvrir", pas un remplacement.
