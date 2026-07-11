# Audit #77 — cadence réelle du candidate flow avant `ARIA_PAPER_TRADING_ENABLED`

> Demandé par l'opérateur (11/07) : avant de compter sur un run de paper-trading de
> 20 jours comme preuve exploitable, vérifier concrètement — sur les vraies données
> de prod, pas une estimation — si le flux de candidats est assez dense.

## Alerte prioritaire — le gate est déjà ON

`ARIA_PAPER_TRADING_ENABLED=true` dans `vanguard/backend/.env` (confirmé en lisant le
fichier réel sur le VPS). `paper_state.created_at = 2026-07-08T13:36:50Z` — le
portefeuille fictif de 1 000 000 $ existe depuis **3 jours** au moment de cet audit
(11/07). Si un run de 20 jours est censé démarrer sur décision explicite, l'horloge
tourne peut-être déjà sans que ce soit tracé comme une décision. `paper_position`
compte **0** ligne : aucune position fictive ouverte depuis l'activation, cohérent
avec le reste de cet audit (voir plus bas).

## Méthode

Requêtes SQL directes sur `/opt/aria-data/aria.db` (le vrai DB de prod, bind-monté
dans le conteneur `aria-api`), recoupées avec 4 jours de logs heartbeat réels
(`/opt/aria-data/memory/vc_2026-07-{08,09,10,11}.md`). **20 lignes de `screened_token`
exclues de l'analyse** : elles proviennent du dry-run manuel de découverte bonding
que j'ai exécuté plus tôt dans cette même session (`python -m
aria_core.dry_run_bonding_discovery`, horodaté 2026-07-11T13:26:01-42), pas du
pipeline automatique — les inclure aurait faussé les chiffres de cadence organique.

## 1. `vc_crawl` — cadence d'absorption réelle

| Métrique | Valeur |
|---|---|
| Fenêtre organique observée | 2026-07-07 17:19 → 2026-07-11 10:56 (3,73 jours) |
| Candidats distincts absorbés dans `screened_token` | **72** |
| Rythme | ~19,3/jour → **~135/semaine (extrapolé)** |
| Cadence du heartbeat `vc_crawl` | toutes les 360 min (4x/jour) |

Le sourcing brut n'est **pas** le goulot d'étranglement — 72 candidats distincts en
moins de 4 jours est un volume correct pour un projet naissant.

## 2. `weekly_forecast` / `resolve_due` — pronostics ouverts et clôturés

| Statut | `screened_token` (organique, n=72) |
|---|---|
| `rejected` (définitif) | 50 (69 %) |
| `pending` (échec mou, retry) | 22 (31 %) |
| **`active` (passe le filtre)** | **0 (0 %)** |

Confirmé indépendamment sur 4 jours de logs heartbeat — **chaque** cycle `vc_crawl`
loggé du 08/07 au 11/07 se termine par `— 0 gardés`, sans exception.

Conséquence directe sur `vc_weekly_forecast` (tire 20 tokens du pool `active` tous
les 2 jours, `screened_pool.draw_lottery`) : a tourné au moins 2 fois (08/07, 10/07)
et produit **`[forecast] 0 pronostics enregistrés`** les deux fois — logique, il n'y
a jamais eu de tokens `active` dans lesquels piocher.

`vc_prediction` (10 lignes au total, toutes strategy=`vc`) : **aucune n'a été
créée via le tirage automatique** (`report_ref` vide, jamais le préfixe `weekly-`
que pose `run_weekly_forecasts`). Les 10 viennent d'analyses manuelles `/vc
<contrat>` sur seulement **6 contrats distincts** (3 contrats réanalysés 2-3 fois
chacun). **0 résolue** — cohérent avec `resolve_due()` qui n'agit que sur un horizon
calendaire strict (30 j pour `vc`, 7 j pour `spec`) : la plus ancienne prédiction
(07/07) n'atteindra son échéance que vers le **6-9 août**, bien après la fin de
n'importe quelle fenêtre de 20 jours démarrée maintenant.

`paper_trade_cycle` tire ses candidats de la même source (`candidate_ranking.
top_candidates` → `screened_pool.list_pool(status="active")`) — même pool vide,
même résultat : 0 position fictive ouverte en 3 jours de gate ON.

## 3. Taux de conversion / cause du filtrage à 0 %

Fréquence des motifs de rejet sur les 72 lignes organiques (un candidat peut cumuler
plusieurs motifs) :

| Motif | Occurrences | % des 72 |
|---|---|---|
| Score de sécurité < 70 | 49 | 68 % |
| Distribution des holders inconnue (donnée indisponible) | 40 | 56 % |
| Contrat non vérifié (code opaque) | 35 | 49 % |
| Verdict de scan `CAUTION` (SAFE requis) | 35 | 49 % |
| Liquidité < 30 000 $ | 33 | 46 % |
| Aucune paire DEX trouvée | 20 | 28 % |
| Fraîchement découvert, paire pas encore indexée | 18 | 25 % |
| Verdict de scan `DANGER` | 14 | 19 % |
| Holder dominant > 30 % | 8 | 11 % |
| **Mint contrôlé par un dev confirmé / owner caché (signal malveillant réel)** | **9** | **12,5 %** |

Le filtre (`safety_screen.py`, seuils `min_score=70` / `min_liquidity=$30k` /
`max_top_holder=30%` / contrat vérifié / verdict `SAFE`, **tous requis
simultanément**) rejette dans l'immense majorité des cas sur des critères liés à la
**fraîcheur** du token (score encore bas, pas encore vérifié sur Blockscout,
holders pas encore lisibles, liquidité pas encore montée à 30k) — pas sur un signal
malveillant confirmé (~12,5 % seulement). Le bar semble calibré pour un token déjà
mature, alors que `vc_crawl`/le sourcing direct trouvent surtout des tokens tout
juste lancés qui n'ont pas encore eu le temps d'accumuler ces signaux.

## 4. Projection à 20 jours

Avec un taux de conversion organique de 0 % sur 72 candidats/3,73 jours observés, la
projection honnête est : **si rien ne change dans le sourcing ou le filtre, un run
de 20 jours démarré maintenant produira vraisemblablement 0 pronostic automatique et
0 position fictive** — pas "un échantillon mince", un échantillon nul. Même en
supposant une amélioration optimiste (quelques candidats proches du seuil finissent
par passer), le volume resterait de l'ordre de 1 à quelques positions sur 20 jours —
trop peu pour tirer une conclusion statistique, même qualitative (même symptôme que
`sample_size: unknown` sur `/feuvert` aujourd'hui, en pire).

## Verdict

**Non — le volume actuel ne suffit pas.** Le run de 20 jours ne peut pas produire de
preuve exploitable tant que le pool `active` reste vide. Ce n'est pas un problème de
sourcing brut (135/semaine de candidats distincts est correct) — c'est un problème de
**conversion** : 0 % passe le filtre, sur des critères eux-mêmes en cause dans ~87,5 %
des rejets (fraîcheur, pas malveillance confirmée).

**Deux leviers possibles, à trancher par l'opérateur (rien touché ici)** :
1. **Élargir le sourcing** vers des pools légèrement plus matures (`discover_top_pools`
   cible peut-être des tokens trop jeunes pour avoir de la liquidité/vérification/
   holders lisibles) — ou activer les launchpads gradués (`ARIA_BONDING_DISCOVERY_ENABLED`,
   dry-run fait ce même segment, toujours OFF) pour élargir la base.
2. **Assouplir un des critères mous du filtre** (ex. tolérer un contrat non vérifié
   quelques jours plutôt qu'un rejet immédiat, ou traiter "holders inconnus" comme
   `pending`-retry plutôt qu'un motif de blocage à égalité avec les autres) — décision
   de calibrage produit, pas une correction de bug.

**Recommandation immédiate, indépendante des deux leviers ci-dessus** : décider
explicitement du sort du gate `ARIA_PAPER_TRADING_ENABLED` déjà actif depuis 3 jours
sans avoir rien produit — le laisser tourner (le compteur de 20 jours inclut alors 3
jours à vide), ou remettre `paper_state`/le compteur à zéro une fois le pool
réellement alimenté, pour que les 20 jours mesurent une vraie fenêtre de trading
simulé et pas 3 jours de silence + 17 jours utiles.
