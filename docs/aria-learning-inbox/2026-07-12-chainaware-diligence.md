[VPS Research]

# Diligence complète — ChainAware.ai (repéré passe 9 + veille #79, jamais approfondi)

## 1. Mécanisme de détection réel

**Pas purement on-chain.** ChainAware combine deux pipelines : (a) inspection
du code du contrat (AST + bytecode, façon scanner classique) et (b) un score
comportemental ("Trust Score") calculé sur l'historique on-chain du
**créateur du contrat et des fournisseurs de liquidité** — entraîné sur
14M+ wallets à travers 8 chaînes. Le point différenciant revendiqué :
"Behavioral AI catches what code scanners miss — en lisant l'historique
on-chain des PERSONNES derrière le contrat", pas seulement le contrat
lui-même. V3 (actuelle) combine les deux ; V2 (comportemental seul)
atteignait ~68 % de précision revendiquée, V3 ~90,1 %.

**Off-chain ?** Rien trouvé indiquant une composante off-chain (pas de
scraping social/presse) — la donnée reste on-chain (transactions, wallets),
juste analysée sur un historique **multi-contrats et multi-chaînes** plutôt
que sur le seul token scanné.

## 2. API programmatique

- Documentée : `swagger.chainaware.ai`, endpoint `predictive_rug_pull`
  (pensé explicitement pour être appelé "avant de lister un token", donc
  latence compatible avec un flux de scan comme celui d'ARIA).
- Auth : header `X-API-Key`, clé récupérable sur `chainaware.ai/profile`.
- Accès agent : serveur MCP dédié (`prediction.mcp.chainaware.ai/sse`), en
  plus du REST classique — pertinent si un jour ARIA consomme des outils
  via MCP plutôt que des clients HTTP dédiés (pas le cas aujourd'hui,
  `services/` reste des clients HTTP directs).
- **Coût** : accès complet **uniquement en abonnement payant** (souscription
  requise via `chainaware.ai/pricing` pour les appels API) — mais les
  outils web (Wallet Auditor, Fraud Detector, Rug Pull Detector) sont
  **utilisables gratuitement sans inscription** pour un test manuel avant
  tout engagement payant. Montant exact de l'abonnement non trouvé dans
  cette recherche (page pricing non détaillée dans les résultats) — à
  vérifier directement avant toute décision de budget.

## 3. Légitimité — ChainAware EST un projet crypto (token AWARE), donc angle financement applicable

**Correction de méthode appliquée** : la grille de jugement de cette
mission dit "l'angle token/financement s'applique uniquement si l'outil
LUI-MÊME est un projet crypto" — c'est le cas ici (token `AWARE`), donc
diligence complète comme pour ODEI, pas un simple outil SaaS neutre.

**Équipe** : identifiée nommément — Martin Ploom et Tarmo Ploom (jumeaux),
ex-Credit Suisse plus de dix ans, fondateurs précédents de "Smart Credit"
(prêt à taux fixe). Zürich, Suisse. Pas anonyme — signal positif.

**Financement** : 850 k$ levés sur 5 tours (dont TGE/IDO), plus une
**subvention Google de 250 k$ (15/01/2025)** — signal de légitimité
institutionnelle réel, pas juste un narratif auto-proclamé.

**Token AWARE** : TGE le 21/01/2025. Capitalisation **très faible et
incohérente selon les sources** (entre ~46 k$ et ~264 k$ selon
CoinGecko/CoinCarp/CryptoRank — écart lui-même un signal de faible
liquidité/traçabilité fiable), supply max 100M, circulant ~33,9M.
Trading principal sur PancakeSwap V3 (BSC), pas sur un exchange centralisé
majeur au moment de la recherche (annonce de listing MEXC trouvée, pas
confirmée comme effective). **Micro-cap illiquide** — cohérent avec un
projet B2B/infra plus qu'un projet spéculatif à hype, mais reste un signal
de risque si jamais le token lui-même devait entrer en jeu (il n'a aucune
raison de le faire pour un simple usage API).

**Signal d'alerte concret trouvé (pas supposé)** : le contenu marketing de
ChainAware affirme "depuis 2017, a aidé plus de 400 organisations... plus
de 110 Md$ d'actifs numériques gérés" — **contredit par les sources
indépendantes** (Crunchbase, Tracxn, EU-Startups) qui datent la fondation
de l'entreprise au **1er janvier 2022**, pas 2017. Cinq ans d'antériorité
gonflée sur leur propre site est un vrai signal à ne pas ignorer — pas
disqualifiant en soi (produit et équipe par ailleurs réels et vérifiables),
mais motif suffisant pour **ne rien prendre au mot sur les chiffres de
précision auto-déclarés (98 % F1, 90,1 % V3)** sans vérification indépendante.

**Track record indépendant** : pas trouvé d'audit tiers ou de benchmark
indépendant des chiffres de précision — seulement des sources primaires
(blog ChainAware) et des agrégateurs de reviews sans contenu substantiel
(Crozdesk : 53/100, basé sur "press buzz", pas une mesure technique).

## 4. Est-ce que ça apporte un signal qu'ARIA n'a pas déjà ? (grep-first, comparé au code réel)

**Vérifié en détail** : `safety_screen.py` + `services/goplus.py` +
`skills/dev_wallet.py::gather_dev_wallet_facts` lus. Ce que couvre déjà
ARIA aujourd'hui :
- GoPlus (`services/goplus.py`) : honeypot, taxes réelles, pouvoirs cachés
  de l'owner, mintable, blacklist — **statique, sur CE contrat uniquement**.
- `dev_wallet.py::gather_dev_wallet_facts` : comportement du wallet
  déployeur — mais **scope limité aux transferts de CE token précis**
  (détention, achats/allocation/ventes classés depuis/vers le pool LP de
  CE contrat). Aucune lecture de l'historique du même wallet sur
  **d'autres contrats/tokens** qu'il aurait déployés ou financés par le
  passé.

**Le vrai gap, confirmé** : ARIA n'a **aucun signal de réputation
cross-token/cross-projet du wallet déployeur** — "ce wallet a-t-il déjà
rugué ailleurs" est une question qu'aucun module actuel ne pose. C'est
précisément l'angle central de ChainAware (Trust Score comportemental sur
14M+ wallets, multi-chaînes) — **pas un doublon habillé différemment**,
un signal réellement complémentaire, orthogonal à la fois à GoPlus (code
du contrat) et à `dev_wallet.py` (comportement mono-token).

## Verdict

**Signal complémentaire réel, pas un doublon** — mais adoption pas
recommandée en l'état, pour deux raisons indépendantes du mécanisme
lui-même :
1. Le signal d'alerte marketing (antériorité gonflée, 2017 vs 2022 vérifié)
   impose de **tester la qualité réelle sur les outils gratuits d'abord**
   (comparer sur un échantillon connu de rugs Base confirmés + de tokens
   légitimes) avant tout engagement payant — ne pas acheter sur la base
   des chiffres auto-déclarés.
2. Coût d'abonnement non déterminé dans cette recherche — à chiffrer avant
   toute décision.

**Si le test manuel gratuit confirme un signal utile** : seam clair et
additif — un nouveau client `services/chainaware.py` (même patron que
`services/goplus.py`, lecture seule, dégradation gracieuse), branché en
plus de `dev_wallet.py` dans `_resolve_dev_behavior` (nouveau signal
`ctx.dev_wallet_reputation` ou équivalent), consommé par
`safety_screen.py` comme un facteur de score additionnel — jamais un
remplacement de GoPlus ni de l'analyse mono-token existante.
