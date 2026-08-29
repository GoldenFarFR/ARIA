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

### Protocole de labellisation du dataset (29/08, version consolidée après le cas COPPERINU)

**Identité canonique d'une observation — jamais le nom/symbole.** Le cas
COPPERINU (même contrat compté deux fois sous deux pools différents,
V3 et V4, avec des noms identiques) a prouvé que le nom affiché n'est pas
fiable. Chaque ligne du dataset doit porter au minimum :
`chain + token_contract + pool_address/pool_id + dex + création_pool +
timestamp`. Le nom/symbole reste une info descriptive, jamais une clé
d'identité ou de déduplication.

**Question de recherche** : pas "est-ce moralement un scam" (intention, rug
prouvé, honeypot) — la question est mesurable directement sur la
trajectoire : pourquoi certains tokens pump à des multiples extrêmes et
continuent, alors que d'autres, après un pump similaire, retombent ?

**Trois groupes, jamais deux** :
- **Groupe A — pump durable** : forte hausse -> consolidation ->
  continuation -> pas de rug évident.
- **Groupe B — pump-and-dump** : forte hausse -> distribution ->
  effondrement.
- **Groupe C — contrôles neutres** : activité comparable, pas de
  continuation significative (jamais explosé). **Indispensable, pas
  optionnel** : sans lui, ARIA risque d'apprendre une règle triviale du
  genre "beaucoup de volume + beaucoup de traders = bon", alors que ces
  mêmes caractéristiques peuvent précéder un dump (cf. cas Optimus — Groupe B
  — dont les agrégats 24h étaient presque aussi équilibrés que Copper Inu —
  Groupe A).

**Mesurer la trajectoire, jamais seulement le résultat final.** Une fenêtre
temporelle centrée sur le pic (`T-60m ... T0 ... T+60m`), avec à chaque pas
les features suivantes calculées et leur VARIATION dans le temps (pas
seulement leur valeur ponctuelle) : `unique_traders`, `new_traders_rate`,
`buy_volume`, `sell_volume`, `buy/sell_ratio`, `swap_count`,
`median_swap_size`, `swap_size_distribution`, `liquidity`,
`liquidity_delta`, `price_return`, `volume_acceleration`,
`buyer/seller_concentration`. L'observation intéressante n'est pas "3616
traders" (un nombre statique) mais "le nombre de participants continue à
augmenter PENDANT que le marché consolide" (une dynamique) — hypothèse plus
exploitable qu'un seuil.

**Feature candidate ajoutée (29/08, confirmation externe indépendante)** :
`higher_lows_count` / structure de la zone d'accumulation post-premier-repli.
Un post externe (X, @0xkioto, capture fournie par l'opérateur) documente le
même schéma sur 3 runners réels de Robinhood Chain (rally initial ->
drawdown -60% à -80% -> nouvel ATH 4.6x-23x le point bas), en insistant sur
une série de creux SUCCESSIVEMENT PLUS HAUTS pendant la consolidation avant
la cassure — pas juste "est-ce que ça reprend une fois" (ce qu'on avait déjà
avec Copper Inu vs Optimus), mais la STRUCTURE des creux qui montre une
conviction acheteuse croissante. Confirme indépendamment que le concept de
zone d'accumulation post-retracement (déjà présent dans ce document) est un
signal recherché par d'autres praticiens du même marché, pas une
construction ad-hoc. Reste une feature à tester empiriquement sur le
dataset A/B/C, pas une règle validée.

**Prudence sur le mot "organique"** : label descriptif, jamais une vérité de
terrain ni une règle de code. Un token avec 3000 traders peut être manipulé ;
un token avec 100 traders peut faire une vraie continuation ; des bots
peuvent aussi représenter une activité légitime. **Interdit** : coder
`traders > X -> organic`. **Recherché** : quelles COMBINAISONS et quelles
TRAJECTOIRES séparent statistiquement A / B / C — jamais un seuil unique sur
une seule variable.

**DexScreener = sanity check externe, jamais vérité d'entraînement.**
Hiérarchie de validation : `ON-CHAIN -> notre reconstruction -> comparaison
DexScreener -> écarts détectés -> correction du décodeur`. Une fois la
reconstruction validée : `ON-CHAIN -> ARIA FEATURES -> REPLAY HISTORIQUE ->
A/B/C -> DISCOVERY`. Les chiffres DexScreener servent à vérifier que notre
décodeur est juste, jamais de source d'entraînement pour les features
elles-mêmes.

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

### Candidats identifiés (29/08, fournis par l'opérateur)

Deux lots de 10 liens DexScreener identifiés via recherche (WebFetch +
claude-in-chrome, DexScreener étant une SPA React). **2 exclusions déjà
trouvées, à ne jamais réintroduire sans re-vérification** : le token "NTF"
(Robinhood, était classé "perdant" mais était en fait +2682% sur 24h au
moment de l'identification) et "COPPERINU" en double (même contrat
`0x531...B63B` sur deux pools — Uniswap V3 ET V4 — identifié une fois côté
"gagnant" +31 439% et une fois côté "perdant" +74.92% : ce n'est PAS un
second exemple, c'est le même token vu sous un pool différent). Toujours
vérifier par ADRESSE DE CONTRAT, jamais par nom/symbole affiché — les clones
de nom sont fréquents sur ce marché.

**Gagnants (pump massif, 8 valides sur 10 fournis — 2 liens ne correspondent
à aucun pool existant)** : Greyson/CACKLE (Solana, PumpSwap), Copper
Inu/COPPERINU (Robinhood, Uniswap V4, `0x531...B63B`), pTokens
Index/PDEX (Robinhood V4), Twofold/TWO (Robinhood V4), MU MU THE
BULL/MU (Robinhood V4), Qubit/QUBIT (Robinhood V4), dawg/DAWG (Solana,
Raydium), Pons Charity/CHARITY (Robinhood V4).

**Perdants (retombée confirmée par le prix, 8 valides sur 10 fournis — 2
exclus, cf. ci-dessus)** : fone/apeonfone -35.70% (Solana, PumpSwap),
ANTSEM -58.48% (Solana, PumpSwap), Pistacio -55.96% (Solana, PumpSwap),
PAWHOOD -44.00% (Robinhood V4), OPTIMUS -93.36% (Robinhood V4, exemple
étudié en détail — crash quasi-vertical puis mort totale malgré des
agrégats 24h buy/sell en apparence équilibrés, cf. note ci-dessous), VERONA
-92.69% (Robinhood V4), HOODHIM -93.58% (Robinhood V4), MSR -11.13%
(Robinhood V2, seul V2/V3 classique du lot perdant avec MSR).

**Insight empirique précoce (29/08, avant même le backfill réel)** :
comparaison visuelle Copper Inu (continuation, paliers de consolidation puis
nouvelle jambe haussière, $377K de liquidité, 3616 traders) vs Optimus (dents
de scie puis crash quasi-vertical puis mort totale, $5.8K de liquidité
seulement, 15h d'âge). **Les agrégats 24h (nombre de traders, ratio buy/sell
volume) étaient PRESQUE IDENTIQUEMENT équilibrés dans les deux cas** — parce
qu'ils mélangent la phase de pump et la phase de dump. Ça confirme qu'un
simple comptage agrégé sur toute la fenêtre ne suffira probablement pas : le
signal différenciateur est plus probablement dans la DYNAMIQUE TEMPORELLE
autour du pic (vitesse/amplitude du retracement immédiat, liquidité
disponible au moment du pic, reprise ou mort de l'activité après) — exactement
ce que la fenêtre "5s/10s/20s/30s/60s avant/après" plus haut dans ce document
est censée capturer. À vérifier empiriquement une fois le backfill possible,
pas encore une conclusion.

### Étape A — prototype (après validation réelle de la Brique 1 en production)

- 10-20 pools historiques (mélange gagnants + pump-and-dump/perdants — jamais
  seulement des gagnants, cf. règle anti-look-ahead). Candidats déjà
  identifiés ci-dessus (8+8 valides).
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

## Cadrage stratégique — memecoin trading comme jeu PvP (29/08, décision opérateur)

**Le trading de memecoins par "vibe" n'est pas une stratégie d'investissement,
c'est un jeu PvP à horizon court.** Ça change la nature des variables qui
comptent, et donc ce que ces capteurs doivent finir par produire.

**Investissement organique** : `fondamentaux -> adoption -> croissance ->
revalorisation` — le temps joue plutôt en faveur du participant.

**Meme trading PvP** : `attention -> flux -> accélération -> positionnement
-> sortie` — la question n'est jamais "est-ce un projet sérieux ?" mais "où
est le flux de capitaux maintenant, qui entre, qui sort, à quelle vitesse, et
combien de temps ce déséquilibre peut-il encore durer ?".

**Principe à graver** : fondamentalement mauvais != tradablement mauvais ;
fondamentalement bon != tradablement bon. Un pump peut être totalement
dépourvu de fondamentaux et rester un excellent trade. Pour un moteur PvP,
les variables prioritaires sont : attention, accélération, participation,
liquidité, concentration, comportement des gros wallets, rythme des swaps,
pression acheteuse/vendeuse, capacité du marché à absorber les ventes — pas
des indicateurs de qualité fondamentale du projet.

**Architecture cible du futur moteur** (au-delà des capteurs, pour mémoire —
pas construit) :

```
DETECTION
    v
FLOW / VIBE
    v
ENTRY AGGRESSIVE
    v
POSITION MANAGEMENT
    v
DETECTION D'EXHAUSTION
    v
EXIT
```

**Le mot-clé est "exhaustion".** L'avantage recherché n'est probablement pas
de trouver le prochain x100 — c'est de détecter assez tôt que le jeu PvP
bascule en notre faveur, PUIS, tout aussi important, que l'avantage est en
train de disparaître, pour sortir avant que la majorité ne le comprenne.
**ARIA doit devenir aussi bonne pour détecter la fin d'un bon pump que son
début** — optimiser uniquement la détection des "bons pumps" serait une
erreur de conception. Le dataset A/B/C (ci-dessus) sert exactement ça : pas
seulement "bon token vs mauvais token", mais "flux soutenable ->
accumulation/distribution -> continuation" vs "flux toxique -> extraction ->
collapse".

Nuance de vocabulaire actée : remplacer "solitaire" par **indépendant du
consensus** — ARIA n'a pas besoin d'être isolée, elle a besoin de ne pas
suivre le narratif ambiant sans le vérifier par le flux réel.

## Roadmap complète du moteur, au-delà des capteurs (29/08, décision opérateur)

Trois éléments verrouillés en plus de la séquence capteurs -> backfill ->
discovery -> replay -> shadow -> exécution déjà posée plus haut.

### 1. Détecteur de phase de marché / régime — dimension du feature engine, pas un module à part

Le même signal on-chain (buy flow élevé, par exemple) peut vouloir dire des
choses différentes selon où le token se trouve dans sa trajectoire :

```
naissance -> découverte -> accélération -> expansion -> consolidation ->
distribution -> exhaustion -> collapse
```

ARIA ne doit pas apprendre "buy flow élevé = intéressant" mais "buy flow
élevé + accélération + participation croissante + POSITION DANS LA
TRAJECTOIRE = intéressant". La phase devient une dimension fondamentale du
feature engine — pas un module séparé qu'on branche après, un axe qui
traverse toutes les features de la Brique 2/3 (les features sont
interprétées différemment selon la phase où l'observation se situe).

### 2. Temps de réaction / edge restant — le signal doit répondre à DEUX questions

Directement lié à l'observation sur le décalage on-chain/frontend et à
l'effet de synchronisation qui en découle :

```
détection on-chain -> visibilité probable publique -> réaction du marché ->
temps avant exhaustion
```

Pas seulement "est-ce intéressant ?" mais surtout **"est-ce encore
intéressant MAINTENANT ?"** — un signal statistiquement excellent qui
n'apparaît qu'après +300% peut déjà être trop tardif pour être tradable.
Métriques à construire plus tard (pas maintenant, notées pour la phase
feature engine) : `lead_time_to_move`, `lead_time_to_visibility`,
`time_from_signal_to_exhaustion`, `price_move_after_signal_1m/5m/10m` — pour
mesurer la valeur temporelle réelle d'un capteur, pas seulement son pouvoir
discriminant statique.

### 3. Boucle complète détection -> entrée -> sortie -> apprentissage

Le système ne s'arrête pas à `discovery -> replay -> shadow`. Le moteur
final doit produire un journal causal exploitable, une ligne par trade
simulé :

```
TOKEN -> SIGNAL -> POURQUOI SIGNAL -> ENTRÉE SIMULÉE -> ÉVOLUTION DU FLOW ->
EXHAUSTION ? -> SORTIE -> MFE/MAE/PnL net -> POST-MORTEM -> AMÉLIORATION DU SIGNAL
```

Raison : pour une approche PvP, **entrée et sortie sont deux problèmes
différents**. Un modèle peut exceller à détecter le début d'un mouvement et
être médiocre à savoir quand sortir — et le second peut compter économiquement
plus que le premier. Chaque étape (Brique 7 "signaux", replay causal, shadow)
doit donc journaliser POURQUOI un signal s'est déclenché, pas seulement s'il
s'est déclenché — pour permettre un vrai post-mortem qui améliore le signal,
pas juste un score final agrégé.

### Roadmap finale complète (remplace le schéma court plus haut)

```
CAPTEURS BRUTS
      v
BACKFILL
      v
FEATURES DYNAMIQUES
      v
PHASE / RÉGIME
      v
DISCOVERY A/B/C
      v
REPLAY CAUSAL
      v
MESURE DU LEAD TIME
      v
OOS (out-of-sample)
      v
PAPER / SHADOW
      v
SIMULATION EXÉCUTION
      v
ENTRÉE + GESTION + EXHAUSTION + SORTIE
      v
POST-MORTEM
      ^ (boucle retour)
DISCOVERY
```

### Objectif final du moteur (gravé, oriente toute conception future)

**ARIA ne cherche pas à prédire "ce token va monter".** L'objectif est de
détecter suffisamment tôt qu'un déséquilibre PvP est en train de
s'auto-renforcer, mesurer si ce déséquilibre reste exploitable maintenant
(pas seulement s'il a existé), puis détecter son épuisement avant le
retournement. Cette formulation aligne les capteurs on-chain, la boucle
réflexive frontend/FOMO, le backfill, le replay causal et l'exécution en un
seul fil directeur — à ne jamais perdre de vue en construisant les briques
suivantes.

## Mini-spec Brique 2 — Buy/Sell Flow (29/08, PRÉPARÉE, PAS IMPLÉMENTÉE)

Statut : Brique 1 fonctionnellement validée en production (checkpoint
2026-08-29T15:19:22Z-15:40Z, zéro anomalie), robustesse encore en
observation (checkpoint élargi à venir). **Cette mini-spec prépare
uniquement Brique 2** — aucune implémentation avant confirmation du
checkpoint élargi ET accord explicite de l'opérateur sur cette spec.
Ancrée dans le vrai code de `evm_swap_ws.py` (relu le 29/08), pas une
convention inventée à l'aveugle.

### Convention de sens, par famille, telle qu'elle existe déjà dans le décodeur

**V2 (`_handle_v2_swap`)** : le Swap event V2 expose 4 `uint256` séparés et
JAMAIS négatifs — `amount0_in`/`amount1_in`/`amount0_out`/`amount1_out`. Le
code calcule déjà `quote_in`/`quote_out` (le côté quote selon
`token_is_currency0`), avec la garantie structurelle qu'**un seul des deux
est non-nul** ("one side of the quote pair is always zero", commentaire
existant du code). Convention : `quote_in > 0` -> le trader a payé en quote
pour recevoir le token tracké -> **BUY**. `quote_out > 0` -> le trader a
reçu du quote en échange du token tracké -> **SELL**.

**V3/V4 (`_record_swap_amount`, partagé)** : le Swap event expose
`amount0`/`amount1` en `int256` SIGNÉS (convention Uniswap V3 standard :
positif = le pool REÇOIT ce token, négatif = le pool ENVOIE ce token). Le
code calcule déjà `quote_raw` (signé, avant le `abs()` actuel qui sert
uniquement à alimenter `cumulative_volume_quote` sans distinction de sens —
**ce signe est aujourd'hui perdu, Brique 2 doit le capturer AVANT le
`abs()`**). Convention : `quote_raw > 0` -> le pool reçoit le quote, le
trader paie en quote -> **BUY** du token tracké. `quote_raw < 0` -> le pool
envoie le quote, le trader reçoit du quote -> **SELL**.

### Cas indéterminé — ne jamais forcer

V2 : si les DEUX amounts (in ET out) sont non-nuls simultanément (ne devrait
jamais arriver selon la mécanique standard, signal d'un event anormal) ->
indéterminé. V3/V4 : si `quote_raw == 0` (rare, un swap dont le côté quote
s'arrondit à zéro après ajustement décimal) -> indéterminé. Un swap
indéterminé compte dans `swap_count`/`cumulative_volume_quote` (Brique 1
inchangée) mais NI dans `buy_count` NI dans `sell_count` — un nouveau champ
`undetermined_count`/`undetermined_volume_quote` absorbe ce cas plutôt que
de forcer un sens arbitraire.

### Champs nouveaux (additifs, zéro nouvel appel RPC/websocket)

Sur `_TrackedPool`/`EVMSwapSnapshot` : `buy_count`, `sell_count`,
`undetermined_count`, `buy_volume_quote`, `sell_volume_quote`,
`undetermined_volume_quote`. `net_flow_quote` = `buy_volume_quote -
sell_volume_quote`, calculé à la volée (jamais stocké séparément). Côté
`onchain_activity_observation.py` : deltas correspondants
(`buy_count_delta`, `sell_count_delta`, `buy_volume_quote_delta`,
`sell_volume_quote_delta`), même doctrine que Brique 1 (`None`/`baseline_reset`
sur restart, jamais un delta fabriqué).

### Invariants — formulation robuste (pas la version simplifiée)

`buy_count + sell_count + undetermined_count == swap_count` — **toujours
vrai par construction**, jamais une approximation. `buy_volume_quote +
sell_volume_quote + undetermined_volume_quote == cumulative_volume_quote` —
idem, toujours vrai. Sur un snapshot "clean" (`undetermined_count == 0`,
le cas normal attendu), la version simple de l'opérateur (`buy_count +
sell_count == swap_count`, `buy_volume_quote + sell_volume_quote ==
cumulative_volume_quote`) redevient vraie automatiquement — elle n'est
donc pas remplacée, juste dérivée du cas général plutôt que supposée toujours
applicable telle quelle.

### Plan de tests obligatoire (avant toute implémentation)

1. V2 buy (`quote_in>0`, `quote_out=0`) -> `buy_count+=1`, volume au bon
   compteur.
2. V2 sell (`quote_out>0`, `quote_in=0`) -> `sell_count+=1`, volume au bon
   compteur.
3. V3/V4 buy (`quote_raw>0` signé) -> `buy_count+=1`.
4. V3/V4 sell (`quote_raw<0` signé) -> `sell_count+=1`.
5. Volume correctement affecté au bon côté sur les 4 cas ci-dessus (aucune
   fuite croisée buy<->sell).
6. `net_flow_quote = buy_volume_quote - sell_volume_quote` (calcul, jamais
   un champ stocké séparément qui pourrait diverger).
7. Event ambigu/indéterminé (V2 in+out simultanés, V3/V4 `quote_raw==0`)
   -> ni buy ni sell forcé, `undetermined_count` incrémenté.
8. Compteurs cohérents sur une séquence mixte réelle de plusieurs swaps
   (test d'intégration bout en bout, pas seulement unitaire).
9. Comportement Brique 1 inchangé — `swap_count`/`cumulative_volume_quote`
   byte-identiques avec/sans ce wiring (non-régression explicite).
10. Les deux invariants ci-dessus vérifiés sur un mélange buy/sell/indéterminé,
    pas seulement sur des snapshots 100% propres.
11. **Garde-fou ajouté par l'opérateur (29/08) — test d'identité du sens au
    niveau du TOKEN TRACKÉ, pas du pair.** Les 4 tests 1-4 ci-dessus doivent
    CHACUN être dédoublés sur `token_is_currency0=True` ET
    `token_is_currency0=False` (8 tests effectifs, pas 4) — la convention
    "quote entrant dans le pool = BUY" ne veut dire "achat du token tracké"
    que si `token_is_currency0` est correctement propagé ; une inversion
    silencieuse de ce flag inverserait BUY/SELL sans qu'aucun autre test ne
    le détecte. C'est un garde-fou obligatoire, pas optionnel, avant toute
    implémentation.

### Ce que cette brique ne fait PAS

Aucun score, aucun seuil, aucune conversion USD. Pas de calcul de
`net_flow`/`buy/sell pressure` en tant que SIGNAL — seulement les compteurs
et volumes bruts, prêts à être consommés par une future feature (Brique 7+),
jamais gravés en règle de trading directement.
