[VPS Research — promu par la session commandement le 18/07]

# Architectures de référence pour un futur module de backtest historique (Freqtrade / NautilusTrader)

## Contexte

Gap déjà documenté à plusieurs reprises dans `CLAUDE.md` (mandat permanent
Research #191/#192, section « Faits établis ») : ARIA ne fait aujourd'hui
que du **paper-trading forward** (`paper_trader.py`, capital fictif, décisions
en temps réel) — aucun module ne permet de rejouer une stratégie contre des
données historiques réelles pour la valider statistiquement avant de la
laisser tourner en conditions réelles (forward ou capital réel). L'entrée de
veille du 18/07 propose deux architectures open-source existantes comme
point de départ plutôt que de concevoir un moteur de backtest from scratch.
Cette note est un dépôt de matière première pour une future diligence — rien
n'a été codé, rien n'a été tranché.

## Les deux pistes

### Freqtrade

- Python, open-source, license GPLv3.
- Backtesting + hyperoptimisation (recherche de paramètres) intégrés
  nativement, avec un moteur de simulation qui rejoue des bougies OHLCV
  historiques contre une stratégie définie en code.
- Pilotable par bot Telegram — patron d'intégration très proche de
  l'architecture ARIA existante (bot Telegram déjà le canal principal
  opérateur), ce qui réduirait potentiellement l'effort d'intégration côté
  UX.
- Écosystème mature, large communauté, nombreuses stratégies publiques à
  étudier (pas forcément à copier).
- Risque à vérifier avant tout choix : la license GPLv3 impose des
  contraintes de distribution du code dérivé — à faire trancher par une
  vraie diligence légale si le code venait à être intégré/modifié
  directement dans le monorepo ARIA (par opposition à un simple appel à un
  service séparé).

### NautilusTrader

- Cœur moteur en Rust (performance, déterminisme), API Python par-dessus.
- Backtesting **déterministe** revendiqué comme différenciateur : mêmes
  données en entrée → mêmes résultats en sortie, contrairement à des
  moteurs qui peuvent introduire un aléa d'ordre d'exécution.
- Conçu pour supporter à la fois backtest ET exécution live avec le MÊME
  code de stratégie (réduit le risque de divergence backtest/prod, un
  piège classique en trading algorithmique).
- Plus jeune/moins de documentation communautaire que Freqtrade, mais
  architecture pensée dès le départ pour la rigueur (pertinent vu
  l'exigence ARIA de preuve/vérifiabilité plutôt que d'anecdote).

## Ce qui reste à faire avant que ce soit actionnable

1. **Licence** — vérifier la compatibilité GPLv3 (Freqtrade) avec le mode
   d'intégration envisagé (dépendance externe vs fork/modification directe).
   NautilusTrader à vérifier aussi (licence différente à confirmer).
2. **Effort d'intégration réel** — ARIA a déjà ses propres clients de
   données (`services/ohlcv.py` GeckoTerminal, DexScreener, etc., doctrine
   « ne jamais dupliquer un client existant ») : un module de backtest
   devrait consommer CES données, pas les redemander à un provider
   différent — à vérifier si Freqtrade/NautilusTrader permettent facilement
   de brancher une source de données OHLCV custom plutôt que leurs
   connecteurs par défaut (Binance, etc., pensés pour des CEX, pas pour des
   tokens Base/Solana microcaps).
3. **Valeur réelle pour ARIA** — le paper-trading forward actuel (protocole
   hebdomadaire 1M$) reste la vraie preuve visée par l'opérateur actuellement
   (cf. « Protocole d'entraînement hebdomadaire » dans `CLAUDE.md`) ; un
   backtest historique serait complémentaire (valider une stratégie AVANT de
   la laisser tourner une semaine complète en forward) mais n'est pas
   demandé comme priorité immédiate — à confirmer avec l'opérateur si ce
   gap doit être comblé maintenant ou rester documenté pour plus tard.
4. **Alternative à considérer** : un backtest maison minimal (rejouer les
   bougies déjà disponibles via `OHLCVClient` contre la logique de décision
   existante de `momentum_entry.py`/`risk_guard.py`) pourrait couvrir le
   besoin sans dépendance externe lourde — à comparer honnêtement avant de
   choisir d'adopter un framework tiers.

## Branches ouvertes (banquées, pas creusées)

- Comparer le coût d'intégration réel des deux frameworks sur un cas
  concret (rejouer une semaine de données Base déjà en base ARIA) avant de
  choisir.
- Vérifier si NautilusTrader ou Freqtrade ont déjà des adaptateurs
  communautaires pour DEX on-chain (Base/Solana) plutôt que seulement des
  CEX — déterminant pour l'effort d'intégration réel.
