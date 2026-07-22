# HANDOFF — Paper-trading (portefeuille 1M$, protocole hebdomadaire)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.
> Protocole actif à jour : section "Protocole d'entraînement hebdomadaire" dans CLAUDE.md.

[CONFIG] Sujet    : Test 1M$ relancé proprement
Date : 2026.07.16  /  Probleme : —
Solution : reset complet, jour 1 officiel du protocole hebdomadaire — action opérateur, pas de commit

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Cycle #1 clôturé manuellement
Date : 2026.07.18  /  Probleme : —
Solution : -1,98%, 25% de réussite sur 8 trades, objectif +10% non atteint. Protocole d'entraînement hebdomadaire formalisé le même jour (remplace le 30j/7j/14j initial)

------------------------------------------------------------

[DEPLOYE] Sujet    : Incident PLAZM/ESHARE — sélection de paire confondue
Date : 2026.07.19  /  Probleme : le contrat interrogé était confondu avec un autre token dont il n'est que le quote-token, +32950% fictif affiché
Solution : root cause dans 3 endroits distincts (momentum_entry, paper_trader, /vc, vitrine publique) corrigés partout ; position neutralisée manuellement (P&L remis à 0), capital corrigé — momentum_entry.py, paper_trader.py (a122b522)

------------------------------------------------------------

[DEPLOYE] Sujet    : Bug de comptabilité trouvé en corrigeant l'incident ci-dessus
Date : 2026.07.19  /  Probleme : le P&L des paliers de prise de profit déjà réalisés disparaissait du capital total à la clôture finale d'une position
Solution : pnl_usd final inclut désormais le P&L déjà réalisé par palier — paper_trader.py (cf. historique git 19/07)

------------------------------------------------------------

[CONFIG] Sujet    : Reset manuel du portefeuille
Date : 2026.07.20  /  Probleme : —
Solution : décision opérateur ("reset le portfolio") après le marathon de correctifs des rounds 5/6/7 — action opérateur, pas de commit

------------------------------------------------------------

[DEPLOYE] Sujet    : Reset hebdomadaire vulnérable à une mèche isolée
Date : 2026.07.20  /  Probleme : clôturait les positions sur un tick spot brut pile au moment du reset
Solution : prix de clôture robuste — médiane des 5 dernières bougies OHLCV — paper_trader.py (cf. historique git 20/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Endpoint diagnostic /api/aria/diagnostics/paper-ledger (registre de trades depuis le cloud)
Date : 2026.07.16  /  Probleme : Une session cloud sans acces VPS direct ne pouvait pas consulter le registre reel des positions ouvertes/cloturees (these, prix, P&L) sans relais manuel de l'operateur.
Solution : endpoint GET /api/aria/diagnostics/paper-ledger, meme patron que /diagnostics/pool-status et /diagnostics/agent-wallet-ledger — gate ARIA_DIAGNOSTIC_TOKEN (header X-Diagnostic-Access), exempte du gate Privy/operateur, lecture seule — vanguard/backend (commit d81ba5a)

------------------------------------------------------------

[DEPLOYE] Sujet    : P&L des paliers de prise de profit déjà réalisés disparaissait à la clôture finale
Date : 2026.07.19  /  Probleme : portfolio_summary() ne lit realized_pnl_partial que pour les positions encore open ; close_position() ne sommait que le P&L de la DERNIÈRE tranche — le P&L des paliers déjà réalisés via reduce_position (prise de profit échelonnée) disparaissait silencieusement du capital total au moment de la clôture finale, sous-estimant le vrai capital sur toute position avec sortie par tiers.
Solution : pnl_usd final = P&L de la dernière tranche + realized_pnl_partial déjà accumulé (celui-ci reste par ailleurs visible séparément sur la ligne) — paper_trader.py (commit 5365b643).

------------------------------------------------------------

[CONFIG] Sujet    : Correction manuelle du capital paper-trading après le gain fictif PLAZM/ESHARE
Date : 2026.07.19  /  Probleme : le bug de mislabeling quote-token (cf. HANDOFF_PIPELINE_MOMENTUM) avait figé equity_high_water_mark à ~12,5M$ (faux pic), bloquant déjà toutes les nouvelles entrées en prod via le coupe-circuit dur de risk_guard (faux drawdown de -61%).
Solution : correction en 3 volets, uniquement sur les DONNÉES de la position concernée (aucun changement de code) — sauvegarde de aria.db prise avant écriture ; pnl_usd/pnl_pct/realized_pnl_partial de la position remis à 0 (jamais supprimée, annotation dans close_notes) ; equity_high_water_mark réinitialisé à 1 000 000$ ; risk_guard.resume_new_entries() appelé. Capital vérifié après coup : 997 685$ (-0,23%), coupe-circuit levé — cf. historique git 19/07 (/opt/aria-data/backups/).

------------------------------------------------------------

[DEPLOYE] Sujet    : Scorecard objective de readiness capital réel (/feuvert)
Date : 2026.07.10  /  Probleme : question directe opérateur ("tu ferais confiance à ARIA pour 100k$ ?") sans outil pour y répondre objectivement plutôt que par avis subjectif.
Solution : skills/real_money_readiness.py calcule les cases du barème docs/protocole-argent-reel.md depuis le vrai journal vc_predictions (integrity/robustness/sample_size/benchmark/risk/judge/lawyer) ; commande /feuvert Telegram admin-only — real_money_readiness.py (cf. historique git 10/07)

------------------------------------------------------------

[CODE] Sujet    : Poche satellite du reset hebdomadaire (Tâche 2, option 3 confirmée par l'opérateur)
Date : 2026.07.22  /  Probleme : le reset hebdomadaire force-clôturait TOUTE position ouverte sans exception, même une position au potentiel encore clairement intact (régime Euphorie, stop ATR loin d'être touché, R/R restant solide) — coupait un vrai gagnant au milieu de son mouvement uniquement parce que le calendrier des 7 jours tombait pile ce jour-là. Exempter purement et simplement entrait en conflit avec la règle gravée "ARIA repart à 1M$ CHAQUE semaine" (comparabilité semaine/semaine).
Solution : poche SÉPARÉE et PLAFONNÉE (5% du capital de départ fixe = 50 000$, cumulé), hors du verdict hebdomadaire principal. Éligibilité (`_satellite_pocket_eligible`) : `strategy == "momentum"` (Formule B/vc_thesis non couverte) + régime ratchet (min(entrée, maintenant) — jamais un assouplissement) == Euphorie + stop ATR pas touché + R/R RESTANT (pas celui de l'entrée) >= 1.5, calculé via `_compute_active_stop` (extrait de la boucle de gestion pour être réutilisé sans dupliquer). Meilleurs R/R restants admis en premier si plusieurs candidats dépassent le plafond ; le reste force-clôturé comme avant. Verdict de la semaine calculé sur `cash + coût immobilisé en poche satellite` (jamais `equity` complète, qui inclurait la valorisation flottante) — neutralise totalement l'effet de la poche satellite sur `validated`/`return_pct`, ni bonus ni pénalité. Nouvelle colonne `pocket` ('main'/'satellite') sur `paper_position`/`paper_position_archive` — une position satellite n'est JAMAIS wipée par l'archivage hebdomadaire, ni réévaluée une fois promue (sort uniquement par sa propre clôture normale). Limite connue, documentée dans le code plutôt que cachée : `risk_guard` lit l'équité COMPLÈTE (poche satellite incluse) pour son coupe-circuit de drawdown — plafond bas (5%) pour borner cet impact en v1, séparer les deux poches dans `risk_guard` resterait un chantier distinct si le besoin se confirme. `paper_trader.py` — 16 nouveaux tests (`test_paper_weekly_cycle.py`), suite complète 6781 passed / 17 skipped, `test_coherence.py` vert (non commité au moment de cette entrée).
