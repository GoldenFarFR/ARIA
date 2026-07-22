# Diligence — Monad comme future chaîne candidate pour ARIA

**Date** : 2026-07-22
**Origine** : recherche opérateur via Grok/X, vérifiée indépendamment (WebSearch) avant tout jugement — méthode habituelle, jamais un portrait de gagnant pris tel quel.
**Statut** : piste banquée, pas d'action de code engagée.

## Ce qui est confirmé exact (vérifié, pas supposé)

- **Mainnet Monad** : lancé le 24 novembre 2025, pleinement opérationnel en 2026, ~410M$ de TVL atteint rapidement. Compatibilité bytecode EVM native confirmée, tooling Ethereum standard (Hardhat, Foundry, Web3.py) fonctionne sans réécriture. Sources : [Bankless](https://www.bankless.com/monad-mainnet-launches-with-airdrop-and-50-of-supply-locked), [BlockEden](https://blockeden.xyz/blog/2026/01/21/monad-mainnet-high-performance-evm-layer-1-solana-killer/).
- **GoPlus Security couvre Monad** — mieux qu'une simple couverture : partenariat officiel (GoPlus Intelligence + SafeToken Locker intégrés à l'écosystème Monad). Le garde-fou honeypot fail-closed d'ARIA (le seul jamais assoupli) serait directement utilisable. Source : [GoPlus](https://gopluslabs.io/en/security-api).
- **DexScreener couvre Monad** — section dédiée avec paires actives (`dexscreener.com/monad`), confirmée par recherche directe.
- **Unlock de token majeur le 24 novembre 2026** : 16,8 milliards de MON vers la trésorerie Category Labs — point de vigilance macro si jamais une intégration devient active avant/pendant cette date. Source : [TradingView](https://www.tradingview.com/news/coinmarketcal:91bdeaee6094b:0-monad-mon-16-8b-token-unlock-24-nov-2026/).
- **Pont cbBTC Base→Monad via Chainlink CCIP** : réel, annoncé le 2 mars 2026 par Chainlink ET le blog officiel Monad. C'est un pont technique via un tiers (Chainlink), **pas** un partenariat stratégique Base↔Monad. Sources : [blog.monad.xyz](https://blog.monad.xyz/blog/cbbtc-to-monad), [crypto.news](https://crypto.news/chainlink-coinbase-cbbtc-monad-defi-bridge-2026/).
- **Aucune alliance officielle Base↔Monad** : deux projets indépendants, positionnements différents (Base = distribution/retail/paiements via Coinbase ; Monad = performance EVM pure, 10 000 TPS). Coexistence concurrentielle sur le marché des développeurs EVM, pas une collaboration stratégique.

## Le vrai angle mort trouvé — que la recherche externe n'avait pas vérifié

**Blockscout ne semble PAS être l'explorateur officiel de Monad.** Les explorateurs officiels documentés sont **MonadScan** (bâti par Etherscan) et **MonadVision** (BlockVision) — pas Blockscout. Une instance Blockscout existe (`monscout.cp0x.com`) mais c'est un déploiement **communautaire tiers**, pas officiel.

Or `services/blockscout.py` est le client dont dépendent directement, dans ARIA : `mint_authority.py` (détection mint via ABI), la concentration des holders (`safety_screen`/`momentum_entry`), et `dev_wallet.py` (comportement du wallet déployeur — exactement le mécanisme renforcé le 22/07 même jour, tâche #4). Brancher ces garde-fous sur un explorateur communautaire non-officiel serait un vrai risque de fiabilité (uptime, maintenance non garantie), pas juste un changement d'URL RPC. **Ceci invalide l'estimation "intégration en quelques jours"** trouvée dans la recherche d'origine — soit valider que l'instance communautaire est fiable en usage soutenu, soit écrire un nouveau client pour MonadScan/Etherscan (vrai travail de développement).

## Verdict honnête

Le socle (EVM + GoPlus + DexScreener) est solide et réel — bien mieux confirmé que ce à quoi on s'attendait. Mais le vrai bloquant (Blockscout) n'a été trouvé qu'en vérifiant nous-mêmes, pas dans la recherche d'origine — rappel que même une recherche externe qui semble sérieuse doit être recoupée contre les dépendances réelles du code, pas seulement contre des critères génériques (sécurité/liquidité).

## Branches ouvertes (pas creusées, à reprendre si l'intérêt se confirme)

- Vérifier la fiabilité réelle en conditions soutenues de `monscout.cp0x.com` (uptime, débit, cohérence avec la doctrine "90% de la capacité réelle") avant de s'y fier, si jamais l'option "instance communautaire" est retenue.
- Chiffrer le coût réel d'un nouveau client MonadScan/Etherscan-style si l'option "nouvel explorateur officiel" est retenue à la place.
- Revisiter après le 24/11/2026 (unlock) pour voir comment le marché absorbe la pression vendeuse — signal de maturité de l'écosystème avant d'y engager du sourcing réel.
- Aucune action requise avant que l'opérateur ne juge la piste prioritaire — pas de code engagé, pas de gate créé.
