[VPS Research]

# Radar large-spectre — Webacy approfondi (réputation wallet) + Arkham Intelligence (entity labels)

## Contexte

Suite du radar du 15/07 (`2026-07-15-radar-goplus-clanker-webacy.md`), qui
avait laissé Webacy non tranché (accès API verrouillé, 401 sans clé).
Dispatch en deux branches : (1) approfondir Webacy — légitimité, tarifs,
et surtout si c'est complémentaire ou redondant avec GoPlus déjà en prod ;
(2) une nouvelle branche de radar au choix. **Aucun compte créé, aucune
clé achetée/activée — diligence uniquement**, conforme à la consigne.

**Bonus non demandé mais directement pertinent** : les outils MCP `dune`
sont devenus disponibles en cours de session (la clé placeholder signalée
dans le rapport précédent a été corrigée entre-temps) — le test live resté
bloqué a donc pu être exécuté, voir §3 et l'amendement dans
`docs/dune-integration-plan.md` §7bis.

---

## 1. Webacy approfondi — verdict : complémentaire à GoPlus, pas redondant

**Vérification de code faite avant de conclure (grep-avant-proposer)** :
`packages/aria-core/src/aria_core/services/goplus.py` confirme que GoPlus
est bien en prod, et scope explicitement au **contrat/token** (honeypot,
taxes, ownership caché — lecture du docstring : « Client de lecture seule
GoPlus Security (Token Security API) — détection honeypot »). Le module
`smart_money.py` (wallet-tracker) ne consulte, lui, **aucune API externe de
réputation d'adresse** — il déduit un score de comportement uniquement à
partir de l'historique on-chain interne (Blockscout) et exclut les wallets
« contrat » via un simple flag `is_contract` + une liste d'adresses
d'infrastructure DEX à exclure construite à la main
(`_build_dex_infrastructure_exclusions`). **Aucun chevauchement avec
Webacy aujourd'hui, dans un sens ou dans l'autre.**

### Légitimité — réelle, financement sérieux, clients identifiables

- **Financement total ≈ 10M$** sur plusieurs tours, dont un tour annoncé le
  16/04/2026 avec des investisseurs identifiables et vérifiables :
  **Mozilla Ventures, GSR, Sui Foundation**, plus la participation de
  **Balaji Srinivasan** et **Sebastien Borget** (co-fondateur The Sandbox) —
  pas un projet anonyme ou de complaisance.
- **Clients/intégrations réels et nommés** : partenariat confirmé avec
  **Revoke.cash** (Approval Risk API intégrée), intégration avec
  **Etherscan**, partenariat wallet matériel avec **Arculus** (coté
  Nasdaq : CMPO), Risk Score intégré dans « plus de 10 dApps » selon leur
  communication (chiffre non vérifié indépendamment, à prendre comme
  auto-déclaré).
- **Aucun audit de sécurité tiers publié trouvé** pour Webacy elle-même
  (recherche dédiée sans résultat) — réserve identique à celle déjà notée
  pour xStocks/GMGN dans des diligences précédentes : absence de preuve,
  pas preuve d'absence, mais à noter comme un point non couvert.

### Ce que l'API apporte de RÉEL par rapport à GoPlus — confirmé : réputation d'ADRESSE, pas de contrat

Lecture de la documentation développeur (`docs.webacy.com`) confirme la
distinction structurelle exacte suggérée par l'opérateur :
- **Exposure Risk** (`GET /addresses/{address}`) — évalue si l'adresse
  fournie **est à risque** (probabilité de se faire drainer), à partir de
  son historique de transactions/comportement/actifs détenus.
- **Threat Risk** — évalue, à l'inverse, si l'adresse **représente un
  risque pour les autres** (drainer connu, scam).
- **Sanctioned Address API** (`GET /addresses/sanctioned/{walletAddress}`)
  — vérifie une adresse contre des bases de sanctions.
- **Approvals with threat risks** — liste les autorisations de dépense
  actives d'une adresse et le risque du spender associé.
- Existe aussi un **Holder Analysis** (`GET /holder-analysis/{address}`)
  qui, lui, analyse les *premiers détenteurs d'un token* — plus proche
  côté token, mais orienté « qui a acheté tôt » plutôt que sécurité du
  contrat (chevauche partiellement l'esprit de `smart_money.py`, pas de
  GoPlus).

**Conclusion nette** : ces quatre premiers endpoints sont bien une
réputation **d'adresse/wallet**, structurellement différente du scope
contrat de GoPlus — cela nourrirait naturellement un futur `/walletscore`
(complément externe au score interne `smart_money.py`, ex. filtrer un
wallet sanctionné ou marqué "threat" avant de le considérer comme
"smart money"), **pas** `safety_screen` (qui reste le domaine de GoPlus).

### Tarification — deux grilles distinctes à ne pas confondre

- **Application grand public "Webacy World"** (Wallet Watch, Backup
  Wallet, Panic Button, Crypto Will) : grille publique **Starter
  (gratuit) / Pro (10$/mois, essai 7 jours) / API (tarif sur mesure)** —
  mais c'est la tarification de l'app **consommateur final**, pas
  nécessairement celle de l'accès développeur programmatique.
  **Point de vigilance identifié** : plusieurs sources reprennent ce
  triptyque sans préciser explicitement s'il couvre l'API — à ne pas
  supposer identique sans vérification directe le jour où un compte
  serait envisagé.
- **Portail développeur** (`developers.webacy.co`) : page non accessible
  en lecture directe ce soir (403 sur WebFetch — probablement du contenu
  dynamique nécessitant JS/authentification, pas un blocage définitif).
  Le tarif exact de l'accès API programmatique (clé + quota) reste donc
  **non confirmé avec certitude** malgré la mention d'un plan « API »
  personnalisé — nécessiterait une inscription pour le voir précisément,
  non fait ce soir (hors frontière sans un « go » explicite).
- **Test réel confirmé** : `curl` sans clé sur
  `api.webacy.com/addresses/{address}?chain=base` → toujours **401
  Unauthorized**, aucun palier de test anonyme, contrairement à GoPlus et
  Clanker.

### Couverture chaîne

Aucune mention explicite de Base trouvée sur la page produit KYT
(mentionne TON explicitement, liste multi-chaînes non exhaustive) — les
docs API référencent des paramètres `chain` génériques dans les
endpoints (`?chain=base` accepté sans erreur de validation au test, juste
un 401 générique), ce qui suggère un support multi-chaînes incluant
probablement Base, **mais ce n'est pas confirmé positivement** (le 401
ne prouve rien sur la validité de la chaîne demandée) — à vérifier avec
une vraie clé avant de compter dessus.

**Verdict Webacy** : légitimité réelle (financement, clients nommés,
pas un simple side-project), complémentarité confirmée avec GoPlus
(adresse vs contrat), mais accès API non gratuit/non testable sans
inscription et couverture Base non positivement confirmée. **Recommandation :
banquer comme piste sérieuse pour un futur `/walletscore`, ne pas
l'activer avant d'avoir confirmé le tarif exact et la couverture Base
avec un compte réel (décision qui appartient à l'opérateur, pas à ce
VPS).**

---

## 2. Nouvelle branche de radar — Arkham Intelligence : entity labels réels vs le simple flag `is_contract` actuel

**Pourquoi cette piste** : en vérifiant le code pour la section 1
ci-dessus, un angle mort concret est apparu dans `smart_money.py` : ARIA
exclut aujourd'hui les « wallets contrat (équipe/vesting/LP) » via
seulement `is_contract` (booléen brut) + une liste d'adresses DEX
construite à la main. **Cela ne distingue pas** un wallet EOA (pas un
contrat) qui appartient pourtant à une équipe/fondateur/exchange connu —
un vrai angle mort pour la doctrine « pas de wash-trading, pas de wallet
équipe » déjà documentée dans le module. Arkham Intelligence est le
produit le plus connu pour ce problème précis : du **labeling d'entité**
réel (pas juste "est-ce un contrat"), via son moteur "Ultra".

**Vérifié** :
- **Chaînes supportées confirmées incluant Base** : liste officielle —
  ethereum, polygon, bsc, optimism, avalanche, arbitrum_one, bitcoin,
  tron, **base**, flare, solana, ton, dogecoin, gnosis, celo, fantom,
  zksync_era, linea.
- **Ce que ça labellise** : adresses appartenant à des exchanges, funds,
  protocoles, DAOs, whales, individus notables — via un modèle de données
  structuré (adresses → entités → labels/tags), pas juste un score
  numérique opaque.
- **Rate limits publiés** : 20 requêtes/seconde sur les endpoints
  standards, 1 requête/seconde sur les endpoints "lourds"
  (`/transfers`, `/swaps`, `/counterparties/`, `/token/top_flow/`,
  `/token/volume/*`) — limites documentées précisément, signe d'une API
  mature avec de vraies contraintes d'infra (pas juste une promesse
  marketing).
- **Tarification — RÉSERVE IMPORTANTE, ce n'est pas un produit gratuit
  comme GoPlus/Clanker** : sources convergentes citent un tarif
  **Standard à 149$/mois** et **Pro à 999$/mois** pour l'accès dashboard ;
  l'existence d'un palier gratuit de test est mentionnée mais **sans
  détail exact sur son quota/scope** pour l'API elle-même (à distinguer du
  dashboard web) — pas vérifié par un appel réel ce soir (nécessiterait
  une clé, non demandée conformément à la frontière « ne rien
  acheter/activer »).

**Verdict Arkham** : la piste technique est réelle et directement
pertinente pour combler un angle mort identifié dans le code existant
(`smart_money.py`), mais c'est un produit **payant et coûteux** (pas un
"quick win" gratuit comme GoPlus/Clanker) — à traiter comme une décision
d'investissement potentielle, pas une intégration réflexe. Recommandation :
**banquer comme piste concrète pour améliorer la qualité de l'exclusion
"wallet équipe/whale connu" dans `smart_money.py`**, mais ne mérite un
« go » que si le sourcing de wallets actuel (interne + Dune, cf.
`docs/dune-integration-plan.md`) s'avère insuffisant en pratique — pas
urgent, une piste ouverte de plus.

---

## 3. Bonus — vérification Dune résolue (voir détail complet dans `docs/dune-integration-plan.md` §7bis)

Les outils MCP `dune` sont devenus disponibles en cours de session (clé
corrigée entre le rapport précédent et celui-ci). Test live exécuté sur
WETH Base : `dex.trades` (10 trades réels des dernières minutes,
0,015 crédit), `prices.usd` (prix à jour à la minute près, 0,104 crédit),
`tokens.transfers` (transferts réels des dernières 24h, 0,042 crédit).
**Total 0,161 crédit sur 2500/mois — négligeable.** Réserve trouvée :
`amount_usd` est `null` sur plusieurs lignes issues du projet agrégateur
`0x API` dans `dex.trades` — à gérer explicitement (jamais supposer une
valeur) dans le futur `services/dune.py`. Détail complet, requêtes SQL
exactes et résultats bruts dans `docs/dune-integration-plan.md` §7bis —
pas dupliqué ici.

---

## Synthèse

| | Statut | Verdict |
|---|---|---|
| Webacy (réputation wallet) | Approfondi, légitimité confirmée | Complémentaire à GoPlus (adresse vs contrat) — piste sérieuse pour `/walletscore`, tarif API exact non confirmé |
| Arkham Intelligence (entity labels) | Nouvelle piste, vérifiée | Comble un angle mort réel de `smart_money.py`, mais produit payant (149-999$/mois) — pas urgent |
| Dune (`dex.trades`/`tokens.transfers`/`prices.usd`) | Bloqué → résolu ce soir | Les trois tables confirmées vivantes et fiables, coût négligeable, une réserve de qualité de donnée à gérer en code |

## Branches ouvertes (banquées, pas creusées)

- Tarif exact de l'accès API programmatique Webacy (portail développeur
  non lisible ce soir, 403) — à vérifier avant toute inscription.
- Couverture Base de Webacy non positivement confirmée (401 ne prouve
  rien sur la validité de la chaîne demandée) — à vérifier avec une vraie
  clé si un « go » est donné un jour.
- Palier gratuit exact de l'API Arkham (scope/quota) — mentionné mais pas
  détaillé dans les sources trouvées ce soir.
- `Holder Analysis` de Webacy (premiers détenteurs d'un token) comme piste
  complémentaire à `smart_money.py`, distincte du KYW pur adresse — pas
  creusée en profondeur ce soir.
- `amount_usd` null sur les lignes `dex.trades` issues d'agrégateurs
  (`0x API`) — implication à documenter si `services/dune.py` est
  construit un jour (§7bis de `docs/dune-integration-plan.md`).

## Sources

- [Webacy — Announcing our Latest Funding Round](https://www.webacy.com/blog/announcing-our-latest-funding-round)
- [Webacy — Funding Rounds & Investors (Tracxn)](https://tracxn.com/d/companies/webacy/__di1JCKOBkJiD8znXc361s1ewPe3ZfcdLj_4FwP-dnIc/funding-and-investors)
- [Webacy Secures $4.0M Seed Funding — Signalbase](https://www.trysignalbase.com/news/funding/webacy-secures-40m-seed-funding)
- [Revoke.cash Integrates Webacy — Webacy Blog](https://www.webacy.com/blog/revoke-cash)
- [Webacy — Products](https://www.webacy.com/products)
- [Webacy — KYT product page](https://www.webacy.com/kyt)
- [Webacy Docs — Check if Wallet Address is Sanctioned](https://docs.webacy.com/reference/get_addresses-sanctioned-walletaddress)
- [Webacy Docs — Threat Considerations for an Address](https://docs.webacy.com/reference/get_addresses-address)
- [Webacy Docs — Approvals with threat risks](https://docs.webacy.com/reference/get_addresses-address-approvals)
- [Webacy Docs — Holder Analysis](https://docs.webacy.com/reference/get_holder-analysis-address)
- [Webacy — API Overview / Introducing EmbeddedSafety™](https://www.webacy.com/blog/introducing-our-embedded-safety-products-webacys-suite-of-apis)
- [Webacy developer portal](https://developers.webacy.co/) (403 en lecture directe ce soir)
- [Arkham — Blockchain Data API](https://arkm.com/api)
- [Arkham — API Documentation](https://arkm.com/api/docs)
- [Arkham Intel API — Introduction](https://docs.intel.arkm.com/openapi/portfolio/n/a)
- [Arkham Exchange — API rate limits](https://arkm.com/limits-api)
- Test réel `curl` ce soir : `api.webacy.com/addresses/...?chain=base` → 401
- Test réel via outils `mcp__dune__*` ce soir : `dex.trades`/`prices.usd`/`tokens.transfers` sur WETH Base — voir `docs/dune-integration-plan.md` §7bis pour le détail complet
- Code local vérifié (grep-avant-proposer) :
  `packages/aria-core/src/aria_core/services/goplus.py`,
  `packages/aria-core/src/aria_core/services/smart_money.py`

## Frontières confirmées respectées

Aucun compte créé (Webacy, Arkham). Aucune clé API achetée ou activée.
Les seuls appels authentifiés effectués ce soir sont les requêtes Dune
via le serveur MCP déjà enregistré par l'opérateur (lecture seule,
0,161 crédit consommé sur 2500/mois, aucune écriture). Aucun code ARIA
modifié — recherche externe + vérification de code en lecture seule.
Aucune approche de `wallet_guard`/`permission_mode`/`config.toml`/
auto-modification/capital réel.
