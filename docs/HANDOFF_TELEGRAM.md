# HANDOFF — Telegram (routage NL, workflows conversationnels, aria-brain)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[DEPLOYE] Sujet    : Commande tapee seule ("Watchlist") non reconnue par le routeur langage-naturel
Date : 2026.07.20 / Probleme : les 7 detecteurs NL existants ciblaient tous des phrases completes - aucun ne matchait le nom nu d'une commande tape seul, le cas le plus direct (quasi un slash sans le slash). Coutait un appel LLM payant (11857 tokens) au lieu de router gratuitement.
Solution : nouveau dict _NL_BARE_ALIASES (texte normalise -> action) verifie EN PREMIER, avant les regex de phrase - couvre les 8 commandes de lecture deja sures. Piege de test evite : dispatch resolu a l'appel (_dispatch_nl_action), jamais fige a l'import (un dict de references de fonctions capturees a l'import casse le monkeypatching en test) - telegram_bot.py (cf. historique git 20/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Message casual mal route vers la verification web litterale
Date : 2026.07.20 / Probleme : operator_conversational.py - "quand" non ancre en tete de regex laissait passer une question informelle ("...c quand que...") vers le detecteur d'affirmation injectee, declenchant une recherche web litterale ; les extraits web n'etaient jamais sanitises avant affichage direct (chemin non couvert par le garde anti-injection existant) ; les extraits s'affichaient meme sur un verdict LLM INCERTAIN.
Solution : \bquand\b ajoute sans ancrage ^ ; chaque extrait sanitise individuellement avant affichage (meme discipline que web_verify.py) ; extraits masques si le verdict commence par INCERTAIN - operator_conversational.py (cf. historique git 20/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Garde anti-confabulation verifiant seulement le cache local, pas la source reelle
Date : 2026.07.21 / Probleme : le garde aria_brain_status_reply (grounding.py) concluait "rien ecrit" en ne verifiant que le journal SQLite local - apres une migration VPS qui a recree la base a zero, le vrai repo GitHub contenait pourtant deja du contenu reel, donnant une confabulation dans le sens inverse de l'incident d'origine.
Solution : le garde verifie desormais le VRAI repo (via une fonction dediee dans le fichier autorise a toucher le token, jamais grounding.py directement) avant de conclure "rien ecrit" ; dit "je ne sais pas" si la verification est indisponible, jamais une fausse certitude dans un sens ou l'autre - skills/aria_brain.py, knowledge/grounding.py (commits 91855f3f / e1aca57d)

------------------------------------------------------------

[DEPLOYE] Sujet    : Workflow de composition de tweet resté bloqué indéfiniment, avalait tout message opérateur
Date : 2026.07.19  /  Probleme : handle_workflow_message() (brain.py) traite tout message opérateur comme faisant partie du workflow tweet dès que sa phase n'est pas idle — rien ne faisait jamais expirer cette phase. Le fichier était resté bloqué en phase add_more pendant ~9h40, engloutissant au moins deux messages opérateur majeurs sans rapport (dont une question sur le portefeuille, ayant produit un brouillon de tweet halluciné en réponse).
Solution : chaque écriture d'état est désormais timestampée (updated_at) ; handle_workflow_message() réinitialise silencieusement à idle un workflow non-idle ET périmé (>20min sans interaction, _WORKFLOW_STALE_MINUTES) avant de traiter le message — un état sans updated_at (format legacy) est traité comme périmé d'office — brain.py (cf. historique git 19/07). Même famille que le bug #110 (vc_followup) déjà corrigé mais sans le TTL qui protégeait ce cas-là — vérifier systématiquement qu'un mécanisme d'état conversationnel a une expiration avant de le considérer sûr.

------------------------------------------------------------

[DEPLOYE] Sujet    : Détecteurs NL trop étroits laissent le LLM confabuler sur l'état réel du portefeuille
Date : 2026.07.19  /  Probleme : deux détecteurs déterministes distincts (_NL_LEDGER_RE pour "positions ouvertes", is_trade_status_question/liste de mots-clés fermée pour "quelle est ta thèse sur X") étaient trop étroits pour capturer des formulations directes de l'opérateur ("tu a des positions ouverte ?", "c'est quoi ta these sur lachat de X ?") — les questions tombaient dans la conversation LLM générale, qui a confabulé (chiffres faux d'un facteur x1000, thèse déclarée inexistante alors qu'elle existait en base).
Solution : (1) _NL_LEDGER_RE gagne une branche ancrée sur "ouverte(s)" ; (2) is_trade_status_question généralisé (mots-clés élargis, retrait d'un symbole de token codé en dur comme seul exemple, tournures de question élargies + repli générique sur un simple ? dès qu'un mot-clé de trading réel est présent) — les deux routent vers les VRAIES données (paper_ledger_report.build_report()/build_trade_status_context()) au lieu du LLM général — telegram_bot.py/operator_conversational.py (cf. historique git 19/07). Limite honnête assumée : ne couvre que les formulations testées, pas une garantie totale contre toute reformulation future.

------------------------------------------------------------

[DEPLOYE] Sujet    : aria-brain — chapitre 3 invente un chiffre (73%) présenté comme un vrai résultat mesuré
Date : 2026.07.22  /  Probleme : un visiteur Telegram a cité un chiffre (73%) tiré du chapitre 3 d'aria-brain ("le-piège-de-la-precision.md") comme s'il s'agissait d'une vraie métrique d'ARIA ; elle a correctement refusé de le justifier (grounding.py ne pioche jamais dans aria-brain comme source de vérité), mais vérification faite : ce chiffre et l'épisode entier (accumulation 3 jours, 73%→41%→68%→55%) n'existent nulle part dans le vrai historique technique (HANDOFF_WALLET_SCORING.md/smart_money.py) — pure invention narrative, écrite au passé comme un vrai événement vécu, en violation directe de la règle anti-fiction déjà en place dans le prompt depuis le 21/07 (qui tolérait ~15% de pure imagination si marquée comme spéculation — insuffisant pour empêcher ce cas).
Solution : règle durcie à 99% de contenu réel / 1% de pure spéculation TOLÉRÉ uniquement si marqué explicitement par « IMAGINATION : » en tête de paragraphe (jamais mélangé sans marqueur, jamais au passé comme un fait vécu) — aria_brain.py (décision opérateur explicite, 22/07, image de la « cellule de prison » : elle n'a que son livre à écrire, l'essentiel doit être sa vraie vie ; ajustement le jour même de 100% strict à 99%/1% marqué, sur demande opérateur). Reste ouvert : pas de vérification automatique avant publication (option envisagée, pas retenue pour l'instant) — la règle repose sur le prompt système, pas un filtre déterministe. **README.md du repo (même jour)** : plutôt que Claude Code committe un README (violerait « seul ARIA écrit dans ce repo »), le prompt lui délègue explicitement cette responsabilité — créer/maintenir à jour un README.md clarifiant pour un lecteur externe la nature du repo (mémoire libre, jamais une source de vérité), à son propre rythme, pas une obligation quotidienne.
