# Roadmap — capteurs on-chain d'activité (source de vérité, 29/08)

Document vivant, à éditer EN PLACE (jamais un paragraphe daté empilé). Détail
factuel des déploiements/checkpoints reste dans `docs/HANDOFF_PIPELINE_MOMENTUM.md`
(pointeur uniquement) — ce fichier porte la VISION et la séquence des briques.

## Objectif final

Aujourd'hui ARIA voit un prix et une liquidité présente sur un pool, jamais
l'activité réelle qui s'y déroule (qui trade, dans quel sens, à quelle vitesse).
Le but est de construire, brique par brique, des capteurs qui exposent cette
activité en quote brut (jamais USD, jamais CoinGecko/DexPaprika), pour ensuite
chercher si elle contient une information exploitable AVANT que le prix ait
déjà bougé visiblement.

Trajectoire jusqu'à l'exécution réelle : `information -> causalité ->
latence/coût -> exécution`. Chaque brique est conçue dès sa conception pour
pouvoir alimenter un futur moteur d'exécution temps réel — jamais une base
analytique à réécrire plus tard.

## Discipline (gravée, ne jamais assouplir)

- **Une brique à la fois, jamais fusionnées** — même techniquement liées (même
  websocket), elles doivent rester séparables expérimentalement pour tracer
  toute anomalie future à la brique exacte qui l'a introduite.
- Chaque brique suit : tests -> 6 checkpoints -> déploiement -> observation
  réelle en production -> seulement ensuite la brique suivante.
- **Mécanique de collecte != comportement réel du code déployé.** Un checkpoint
  qui valide "les écritures fonctionnent" ne valide PAS "le nouveau décodeur
  est correct en production" tant que le code n'est pas réellement chargé par
  le process (toujours vérifier le commit réellement servi, jamais la sortie
  texte d'un script).
- Tout changement de sémantique d'une colonne existante (ex. `swap_count`
  corrigé par la Brique 1) doit être journalisé avec le T0 RÉELLEMENT SERVI
  (jamais l'heure du commit) dans `docs/HANDOFF_PIPELINE_MOMENTUM.md`, en
  précisant explicitement ce qui change avant/après ce T0 — jamais mélanger
  les deux périodes dans une même analyse sans filtrer par `observed_at`.

## Les 5 briques (temps réel, prospectif)

1. **Vrai `Swap` V2** — souscription topic réel, décodeur, distinction stricte
   Swap vs Mint/Burn, `swap_count` corrigé, volume quote, sender. **CODE FAIT
   (commit `f202d289`), en cours de validation production** (cf. HANDOFF).
2. **Buy/Sell flow** — V3/V4 exposer le sens déjà disponible ; V2 dériver
   depuis le nouveau Swap décodé. `buy_count`/`sell_count`/`buy_volume_quote`/
   `sell_volume_quote`/`net_flow_quote`. Invariants : `buy_count+sell_count ==
   swap_count` et `buy_volume_quote+sell_volume_quote == cumulative_volume_quote`
   sur les snapshots clean. Ne jamais forcer buy/sell si le sens n'est pas
   déterminable. PAS COMMENCÉ — attend la validation réelle de la Brique 1.
3. **Mint/Burn (liquidity_delta)** — liquidité ajoutée/retirée, distinguer
   activité de trading vs argent qui entre/sort du pool.
4. **Oracle WETH/USD on-chain** — Robinhood : pool WETH/USDG déjà identifié
   ($45.77M/24h vol). Base : équivalent à chercher. Objectif : `reserve_usd`
   sans dépendre de CoinGecko/DexPaprika (cf. incident #271, quota CoinGecko
   épuisé 26/08, bloque toute la collecte liquidité actuelle).
5. **Persistance complète** — snapshots/deltas dans le temps, reprise propre
   après restart (déjà largement couvert par `onchain_activity_observation.py`,
   à étendre une fois les nouvelles primitives câblées).

## Brique 6 — Backfill historique on-chain (ajoutée 29/08, priorité élevée)

**L'avantage spécifique de cet environnement** : les événements passés restent
interrogeables sur la blockchain (immuabilité). On n'est pas condamné à
attendre des semaines de collecte prospective pour apprendre — on peut
reconstruire des centaines/milliers de trajectoires historiques déjà connues
d'ARIA (pools/tokens déjà tradés en shadow) via des appels RPC ciblés
(`eth_getLogs` sur une plage de blocs passés), au lieu de dépendre uniquement
de la collecte en direct.

**Objectif** : pour des pools/tokens déjà connus par ARIA, reconstruire
historiquement Swap / volume quote / buy-sell / traders / Mint-Burn /
timestamps / prix / liquidité (si reconstructible), sur une plage de blocs
définie — puis produire le MÊME format que celui utilisé plus tard par le
replay causal et le futur moteur de signal temps réel.

### Séquence recherche visée (une fois le dataset construit)

```
création pool -> premiers swaps -> évolution activité -> buy/sell flow ->
liquidité -> accélération -> MFE/MAE -> résultat réel
```

Travailler À REBOURS sur les meilleurs trades historiques : 5s/10s/20s/30s/60s
avant l'explosion, qu'est-ce qui était déjà visible on-chain ? Puis comparer
EXACTEMENT les mêmes fenêtres sur des trades moyens/perdants/pools qui n'ont
jamais accéléré. Candidats de features à chercher : accélération des swaps,
accélération du buy flow, nouveaux traders, taille des swaps, rythme des
transactions, liquidity delta, migration AMM, prix+activité simultanément,
rupture brutale vs activité précédente.

### Règle anti-look-ahead (absolue, jamais assouplir)

Une feature à l'instant `t` ne doit utiliser QUE des événements dont le
timestamp/bloc est <= `t`. Interdit : utiliser le futur du trade, utiliser le
MFE pour construire une feature, sélectionner un pool PARCE QU'IL a explosé
puis ne mesurer que les signaux qui marchaient sur celui-là. Le statut
gagnant/perdant sert UNIQUEMENT à l'analyse après coup, jamais à la sélection
ou au calcul des features elles-mêmes. Le dataset doit contenir gagnants,
moyens ET perdants — sinon on construit un détecteur de passé, pas de futur.

### Protocole de labellisation du dataset (ajouté 29/08, affiné le même jour — décision opérateur)

**Question de recherche prioritaire, reformulée par l'opérateur** : pas "est-ce
moralement un scam" (intention, rug prouvé, honeypot) — la question est plus
simple et directement mesurable : **pourquoi certains tokens pump à des
multiples extrêmes (+30 000%) et continuent, alors que d'autres, après un
pump similaire, retombent ?** Le label se fonde sur le **résultat de prix
observé**, jamais sur une classification d'intention. Pas besoin de prouver
un rug/honeypot pour qu'un token compte comme "retombé" — la trajectoire de
prix (variation depuis le pic, tenue du niveau) suffit et est directement
lisible sur DexScreener.

Catégories utiles : **pump massif + tenue/continuation** vs **pump massif +
retombée** — une 3e catégorie "contrôles neutres" (jamais explosé du tout)
reste utile plus tard pour éviter qu'un modèle apprenne juste "grosse hausse
= bon", mais n'est pas le sujet prioritaire immédiat.

Règle qui reste absolue : les labels doivent être **figés avant tout calcul
de feature**, et **indépendants des features elles-mêmes** — jamais choisir
un token parce qu'une feature candidate "marche dessus". Voir la règle
anti-look-ahead ci-dessus, qui s'applique symétriquement au choix des labels.

### Architecture cible

```
BLOCKCHAIN
    v
HISTORICAL BACKFILL
    v
EVENTS + POOLS + TIMESTAMPS
    v
FEATURE ENGINE
    v
  BONS TRADES  <->  MAUVAIS TRADES
    v
DISCOVERY RULES
    v
REPLAY CAUSAL
    v
PAPER / SHADOW
    v
EXECUTION
```

### Étape A — prototype (après validation réelle de la Brique 1 en production)

- 10-20 pools historiques (mélange gagnants + pump-and-dump/perdants — jamais
  seulement des gagnants, cf. règle anti-look-ahead).
- Backfill ciblé via `eth_getLogs`.
- Reconstruire les événements avec le décodeur validé (Brique 1).
- Comparer aux transactions/events connus (vérité terrain).
- Valider zéro confusion Swap vs Sync/Mint/Burn sur l'historique.
- Vérifier timestamps et absence de doublons.
- Livrables attendus avant de passer à l'étape B : design du format de
  dataset, coût RPC réel mesuré, stratégie de découpage en plages de blocs,
  déduplication, gestion des rate limits (cf. throttle Chainstack partagé
  existant), validation du prototype, estimation chiffrée du coût du backfill
  complet.

### Étape B — backfill scalable (seulement si A validée)

Construire le backfill pour l'ensemble des pools historiques connus d'ARIA,
en réutilisant le format de dataset validé en étape A.

**Statut actuel (29/08) : brique ajoutée à la roadmap, AUCUN CODE, AUCUN appel
RPC de backfill lancé.** Candidats de tokens à fournir par l'opérateur (mix
pump réussi / pump-and-dump) déjà proposés — à collecter pour préparer l'étape
A, mais l'exécution réelle du backfill attend la validation production de la
Brique 1 (cf. `docs/HANDOFF_PIPELINE_MOMENTUM.md` pour l'état exact).
