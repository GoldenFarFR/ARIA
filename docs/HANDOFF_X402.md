# HANDOFF — x402 (micropaiements, budget hebdomadaire 5$)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[DEPLOYE] Sujet    : #199 — Cybercentry retenu comme 1ère ressource x402
Date : 2026.07.17  /  Probleme : schéma x402 obsolète attendu (amount/asset="USDC") vs le vrai facilitator CDP (maxAmountRequired/asset=<adresse>) + extra Docker [x402] non installé
Solution : repli sur les deux conventions de schéma + extra installé — services/cybercentry.py (7baa0b67)

------------------------------------------------------------

[CONFIG] Sujet    : Leçon — premier paiement réel gaspillé
Date : 2026.07.17  /  Probleme : 0,02$ dépensés pour vérifier un fait que l'opérateur connaissait déjà gratuitement
Solution : réflexe désormais obligatoire — vérifier une réponse gratuite/plus rapide avant tout appel x402 payant (doctrine, pas de commit)

------------------------------------------------------------

[DEPLOYE] Sujet    : Fournisseurs Bazaar v2 systématiquement en échec
Date : 2026.07.19  /  Probleme : lionx402/sociavault en échec "Invalid payment required response" — le SDK exige le header brut pour décoder une offre v2, notre code reconstruisait un corps synthétique v1 uniquement
Solution : transport du header brut (_raw_v2_header) quand le chemin header a été emprunté — x402_executor.py

------------------------------------------------------------

[DEPLOYE] Sujet    : 2e bug après le 1er fix — mauvais nom de header en sortie
Date : 2026.07.19  /  Probleme : X-PAYMENT envoyé systématiquement au lieu de PAYMENT-SIGNATURE attendu par le SDK en v2
Solution : nom de header dépend de x402Version du requirement, jamais deviné — x402_cdp_signer.py (6556e5af)

------------------------------------------------------------

[DEPLOYE] Sujet    : Traçabilité — aucun paiement relié à un contrat précis
Date : 2026.07.19  /  Probleme : reconstruction forensique fragile après coup pour savoir quel paiement concernait quel token
Solution : champs contract/token_symbol ajoutés à x402_budget.record_spend, propagés depuis conviction_research.py (382565f5)

------------------------------------------------------------

[DEPLOYE] Sujet    : twit.sh câblé en repli de l'API X officielle
Date : 2026.07.19  /  Probleme : created_at twit.sh au format Twitter v1.1 legacy, pas ISO 8601 — sans normalisation, cadence de publication tombait silencieusement à "unknown"
Solution : normalisation explicite avant usage — services/twitsh.py::_normalize_created_at (e4262044)

------------------------------------------------------------

[DEPLOYE] Sujet    : Fournisseurs x402 protocole v2 systématiquement impayables
Date : 2026.07.19  /  Probleme : deux bugs distincts bloquaient TOUT fournisseur x402 v2 du catalogue Bazaar (lionx402, sociavault, deepnets.ai...) : (1) l'exécuteur reconstruisait un corps JSON synthétique pour décoder l'offre au lieu de décoder le header brut payment-required — le SDK officiel n'accepte le corps que pour x402Version==1 ; (2) après signature, le paiement était toujours envoyé sous le header legacy X-PAYMENT au lieu de PAYMENT-SIGNATURE exigé par le v2.
Solution : (1) x402_executor.py transporte le header brut original dans requirement["_raw_v2_header"], x402_cdp_signer.py décode directement via decode_payment_required_header du SDK quand ce champ est présent ; (2) le nom du header envoyé est choisi selon requirement["x402Version"], jamais supposé — x402_cdp_signer.py/x402_executor.py (commit 6556e5af et un commit précédent le même jour). Vérifié par 4 paiements réels après coup.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Verdict de qualité réel des fournisseurs x402 Bazaar pour la recherche X/web
Date : 2026.07.19  /  Probleme : quel fournisseur x402 utiliser pour la recherche X par projet (conviction_research.py) sans re-tester à chaque fois.
Solution : twit.sh (x402.twit.sh/tweets/search + /tweets/user) validé en conditions réelles — le plus utilisé du Bazaar (91k+ appels/30j), schéma compatible X API v2 (date au format legacy Twitter, normalisée dans services/twitsh.py::_normalize_created_at), câblé en REPLI de l'API X officielle, jamais un remplacement. Écartés après test réel : lionx402 (qualité médiocre, wrapper DuckDuckGo), glim.sh (listing Bazaar périmé, 404), sociavault (exige un corps JSON sur GET, non supporté par fetch_paid_resource), deepnets.ai (paiement Solana uniquement, wallet ARIA Base-only). ottoai/twitter-summary retenu ailleurs (digest marché général, pas par-projet) pour market_alerts.py.

------------------------------------------------------------

[DEPLOYE] Sujet    : Paiements x402 non traçables jusqu'au token/contrat concerné
Date : 2026.07.19  /  Probleme : x402_budget.py n'enregistrait ni contrat ni symbole de token — une corrélation après coup entre un paiement orphelin et sa cause réelle exigeait une reconstitution manuelle fragile (horodatages contre paper_position, logs disparus au redéploiement suivant).
Solution : champs optionnels contract/token_symbol threadés de bout en bout (x402_budget.record_spend -> x402_executor.fetch_paid_resource/_blocked -> services/twitsh.py -> conviction_research.py) — chaque paiement, succès ou blocage, reste désormais traçable sans reconstitution forensique — x402_budget.py (commit 382565f5, tâche #143).
