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

### Ce que Brique 2 débloque pour la suite (candidates, PAS codées maintenant)

Brique 1 répondait "il se passe quelque chose". Brique 2 répond "qui pousse
le marché, dans quel sens, avec quelle intensité". Exemple concret : deux
tokens tous deux à +500% au même instant peuvent avoir un graphique
identique en apparence, alors qu'on-chain ils sont dans deux situations
opposées — Token A (`buyers` en hausse, `buy_volume` dominant, `net_flow`
fortement positif) vs Token B (`buyers` stagnant, `sell_volume` dominant,
`net_flow` fortement négatif). C'est précisément ce que Brique 1 seule ne
pouvait pas distinguer.

**Le vrai intérêt n'est pas `net_flow` pris isolément à un instant T — c'est
sa DYNAMIQUE.** Un `net_flow` qui accélère régulièrement (`+20k -> +35k ->
+60k -> +110k -> +180k -> +240k`) signale un déséquilibre acheteur qui
s'intensifie. Un `net_flow` qui s'érode puis s'inverse (`+200k -> +160k ->
+100k -> +30k -> -40k -> -150k`) signale un flux qui se retourne — c'est
directement la question PvP posée plus haut : "est-ce que le mouvement a
encore du carburant maintenant ?".

Features candidates que Brique 2 rend calculables plus tard (Brique 7+,
jamais avant validation empirique sur le dataset A/B/C) : `buy_flow_
acceleration`, `net_flow_velocity`, `buy/sell_imbalance`, `flow_reversal`,
`buy_volume_per_new_trader`, `selling_pressure / liquidity`,
`flow_persistence`.

Séquence qui reste inchangée : Brique 2 (données buy/sell propres) ->
Brique 3 (liquidity delta) -> features dynamiques -> comparaison type
Copper Inu vs Optimus -> dataset A/B/C historique -> discovery. Aucune
feature de cette liste n'est codée avant ce point.

## Notes d'exploration Brique 3 — Mint/Burn/liquidity delta (29/08, terrain préparé, AUCUN CODE)

Exploré pendant l'observation de Brique 2 (lecture seule, périmètre
différent du mécanisme en cours de mesure). Pas encore une mini-spec
validée — juste ce qu'il faut savoir avant d'en écrire une.

**Confirmé par grep : aucun topic Mint/Burn n'est actuellement souscrit**
(`_SYNC_TOPIC`/`_V2_SWAP_TOPIC`/`_V3_SWAP_TOPIC`/`_V4_SWAP_TOPIC` sont les
seuls dans `evm_swap_ws.py`). 5 nouveaux `topic0` à calculer : Mint V2,
Burn V2, Mint V3, Burn V3, `ModifyLiquidity` V4.

**Zéro nouvelle connexion possible** — `_resubscribe()` construit déjà le
filtre v2/v3 avec une LISTE de topic0 sur `topics[0]` (`[_SYNC_TOPIC,
_SYNC_TOPIC_AERODROME, _V2_SWAP_TOPIC, _V3_SWAP_TOPIC]`) — ajouter Mint/Burn
à cette même liste ne coûte aucune souscription/connexion supplémentaire,
même pattern que Brique 1 pour `_V2_SWAP_TOPIC`.

**Asymétrie structurelle importante entre familles, à trancher AVANT de
coder** :
- **V2** : `Mint(sender, amount0, amount1)` / `Burn(sender, amount0,
  amount1, to)` — deux events séparés, PAS de signe explicite (le sens
  ajout/retrait est déterminé par QUEL event est reçu, Mint=ajout,
  Burn=retrait). `amount0`/`amount1` sont déjà en unités de token (uint,
  jamais négatifs), directement convertibles en quote comme le reste du
  module.
- **V3** : `Mint(sender, owner, tickLower, tickUpper, amount, amount0,
  amount1)` / `Burn(owner, tickLower, tickUpper, amount, amount0,
  amount1)` — même principe (deux events séparés, signe implicite par
  l'event), `amount0`/`amount1` en unités de token comme V2. `amount`
  (uint128) est la quantité de liquidité concentrée (L), une unité
  abstraite distincte, pas directement en quote.
- **V4** : **UN SEUL event** `ModifyLiquidity(id, sender, tickLower,
  tickUpper, liquidityDelta, salt)` — couvre mint ET burn, distingué par
  le SIGNE de `liquidityDelta` (int256, positif=ajout/négatif=retrait) --
  déjà signé nativement, contrairement à V2/V3. **Mais `liquidityDelta` est
  en unité de liquidité concentrée abstraite (L), PAS en montant de token**
  — contrairement à V2/V3 qui donnent `amount0`/`amount1` en clair. Convertir
  V4's liquidity_delta en "quote units" comparables à V2/V3 demanderait un
  calcul supplémentaire (prix courant + ticks), pas juste lire un champ.

**Décision tranchée par l'opérateur (29/08)** : deux unités différentes
selon la famille, ne PAS forcer V4 dans une pseudo-unité quote artificielle
au stade capteur brut. V2/V3 -> `liquidity_added_quote`/`liquidity_
removed_quote` (montants réels de token). V4 -> `liquidity_delta_raw`
signé, unité abstraite, jamais converti. Convertir V4 trop tôt ajouterait
une couche de calcul (prix, tick/range, sqrtPrice, décimales, position) qui
transformerait un capteur mécanique en mini-modèle — hors scope de cette
brique. Une feature comparable entre familles, si les données le
permettent proprement, se construira bien plus tard dans le feature engine,
jamais ici.

### Ce que Brique 3 ajoute conceptuellement (troisième axe, distinct de buy/sell)

Avec Brique 1+2, ARIA sait : `Swap -> BUY/SELL -> volume`. Brique 3 ajoute
un axe complètement séparé : `Mint -> liquidité ajoutée`, `Burn ->
liquidité retirée`. Trois phénomènes distincts sur un même pool : BUY,
SELL, et LIQUIDITY (elle-même MINT/BURN) — jamais mélangés. C'est
déterminant pour l'hypothèse PvP : "prix ↑, buy flow ↑, traders ↑,
liquidité stable" n'est PAS la même situation que "prix ↑, buy flow ↑,
traders ↑, liquidité retirée fortement" (le deuxième cas sépare le flux de
trading de l'érosion de la profondeur disponible — un signal d'alarme que
Brique 1+2 seules ne peuvent pas voir).

### Invariants conceptuels (à respecter dès la mini-spec formelle)

**V2/V3** : Mint et Burn ne doivent JAMAIS augmenter `swap_count` (ce ne
sont pas des trades). Un Mint augmente le bucket liquidité ajoutée. Un Burn
augmente le bucket liquidité retirée. Aucun montant de Mint/Burn ne doit
contaminer `buy_volume_quote`/`sell_volume_quote` — trois compteurs
complètement séparés, jamais additionnés entre eux.

**V4** : `liquidityDelta > 0` -> ajout. `liquidityDelta < 0` -> retrait.
`liquidityDelta == 0` -> cas ignoré/indéterminé (à vérifier contre la
mécanique réelle une fois la mini-spec écrite). Ne jamais transformer
silencieusement `liquidityDelta` en quote.

**Absolu, toutes familles** : Brique 3 ne dit JAMAIS "bonne" ou "mauvaise"
liquidité — elle constate seulement `+liquidity`, `-liquidity`, quand, où,
combien, dans l'unité native de la famille concernée. Aucun jugement,
aucun seuil.

### Détail technique par famille (exploration précise, 29/08)

**V2** : `Mint(address indexed sender, uint amount0, uint amount1)` /
`Burn(address indexed sender, uint amount0, uint amount1, address indexed
to)` — deux events séparés, chacun donne `amount0`/`amount1` en clair
(unités de token réelles, toujours positives, jamais négatives comme dans
un Swap). Même pattern d'extraction que le décodeur swap déjà existant :
le côté quote (`token_is_currency0` déjà connu du pool) donne directement
`liquidity_added_quote` (depuis Mint) ou `liquidity_removed_quote` (depuis
Burn), décimal-ajusté comme le reste du module. 2 nouveaux `topic0` à
calculer : `Mint(address,uint256,uint256)`, `Burn(address,uint256,uint256,address)`.

**V3** : `Mint(sender, owner, tickLower, tickUpper, amount, amount0,
amount1)` / `Burn(owner, tickLower, tickUpper, amount, amount0, amount1)`
— même principe, `amount0`/`amount1` en clair permettent le même calcul que
V2. `amount` (uint128) est la quantité de liquidité concentrée L modifiée à
CETTE position (range `[tickLower, tickUpper]`) — une mesure différente,
utile plus tard (comparable pool à pool comme `raw_liquidity` déjà fait
pour le swap) mais pas nécessaire pour `liquidity_added_quote`/`_removed_
quote`. **Nuance technique réelle à documenter, pas à sur-résoudre
maintenant** : dans Uniswap V3, un `Burn` crédite les montants au owner
sans forcément les transférer immédiatement (un `collect()` séparé règle
le vrai mouvement de trésorerie) — mais pour notre usage (mesurer la
profondeur active disponible pour absorber un futur swap), c'est le
`Burn` lui-même (réduction de la position active) qui est le signal
pertinent, pas le règlement. 2 nouveaux `topic0` (V3 partage la famille
avec Aerodrome Slipstream, même event shape déjà vérifié pour Swap).

**V4** : `ModifyLiquidity(PoolId indexed id, address indexed sender, int24
tickLower, int24 tickUpper, int256 liquidityDelta, bytes32 salt)` — UN
SEUL event, `liquidityDelta` déjà signé (positif=ajout, négatif=retrait).
**Mesure brute fiable directement extractible, sans aucun calcul** :
`liquidity_delta_raw` (le champ signé lui-même), `tickLower`/`tickUpper`
(la range concernée), `sender`. **Ce qui serait nécessaire pour une
conversion ultérieure en quote units** (PAS fait maintenant) : le prix
courant du pool (déjà tracké via `pool.ticks`), les ticks de la range
(déjà dans l'event), et la formule Uniswap standard convertissant
liquidité(L) + range + prix courant en montants réels de token0/token1 --
une vraie formule mathématique non triviale (différente selon que le prix
courant est dans la range ou en dehors), pas un champ à lire. Exactement
pourquoi l'opérateur tranche pour garder V4 en unité abstraite au stade
capteur brut. 1 nouveau `topic0` : `ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)`.

### Impact sur le format temporel du backfill (point 3, vérifié conceptuellement)

Le pattern déjà établi (Brique 1/2) : `_TrackedPool` accumule des
compteurs CUMULATIFS en mémoire, `onchain_activity_observation.py` calcule
des DELTAS entre observations successives avec `baseline_reset` sur
restart. Brique 3 suivrait le même pattern -- `liquidity_added_quote`/
`liquidity_removed_quote` cumulatifs (V2/V3), `liquidity_added_raw`/
`liquidity_removed_raw` cumulatifs séparés par signe (V4) -- donc le MÊME
format temporel (timestamp + cumulatif + delta) que Brique 1/2. Pas de
cassure structurelle prévue pour le backfill : les events Mint/Burn/
ModifyLiquidity sont on-chain immuables comme Swap/Sync, rejouables via
`eth_getLogs`. **Le vrai risque à surveiller, pas une cassure de format
mais une cassure de DÉFINITION** : le décodeur de backfill devra utiliser
EXACTEMENT la même logique de classification (V2/V3 -> quote via
amount0/amount1, V4 -> raw `liquidityDelta` jamais converti) que le
décodeur temps réel -- sinon on aurait deux définitions différentes de
"liquidity_delta" selon la source, le même risque déjà identifié pour la
frontière `v2_biased` avant/après le T0 de Brique 1.

### Point de vigilance gravé (29/08, précisé par l'opérateur) — deux mesures distinctes, ne jamais les confondre

Le commentaire sur V3 (`Burn` ne transfère pas forcément les tokens
immédiatement, `collect()` séparé) implique qu'ARIA devra distinguer,
maintenant et pour toujours, deux affirmations différentes :
1. **"la position/liquidité active a été réduite"** — ce que Brique 3
   mesure réellement (l'event `Burn`/`ModifyLiquidity` lui-même).
2. **"des tokens ont effectivement quitté le pool à cet instant"** — une
   affirmation plus forte, que Brique 3 ne prouve PAS pour V3 (le
   règlement réel passe par `collect()`, un event distinct, pas encore
   capturé).

**Pour Brique 3 telle que scopée maintenant, la mesure (1) suffit** — mais
il ne faut jamais lui faire dire (2) implicitement dans une future feature
ou un futur signal. Si le règlement réel (`collect()`) devient un jour
nécessaire, ce sera une brique/extension séparée, jamais supposée acquise
par Brique 3 seule.

### Ce que Brique 3 débloquera pour la lecture PvP (candidats de réflexion, PAS de code)

Trois configurations qui peuvent toutes montrer un graphique en forte
hausse, mais avec une mécanique sous-jacente différente une fois les 3 axes
disponibles (BUY/SELL, LIQUIDITY, prix) :

```
Cas A: buy flow ↑, traders ↑, liquidité stable
Cas B: buy flow ↑, traders ↑, liquidité ↑ (de nouveaux apporteurs rejoignent)
Cas C: buy flow ↑, traders ↑, liquidité ↓↓↓ (la profondeur se retire pendant que le prix monte)
```

Le cas C est particulièrement intéressant pour la recherche d'exhaustion —
**pas parce que "liquidité qui sort = dump" serait une règle valable en soi**
(exactement le genre de raccourci que la règle "jamais coder un seuil sur
une seule variable" interdit déjà), mais parce que ça donne une information
supplémentaire sur la CAPACITÉ du marché à absorber une vente future — un
pool dont la profondeur s'érode pendant que le prix monte a moins de
marge pour encaisser un gros vendeur que celui dont la liquidité arrive en
même temps que les acheteurs.

Candidats de features combinées (Brique 7+, jamais avant validation sur
le dataset A/B/C) : distinguer accumulation (liquidité + buy flow montent
ensemble) de distribution (buy flow monte mais liquidité s'érode) de
véritable exhaustion (les deux se retournent). Pure réflexion à ce stade,
aucune formule/seuil arrêté.

### Principe consolidé (29/08, décision opérateur) — la force d'un pump est une INTERACTION, jamais une métrique isolée

Formulation qui généralise et verrouille le Cas A/B/C ci-dessus, à traiter
comme principe de conception pour tout le feature engine à venir (Brique 7+),
pas seulement pour Brique 3 : **la force réelle d'un pump ne se lit pas dans
le buy flow seul — elle se lit dans la relation entre buy flow, participation
(traders), liquidité disponible, et capacité du marché à absorber les ventes
à venir.** Un buy flow massif porté par une liquidité qui s'érode n'a pas la
même solidité qu'un buy flow identique porté par une liquidité stable ou
croissante — même signal de surface, mécanique sous-jacente opposée.

**Ce que ça implique pour la suite** : une fois Brique 2 (direction) et
Brique 3 (liquidité) validées en production, le vrai candidat de feature
n'est probablement pas un ratio simple (`buy_volume / liquidity`) mais une
mesure d'INTERACTION dynamique entre au moins 4 axes — `buy_flow`,
`participation` (traders actifs/nouveaux), `liquidity_level`,
`liquidity_trend` (Brique 3) — dont la combinaison estime la capacité
d'absorption RESTANTE du pool (combien de pression vendeuse le marché
peut encore encaisser avant que le prix ne casse). C'est cette capacité
d'absorption, plutôt qu'une métrique isolée à un instant T, qui distinguerait
le mieux continuation (Groupe A) d'exhaustion imminente (bascule vers
Groupe B) dans le dataset A/B/C.

**Reste une direction de recherche, pas une formule** — aucun poids, aucun
seuil, aucune combinaison arrêtée à ce stade. La validation empirique sur le
dataset A/B/C (une fois le backfill réel possible) tranchera si cette
interaction se mesure bien par un ratio composite, une dérivée temporelle
multi-axes, ou autre chose — à découvrir sur données réelles, jamais deviné
à l'avance. Prochaine étape concrète : possible seulement après Brique 3
(liquidity delta) validée en production, elle-même après le GO explicite de
l'opérateur sur sa mini-spec.

### Confirmation architecturale majeure (temps réel == backfill, même sémantique)

Point le plus important validé par cette exploration : **le décodeur temps
réel et le futur décodeur backfill peuvent utiliser exactement la même
sémantique de données** (mêmes primitives, mêmes conventions V2/V3/V4).
Ça évite de construire un moteur historique différent du moteur live — la
chaîne `blockchain historique -> même décodage -> mêmes primitives ->
mêmes features -> replay` doit produire des résultats comparables à
`blockchain live -> même décodage -> mêmes primitives -> mêmes features ->
signal`. C'est précisément ce qui permettra de faire confiance au replay
plus tard — un backfill qui redéfinirait ses propres règles de décodage
romprait cette confiance dès le départ.

### Séquence des 3 dimensions maintenant disponibles (une fois Brique 3 validée)

`Brique 1 = activité (quand quelqu'un trade) -> Brique 2 = direction (dans
quel sens) -> Brique 3 = profondeur/liquidité (le marché se remplit ou se
vide)`. Première fois qu'ARIA disposera des trois dimensions nécessaires
pour commencer à comprendre ce qui se passe réellement derrière la courbe
de prix, plutôt que le prix seul.

**Statut : GO opérateur reçu (29/08, post-checkpoint Brique 2 concluant) —
mini-spec formelle ci-dessous, verrouillée avant implémentation.**

## Mini-spec Brique 3 — Mint/Burn/Liquidity Delta (29/08, PRÉPARÉE, GO REÇU)

### Invariants de non-régression (à respecter dès le premier commit, jamais assouplir)

```
Mint/Burn/ModifyLiquidity
        v
    liquidity*
        =/=
    swap_count
        =/=
    buy_volume
        =/=
    sell_volume
```

Aucun événement Mint/Burn/ModifyLiquidity ne doit jamais incrémenter
`swap_count`/`cumulative_volume_quote`/`buy_count`/`sell_count`/
`buy_volume_quote`/`sell_volume_quote` — trois familles de compteurs
(SWAP, BUY/SELL, LIQUIDITY) strictement séparées, jamais additionnées.

### Principe architectural (rappel, gravé, condition de confiance du futur replay)

```
temps réel  ==  même sémantique de décodage  ==  backfill historique
```

Le décodeur Brique 3 doit utiliser exactement la même logique de
classification en temps réel et lors du futur backfill (Brique 6) — deux
définitions différentes de "liquidity_delta" selon la source romprait la
confiance dans le replay causal avant même qu'il existe.

### Convention par famille (reprise de l'exploration, verrouillée, ne pas rouvrir)

**V2/V3** : `Mint(sender, amount0, amount1)` / `Burn(sender/owner, amount0,
amount1, ...)` — deux events séparés, `amount0`/`amount1` en clair (unités
de token réelles, jamais négatives). Le côté quote (`token_is_currency0`
déjà connu du pool) donne directement `liquidity_added_quote` (depuis
Mint) ou `liquidity_removed_quote` (depuis Burn), décimal-ajusté comme le
reste du module. Deux compteurs CUMULATIFS séparés (jamais un net stocké
directement) — même doctrine que `buy_volume_quote`/`sell_volume_quote` :
un `net_liquidity_quote` = `liquidity_added_quote - liquidity_removed_quote`
se calcule à la volée (propriété, jamais un champ stocké séparément).

**V4** : `ModifyLiquidity(id, sender, tickLower, tickUpper, liquidityDelta,
salt)` — un seul event, `liquidityDelta` (int256) déjà signé. **Décision
tranchée : deux compteurs cumulatifs séparés par signe**, jamais un seul
champ signé cumulé (qui perdrait de l'information par annulation, comme un
`net_flow` stocké directement aurait fait pour Brique 2) : `liquidity_
added_raw` (somme des `liquidityDelta` positifs), `liquidity_removed_raw`
(somme des valeurs absolues des `liquidityDelta` négatifs). `net_
liquidity_delta_raw` = `liquidity_added_raw - liquidity_removed_raw`,
calculé à la volée, même pattern que `net_flow_quote`. Unité abstraite (L),
jamais convertie en quote units — un `liquidityDelta == 0` est un no-op
silencieux (aucun compteur touché, pas un bucket "undetermined" séparé :
contrairement à BUY/SELL, ici il n'y a pas d'ambiguïté de SENS, seulement
un montant nul dégénéré).

### Distinction V3 — réduction de position active vs transfert effectif (gravée, jamais assouplir)

Un `Burn` V3 crédite le owner sans transférer immédiatement les tokens (un
`collect()` séparé, non capturé par cette brique, règle le mouvement réel
de trésorerie). Brique 3 mesure UNIQUEMENT "la position/liquidité active a
été réduite" — jamais "des tokens ont effectivement quitté le pool à cet
instant". Cette seconde affirmation, plus forte, ne doit JAMAIS être
déduite implicitement d'un `Burn` dans une future feature ou un futur
signal. Si le règlement réel (`collect()`) devient un jour nécessaire, ce
sera une brique/extension séparée.

### Champs nouveaux (additifs, zéro nouvel appel RPC/websocket)

Sur `_TrackedPool`/`EVMSwapSnapshot` : `liquidity_added_quote`,
`liquidity_removed_quote` (V2/V3, cumulatifs, REAL uniquement pour ces
familles — `None`/`0.0` pour V4), `liquidity_added_raw`, `liquidity_
removed_raw` (V4, cumulatifs, REAL uniquement pour V4). `net_liquidity_
quote` et `net_liquidity_delta_raw` calculés à la volée (properties,
jamais stockés). Côté `onchain_activity_observation.py` : deltas
correspondants (`liquidity_added_quote_delta`, `liquidity_removed_quote_
delta`, `liquidity_added_raw_delta`, `liquidity_removed_raw_delta`), même
doctrine `baseline_reset`/jamais un delta fabriqué que Briques 1/2.

### Nouveaux topics à souscrire (zéro nouvelle connexion, ajoutés à la liste topic0 déjà ouverte dans `_resubscribe()`)

`Mint(address,uint256,uint256)` (V2), `Burn(address,uint256,uint256,
address)` (V2), `Mint(address,address,int24,int24,uint128,uint256,
uint256)` (V3), `Burn(address,int24,int24,uint128,uint256,uint256)` (V3),
`ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)` (V4) — 5
nouveaux `topic0` (keccak256 des signatures), à calculer et vérifier
contre une vraie transaction connue avant de les coder en dur (même
discipline que `_V2_SWAP_TOPIC` en Brique 1).

### Plan de tests obligatoire (avant toute implémentation)

1. V2 Mint → `liquidity_added_quote` += montant côté quote, `swap_count`/
   `buy_count`/`sell_count` inchangés.
2. V2 Burn → `liquidity_removed_quote` += montant côté quote, mêmes
   compteurs inchangés.
3. V3 Mint → idem V2 (même calcul côté quote, `amount` L ignoré pour ce
   champ).
4. V3 Burn → idem.
5. V4 `ModifyLiquidity` positif → `liquidity_added_raw` += `liquidityDelta`.
6. V4 `ModifyLiquidity` négatif → `liquidity_removed_raw` += `abs(liquidityDelta)`.
7. V4 `liquidityDelta == 0` → no-op silencieux, aucun compteur touché.
8. **Garde-fou obligatoire, même raison qu'en Brique 2** : les tests 1-4
   doublés sur `token_is_currency0=True/False` (8 tests effectifs) — une
   inversion silencieuse de ce flag inverserait quel côté est "added"/
   "removed" sans qu'aucun autre test ne le détecte.
9. Invariant de non-contamination : sur une séquence mixte réelle (swaps +
   Mint + Burn entrelacés), `swap_count`/`buy_count`/`sell_count`/
   `buy_volume_quote`/`sell_volume_quote` restent BYTE-IDENTIQUES avec/sans
   ce wiring (non-régression Brique 1/2 explicite).
10. `net_liquidity_quote`/`net_liquidity_delta_raw` calculés (jamais des
    champs stockés qui pourraient diverger).
11. Wiring réel de souscription : les 5 nouveaux `topic0` effectivement
    présents dans `_resubscribe()`'s topics list, pas seulement décodables
    en isolation.
12. `get_snapshot()` expose les nouveaux champs.
13. Séquence bout en bout (test d'intégration, pas seulement unitaire) sur
    un pool réel simulé mêlant les trois familles d'événements.

### Ce que cette brique ne fait PAS

Aucun score, aucun seuil, aucun jugement "bonne"/"mauvaise" liquidité.
Aucune conversion V4 en quote units (ça resterait une unité abstraite tant
qu'aucune feature validée n'en a besoin). Aucun mélange avec BUY/SELL —
trois axes (SWAP, DIRECTION, LIQUIDITY) qui restent lisibles séparément,
jamais combinés au niveau capteur.

### Séquence d'exécution (ordre imposé par l'opérateur)

Documentation/mini-spec (ci-dessus, verrouillée) → tests (plan ci-dessus,
écrits AVANT le code) → implémentation → suite complète → déploiement →
observation → checkpoint, exactement le même protocole que Briques 1/2,
sans élargir le scope.

## Scénario post-capteurs — contrat de destination (29/08, document d'architecture expérimentale, AUCUN CODE, AUCUNE règle de trading)

Écrit maintenant que Briques 1-2 ont suffisamment avancé, pour que chaque
brique restante sache précisément à quoi ses données doivent servir ensuite.
**Ce n'est pas une nouvelle brique à coder** — le jalon concret reste
inchangé : checkpoint Brique 2 -> Brique 3 -> Brique 4 -> Brique 5 ->
backfill -> features -> discovery -> replay. Ce document sert de contrat :
quand les capteurs sont terminés, on sait exactement ce que leurs données
doivent permettre de faire, avant même d'écrire la première ligne de code
du feature engine.

### Séquence complète (remplace/précise le schéma court de la section "Roadmap complète du moteur")

```
1.  CAPTER
2.  NORMALISER / PERSISTER
3.  RECONSTRUIRE L'HISTORIQUE (backfill, Brique 6)
4.  CONSTRUIRE LES FEATURES DYNAMIQUES
5.  DÉTECTER LE RÉGIME (phase de marché)
6.  DISCOVERY A/B/C
7.  REPLAY CAUSAL
8.  TEST OOS (out-of-sample)
9.  SHADOW / PAPER
10. SIMULATION D'EXÉCUTION
11. ENTRÉE
12. GESTION
13. EXHAUSTION / SORTIE
14. POST-MORTEM
     ^ (boucle retour vers DISCOVERY)
```

### Distinction fondamentale — simulation des DÉCISIONS, jamais simulation du MARCHÉ

Point le plus important de ce document : à l'étape 10 (simulation d'exécution),
ARIA simule des achats/ventes, mais **jamais des données**. Le déroulé réel :

```
t0 : prix réel, swaps réels, BUY/SELL réels, traders réels, liquidité réelle
  -> SIGNAL à t0
  -> simulation d'un ACHAT (jamais un vrai ordre)
  -> prix d'entrée = le prix réellement observable à t0, rien d'estimé
  -> on rejoue ensuite le marché RÉEL : t+1s, t+5s, t+10s, t+30s, t+1m, ...
  -> question posée à chaque pas : aurait-elle pu sortir ? à quel prix
     réel ? avec quel slippage estimé ? quel MFE ? quel MAE ? quel PnL
     net ? combien de temps avant exhaustion ?
```

Le marché rejoué est toujours celui qui s'est réellement produit (backfill
ou temps réel) — seule la DÉCISION d'entrer/gérer/sortir est simulée. Un
moteur qui inventerait des trajectoires de marché fictives briserait toute
la chaîne de confiance construite depuis Brique 1 (données brutes jamais
fabriquées) jusqu'ici.

### Deux scénarios opposés à écrire dès maintenant (vocabulaire de référence, pas encore des règles)

**Scénario A — continuation** :

```
pump -> retracement -> buy flow revient -> nouveaux traders ->
higher lows -> liquidité suffisamment saine -> reprise ->
entrée simulée -> continuation -> exhaustion -> sortie
```

**Scénario B — piège** :

```
pump -> distribution -> buy flow ralentit -> retracement persistant ->
nouveaux acheteurs insuffisants -> pression vendeuse ->
liquidité se détériore -> collapse
```

**Le vrai objectif d'apprentissage n'est pas "A a gagné, B a perdu"** — c'est
comprendre POURQUOI le replay causal aurait dû choisir A plutôt que B au
moment de la décision (quelles features, quelle combinaison, quelle
trajectoire les distinguaient déjà à cet instant), jamais se contenter de
constater le résultat final a posteriori. Cohérent avec le dataset A/B/C
(Brique 6) et avec le journal causal déjà posé plus haut (`POURQUOI SIGNAL`,
pas seulement `SIGNAL`).

### Troisième simulation à ajouter — signal tardif (répond à "est-ce encore rentable d'entrer maintenant ?")

Distinct des deux scénarios ci-dessus : mesurer, pour un même token gagnant,
la valeur d'un signal détecté à différents points de sa trajectoire plutôt
que de chercher obsessionnellement le tout premier mouvement :

```
signal à +100%  ->  probabilité de continuation, rendement potentiel
signal à +300%      restant, risque de retracement, temps jusqu'à
signal à +500%      exhaustion, coût d'exécution -- mesurés SÉPARÉMENT
signal à +800%      à chaque point d'entrée, jamais agrégés ensemble
signal à +1200%
```

Directement lié aux métriques de lead-time déjà posées plus haut
(`lead_time_to_move`, `time_from_signal_to_exhaustion`) — ici appliquées
spécifiquement à la question du timing d'entrée tardif, pas seulement à la
détection initiale. Réponse attendue empirique, jamais supposée à l'avance :
un signal statistiquement excellent qui n'apparaît qu'après +500% peut très
bien rester rentable, ou au contraire déjà trop tardif — seul le replay
causal sur le dataset A/B/C tranchera, pas une intuition.

### Ce que ce contrat de destination fige (et ce qu'il ne fige PAS)

**Fige** : le vocabulaire de référence (scénarios A/B, notion de signal
tardif, distinction décision-simulée vs marché-réel), la séquence complète
des 14 étapes, l'exigence que chaque étape journalise le POURQUOI. **Ne fige
PAS** : aucune formule, aucun seuil, aucun poids, aucune feature validée —
tout ça reste à découvrir empiriquement une fois le backfill (Brique 6)
possible, exactement comme le reste de ce document.
