[VPS Research]

# DexScreener API — capacités de découverte de tokens Base établis+actifs (pour #77 / discover_top_pools)

## Contexte

ARIA utilise déjà DexScreener dans `acp_onchain_scan.py::_fetch_token_pairs`
(`GET /token-pairs/v1/{chain}/{contract}`) pour récupérer prix/liquidité/
paires d'un token **déjà connu** (contrat en entrée). Le besoin ici est
inverse : **découvrir** des tokens candidats sur Base avec liquidité établie
ET activité soutenue sur une fenêtre longue, en excluant les tokens morts
(liquidité sans volume) et les tokens qui viennent de pomper (volume 5min/1h
sans historique). Alimente directement le plan de Principal sur
`discover_top_pools` (#77 follow-up).

Méthode : documentation officielle + **appels empiriques réels** à l'API
publique (aucune clé, lecture seule, aucun état modifié) pour vérifier le
comportement réel plutôt que de se fier uniquement à la doc, qui s'est
révélée incomplète sur certains points (schémas de réponse non inlinés
pour deux endpoints).

---

## 1. Endpoint de classement/recherche (pas juste "get pair by address") ?

**Non, pas de "top pairs by volume/liquidité sur une chaîne" natif.**
Endpoints documentés (`docs.dexscreener.com/api/reference`) :

| Endpoint | Nature |
|---|---|
| `/latest/dex/search?q=<texte>` | Recherche **texte libre** (symbole/nom/adresse), pas un browse par chaîne |
| `/token-pairs/v1/{chainId}/{tokenAddress}` | Paires d'un token **connu** (déjà utilisé par ARIA) |
| `/tokens/v1/{chainId}/{tokenAddresses}` | Lookup de tokens **connus** |
| `/token-boosts/top/v1` | Classement des **promotions payées**, pas de l'activité organique |
| `/metas/trending/v1` | Score "buzz" par **catégorie narrative** (ex. "AI", "Cat"), pas par paire individuelle |

Aucune authentification requise. Rate limit documenté : 300 req/min en
général, 60 req/min sur certains endpoints (`token-profiles`, `metas`).

**Vérifié empiriquement** (`curl` direct, lecture seule, 2026-07-12) :
- `GET /latest/dex/search?q=base` → retourne des paires Base, mais c'est un
  match texte sur "base" (nom/symbole), pas un filtre de chaîne.
- `GET /latest/dex/search?q=WETH` → **30 paires max**, réparties sur des
  chaînes très diverses (metis, cronos, celo, solana, scroll...) — **aucun
  paramètre de tri par volume/liquidité, aucun filtre de chaîne propre,
  plafonné à 30 résultats sans pagination visible.**
- `GET /token-boosts/top/v1` → contenu marketing (description, liens),
  **aucun champ volume/liquidité** — promotion payée, pas organique.
- `GET /metas/trending/v1` → agrégats par **catégorie**
  (`name`, `slug`, `marketCap`, `liquidity`, `volume`, `tokenCount`), pas
  de liste de paires individuelles, **aucun champ `chainId`**.

**Verdict : pas d'endpoint de découverte/classement par chaîne chez
DexScreener.** Confirmé par la doc ET par test direct.

## 2. Filtrage/tri par fenêtre de volume (5min/1h/6h/24h) via l'API ?

Le champ existe dans les réponses (schéma `Pair` : `volume` en objet
m5/h1/h6/h24, `txns` idem — confirmé sur `/latest/dex/search`), **mais
aucun paramètre `sort=`/`order=` documenté ou testé avec effet.** Le tri
devrait se faire côté client sur un jeu déjà plafonné à 30 résultats — pas
fiable pour du sourcing.

**Verdict : les données existent, le tri/filtre serveur n'existe pas.**
L'API n'aide pas pour un vrai balayage "top X sur Base", seulement pour
enrichir un token déjà identifié — exactement l'usage qu'en fait déjà ARIA.

## 3. Distinguer "actif depuis longtemps" vs "vient de pomper" depuis les champs ?

**Oui, si on obtient les paires par un autre moyen que le browse.**
Confirmé empiriquement : chaque paire retourne `pairCreatedAt` (epoch ms)
+ `volume.{m5,h1,h6,h24}` + `txns.{m5,h1,h6,h24}` dans la même réponse.
Calcul possible : `pairCreatedAt` ancien + volume réparti cohéremment
entre h6/h24 (pas concentré sur m5/h1) = établi + actif.

**Note pour Principal** : ARIA parse déjà `PairSnapshot` dans
`acp_onchain_scan.py::_parse_pair` mais **ne récupère aujourd'hui que
`priceChange.h24` et `buys_24h/sells_24h` — pas l'objet `volume` complet
ni `txns` par fenêtre fine (m5/h1/h6)**. Si `discover_top_pools` doit
calculer ce ratio soutenu/pump, il faudra étendre le parsing, pas juste
réutiliser `_parse_pair` tel quel.

**Verdict : calculable depuis les champs, mais seulement une fois qu'on a
la liste de paires candidates — ce qui ramène au blocage de la question 1.**

## 4. Alternative si DexScreener est trop limité pour ce cas d'usage ?

**Oui — GeckoTerminal API a exactement l'endpoint qui manque.** Vérifié
empiriquement, gratuit, sans clé :

```
GET https://api.geckoterminal.com/api/v2/networks/base/pools?sort=h24_volume_usd_desc
```

Réponse réelle obtenue (extrait) : classement **effectif** des pools Base
triés par volume 24h, avec pour chaque pool :
- `volume_usd` en objet **m5/m15/m30/h1/h6/h24** (plus fin que DexScreener)
- `reserve_in_usd` (liquidité)
- `pool_created_at` en **timestamp ISO exact** (`"2026-07-12T10:13:25Z"`)
  — filtre direct "âge de la paire > N jours"
- `transactions` par fenêtre avec `buyers`/`sellers` distincts — signal
  supplémentaire pour repérer un pump artificiel (peu d'acheteurs uniques
  malgré un volume élevé)

Documenté officiellement : le classement "Top Pools by Network" combine
liquidité (`reserve_in_usd`) et volume 24h. Paramètres de tri confirmés :
`order=h24_volume_usd_desc`, `order=h24_tx_count_desc`. Gratuit, aucune
authentification requise, rate limit 10 req/min en gratuit (250 req/min en
payant, x25). — [GeckoTerminal API docs](https://apiguide.geckoterminal.com/), [Top Pools by Network (CoinGecko API ref)](https://docs.coingecko.com/reference/top-pools-network)

**Verdict : GeckoTerminal résout structurellement le problème que
DexScreener ne résout pas** — vrai endpoint de découverte/classement par
réseau, testé et fonctionnel en direct sur Base au moment de cette recherche.

## Point anti-duplication (ajouté après relecture opérateur)

**ARIA a déjà un client GeckoTerminal** : `services/ohlcv.py`
(`OHLCVClient`, `BASE_URL = "https://api.geckoterminal.com/api/v2"`,
`DEFAULT_NETWORK = "base"`), utilisé aujourd'hui pour les séries OHLCV
(bougies) consommées par `skills/ta_levels.py`/`skills/chart_render.py`.
Il a déjà : throttle intégré (`min_interval=2.2`), politique d'erreurs
standard (backoff 429, retry timeout, `available=False` explicite jamais
de donnée inventée — même doctrine que `services/goplus.py`).

**Toute intégration de `/networks/base/pools` pour `discover_top_pools`
doit réutiliser/étendre ce client existant** (nouvelle méthode sur
`OHLCVClient`, ou nouveau module sœur partageant `BASE_URL`/le throttle),
**pas un nouveau client GeckoTerminal séparé** — norme anti-duplication
déjà appliquée ailleurs dans ce scan (ex. GoPlus/Blockscout déjà
intégrés, passe 7).

---

## Synthèse pour Principal (#77 / discover_top_pools)

**DexScreener reste le bon choix pour enrichir un token une fois identifié**
(déjà en place, ne rien changer côté `acp_onchain_scan.py` pour cet usage).
**Pour la découverte elle-même, DexScreener ne convient pas** (pas
d'endpoint de classement par chaîne, confirmé par doc + tests).
**GeckoTerminal `/networks/base/pools?sort=h24_volume_usd_desc` est la
source recommandée**, via une extension de `services/ohlcv.py` existant,
pas un nouveau client.

Si Principal construit `discover_top_pools` : récupérer les pools Base
triés par volume 24h via GeckoTerminal, filtrer sur `pool_created_at` (âge
minimum) et sur un ratio volume h6/h24 vs m5/h1 (activité répartie vs
concentrée récemment), puis **repasser par le pipeline DexScreener/GoPlus
existant** (`acp_onchain_scan.py`, `safety_screen.py`) pour la sécurité —
GeckoTerminal sert à la découverte, pas au scoring de sécurité.

## Sources

- [DexScreener API reference](https://docs.dexscreener.com/api/reference)
- [DexScreener Trending](https://docs.dexscreener.com/trending)
- Tests empiriques directs : `api.dexscreener.com/latest/dex/search`,
  `/token-boosts/top/v1`, `/metas/trending/v1` (curl, lecture seule, 2026-07-12)
- [GeckoTerminal API guide](https://apiguide.geckoterminal.com/)
- [GeckoTerminal API docs](https://api.geckoterminal.com/docs/index.html)
- [Top Pools by Network — CoinGecko API reference](https://docs.coingecko.com/reference/top-pools-network)
- Test empirique direct : `api.geckoterminal.com/api/v2/networks/base/pools?sort=h24_volume_usd_desc` (curl, lecture seule, 2026-07-12)
- Code ARIA vérifié : `packages/aria-core/src/aria_core/skills/acp_onchain_scan.py` (`_fetch_token_pairs`), `packages/aria-core/src/aria_core/services/ohlcv.py` (client GeckoTerminal existant)
