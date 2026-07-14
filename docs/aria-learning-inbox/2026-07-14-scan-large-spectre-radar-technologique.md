[VPS Research]

# Radar technologique — reprise du mode par défaut après inactivité

## Contexte et méthode

Retour au rôle par défaut (scan large-spectre, pas une tâche ciblée) après
une période d'inactivité. Quatre branches banquées, une piste trouvée et
**explicitement écartée** avant d'être creusée (transparence sur ce qui a
été vu et refusé, pas juste sur ce qui a été retenu) — conformément à la
doctrine des frontières absolues.

---

## 1. Ethereum Attestation Service (EAS) — répond directement à un besoin déjà documenté

**Légitimité** : infrastructure open-source de bien public, deux contrats
intelligents seulement (registre de schémas + attestation), déployée sur
Ethereum mainnet et de nombreux L2 dont Base (`base.easscan.org`
confirmé) depuis plusieurs années — pas un projet expérimental.
Documentation complète (`docs.attest.org`), explorateur no-code
(EASSCAN).

**Connectabilité — lien direct avec une exigence déjà actée** :
`docs/protocole-argent-reel.md` (§2, critère de track record) exige
explicitement un « horodatage + empreinte SHA-256, idéalement **ancrée
on-chain (hash sur Base)** » pour chaque verdict avant tout feu vert
argent réel. EAS est construit **exactement pour ce besoin** : créer un
schéma d'attestation (ex. « verdict ARIA : hash, timestamp, issue ») et
publier chaque verdict comme attestation on-chain sur Base, vérifiable
publiquement par n'importe qui via EASSCAN, sans construire de contrat
sur-mesure. Le mécanisme de timestamping EAS documente explicitement une
« preuve tamper-proof que les données existaient avant l'horodatage » via
arbre de Merkle — le proof exact recherché.
**Complète une brique déjà existante côté ARIA** (le hash d'ancrage
on-chain de décision constaté dans `onchain/sepolia_autonomous.py` d'un
précédent audit — non ré-ouvert ici, hors périmètre de cette veille en
lecture seule) plutôt que d'inventer un besoin nouveau.

**Limites connues** : une attestation on-chain sur Base coûte du gas réel
à chaque publication (même faible) — implique une décision de fréquence
(chaque verdict ? un résumé périodique ?) laissée au commandement,
pas tranchée ici. EAS n'est qu'un mécanisme de preuve, pas un juge — ne
remplace en rien le moteur de jugement adversarial déjà exigé par le
protocole.

---

## 2. Ponder — framework d'indexation EVM auto-hébergé, alternative/complément à la dépendance GeckoTerminal

**Légitimité** : framework TypeScript open-source (`ponder.sh`,
`github.com/ponder-sh/ponder`), maintenu activement, benchmarks
indépendants le citant comme 10 à 15x plus rapide que The Graph
(subgraphs) sur certaines tâches, adopté par des équipes DeFi pour du
tracking temps réel — pas un side-project isolé.

**Connectabilité** : ARIA dépend aujourd'hui de l'API publique
GeckoTerminal en polling (`base_crawler.py`, `services/ohlcv.py`) pour la
découverte et l'historique OHLCV — un point de défaillance unique déjà
identifié dans plusieurs veilles précédentes (rate limits GeckoTerminal,
schéma de tri par défaut trompeur déjà corrigé une fois). Ponder
permettrait, en théorie, une indexation Base auto-hébergée
(événements de pool en temps réel écrits dans Postgres, requêtables en
GraphQL/SQL) — **réduirait la dépendance à un seul fournisseur externe**
pour la découverte de tokens, sans changer l'architecture en aval
(`token_absorber`, `screened_token` restent les mêmes consommateurs).

**Limites connues** : nécessite une infrastructure propre (un nœud
RPC Base fiable + une base Postgres à opérer et monitorer) — pas un
simple appel API gratuit comme les briques actuelles, un vrai chantier
d'infra avec un coût opérationnel (RPC, stockage, maintenance) à mettre
en balance avec le gain de résilience. Pas un remplacement immédiat,
plutôt une piste de robustesse à moyen terme.

---

## 3. ERC-8004 (« Trustless Agents ») — standard émergent d'identité/réputation on-chain pour agents IA

**Légitimité** : EIP proposé le 13 août 2025, contributeurs identifiés de
poids (MetaMask, Ethereum Foundation, Google, Coinbase), déploiements de
référence live sur Ethereum mainnet fin janvier 2026, puis Base Sepolia,
Linea Sepolia, Hedera Testnet, et Avalanche C-Chain (annoncé février
2026) — **adoption réelle et multi-acteurs, pas un simple brouillon
théorique**, mais un standard encore jeune (moins d'un an d'existence).

**Connectabilité** : trois registres on-chain légers (identité,
réputation, validation) permettant à des agents autonomes tiers de se
découvrir et s'évaluer mutuellement « sans confiance préexistante ».
Pertinent pour ARIA en tant qu'**observateur/lecteur**, pas
participant : si l'écosystème agentique Base (Virtuals et au-delà)
adopte ce standard, ARIA pourrait un jour lire les registres de
réputation ERC-8004 d'un token/projet agentique qu'elle scanne comme un
signal de légitimité supplémentaire (même logique que GoPlus/Blockscout
aujourd'hui : lecture seule d'une donnée de confiance tierce, jamais une
inscription d'ARIA elle-même dans ces registres).

**Limites connues** : standard trop jeune pour juger de sa pérennité
(moins d'un an), couverture Base encore en testnet (Base Sepolia) au
moment de cette recherche, pas encore mainnet Base confirmé — **piste à
re-vérifier dans quelques mois**, pas encore actionnable telle quelle.

---

## 4. x402 (paiement HTTP-natif pour agents) — signal d'écosystème à connaître, pas un chantier

**Légitimité** : protocole ouvert lancé par Coinbase et Cloudflare,
adoption chiffrée publiquement (69 000 agents actifs, 165 millions de
transactions, ~50M$ de volume cumulé rapportés par Coinbase fin avril
2026) — déjà croisé indirectement dans une veille précédente de ce
dépôt (Nansen propose un tier de tarification x402 pour ses endpoints
Smart Money) : **ce n'est pas une découverte isolée, c'est un motif qui
revient** dans l'écosystème que scanne ARIA.

**Connectabilité — awareness seulement, aucune action proposée** : le
mécanisme (un serveur répond HTTP 402, l'agent signe un paiement
stablecoin via un contrat facilitateur sur Base L2, le serveur vérifie et
sert la ressource) est en train de devenir un standard de facturation
pour les API consommées par des agents IA — **pertinent à connaître
pour comprendre comment les futurs fournisseurs de données qu'ARIA
pourrait consulter (Nansen et d'autres) factureront leurs accès**, pas
comme une capacité à construire pour ARIA elle-même. **Toute
implémentation réelle impliquerait qu'ARIA détienne et dépense du
capital de façon autonome — hors frontière absolue, explicitement non
proposé ici.**

**Limite à documenter honnêtement** : la littérature de sécurité déjà
identifie des vecteurs d'attaque spécifiques à ce protocole tout jeune
(arXiv 2605.11781, « Five Attacks on x402 Agentic Payment Protocol »)
— renforce la prudence déjà actée par ARIA de ne pas s'approcher de
l'exécution de paiement autonome tant que ce standard n'a pas mûri.

---

## Piste trouvée et explicitement écartée (pas creusée, pas banquée comme opportunité)

**Coinbase AgentKit / CDP SDK** — toolkit officiel permettant à un agent
IA de détenir un wallet et d'exécuter des actions on-chain autonomes
(transferts ERC-20, swaps, appels de contrat, mint NFT), avec support
natif du protocole x402 pour payer automatiquement des accès API. Trouvé
en recherchant autour de x402/ERC-8004 (écosystème adjacent), **mais sa
proposition de valeur centrale est exactement ce que la doctrine ARIA
interdit d'approcher** (wallet autonome + exécution on-chain autonome +
capital réel potentiel). Conformément à la consigne, **cette piste n'est
pas creusée davantage et n'est pas présentée comme une opportunité** —
mentionnée ici uniquement par souci de transparence sur ce qui a été vu
et refusé, pas comme un « branches ouvertes » à explorer plus tard.

---

## Branches ouvertes (banquées, pas creusées maintenant)

- **Fréquence d'ancrage EAS** : si la brique 1 est retenue, une décision
  de conception reste ouverte (chaque verdict individuel vs résumé
  périodique) — arbitrage coût de gas / granularité de preuve, à trancher
  avec le commandement, pas creusé ici.
- **Interaction EAS ↔ ancrage Sepolia existant** : le mécanisme
  d'ancrage de hash déjà présent côté ARIA (`onchain/sepolia_autonomous.py`,
  identifié dans un audit précédent, non rouvert dans cette veille par
  prudence sur le périmètre onchain) pourrait soit être remplacé par
  EAS, soit coexister — comparaison approfondie à faire séparément,
  potentiellement par une session ayant explicitement mandat de regarder
  ce module.
- **Ponder vs alternatives** (Envio, Subsquid — croisés en passant dans
  les résultats de recherche sans être vérifiés ici) : plusieurs
  frameworks d'indexation EVM open-source existent, Ponder n'a pas été
  comparé à ses concurrents directs dans cette veille — à faire si la
  piste d'indexation auto-hébergée est retenue comme sérieuse.
  - Note liée : `2026-07-12-dexscreener-api-discovery-capabilities.md`
    (mentionné dans l'inbox existant, à recouper si la discussion sur
    la résilience de la découverte de tokens reprend).
- **ERC-8004 à re-vérifier dans quelques mois** : standard trop jeune
  (< 1 an), couverture Base encore en testnet au moment de cette
  recherche — revisiter quand/si un déploiement mainnet Base est
  confirmé et qu'une masse critique de projets agentiques Base
  l'adoptent réellement (pas juste les registries vides).

## Sources

- [attest.org — EAS, sign and verify data about anything](https://attest.org/)
- [docs.attest.org — How EAS Works](https://docs.attest.org/docs/core--concepts/how-eas-works)
- [EAS Tools sur Base (base.easscan.org)](https://base.easscan.org/tools)
- [QuickNode — What Is Ethereum Attestation Service (EAS)](https://www.quicknode.com/guides/ethereum-development/smart-contracts/what-is-ethereum-attestation-service-and-how-to-use-it)
- [ponder.sh — documentation officielle](https://ponder.sh/)
- [ponder-sh/ponder (GitHub)](https://github.com/ponder-sh/ponder)
- [Dune Blog — The State of EVM Indexing](https://dune.com/blog/the-state-of-evm-indexing)
- [Eco — What is ERC-8004? The Ethereum Standard Enabling Trustless AI Agents](https://eco.com/support/en/articles/13221214-what-is-erc-8004-the-ethereum-standard-enabling-trustless-ai-agents)
- [QuickNode — ERC-8004: A Developer's Guide to Trustless AI Agent Identity](https://www.quicknode.com/blog/erc-8004-a-developers-guide-to-trustless-ai-agent-identity)
- [arXiv 2606.26028 — Can Trustless Agents Be Trusted? Empirical Study of ERC-8004](https://arxiv.org/pdf/2606.26028)
- [x402.org — whitepaper officiel](https://www.x402.org/x402-whitepaper.pdf)
- [Eco — x402 Protocol Explained: How AI Agents Pay Onchain](https://eco.com/support/en/articles/12328618-x402-protocol-explained-how-ai-agents-pay-onchain)
- [arXiv 2605.11781 — Five Attacks on x402 Agentic Payment Protocol](https://arxiv.org/pdf/2605.11781)
- [Coinbase — AgentKit overview (CDP docs)](https://docs.cdp.coinbase.com/agent-kit/welcome)
- [coinbase/agentkit (GitHub)](https://github.com/coinbase/agentkit)
- Code/doc ARIA vérifié : `docs/protocole-argent-reel.md` (§2, exigence
  SHA-256/ancrage on-chain — lecture directe, 2026-07-14) ; existence
  confirmée mais non rouverte : `packages/aria-core/src/aria_core/onchain/sepolia_autonomous.py`
  (périmètre onchain, hors lecture par prudence sur les frontières)

## Frontières confirmées respectées

Aucun fichier `permission_mode`/`wallet_guard`/règles-uniques/`config.toml`
ouvert. Le module `onchain/sepolia_autonomous.py` a été localisé
(`grep`/`find`) mais **délibérément pas lu en détail** dans cette veille
lecture-seule à mandat large-spectre, pour respecter la frontière
« exécution autonome » même par simple curiosité de vérification. Aucun
capital réel, aucune exécution autonome, aucune auto-modification
approchée dans les quatre branches banquées. Une piste (Coinbase
AgentKit) a été rencontrée en cours de recherche et explicitement écartée
sans être creusée ni présentée comme une opportunité, conformément à la
consigne « une branche qui mènerait là n'est pas une opportunité ».
