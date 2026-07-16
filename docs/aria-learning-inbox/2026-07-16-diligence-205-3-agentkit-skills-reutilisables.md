# #205 (3/5) -- Skills bundlées CDP AgentKit : liste vérifiée, ce qui serait
réutilisable pour ARIA plutôt que reconstruit

**Source primaire** : rendu du README sur [PyPI -- coinbase-agentkit](https://pypi.org/project/coinbase-agentkit/)
(paquet Python officiel). Liste complète des 19 action providers embarqués --
les pages `docs.cdp.coinbase.com` et le README GitHub brut ne donnaient qu'un compte
("50+ actions TypeScript / 30+ Python") sans énumération exploitable ; le rendu
PyPI a donné la liste réelle.

## Les 19 action providers d'AgentKit

| Provider | Fonction | Pertinence ARIA |
|---|---|---|
| **x402** | Requêtes HTTP avec support du protocole de paiement | **Directement pertinent** -- client prêt à l'emploi pour le protocole audité en #205(1/5), si x402 est un jour activé côté ARIA. |
| **CDP EVM Wallet** | Swap de tokens + devis de prix | **Directement pertinent** -- brique d'exécution swap prête, pour le pilote 10$ (`docs/pilote-agent-wallet-10usd.md`) si Coinbase Agentic Wallet est retenu. |
| **CDP Smart Wallet** | Swap pour smart wallets (ERC-4337) | Pertinent si le pilote va vers un smart wallet plutôt qu'un wallet EVM classique -- à trancher au choix de produit. |
| **WETH** | Wrap/unwrap ETH | Pertinent pour tout swap natif -- déjà en partie manuel côté ARIA (`sepolia_wallet.send_test_swap_transaction`, wrap WETH -> approve -> exactInputSingle). |
| **WOW** | Opérations sur courbes de bonding (memecoin, protocole Zora) | **Croise directement la niche bonding déjà connue d'ARIA** (`mint_authority.is_bonding_launchpad`, diligence Zora/Flaunch du 13-15/07 déjà faite). AgentKit fournit un CLIENT PRÊT pour cette mécanique précise -- pas besoin de le reconstruire si le pipeline bonding Zora est un jour activé. |
| **Pyth** | Flux de prix on-chain | Redondant avec la cascade OHLCV déjà construite (GeckoTerminal→CoinMarketCap→DexScreener→Dune, 4 étages) -- pas prioritaire, éventuel 5e repli si jamais nécessaire. |
| **ERC20** | Solde/transfert de tokens | Probablement déjà couvert par les appels web3 existants d'ARIA (`base_onchain.py`) -- à vérifier avant de dupliquer. |
| **ERC721** | Solde/transfert/mint NFT | Hors du périmètre actuel d'ARIA (tokens fongibles uniquement). |
| **Compound** | Prêt (supply/withdraw/borrow/repay) | Hors stratégie actuelle (85% VC / 15% trading), mais même famille que "Yield Seeker" (#205 4/5, gagnant CDP Grants) -- piste pour une future poche de rendement si jamais envisagée. |
| **Morpho** | Dépôt/retrait de vault | Idem Compound -- piste de gestion de trésorerie future, pas construite. |
| **Superfluid** | Flux de streaming de tokens | Hors périmètre. |
| **Onramp** | URLs d'achat crypto | Hors périmètre (ARIA ne vend pas d'accès on-ramp). |
| **Basename** | Enregistrement de nom .base.eth | Cosmétique/branding -- hors priorité actuelle. |
| **Twitter** | Gestion de compte, publication | **Redondant** -- ARIA a déjà sa propre intégration X (`x_social.py`, `tweet_compose_workflow.py`, `x_profile.py`) -- ne jamais dupliquer (doctrine "jamais dupliquer un client existant"). |
| **CDP API** | Financement testnet (faucet) | Redondant -- ARIA a déjà son propre wallet Sepolia dédié (`sepolia_wallet.py`). |
| **Hyperbolic** | Services de modèles IA (texte/image/audio/GPU) | Hors périmètre -- ARIA a déjà sa propre pile LLM (Virtuals/Spark). |
| **Nillion** | Coffre de secrets chiffrés | Intéressant en théorie (stockage de secrets) mais hors scope immédiat -- ARIA gère déjà ses secrets via `.env`/vault existant. |
| **SSH** | Connexion/exécution de commandes sur serveur distant | **Signal d'alerte, pas une opportunité** -- une "skill SSH" exposée à un LLM agent est une surface de risque majeure (exécution de commande arbitraire) si jamais mal cadrée. À ne jamais activer sans un cadrage aussi strict que les garde-fous existants d'ARIA sur l'auto-modification. |
| **Wallet** | Opérations natives, vérif de solde | Primitive de base, déjà couverte par tout client wallet. |

## Conclusion actionnable

**Deux vraies pistes de réutilisation directe** si le pilote 10$ (Coinbase Agentic
Wallet) avance : le provider **x402** et le provider **CDP EVM/Smart Wallet
(swap)** -- éviterait de reconstruire ces briques depuis zéro le jour où le
pilote est vraiment lancé. **Une piste secondaire notée mais pas prioritaire** :
le provider **WOW** recouperait directement la niche bonding Zora déjà identifiée
dans la connaissance ARIA (`knowledge/launchpads.yaml`).

**Rien construit** -- décision d'utiliser AgentKit (plutôt que le CDP SDK nu déjà
prévu dans le plan pilote) reste à trancher avec l'opérateur : AgentKit ajoute une
dépendance/abstraction supplémentaire, pas forcément nécessaire si seul le swap et
x402 sont utiles (les deux providers ciblés pourraient aussi être réimplémentés
directement sur `cdp-sdk` sans le framework agent complet).

## Branches ouvertes

- Vérifier si `ERC20`/`Wallet` dupliquent réellement des fonctions déjà présentes
  dans `base_onchain.py` avant toute décision d'adoption.
- Si le pilote 10$ avance, comparer explicitement "AgentKit complet" vs "cdp-sdk nu
  + les deux providers utiles réimplémentés à la main" -- gain de vitesse de dev vs
  coût de dépendance supplémentaire, pas tranché ici.
