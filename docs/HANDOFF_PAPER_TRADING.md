# HANDOFF — Paper-trading (portefeuille 1M$, protocole hebdomadaire)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.
> Protocole actif à jour : section "Protocole d'entraînement hebdomadaire" dans CLAUDE.md.

[DEPLOYE] Sujet    : reset_portfolio() effacait l'historique sans archive (DROP brut)
Date : 2026.07.24 / Probleme : audit 5-agents -- reset_portfolio() (reset manuel, ex. apres un incident forçant un redémarrage hors cycle, cf. CNX le 22/07) faisait un DROP TABLE direct sans jamais archiver dans paper_position_archive au préalable, contrairement à run_weekly_reset() qui archive toujours avant de vider -- confirmé en base : le Cycle #2 (18-22/07) n'a laissé aucune trace archivée après le reset manuel du 22/07.
Solution : reset_portfolio() archive désormais tout le contenu de paper_position (ouvert ET clôturé) sous le cycle_number courant avant le DROP, même doctrine non-destructive que run_weekly_reset -- paper_trader.py, tests dédiés (cf. historique git 24/07)

------------------------------------------------------------

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

------------------------------------------------------------

[CODE] Sujet    : Décote de liquidité sur le PnL affiché des positions ouvertes (mark-to-market)
Date : 2026.07.23  /  Probleme : `portfolio_summary()` valorisait chaque position ouverte au prix spot exact (`price_lookup(contract)`), comme si toute sa taille pouvait être liquidée sans le moindre glissement — un x50 fictif était possible sur un pool devenu mince (point trouvé par le stress-test, jamais corrigé jusqu'ici).
Solution : nouveau `risk_guard.simulated_exit_price` (symétrique de `simulated_fill_price` déjà utilisé à l'achat, même formule `_price_impact_pct` — jamais un second calcul divergent) — dégrade le prix affiché d'une position ouverte selon l'impact qu'une vente de sa taille aurait sur le pool. Liquidité utilisée : `last_liquidity_usd` (positions `vc_thesis`, watermark déjà tenu à jour depuis le 22/07) sinon repli sur `entry_liquidity_usd` (toutes stratégies, figé à l'entrée) — approximation honnête plutôt qu'aucune décote du tout ; `None` sur les deux (position ouverte sans liquidité connue) -> fail-open, comportement historique inchangé. `risk_guard.py` / `paper_trader.py` — 9 nouveaux tests (`test_risk_guard.py`, `test_paper_trader.py`), suite ciblée 325 passed, suite complète à confirmer, `test_coherence.py` vert (non commité au moment de cette entrée).

------------------------------------------------------------

[CODE] Subject  : Daily trade FLOOR -- force >= 5 momentum trades/day (diagnostic)
Date : 2026.07.23 / Problem : operator wants ARIA to make at least 5 trades/day "for now, so we can judge the tokens she picks even if she loses" -- the pipeline's selectivity produced too few opens/day to evaluate her selection. Root cause is selectivity, not scan frequency (the WebSocket already covers ~30s detection).
Solution : new INDEPENDENT additive cycle `paper_trader.run_daily_trade_floor_cycle` (never touches the normal `run_paper_cycle` decision path -- zero risk to normal entries). Paces `DAILY_TRADE_FLOOR=5` across the day (`_daily_floor_target` = ceil(5 * fraction-of-day-elapsed)); when behind, it evaluates the same momentum candidates in RELAXED mode and opens small tagged trades. Relaxed mode (`momentum_entry.evaluate_momentum_entry(relaxed=True)` -> `evaluate_hard_gates(relaxed=True)`, both default False = strictly unchanged normal behavior, proven by the full 213-test momentum suite still green) waives ONLY the two QUALITY gates (24h-volume floor, established-project-profile) + the R/R-floor / RVOL-reject; it NEVER waives the SAFETY gates (blacklist, liquidity floor, wash-trading, holder concentration, honeypot) NOR the parabolic cap (kept, matching the operator's "never buy the top" instinct) NOR the final LLM security guard. Forced trades: SMALL (`FLOOR_TRADE_ALLOC_PCT=1%` of start), capped `FLOOR_MAX_OPENS_PER_CYCLE=2`/cycle (no burst), tagged `discovery_channel="floor"` + `conviction_tier="floor"` so `/performance` separates them from real conviction picks. Respects the risk circuit breaker (operator decision 07/23: stops forcing on drawdown/consecutive-loss hard stop -- observing her risk management is itself diagnostic), MAX_POSITIONS, cash, `/stop`. Wired to heartbeat `daily_trade_floor_cycle` (60min), gated `ARIA_PAPER_TRADING_ENABLED` AND `ARIA_DAILY_TRADE_FLOOR_ENABLED` (both required, OFF by default). Same commit lowered `momentum_discovery_cycle` 60->30min (operator "on peut baisser le delai" -- WebSocket already covers speed, 30min catches the trending universe faster at modest GoPlus cost, not lowered to 15min per the rate-limit doctrine). `momentum_entry.py`/`paper_trader.py`/`heartbeat.py` -- 9 new floor tests (`test_paper_trader.py`), full suite to confirm, `test_coherence.py` green (not yet committed at the time of this entry).
