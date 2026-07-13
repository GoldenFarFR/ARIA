# Diligence — launchpads Base : Flaunch et Zora Coins

Source : VPS Research, 13/07/2026. Recherche externe sourcée (WebSearch, sources
citées) — aucun appel à du code interne (l'architecture multi-launchpad réelle,
`services/launchpad_discovery.py` etc., vit dans ce monorepo mais n'était pas
clonée sur la machine où la recherche a été menée ; traitée en diligence de
fond autonome, sur le modèle de structure de l'audit ODEI).

## Flaunch

**Mécanisme** : launchpad Base sur Uniswap v4. "Fixed Price Fair Launch" —
fenêtre de 30 minutes à prix fixe interdisant la vente, contre-mesure au dump
précoce. Aucun frais protocole sous 10k$ de market cap.

**Frais / garde** : 1% par swap, réparti en cascade (waterfall) entre jusqu'à 3
destinataires. Le créateur fixe sa part (0-100%) et peut la désigner à jusqu'à
100 wallets/comptes sociaux. Droit aux frais tokenisé en NFT transférable
(marché secondaire "Memestream") — un actif de revenu cessible, pas un split
figé. Fee switch protocole gouverné par les détenteurs du token FLAY.

**API de découverte réelle** : oui — Flaunch V2, documentée
(`docs.flaunch.gg/references`, portail `builders.flaunch.gg`, clé API),
endpoint `api-v2.flayerlabs.xyz/v2/coins/trending` confirmé (Bearer). SDK
TypeScript officiel (`flayerlabs/flaunch-sdk`) + subgraph. Signal nettement
plus fort que DexScreener sur ce point précis (pas de classement natif par
chaîne côté DexScreener).

**Équipe** : Flayer Labs, dépôt GitHub organisé et actif. Co-fondateur nommé :
Joel Strahl (liste incomplète au-delà). Contrats "audités, aucune
préoccupation identifiée" selon la doc officielle — auditeur précis non
confirmé, à vérifier avant tout usage. Token FLAY listé LBank/BingX (listings
réels, pas juste des agrégateurs).

**Rug** : aucun signalement spécifique attribué au protocole lui-même
(distinct des memecoins individuels lancés dessus). Fenêtre fixe 30 min =
contre-mesure, pas garantie absolue.

**Verdict : vert pour usage en découverte/lecture**, réserve sur l'auditeur
non nommé et l'équipe partiellement anonyme.

## Zora Coins

**Mécanisme** : protocole "Coins" de Zora sur Base — chaque post/profil devient
un token tradable via pool Uniswap v4 dédié + hook personnalisé. "Sniper Tax"
anti-snipe démarrant à 99%, décroissant sur 10 secondes.

**Garde de fonds** : creator coins vestent linéairement sur 5 ans (déblocage
progressif). Créateur touche 1% de frais par trade ultérieur. Frais protocole
très bas (0,01%).

**API de découverte réelle** : oui, plus mature que Flaunch sur ce point — SDK
officiel `@zoralabs/coins-sdk` (npm), API REST avec Swagger interactif
(`api-sdk.zora.engineering/docs`), requêtes "Explore" dédiées (nouveaux
tokens, trending, top gainers) — exactement le type de classement qui
manquait chez DexScreener.

**Frais** : 1% créateur + 0,01% protocole, structure stable et documentée.

**Équipe** : Zora Labs, co-fondé par Jacob Horne (ex-Coinbase, identité
publique confirmée) et Dee Goens — équipe non anonyme. Tokenomics ZORA
publiées en détail (supply fixe 10 milliards, répartition précisée, cliffs
6 mois puis vesting 36-48 mois).

**Rug — signal d'alerte réel identifié** : incident documenté "Base is for
everyone" — un token émis via Zora Coins par le compte officiel Base a atteint
17M$ de market cap puis chuté de 90% en 5 minutes (qualifié d'"expérience
artistique" par Base/Jesse Pollak, pas une vente organisée). Signal
structurel plus large : le pool de liquidité automatisé de Zora reste
vulnérable au sniping par bots malgré la Sniper Tax (détection en
millisecondes, pump puis dump immédiat) — un token "trending" peut être un
pump snipé plutôt qu'une traction organique.

**Verdict : vert avec garde-fou explicite à coder côté ARIA** — équipe
identifiée, tokenomics transparentes, API mature, mais le filtre "établi +
actif, pas juste pompé" (déjà recommandé pour DexScreener/GeckoTerminal)
s'applique ici avec une urgence particulière.

## Synthèse

| | Flaunch | Zora Coins |
|---|---|---|
| API découverte | Oui (V2 REST, Bearer, SDK TS) | Oui, plus mature (SDK+REST+Swagger+Explore) |
| Équipe | Partiellement identifiée | Totalement identifiée |
| Anti-dump | Fenêtre fixe 30 min | Vesting 5 ans + Sniper Tax (contournée en pratique) |
| Signal de rug | Aucun sur le protocole lui-même | Un incident notable + sniping documenté |
| Verdict | Vert, réserve auditeur non nommé | Vert, garde-fou anti-snipe à coder |

Aucun signal rouge disqualifiant sur l'un ou l'autre. Les deux ont une vraie
API de découverte (contrairement à DexScreener) et une équipe au moins
partiellement traçable.

## Branches ouvertes (banquées, non creusées)

- **Memestream** (marché secondaire des NFT de droits de frais Flaunch) — un
  actif de revenu cessible distinct du token lui-même, potentiel angle
  d'analyse si Flaunch est un jour branché.
- **Gouvernance FLAY** (fee switch protocole contrôlé par les détenteurs) —
  signal de décentralisation à surveiller si le protocole prend de l'ampleur.
- **SDK Zora (`@zoralabs/coins-sdk`)** comme brique potentielle au-delà du
  scan on-chain pur — API "Explore" assez riche pour d'autres usages que la
  seule découverte de candidats VC.

## Sources

- [Flaunch Docs](https://docs.flaunch.gg/)
- [Flaunch API references](https://docs.flaunch.gg/references)
- [Flaunch Developer Portal](https://builders.flaunch.gg/)
- [flayerlabs/flaunch-sdk (GitHub)](https://github.com/flayerlabs/flaunch-sdk)
- [Flaunch: Redefining Launchpads with Fixed Price Fair Launch — blocmates](https://www.blocmates.com/articles/flaunch-redefining-launchpads-with-fixed-price-fair-launch)
- [Flaunch Audits](https://docs.flaunch.gg/protocol/audits)
- [FLAY Token Soars 265% — LBank listing — NullTX](https://nulltx.com/flay-token-soars-265-as-flaunch-goes-live-on-base-and-secures-lbank-listing/)
- [Zora Coins Protocol docs](https://docs.zora.co/coins)
- [Zora Coins architecture](https://docs.zora.co/coins/contracts/architecture)
- [Zora Coins Public REST API](https://docs.zora.co/coins/sdk/public-rest-api)
- [Zora Coins Explore Queries](https://docs.zora.co/coins/sdk/queries/explore)
- [@zoralabs/coins-sdk (npm)](https://www.npmjs.com/package/@zoralabs/coins-sdk)
- [Swagger UI — api-sdk.zora.engineering](https://api-sdk.zora.engineering/docs)
- [Jacob Horne — Zora co-founder profile](https://usethebitcoin.com/crypto-personalities/all-you-need-to-know-about-jacob-horne-the-co-founder-of-zora-labs/)
- ["Base is for everyone" incident — Followin](https://followin.io/en/feed/17572962)
