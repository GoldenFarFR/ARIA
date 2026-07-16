# #205 (2/5) -- Techniques de vitesse des bots sur Base : écart réel avec #194

**Constat central, vérifié -- change le cadrage de la question posée.** La prémisse
"les bots établis utilisent Flashbots/mempool privé pour être plus rapides" est
**en grande partie inapplicable à Base telle qu'elle existe aujourd'hui**, pas
seulement un écart de mise en oeuvre côté ARIA.

## Base n'a pas de mempool public exploitable de la même façon qu'Ethereum L1

Base (comme la plupart des rollups -- Arbitrum, Optimism, Unichain, ZKsync) opère
un **séquenceur centralisé** (Coinbase pour Base) qui ordonne les transactions
lui-même, aujourd'hui typiquement en **premier arrivé, premier servi** (parfois
avec mécanisme de priority fee) -- et surtout, **le mempool L2 lui-même est déjà
privé par construction** : un observateur externe ne voit pas les transactions en
attente de la même façon que sur le mempool public d'Ethereum L1. Conséquence
directe citée par les sources : *"Most rollups (e.g., Arbitrum, Optimism, Base,
Unichain, ZKsync) operate private mempools, rendering front-running infeasible"* --
le MEV exploitable sur ces L2 se limite largement au **back-running** (arbitrage,
liquidations), pas au front-running classique qu'un mempool public permettrait.
Sources :
[Chainlink -- What Is an L2 Sequencer?](https://chain.link/article/what-is-l2-sequencer),
recherche croisée (Orochi Network, eco.com, ChainScore Labs).

**Conséquence pour ARIA** : Flashbots Protect (outil cité dans la demande initiale)
est un produit **Ethereum L1-centré** -- son utilité sur Base est structurellement
différente (voire nulle pour le cas d'usage "éviter le sandwich sur un swap Base",
puisque le sandwich via mempool public y est déjà largement neutralisé par le
design du séquenceur). Ne pas investir dans une infra Flashbots-style pour Base sur
la seule base de son succès sur Ethereum L1 -- vérifier d'abord si le problème
qu'elle résout existe réellement sur Base.

## Où est alors le vrai avantage de vitesse sur Base ?

Si le front-running via mempool est déjà structurellement limité, la vitesse pour
"être avant tout le monde" (exigence opérateur explicite, CLAUDE.md #194) se joue
ailleurs :
1. **Détection/réaction la plus rapide à un événement on-chain** (nouveau pool,
   nouveau boost DexScreener, graduation de bonding curve) -- latence réseau vers
   Base + vitesse d'indexation du fournisseur de données utilisé, pas mempool
   privé. C'est exactement l'angle déjà identifié et banqué dans CLAUDE.md **#196**
   (écoute WebSocket temps réel DexScreener, vérifiée fonctionnelle le 15/07 --
   handshake réel confirmé) -- cette recherche **confirme et renforce la priorité de
   #196** plutôt que d'ouvrir un nouveau chantier MEV/Flashbots.
2. **Latence de soumission au séquenceur** -- utiliser un RPC Base rapide/fiable
   (déjà un choix d'infra basique, pas un outil MEV spécialisé).
3. **Vitesse de décision propre à ARIA** -- le pipeline momentum #194 est déjà conçu
   pour être déterministe-d'abord (honeypot + TA avant tout appel LLM), ce qui est
   la bonne architecture pour la vitesse ; le vrai goulot de latence documenté ce
   mois-ci était le THROTTLE GeckoTerminal (2,1s/appel, déjà connu, déjà pris en
   compte dans le calibrage de cadence #195), pas un manque d'infra anti-MEV.

## Outils de sniping existants sur Base -- vérifiés, pas de découverte technique
   nouvelle

Des services commerciaux de sniping (ex. "MEV Sniper Bot") annoncent une couverture
Base via "dedicated private RPC endpoints and Flashbots-compatible private mempool
relay bundles" -- mais ce langage marketing ne clarifie pas s'il s'agit d'un vrai
avantage technique sur Base ou d'une réutilisation de la même infra que pour
Ethereum/BSC/Arbitrum sans bénéfice spécifique prouvé sur Base. **Pas de preuve
trouvée dans cette recherche qu'un avantage de vitesse mesurable et spécifique à
Base existe via cette classe d'outils** -- absence de preuve, pas preuve d'absence,
mais rien qui justifie une dépense d'ingénierie dans cette direction avant d'avoir
un signal plus concret.

## Conclusion actionnable

**Aucun nouveau chantier recommandé côté "MEV protection Base"** -- le problème que
ces outils résolvent sur Ethereum L1 n'existe pas dans la même mesure sur Base. Le
véritable levier de vitesse identifié par cette recherche est déjà dans le backlog
(**#196**, écoute temps réel) -- cette note sert à confirmer/prioriser #196 plutôt
qu'à proposer un nouvel axe. Rien construit ce soir (mandat = veille).

## Branches ouvertes

- Si l'écosystème Base évolue vers un séquenceur partagé/décentralisé (Superchain,
  Espresso Systems visé pour 2026 selon les sources) -- revérifier si le paysage
  MEV change à ce moment-là, pas avant.
- Vérifier une fois #196 construit si un avantage de latence mesurable existe
  réellement contre le polling classique -- donnée empirique à collecter après
  déploiement, pas avant.
