[VPS Research]

# Sources de données réelles manquantes — actions, ETF, matières premières

## Contexte et doctrine de référence

La vitrine affiche déjà le forex via `packages/aria-core/src/aria_core/services/forex.py`
(Frankfurter, taux de référence BCE) — lu directement pour cette veille afin
d'en extraire la doctrine à respecter : **gratuit, sans clé si possible,
documenté depuis des années, GET uniquement, aucune donnée manquante jamais
remplacée par une supposition, `available=False` explicite en cas
d'échec**. Actions, ETF et matières premières n'ont aujourd'hui aucune
source câblée dans le dépôt — recherche de candidats respectant cette même
doctrine, catégorie par catégorie.

**Verdict global à l'avance, pour cadrer la lecture** : aucune source
trouvée n'atteint le standard exact de Frankfurter (gratuit + sans clé +
illimité + documenté depuis des années) pour les trois catégories. Ce n'est
pas un échec de recherche — c'est un constat de marché : les données de
référence banque centrale (forex) sont republiées librement par nature,
les cotations actions/ETF/matières premières sont un marché de données
propriétaire par nature, même les fournisseurs "gratuits" gardent une clé
et un plafond. La suite documente les meilleures options réalistes malgré
cet écart à la doctrine idéale, avec les compromis explicites à trancher
par le commandement.

---

## 1. Indices actions (S&P 500, Nasdaq, CAC 40, etc.)

**Aucune source équivalente à Frankfurter (sans clé, illimitée) trouvée.**
Toutes les options légitimes identifiées demandent une clé API gratuite et
imposent un plafond quotidien :

### Alpha Vantage — candidat le plus documenté, plafond très bas
- Clé API gratuite requise (inscription email, gratuite).
- **Plafond réel : 25 requêtes/jour** — réduit depuis un plafond historique
  de 500/jour il y a plusieurs années ; confirmé par test réel indépendant,
  atteint "en quelques minutes d'usage réel".
- Historique de disponibilité long (service actif depuis des années,
  documentation stable, largement cité comme référence de longue date).
- **Limite structurelle pour les indices précisément** : Alpha Vantage
  n'a pas d'endpoint natif pour un ticker d'indice façon `^GSPC`/`^IXIC` —
  l'usage courant documenté consiste à interroger un ETF réplicant l'indice
  (SPY pour le S&P 500, QQQ pour le Nasdaq 100) via `GLOBAL_QUOTE`, pas
  l'indice lui-même. Donc utilisable comme proxy, pas comme source
  d'indice au sens strict.
- Format : JSON, bien documenté.

### Twelve Data — plafond plus généreux, mais restriction d'usage à vérifier
- Clé API gratuite requise.
- Plafond : 800 requêtes/jour, 8/minute — largement suffisant pour un
  usage "quelques requêtes/heure".
- **Réserve importante, à trancher par le commandement avant tout
  câblage** : les conditions d'utilisation du plan gratuit restreignent
  explicitement l'usage à un cadre personnel/interne/non-commercial — si
  la vitrine ARIA est considérée comme un usage commercial (produit
  public), cette clause pourrait exclure le plan gratuit en toute rigueur,
  indépendamment du plafond technique. À vérifier avant intégration, pas
  juste une question de rate-limit.
- Infrastructure jugée fiable dans les comparatifs indépendants 2026 (pas
  de downtime aléatoire signalé, contrairement à des alternatives basées
  sur du scraping).

### Financial Modeling Prep (FMP)
- Clé API gratuite requise, 250 requêtes/jour.
- Conditions du plan gratuit orientées "usage personnel" également —
  mêmes réserves que Twelve Data sur un usage en produit public, non
  précisées en détail dans la documentation publique consultée.

### Stooq — sans clé, mais fragile (motif déjà rencontré dans ce dépôt)
- Pas de clé requise, endpoints CSV non officiels imitant le
  téléchargement manuel (`stooq.com/q/l/?s=...`), couvrant actions,
  indices, ETF, forex, crypto.
- **Pas d'API officielle documentée** — endpoints non documentés
  officiellement, quota quotidien bas non chiffré précisément dans les
  sources trouvées, conçus pour du téléchargement en lot plutôt qu'un
  usage applicatif temps réel.
- Format CSV, pas JSON — nécessiterait un parsing supplémentaire côté
  ARIA (rupture de cohérence avec le pattern JSON des autres clients de
  `services/`).
- **Signal d'alerte du même type que Clanker/Virtuals déjà rencontré dans
  ce dépôt** (cf. commentaire `forex.py` ligne 6) : source non officielle,
  sans garantie de stabilité contractuelle — à ne pas câbler sans détour
  selon la doctrine déjà en place ("profondeur proportionnelle à
  l'enjeu").

**Recommandation pour les indices** : aucune option "verte sans réserve".
Si une source doit être retenue malgré tout, **Alpha Vantage** est la
moins mauvaise (documentation et historique les plus longs, clause de
non-commercialité absente de ses CGU contrairement à Twelve Data/FMP) —
mais avec un plafond de 25 requêtes/jour qui impose un cache local
agressif côté ARIA (pas un point de conception réglé ici), et l'usage
d'ETF-proxy plutôt que d'un vrai ticker d'indice pour certains indices.

---

## 2. ETF (prix/cours)

**Mêmes fournisseurs que la section 1** — Alpha Vantage, Twelve Data et FMP
traitent un ETF comme un ticker actions ordinaire (`GLOBAL_QUOTE`/
équivalent), donc les mêmes plafonds, clés et réserves de conditions
d'usage s'appliquent identiquement, sans source supplémentaire dédiée aux
ETF trouvée qui changerait le calcul. Stooq couvre aussi les ETF avec la
même réserve de fragilité que la section 1.

**Recommandation pour les ETF** : identique à la section 1 — pas de
source distincte à évaluer séparément, la décision retenue pour les
indices (Alpha Vantage comme moins mauvaise option, ou statu quo
"indisponible, non fabriqué") s'applique de facto aux ETF.

---

## 3. Matières premières (or, pétrole, etc.)

### Alpha Vantage — seule option couvrant à la fois énergie et matières agricoles avec la même clé déjà évaluée en section 1
- Endpoints commodities dédiés et documentés : WTI, BRENT (pétrole),
  NATURAL_GAS, COPPER, ALUMINUM, WHEAT, CORN, COTTON, SUGAR, COFFEE, et un
  indice global de matières premières (`ALL_COMMODITIES`).
- Même plafond de 25 requêtes/jour, même clé gratuite que la section 1 —
  mais signal positif trouvé spécifiquement pour cette catégorie : "les
  données de matières premières sur le plan gratuit ne sont pas soumises
  aux mêmes restrictions que les actions US et se rapprochent davantage
  du temps réel" — donc un signal de priorité produit plus favorable aux
  matières premières qu'aux indices actions chez ce même fournisseur.
- **Ne couvre PAS l'or/l'argent (métaux précieux)** — la liste
  documentée des commodities Alpha Vantage ne mentionne aucun endpoint
  or/argent : il faut une source séparée pour ce sous-ensemble précis.

### Or/argent — aucune option gratuite sans clé et éprouvée trouvée
- **GoldAPI.io** : clé gratuite (pas de carte bancaire requise), mais
  plafond très bas — **100 requêtes/mois** (pas par jour), soit environ 3
  requêtes/jour en moyenne lissée — probablement insuffisant même pour un
  usage "quelques requêtes/heure" si interprété au sens large. Fournisseur
  établi et cité dans plusieurs comparatifs indépendants.
- **gold-api.com ("Gold API")** : revendique explicitement "aucune clé
  requise, aucune limite de requêtes" pour les prix temps réel — vérifié
  directement sur sa documentation. **Signal de prudence explicite à
  documenter** : aucun historique de disponibilité, aucun rapport
  d'incident tiers, aucune preuve de stabilité dans la durée trouvée —
  la documentation elle-même mentionne un mécanisme de "fallback vers la
  source suivante" en cas de défaillance d'un fournisseur sous-jacent, ce
  qui suggère une dépendance indirecte à des sources non nommées, pas une
  source primaire propre. **Exactement le profil "prometteur mais non
  vérifié dans la durée" que la doctrine du dépôt (cf. Clanker,
  `forex.py` ligne 6) traite comme à ne pas câbler sans détour
  supplémentaire.**
- **Metals-API / Commodities-API / CommodityPriceAPI** : toutes
  legitimes mais toutes à clé, avec essais gratuits limités dans le temps
  (7 jours) plutôt qu'un plan gratuit permanent — ne correspondent pas au
  besoin d'un usage prolongé gratuit.

**Recommandation pour les matières premières** : pétrole et matières
premières industrielles/agricoles couvrables via Alpha Vantage (même
compromis clé + 25/jour que section 1, mais signal de priorité produit
plus favorable). **Or/argent : aucune option satisfaisante trouvée** —
soit GoldAPI.io à un plafond trop bas pour un usage réel (100/mois), soit
gold-api.com sans aucune preuve de fiabilité dans la durée. **À documenter
honnêtement comme un vrai manque** pour ce sous-ensemble précis, pas à
combler par gold-api.com sans réserve explicite.

---

## Synthèse

| Catégorie | Source la moins mauvaise | Clé requise | Plafond gratuit | Réserve principale |
|---|---|---|---|---|
| Indices actions | Alpha Vantage (proxy ETF) | Oui | 25/jour | Pas d'endpoint indice natif, plafond très bas |
| ETF | Alpha Vantage (idem indices) | Oui | 25/jour | Identique à la ligne indices |
| Pétrole / matières premières (hors métaux précieux) | Alpha Vantage | Oui | 25/jour | Plafond bas mais priorité produit plus favorable signalée |
| Or / argent | **Aucune option satisfaisante** | — | — | GoldAPI.io trop restrictif (100/mois) ; gold-api.com sans historique de fiabilité |

**Aucune des trois catégories n'atteint le standard Frankfurter** (gratuit,
sans clé, illimité, documenté depuis des années). Le compromis le plus
défendable si le commandement souhaite avancer malgré tout : une seule
clé Alpha Vantage pour couvrir indices (via proxy ETF)/ETF/matières
premières hors métaux précieux, avec un cache local strict pour rester
sous 25 requêtes/jour — et un manque assumé, non comblé, pour l'or/argent
en l'état actuel du marché des API gratuites.

## Sources

- [Alpha Vantage API Request Limits — Macroption](https://www.macroption.com/alpha-vantage-api-limits/)
- [Alpha Vantage API Documentation (officielle)](https://www.alphavantage.co/documentation/)
- [Alpha Vantage API: The Complete 2026 Guide — AlphaLog Blog](https://alphalog.ai/blog/alphavantage-api-complete-guide)
- [Twelve Data — Terms of Service](https://twelvedata.com/terms)
- [Twelve Data Individual Pricing](https://twelvedata.com/pricing)
- [Best Free Stock Market APIs in 2026 (Tested) — The Next Gen Nexus](https://thenextgennexus.com/2026/05/15/10-best-free-stock-market-apis-2026/)
- [Financial Modeling Prep — Pricing Plans](https://site.financialmodelingprep.com/pricing-plans)
- [An Introduction to Stooq Pricing Data — QuantStart](https://www.quantstart.com/articles/an-introduction-to-stooq-pricing-data/)
- [Stooq — Free Market Data](https://stooq.com/db/)
- [GoldAPI.io — Free Real-Time Gold and Silver Spot Prices](https://www.goldapi.io/)
- [Gold API — API Documentation](https://gold-api.com/docs)
- [Commodities API](https://commodities-api.com/)
- [Metals-API](https://metals-api.com/)
- Code ARIA vérifié : `packages/aria-core/src/aria_core/services/forex.py`
  (lecture directe, doctrine de référence pour cette veille, 2026-07-13)

## Frontières confirmées respectées

Aucun code touché, aucun client câblé. Recherche et références externes
uniquement. Aucune source médiocre présentée comme suffisante — le manque
sur l'or/argent est documenté comme tel, pas comblé par une option non
éprouvée. Décision d'intégration (choix de fournisseur, gestion du cache
pour rester sous les plafonds, acceptation ou non des clauses de
non-commercialité) laissée au commandement.
