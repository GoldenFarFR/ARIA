# HANDOFF — Grounding / anti-hallucination (web_verify, epistemic, brain routing)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[DEPLOYE] Sujet    : Fuzz-testing du routage web/factuel + bug de propagation `public`
Date : 2026.07.09  /  Probleme : `web_verify.py`/`grounding.py` jamais validés en volume ; `public` (opérateur vs visiteur) n'était jamais réellement propagé jusqu'à `resolve_calibrated_answer` (réglage global toujours `True` en prod), root cause d'une hallucination auto-rapportée par ARIA.
Solution : Validé sur 1482+64 cas générés (négations, argot, homographes) à 100%, chaîne `public` corrigée de bout en bout, verrouillée par tests ; `heartbeat.py` rendu résilient (une tâche cassée ne coupe plus le cycle) — web_verify.py / grounding.py (cf. historique git 09/07).

------------------------------------------------------------

[DEPLOYE] Sujet    : Hallucination web citant la mauvaise entité/événement
Date : 2026.07.10 vers 11  /  Probleme : une réponse "LIVE INFO — verified web sources" citait un événement pas encore joué (rugby) ou l'opinion d'un tiers (ex. un investisseur connu) attribuée à tort à ARIA — le prompt ne vérifiait que "même compétition", jamais "même entité que celle interrogée".
Solution : Règle "même ENTITÉ que celle interrogée" ajoutée aux prompts de recalibration web (`_WEB_RECAL_PROMPT_FR`/`_EN`), avec rappel de la vraie doctrine (85% VC + 15% trading, aucune position maximaliste) — limite assumée : correctif de prompt, pas un filtre déterministe — web_verify.py (cf. historique git 11/07, #113).

------------------------------------------------------------

[DEPLOYE] Sujet    : Confabulation identité LLM et méthodologie côté opérateur
Date : 2026.07.11  /  Probleme : `grounded_for_audience(public)` était toujours `False` côté opérateur par design ("Operator gets founder LLM") — tout le grounding (dont `grounded_llm_identity`) restait donc inatteignable sur la conversation opérateur, qui pouvait confabuler son propre modèle ou sa méthodologie.
Solution : Deux détecteurs déterministes audience-indépendants (`is_llm_identity_question`/`is_analysis_methodology_question`) routés vers une réponse zéro-appel-LLM, vérifiés tout en haut de `process()` avant tout autre routage (dont `vc_followup`, qui pouvait les court-circuiter) — brain.py / grounding.py (#105/#110, cf. historique git 11/07).

------------------------------------------------------------

[CODE] Sujet    : Faux positif épistémique sur question d'actualité
Date : 2026.07.11  /  Probleme : `resolve_calibrated_answer` laissait un match SANS vrai trigger (juste un mot générique partagé type "crypto" compté plusieurs fois) court-circuiter une question d'actu détectée (`is_live_info_question`), donnant une réponse canonique hors-sujet avec un faux P(vrai) élevé.
Solution : `_score_claim()` expose désormais `trigger_hit` (vrai trigger explicite requis) — un match sans trigger réel ne bloque plus le routage vers `web_first_answer` — knowledge/epistemic.py (#111, cf. historique git 11/07).

------------------------------------------------------------

[DEPLOYE] Sujet    : Mauvais routage web sur texte long (mot générique isolé)
Date : 2026.07.12  /  Probleme : un texte de raisonnement long (650+ car., un seul mot marché type "prix") partait en recherche web littérale au lieu d'être raisonné.
Solution : Garde de longueur (`_LIVE_INFO_LONG_TEXT_CHARS`) — au-delà, un mot générique seul ne déclenche plus, sauf signal vraiment non ambigu (rugby/coupe du monde/etc.) — web_verify.py (commit `7610dea1`).

------------------------------------------------------------

[CODE] Sujet    : Second chemin de routage web bugué (`verify_external_claim`)
Date : 2026.07.12  /  Probleme : `_QUESTION_RE` n'exigeait le `?` qu'en toute fin de chaîne — un message multi-phrases avec une vraie question au milieu suivie d'une consigne sans `?` final échappait au garde "ceci est une question, pas une affirmation à vérifier", et partait en recherche web littérale via le chemin opérateur.
Solution : `?` détecté n'importe où dans le texte — operator_conversational.py (commit `27c6057`).

------------------------------------------------------------

[CODE] Sujet    : Réponse LLM tronquée jamais signalée
Date : 2026.07.12  /  Probleme : `llm.py::_post_chat` ne vérifiait jamais `finish_reason` — une réponse coupée par l'API (`finish_reason=length`) était affichée telle quelle sans aucun signal ni log.
Solution : Warning journalisé + `truncated=true` enregistré dans le journal d'usage LLM quand `finish_reason == "length"` — llm.py (commit `27c6057`).
