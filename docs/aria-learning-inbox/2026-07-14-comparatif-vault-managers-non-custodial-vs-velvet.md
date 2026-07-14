[VPS Research]

# Comparatif vault managers non-custodiaux — Velvet Capital vs. concurrents réels

## Contexte

Suite à la piste Velvet Capital bancée plus tôt ce soir
(`2026-07-14-velvet-capital-vault-manager-piste-capital-reel.md`), qui notait
explicitement en "branche ouverte" n'avoir couvert QUE Velvet, pas un vrai
comparatif. Cette note comble ce trou : Enzyme Finance, Yearn V3, dHEDGE
(rebrandé **Chamber** depuis fin 2025/2026 — même protocole, nouveau nom, à
noter pour toute recherche future) + Morpho (rencontré en cherchant, catégorie
différente mais pertinente à documenter). Aucune action requise maintenant —
la piste vault manager reste conditionnée au déblocage de l'étape 2 du pacte
argent réel (`docs/protocole-argent-reel.md`), non atteinte à ce jour.

Grille identique à Velvet et aux diligences launchpads : (1) garde, (2) Base,
(3) API réelle, (4) frais, (5) légitimité, (6) historique sécurité honnête.

---

## 1. Velvet Capital (rappel, déjà bancé — pas re-creusé ici)

Non-custodial (manager signe, ne peut pas retirer les fonds des déposants) ;
Base native (+ Ethereum, BNB, Solana, Hyperliquid, Monad, Sonic) ; API REST
documentée avec endpoints trending/portfolio/dépôt/retrait/trade (calldata
généré) ; 7 audits indépendants + bug bounty ; financement YZi Labs/DWF
Labs/Selini/Mucker ; un incident de phishing frontend (2024, pas de perte),
une faille de smart contract corrigée (arbitrage de mint via délai Chainlink).
Détail complet dans la note du soir, non répété ici.

---

## 2. Enzyme Finance — le doyen du secteur, TVL la plus élevée

**Garde** : non-custodial confirmé — le manager configure et exécute les
stratégies via le contrat du vault, mais ne détient jamais la garde des fonds
des déposants. Système de "policies" configurable par le manager pour
restreindre ce que le vault peut faire.

**Base** : **confirmé** — "Enzyme.Blue est compatible avec Ethereum, Base,
Arbitrum et Polygon" (Onyx, la version entreprise, vise une compatibilité
multi-chaîne plus large, y compris Canton Network).

**API/SDK** : SDK officiel TypeScript **et Python** (`@enzymefinance/api` sur
npm), API gRPC documentée (`sdk.enzyme.finance`) couvrant vault/depositor/
manager/network — endpoints de création/reconfiguration de vault, dépôt/
retrait confirmés. **Réserve honnête** : la doc consultée ce soir ne permet
pas de confirmer si l'API elle-même peut déclencher un swap/trade
programmatique (calldata) comme le fait Velvet, ou si elle sert surtout à la
gestion du cycle de vie du vault (dépôt/retrait/config) — l'exécution de
trades pourrait passer par une interaction directe avec le contrat plutôt que
par l'API. Authentification par clé API requise, obtenue via l'app Enzyme.

**Frais** : le manager fixe librement frais de gestion (% périodique de l'AUM),
de performance (% des gains au-dessus du plus-haut historique, période de
cristallisation configurable), et d'entrée — les investisseurs voient le
barème exact avant de déposer. Changements de frais limités à la migration
entre versions du protocole.

**Légitimité** : le plus ancien du comparatif de loin — fondé en 2016 sous le
nom **Melon** par Mona El Isa (ex-VP Goldman Sachs) et Rito Trinkler, ICO
2,9M$ en 2017 (Defiance Capital, Placeholder Ventures, Collab+Currency),
dissous en société privée en 2019 au profit d'une DAO (Melon Council),
rebrandé Enzyme fin 2020. **TVL ~190M$ (mai 2026)** — présenté comme "le plus
grand protocole de gestion d'actifs actif" du secteur par TVL, plus de 200
actifs supportés via intégrations Aave/Uniswap/Curve/Yearn. Audits confirmés
mais liste précise des cabinets non retrouvée avec certitude ce soir
(DefiLlama confirme "Audits: Yes" sans détailler).

**Historique sécurité — honnête et plutôt rassurant** : pas de hack avec perte
de fonds trouvé. Deux vulnérabilités critiques divulguées de façon responsable
via Immunefi (bug bounty) : manipulation de l'oracle de prix des tokens Idle
(nov. 2021, ~400k$ à risque, payout généreux au white hat) et un contrôle de
privilège manquant pouvant vider le vault de paymasters (mars 2023, 400k$ de
récompense payée). Divulgation responsable dans les deux cas, pas d'exploit
réel.

---

## 3. Yearn V3 vaults — mauvaise catégorie pour le cas d'usage ARIA

**Garde** : non-custodial — un "manager"/"debt allocator" peut ajuster les
ratios de dette entre stratégies mais ne peut pas retirer les fonds ; les
frais sont gérés par un contrat "Accountant" séparé, configurable par le
déployeur du vault.

**Base** : **confirmé** — vaults V3 déployés sur Ethereum, Arbitrum, Polygon,
Base et Katana, adresses stables cross-chain via des factories create2.

**API/SDK** : pas d'API REST/SDK managé équivalent à Velvet/Enzyme trouvé —
l'intégration se fait par lecture/écriture directe des contrats on-chain
(`ProtocolAddressProvider`, `ReleaseRegistry`, `RoleManager`), en Vyper. Plus
bas niveau, pas de couche API packagée pour développeur tiers.

**Frais** : gérés par le contrat "Accountant" — 0% par défaut, doit être
explicitement configuré, logique de frais entièrement définie par
l'implémentation choisie par le déployeur du vault.

**Légitimité** : très ancien (Yearn depuis 2020, Andre Cronje), écosystème
majeur, pic TVL 7 milliards$ fin 2021, `yvUSD` (vault V3 zéro frais) lancé
19 janvier 2026.

**Historique sécurité — le moins rassurant du comparatif** : **plusieurs
exploits documentés**, pas juste des bugs divulgués — hack v1 yDAI en février
2021 (11M$ perdus au total, 2,8M$ effectivement dérobés, mitigé en 11 minutes
par l'équipe, remboursement des victimes confirmé), un exploit yETH
(perte envoyée vers Tornado Cash), et un "quatrième exploit" documenté sur un
vault v1 legacy. **Distinction importante non résolue par cette recherche** :
ces hacks touchent spécifiquement les vaults v1/legacy, pas directement
l'architecture V3 actuelle — mais c'est un historique réel du même
écosystem/marque, à ne pas balayer.

**Point structurel disqualifiant pour le cas d'usage ARIA** : Yearn V3 est
conçu pour l'**allocation de stratégies de rendement/prêt** (lending,
farming) entre stratégies pré-vérifiées, pas pour un trading discrétionnaire
de tokens spéculatifs choisis librement par un manager — mauvaise catégorie
d'outil pour la poche "15% spéculation small-cap" d'ARIA, même si
techniquement non-custodial et sur Base.

---

## 4. dHEDGE / Chamber (rebrand fin 2025-2026, même protocole) — restriction d'actifs, doc peu accessible ce soir

**Point de nomenclature à retenir** : dHEDGE a rebrandé en **Chamber**
(`chamberfi.com`) — la documentation `docs.dhedge.org` redirige automatiquement
vers `docs.chamberfi.com`. Toute recherche future doit chercher "Chamber
(formerly dHEDGE)", pas seulement "dHEDGE".

**Garde** : non-custodial confirmé, architecture "Guarded Open Access
Transactions" (GOAT) — les transactions du vault s'exécutent uniquement dans
le contrat du vault (jamais dans le wallet du manager), le manager peut
trader/prêter/LP/staker pour le compte du vault mais **ne peut jamais retirer
le capital**.

**Base** : confirmé de fait — une "RACE BASE Points Vault" existe et est
listée sur `chamberfi.com`, donc au moins un vault opère sur Base, sans
confirmation aussi explicite qu'Enzyme/Velvet sur la nature "native" complète
du support.

**API/SDK** : **réserve la plus forte de ce comparatif** — la documentation
consultée ce soir (`docs.chamberfi.com`) a renvoyé des pages 404 à répétition
sur les sections manager/exécution/API, y compris après le rebrand. Aucune
confirmation d'une API programmatique de trading équivalente à Velvet trouvée
ce soir — pas une preuve qu'elle n'existe pas, mais un signal négatif net sur
l'accessibilité de la documentation développeur au moment de cette recherche.

**Frais** : plafond de 3%/an sur les frais de gestion, frais de performance
calculés au-dessus du plus-haut historique (high-water mark, formule
documentée), "streaming fee" cumulée en continu — chaque vault fixe ses
propres valeurs dans ce plafond (exemple concret vu : un vault avec 0% de
performance, 2,5% de gestion).

**Légitimité** : 6 cabinets d'audit sur la durée de vie du protocole (Obsidian
pour l'intégration Hyperliquid 2026, Sherlock 2024-2025, Santipu pour
Aave/GMX 2024-2025, iosiro pour Synthetix V3/legacy 2020-2023, CertiK pour V2
2021-2022, Trust Security pour les contrats cross-chain 2024), bug bounty
actif sur Immunefi. TVL ~38-50M$ (2025-2026, source non première main), bien
en dessous d'Enzyme.

**Historique sécurité** : "dossier de sécurité parfait depuis le lancement"
selon le site officiel lui-même — **affirmation non vérifiée par une source
tierce indépendante ce soir** (aucun hack trouvé dans les recherches externes
non plus, donc cohérent, mais la seule source de l'affirmation "parfait" est
Chamber lui-même).

**Point structurel notable** : les vaults dHEDGE/Chamber sont restreints à un
nombre limité d'actifs whitelistés (jusqu'à 12 actifs actifs par vault) et à
des protocoles/fonctions whitelistés au niveau du protocole — **contrainte
réelle pour le cas d'usage ARIA** (achat discrétionnaire de petits tokens
Base tout juste découverts) si ces tokens ne sont pas déjà sur la liste
blanche globale du protocole, ce qui impliquerait probablement un délai de
gouvernance/curation avant de pouvoir les trader.

---

## 5. Morpho Vaults V2 — rencontré en cherchant, catégorie structurellement différente

**Pourquoi mentionné sans être un vrai concurrent** : Morpho Vaults V2 est un
modèle de "curator" non-custodial (les curators réallouent librement entre
marchés de prêt approuvés, ne peuvent jamais retirer les fonds des
déposants) — même famille de garanties non-custodiales que les autres, Base
supportée nativement, adoption institutionnelle réelle (Bitwise a lancé une
offre de curation Morpho en janvier 2026, Morpho+Spark ont grossi de 2,46 à
5,9 milliards$ d'AUM en 2025). **Mais c'est un protocole d'allocation de prêt
(lending), pas un vault de trading discrétionnaire multi-token** — un
curator choisit quels marchés de prêt Morpho approvisionner, pas quels
tokens acheter/vendre sur un DEX. Structurellement hors-sujet pour la poche
"15% spéculation" d'ARIA, mais à garder en tête si un futur besoin de
rendement passif sur stablecoins (poche "85% VC" en attente de déploiement,
par exemple) se présentait — angle non creusé ici, hors scope de cette
recherche.

---

## Tableau comparatif

| | Velvet Capital | Enzyme Finance | Yearn V3 | dHEDGE/Chamber |
|---|---|---|---|---|
| Garde | Non-custodial, manager signe | Non-custodial, policies | Non-custodial, accountant séparé | Non-custodial, GOAT guard |
| Base natif | Oui, confirmé | Oui, confirmé (Enzyme.Blue) | Oui, confirmé | Oui (au moins 1 vault vu) |
| API trading réelle | Oui, REST + calldata confirmé | SDK/gRPC, exécution de trade non confirmée | Non (interaction contrat direct) | Non confirmée ce soir (docs 404) |
| Restriction d'actifs | Non signalée | ~200 actifs via intégrations | Stratégies pré-vérifiées seulement | Whitelist ≤12 actifs/vault |
| Ancienneté | Récent (financement 2023-2024) | **2016** (le plus ancien) | 2020 | 2020 |
| TVL | Non retrouvé précisément | **~190M$** (le plus élevé) | Pic 7Mds$ (2021), déclin depuis | ~38-50M$ |
| Audits | 7 cabinets + concours public | Confirmés, liste précise non retrouvée | Non détaillé ce soir | 6 cabinets sur la durée |
| Historique hacks | 1 faille corrigée, pas d'exploit | 2 failles divulguées (bug bounty), pas d'exploit | **Plusieurs exploits réels (v1/legacy)** | Aucun trouvé (affirmation surtout auto-déclarée) |
| Catégorie d'usage | Trading discrétionnaire multi-DEX | Trading discrétionnaire multi-protocole | Allocation de rendement/prêt | Trading discrétionnaire, actifs limités |

---

## Verdict argumenté

**Velvet Capital reste le meilleur choix pour ARIA au moment où l'étape 2 du
pacte argent réel sera débloquée**, pour trois raisons qui se recoupent,
pas une seule :

1. **Absence de restriction d'actifs pré-approuvés.** C'est le point le plus
   décisif contre dHEDGE/Chamber (whitelist ≤12 actifs/vault) et contre Yearn
   V3 (stratégies pré-vérifiées seulement) : la poche "15% spéculation" d'ARIA
   repose sur l'achat de **petits tokens Base tout juste découverts** via le
   pipeline GeckoTerminal existant — un vault qui exige qu'un token soit
   d'abord ajouté à une liste blanche de gouvernance avant de pouvoir le
   trader casserait structurellement ce flux. Velvet n'a pas cette
   restriction documentée ; Enzyme non plus explicitement (mais son
   intégration passe par ~200 actifs référencés, pas une preuve qu'un
   micro-cap tout juste lancé y serait automatiquement tradable).

2. **La seule API de trading programmatique confirmée avec certitude ce
   soir.** Velvet a un endpoint REST documenté générant du calldata de trade
   directement exploitable par un agent — exactement le patron d'intégration
   dont ARIA aurait besoin pour que le "manager" soit un processus autonome
   (soumis à validation `wallet_guard`/Telegram, jamais une exception à cette
   règle). Enzyme a un SDK/API mais la doc n'a pas permis de confirmer ce
   soir qu'elle couvre l'exécution de trade et pas seulement le cycle de vie
   du vault ; Yearn V3 n'a pas de couche API packagée du tout ; dHEDGE/Chamber
   a une doc développeur qui a renvoyé des 404 à répétition pendant cette
   recherche — signal négatif concret, pas juste une lacune de recherche.

3. **Profil de sécurité/légitimité déjà jugé suffisant** (7 audits, bug
   bounty actif, financement institutionnel réel, un seul incident mineur
   sans perte) — Velvet n'a pas besoin d'être le plus ancien ou le plus gros
   par TVL pour être un choix défendable, il a juste besoin d'être
   suffisamment audité et non-custodial, ce qui est déjà établi.

**Enzyme Finance est le meilleur second choix / alternative de repli**, pas
un concurrent à écarter : c'est le protocole le plus ancien (2016), le plus
gros par TVL (~190M$), gouverné par une DAO depuis 2019, avec un historique
de sécurité géré par divulgation responsable plutôt que par exploit réel —
un profil de légitimité supérieur à Velvet sur la durée. S'il s'avère, en
creusant plus tard au moment de l'intégration réelle, que son API supporte
bien l'exécution de trade programmatique sur des actifs Base spéculatifs
(question laissée ouverte ce soir), Enzyme mériterait d'être réévalué comme
premier choix, pas seulement comme repli.

**Yearn V3 et Morpho sont écartés pour une raison de catégorie, pas de
légitimité** : ce sont d'excellents protocoles, mais construits pour
l'allocation de rendement/prêt entre stratégies pré-vérifiées, pas pour le
trading discrétionnaire de tokens spéculatifs — le mauvais outil pour ce
cas d'usage précis, indépendamment de leur qualité.

**dHEDGE/Chamber est écarté pour l'usage spéculatif small-cap précis d'ARIA**
(restriction d'actifs + accessibilité de doc développeur en dessous du
standard des autres ce soir), mais reste un candidat valable à re-regarder
si ARIA devait un jour proposer un vault sur des actifs déjà établis
(majors) plutôt que sur des micro-caps fraîchement découverts.

## Branches ouvertes (non creusées maintenant)

- **Confirmer si l'API Enzyme peut réellement déclencher un swap/trade**
  (pas seulement dépôt/retrait/config de vault) — question laissée ouverte
  ce soir, déterminante si Enzyme doit un jour remplacer Velvet comme premier
  choix.
- **Revérifier dHEDGE/Chamber via une autre voie que la doc publique** (ex.
  GitHub, Discord) si Chamber redevient pertinent malgré la restriction
  d'actifs — la doc était en travaux/404 au moment de cette recherche, pas
  forcément représentatif de l'état final post-rebrand.
- **Morpho Vaults V2 comme brique de rendement passif** pour la poche "85%
  VC" en attente de déploiement (pas la poche spéculative) — angle
  entièrement différent, non creusé ici.
- **Vérifier en détail le contrôle exact qu'un "vault manager" Velvet a sur
  les fonds** (limites de rééquilibrage, whitelist de tokens/protocoles
  autorisés côté vault) — déjà noté comme branche ouverte dans la note Velvet
  initiale, toujours non résolue.

## Sources

- [Enzyme SDK](https://sdk.enzyme.finance/) · [Enzyme API Overview](https://sdk.enzyme.finance/api/overview/) · [Enzyme SDK docs](https://docs.enzyme.finance/what-is-enzyme/use-cases/sdk)
- [Enzyme Fees — User Docs](https://userdocs.enzyme.finance/managers/setup/fees)
- [Enzyme TVL — DefiLlama](https://defillama.com/protocol/enzyme-finance)
- [From Melon to Enzyme — Medium](https://medium.com/enzymefinance/from-melon-to-enzyme-b5b56512f40d)
- [Enzyme Finance Price Oracle Manipulation Bugfix Review — Immunefi](https://immunefi.com/blog/bug-fix-reviews/enzyme-finance-price-oracle-manipulation-bugfix-review/)
- [Enzyme Finance Missing Privilege Check Bugfix Review — Immunefi](https://medium.com/immunefi/enzyme-finance-missing-privilege-check-bugfix-review-ddb5e87b8058)
- [Yearn V3 Overview](https://docs.yearn.fi/developers/v3/overview) · [Vault Management](https://docs.yearn.fi/developers/v3/vault_management) · [V3 Contract Addresses](https://docs.yearn.fi/developers/addresses/v3-contracts)
- [Legacy Yearn Vault Exploited — The Defiant](https://thedefiant.io/news/defi/yearn-finance-iearn-vault-hacked)
- [Yearn Finance Suffers $9 Million Exploit — The Defiant](https://thedefiant.io/news/defi/yearn-finance-suffers-usd9-million-exploit)
- [Yearn.Finance repays victims of $11M hack — Cointelegraph](https://cointelegraph.com/news/yearn-finance-puts-expanded-treasury-to-use-by-repaying-victims-of-11m-hack)
- [Chamber (formerly dHEDGE)](https://chamberfi.com/) · [Chamber Docs — Audits](https://docs.chamberfi.com/security/audits)
- [dHEDGE rebrand to Chamber — dHEDGE blog](https://blog.dhedge.org/the-dhedge-ecosystem-a-2025-recap-and-whats-ahead-in-2026/)
- [dHEDGE Technical Architecture / GOAT guard](https://docs.dhedge.org/dhedge-protocol/technical-architecture)
- [dHEDGE Management Fees](https://docs.dhedge.org/dhedge-protocol/vault-fees/management-fees) · [Performance Fees](https://docs.dhedge.org/dhedge-protocol/vault-fees/performance-fees)
- [Morpho Vault V2 docs](https://docs.morpho.org/learn/concepts/vault-v2/) · [Morpho Curation](https://docs.morpho.org/curate/)
- [Bitwise Non-Custodial Vault via Morpho](https://bitwiseinvestments.com/newsroom/bitwise-expands-onchain-solutions-with-introduction-of-non-custodial-vault)
- [What Is Velvet Capital — MEXC](https://blog.mexc.com/what-is-velvet/) · [How Velvet Connects AI — Medium](https://medium.com/@XT_com/how-velvet-connects-ai-on-chain-trading-and-vault-based-portfolios-66a2e60f9253)
- Note ARIA de référence : `docs/aria-learning-inbox/2026-07-14-velvet-capital-vault-manager-piste-capital-reel.md`
- `docs/protocole-argent-reel.md` (lu en intégralité plus tôt ce soir — contexte de l'étape 2 gating)

## Frontières confirmées respectées

Aucun fichier `permission_mode`/`wallet_guard`/règles-uniques/`config.toml`
ouvert, aucun capital réel engagé, aucune exécution autonome, aucune
auto-modification approchée. Recherche 100% lecture externe (WebSearch/
WebFetch), aucun compte créé sur aucun des protocoles comparés, aucune clé
API souscrite. Aucune décision d'intégration prise — cette note documente un
comparatif, la décision reste entièrement au commandement et reste
conditionnée au déblocage de l'étape 2 du pacte argent réel.
