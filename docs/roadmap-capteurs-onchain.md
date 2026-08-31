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
   ($45.77M/24h vol). Base : équivalent à chercher. **Reformulation de
   l'objectif (29/08, précision opérateur)** : ne jamais la poser comme
   "obtenir un prix USD" — la question est "établir une conversion
   quote→USD vérifiable et indépendante des fournisseurs externes". Le
   capteur de prix/activité en quote existe déjà (Briques 1-3) ; l'oracle
   n'apporte QUE la référence USD, jamais une redéfinition du capteur
   existant. Objectif technique inchangé : `reserve_usd` sans dépendre de
   CoinGecko/DexPaprika (cf. incident #271, quota CoinGecko épuisé 26/08,
   bloque toute la collecte liquidité actuelle). **Pas commencée** — attend
   sa propre mini-spec et un GO explicite, même discipline que les briques
   précédentes (jamais deux ouvertes en même temps).
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

## Mini-spec Brique 4 — Oracle WETH/USD on-chain (29/08, DÉPLOYÉE 30/08 — voir `docs/HANDOFF_PIPELINE_MOMENTUM.md` entrée 2026.08.30)

Statut : Briques 1-3 validées en production sur Base ET Robinhood
(checkpoint live fermé 22:33Z, cf. HANDOFF). **Cette mini-spec prépare
uniquement Brique 4** — aucune implémentation avant accord explicite de
l'opérateur. Ancrée dans le vrai code de `evm_swap_ws.py` et `doppler.py`
(relus le 29/08), pas une convention inventée à l'aveugle.

### Reformulation de l'objectif (rappel, gravé par l'opérateur)

Ne jamais poser Brique 4 comme "obtenir un prix USD" — la question est
"établir une conversion quote→USD vérifiable et indépendante des
fournisseurs externes". Le capteur de prix/activité en quote existe déjà
(Briques 1-3) ; l'oracle n'apporte QUE la référence USD, jamais une
redéfinition du capteur existant.

### Point d'ancrage exact dans le code déjà déployé

`EVMSwapWebSocketFeed.get_snapshot()` (`evm_swap_ws.py:822-829`) résout
déjà `price_usd` directement pour un pool `quote_is_stable` (`price_usd =
last_price`), mais retourne explicitement `price_usd = None` pour un pool
`quote_is_weth`/`quote_is_btc`, avec le commentaire du code lui-même :
*"resolved by the caller via doppler.eth_usd_rate() -- no network I/O
here"*. `doppler.eth_usd_rate()`/`btc_usd_rate()` (`doppler.py:213-249`)
sont la SEULE conversion USD existante aujourd'hui pour ce cas, et
dépendent TOUTES LES DEUX de CoinGecko (`coingecko_client.get_simple_price`)
— exactement la dépendance externe que l'incident #271 (quota CoinGecko
épuisé depuis le 26/08) a mise en évidence comme point de blocage. Brique
4 comble ce trou précis, sans toucher au reste de `doppler.py`.

### Mécanisme proposé (réutilisation stricte de l'existant, zéro nouveau capteur)

```
pool de référence WETH/stable (déjà lisible par Brique 1-3, quote_is_stable=True)
        v
son propre get_snapshot().price_usd DÉJÀ résolu nativement (ligne 824-825)
        v
onchain_eth_usd_rate(chain) -> lit ce snapshot, expose le taux
        v
caller (ex. _handle_sync pour un pool quote_is_weth) l'utilise à la place
de doppler.eth_usd_rate()
```

Aucun nouveau mécanisme de lecture on-chain : le taux ETH/USD est déjà
calculable par le code existant, à condition qu'un pool WETH/stable à
forte liquidité soit ajouté à la liste des pools **suivis en permanence**
par `EVMSwapWebSocketFeed` (via `add_pool`, déjà existant, jamais
réimplémenté) — actuellement les pools suivis sont ceux découverts
dynamiquement par `onchain_pool_discovery.py`, jamais des pools de
référence fixes.

### Pools de référence candidats (un seul confirmé par clic ce jour, l'autre à reconfirmer)

- **Base** : `0x6c561b446416e1a00e8e93e221854d6ea4171372` (WETH/USDC,
  Uniswap V3, $116M liquidité) — **vérifié par clic-through DexScreener le
  29/08** (même pool déjà utilisé pour le test de preuve du décodeur
  Brique 3, cf. HANDOFF). `token0=WETH`, `token1=USDC`.
- **Robinhood** : pool WETH/USDG déjà mentionné dans CLAUDE.md ($45.77M/24h
  vol) — **adresse exacte à reconfirmer par clic-through avant toute
  implémentation, jamais devinée** (navigateur indisponible au moment de
  cette mini-spec).

### Fail-closed / staleness (même doctrine que Briques 1-3)

`onchain_eth_usd_rate(chain)` retourne `None` si : le pool de référence
n'est pas encore suivi, `get_snapshot()` retourne `available=False`, ou le
snapshot est trop périmé (`stale_seconds` au-delà d'un seuil à définir,
cohérent avec le reste du dôme) — jamais un taux fabriqué ou périmé
silencieusement réutilisé. Le caller (ex. `_handle_sync`) garde le même
comportement fail-open déjà en place : `reserve_usd`/`price_usd` restent
`None` si le taux n'est pas disponible, jamais une conversion approximative.

### Ce que Brique 4 ne fait PAS

Ne remplace pas `doppler.eth_usd_rate()`/`btc_usd_rate()` partout dans le
dôme — scope strictement limité au besoin direct des capteurs on-chain
(Briques 1-3/6, bloqués par #271 pour la collecte de liquidité). Migrer
d'autres consommateurs existants de `doppler.eth_usd_rate()` vers ce
nouveau mécanisme est un chantier séparé, hors scope. Aucun score, aucun
seuil, aucune conversion V4/liquidité abstraite touchée par cette brique.
Le cas BTC/cbBTC reste hors scope (aucun pool cbBTC-quoté identifié comme
prioritaire à ce stade).

### Plan de tests obligatoire (avant toute implémentation)

1. Pool de référence `quote_is_stable=True` déjà suivi, snapshot
   disponible -> `onchain_eth_usd_rate` retourne le `price_usd` exact du
   pool.
2. Pool de référence pas encore suivi -> `None`, jamais une exception.
3. Snapshot périmé (au-delà du seuil de staleness) -> `None`.
4. Un pool `quote_is_weth` consommant ce taux calcule un `reserve_usd`
   cohérent (`quote_reserve_raw * onchain_eth_usd_rate`), comparé à une
   valeur de référence externe (DexScreener) à titre de sanity check
   uniquement, jamais de vérité d'entraînement (même doctrine que Brique 6).
5. Non-régression : `doppler.eth_usd_rate()` et son usage existant
   restent inchangés — Brique 4 AJOUTE une alternative, ne modifie rien
   dans `doppler.py`.
6. Wiring réel : le pool de référence effectivement présent dans la liste
   des pools suivis au démarrage du process, pas seulement testable en
   isolation.

### Séquence d'exécution (même protocole que Briques 1-3)

Documentation/mini-spec (ce document) → GO explicite de l'opérateur →
tests (plan ci-dessus, écrits AVANT le code) → implémentation → suite
complète → déploiement → observation → checkpoint. Aucune ligne de code
avant le GO.

### Découverte empirique post-déploiement (30/08) — le seuil de staleness peut être plus strict que le rythme naturel du pool de référence

En vérifiant Brique 5 en production, un premier test (fenêtre 30s) a
donné l'impression d'un échec de souscription websocket sur le pool de
référence Base -- diagnostic infirmé par un second test avec une fenêtre
de 150s : un vrai swap est arrivé à t=40s (`price_usd=2459.22`, tx/bloc
réels confirmés), pas de bug de souscription/dispatch. Une lecture RPC
directe (`eth_getLogs`, hors websocket) a confirmé 15 vrais swaps sur ce
pool $116M dans les ~10 minutes précédentes, soit un espacement moyen
d'environ 40s -- **du même ordre de grandeur que `_ETH_USD_RATE_MAX_STALE_SECONDS
= 30.0`**. Conséquence probable en usage normal (pas un bug, le
fail-closed fonctionne exactement comme prévu) : `onchain_eth_usd_rate()`
va retourner `None` plus souvent que souhaitable, simplement parce que le
pool de référence a des creux d'activité naturels proches du seuil de
staleness lui-même. **Sujet de calibration ouvert, jamais tranché ici** --
la valeur `30.0` reste une constante de sécurité déjà validée par
l'opérateur dans cette mini-spec, à ne modifier qu'avec un GO explicite
distinct, jamais silencieusement en marge d'une autre brique.

## Mini-spec Brique 5 — Persistance complète (30/08, DÉPLOYÉE ET CHECKPOINT FERMÉ — voir `docs/HANDOFF_PIPELINE_MOMENTUM.md` entrée 2026.08.30)

Statut : Briques 1-4 closes/déployées sur Base ET Robinhood (Brique 4
vérifiée en production le 30/08, cf. HANDOFF). **Cette mini-spec prépare
uniquement Brique 5** — aucune implémentation avant accord explicite de
l'opérateur, même discipline que les briques précédentes.

### Reformulation de l'objectif (cadrage opérateur, 30/08)

Faire de `onchain_activity_observation_log` **la représentation temporelle
complète** des primitives déjà validées (Briques 1-4), sans introduire de
nouvelle sémantique ni de nouveau capteur. Concrètement : traiter
explicitement `price` (prix instantané), `liquidity_level` (niveau de
liquidité instantané, distinct des deltas déjà stockés), les deltas
Brique 2/3 (déjà couverts, inchangés), la valeur USD issue de Brique 4, et
les règles `baseline_reset`/restart existantes.

### Point d'ancrage exact dans le code déjà déployé

`onchain_activity_observation.py` (lu intégralement le 30/08) capture déjà
trois axes -- A (activité : `swap_count`/`cumulative_volume_quote`/
`distinct_traders_count`), B (direction : `buy_count`/`sell_count`/
volumes), C (liquidité en MOUVEMENT : `liquidity_added_quote`/
`liquidity_removed_quote`/leurs équivalents `_raw`) -- chacun avec ses
deltas restart-safe (`_last_observed`, `baseline_reset`). **Ce qui manque,
confirmé en relisant `EVMSwapSnapshot` (`evm_swap_ws.py:269-385`) champ par
champ** : la table n'a AUCUNE colonne pour un état INSTANTANÉ -- ni le prix
(`price_quote`/`price_usd`), ni le niveau de liquidité ABSOLU résident
dans le pool à cet instant (`reserve_usd`/`raw_liquidity`/
`quote_reserve_raw`). Elle ne répond aujourd'hui qu'à "qu'est-ce qui s'est
passé depuis la dernière observation", jamais à "où en était le marché à
cet instant T" -- exactement le trou que Brique 5 doit combler, sans
toucher aux trois axes déjà câblés.

Le point d'appel réel (`onchain_pool_discovery.py:425-449`, à étendre, pas
réécrire) construit déjà chaque appel à `record_observation()` à partir du
MÊME `snapshot` (`EVMSwapWebSocketFeed.get_snapshot()`) qui expose déjà
`price_quote`/`price_usd`/`reserve_usd`/`raw_liquidity`/
`quote_reserve_raw` -- zéro nouvel appel réseau, zéro nouveau capteur,
strictement de la lecture de champs déjà résolus par les Briques 1-4.

### Distinction cruciale -- valeurs cumulatives/delta (existant) vs valeurs instantanées/snapshot (nouveau)

Les colonnes existantes (`swap_count`, `cumulative_volume_quote`,
`buy_volume_quote`, `liquidity_added_quote`, etc.) sont des **cumulatifs
depuis `add_pool()`**, avec un delta calculé contre la dernière
observation via `_last_observed` -- ce mécanisme reste inchangé, Brique 5
n'y touche pas.

`price_quote`/`price_usd`/`reserve_usd`/`raw_liquidity`/
`quote_reserve_raw` sont d'une nature DIFFÉRENTE : ce sont des valeurs
**instantanées** (l'état du pool AU MOMENT de l'observation, pas une somme
depuis le démarrage du suivi). Le concept de "delta contre la dernière
observation" ne s'applique donc PAS à ces champs -- il n'y a rien à
comparer, chaque observation enregistre l'état réel à cet instant, point.
**Ne jamais réutiliser le mécanisme `_last_observed`/`baseline_reset`
existant pour ces colonnes** -- ce serait une fausse delta sur une donnée
qui n'en a pas besoin, et une confusion pour toute lecture future de la
table. `baseline_reset` reste un signal valable pour les colonnes
cumulatives existantes uniquement.

### Valeur USD issue de Brique 4 (le second manque explicite du cadrage)

Deux informations distinctes à distinguer, ne jamais les fusionner en une
seule colonne :

1. **`reserve_usd` du pool observé lui-même** -- déjà exposé par
   `get_snapshot()`, résolu nativement pour `quote_is_stable`, et
   maintenant aussi pour `quote_is_weth` via `_handle_sync` +
   `onchain_eth_usd_rate()` (Brique 4, `evm_swap_ws.py`, commit
   `649ba907`). Une simple lecture du snapshot, comme tous les autres
   champs de cette section.
2. **Le taux `onchain_eth_usd_rate(chain)` utilisé à ce moment précis** --
   une donnée DIFFÉRENTE : le taux de référence lui-même, indépendant du
   pool observé. Utile pour la traçabilité/le debug (comprendre POURQUOI
   un `reserve_usd` a une certaine valeur, ou pourquoi il est resté `None`
   un instant donné si le pool de référence était stale/non suivi) --
   candidat à une colonne séparée (`eth_usd_rate_at_observation` ou
   équivalent), à confirmer avec l'opérateur avant implémentation : ce
   n'est pas strictement une primitive de POOL comme le reste de la
   table, c'est une donnée de CONTEXTE partagée par tous les pools
   `quote_is_weth` de la même chaîne au même instant.

### Nouvelles colonnes proposées (additives, migration à chaud comme Briques 2/3)

```
price_quote REAL          -- snapshot.price_quote, instantané, jamais de delta
price_usd REAL             -- snapshot.price_usd, instantané, jamais de delta
reserve_usd REAL           -- snapshot.reserve_usd, instantané, jamais de delta
raw_liquidity REAL         -- snapshot.raw_liquidity (v3/v4), instantané, jamais de delta
quote_reserve_raw REAL     -- snapshot.quote_reserve_raw (v2), instantané, jamais de delta
eth_usd_rate_at_observation REAL  -- CANDIDAT, à confirmer (cf. section ci-dessus)
```

Toutes `NULL` par défaut, jamais un `0.0` fabriqué -- même doctrine
"`None` reste `None`" que le reste du module (docstring déjà en tête de
fichier, inchangée).

### Fail-closed / `None` reste `None` (même doctrine que Briques 1-4)

`available=False` (déjà le branchement existant du module) doit continuer
à enregistrer TOUTES les colonnes -- anciennes et nouvelles -- comme
`NULL`. Un `price_usd`/`reserve_usd` à `None` dans le snapshot (pool
`quote_is_weth` dont le taux Brique 4 n'a pas résolu) doit rester `None`
dans la table, jamais une valeur de repli calculée localement dans ce
module -- l'observation reste strictement un miroir du snapshot, jamais
une seconde source de vérité.

### Ce que Brique 5 ne fait PAS

Aucun score, aucun seuil, aucune feature dérivée (ex. volatilité,
acceleration de prix) calculée ou stockée ici -- strictement la même
doctrine "log-only, best-effort" que les trois axes déjà en place. Ne
touche pas au format des colonnes existantes ni à leur mécanisme de
delta. Ne migre pas `discovery_liquidity_observation.py` (axe liquidité
DEX-fournisseur séparé, hors scope). N'introduit aucune nouvelle connexion
réseau -- 100% des champs proposés existent déjà sur `EVMSwapSnapshot`.

### Plan de tests obligatoire (avant toute implémentation)

1. Snapshot disponible avec `price_quote`/`price_usd`/`reserve_usd`
   résolus (pool `quote_is_stable`) -> les trois colonnes enregistrées
   exactement, aucun delta calculé pour elles.
2. Snapshot disponible mais `price_usd`/`reserve_usd` à `None` (pool
   `quote_is_weth`, taux Brique 4 indisponible) -> colonnes `NULL` dans la
   table, jamais une valeur de repli.
3. `available=False` -> toutes les colonnes (anciennes ET nouvelles)
   `NULL`, comportement inchangé.
4. Deux observations consécutives du même pool avec un prix différent ->
   les deux valeurs enregistrées telles quelles, AUCUN champ delta associé
   à `price_quote`/`price_usd`/`reserve_usd`/`raw_liquidity`/
   `quote_reserve_raw` n'existe dans le schéma (vérifier qu'aucun a été
   ajouté par erreur).
5. Non-régression : les colonnes/deltas existants (axes A/B/C) restent
   strictement inchangés en valeur et en comportement restart
   (`baseline_reset`) pour une séquence d'observations identique à avant
   Brique 5.
6. Migration à chaud : une base déjà en production avec l'ancien schéma
   (sans les nouvelles colonnes) doit passer par `db_migrations.ensure_columns`
   sans perte des lignes existantes, même pattern que Briques 2/3.

### Séquence d'exécution (même protocole que Briques 1-4)

Documentation/mini-spec (ce document) → GO explicite de l'opérateur,
notamment sur la colonne candidate `eth_usd_rate_at_observation` → tests
(plan ci-dessus, écrits AVANT le code) → implémentation → suite complète →
déploiement → observation → checkpoint. Aucune ligne de code avant le GO.

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

### Format exact d'une observation — contrat de ligne pour le feature engine (29/08, PAS de code)

Complète le scénario ci-dessus par le SCHÉMA concret que chaque ligne
d'observation devra porter avant d'attaquer l'étape "CONSTRUIRE LES
FEATURES DYNAMIQUES" — une primitive brute par colonne, jamais une feature
déjà calculée :

```
timestamp
price
swap_count_delta
buy_count_delta
sell_count_delta
buy_volume_delta
sell_volume_delta
traders_delta
liquidity_added_delta
liquidity_removed_delta
liquidity_level
```

**État réel de chaque colonne aujourd'hui (vérifié dans le code, jamais
supposé)** : `timestamp` (`observed_at`), `swap_count_delta`
(`swaps_delta`), `buy_count_delta`/`sell_count_delta`/`buy_volume_
delta`/`sell_volume_delta`, `traders_delta`, `liquidity_added_delta`/
`liquidity_removed_delta` (`liquidity_added_quote_delta`/`liquidity_
removed_quote_delta` ou leur variante `_raw` pour v4) sont TOUS déjà
persistés cycle par cycle dans `onchain_activity_observation_log`
(Briques 1/2/3). **Deux colonnes du schéma cible n'ont PAS encore de
persistance continue, gap identifié maintenant, pas encore comblé** :
- `price` : déjà calculé à chaque événement (`EVMSwapSnapshot.price_quote`/
  `price_usd`), mais `onchain_activity_observation.py` ne l'enregistre pas
  aujourd'hui — seul `swap_count`/volume/traders/buy-sell/liquidity le sont.
- `liquidity_level` (le niveau ABSOLU de profondeur présente, distinct des
  deltas ajout/retrait ci-dessus) : correspond à `reserve_usd` (v2, exact)
  ou `raw_liquidity` (v3/v4, abstrait) sur `EVMSwapSnapshot` — capturé
  aujourd'hui par `discovery_liquidity_observation.py` mais seulement au
  moment de la découverte initiale, pas en continu à chaque cycle comme le
  reste de ce contrat l'exige.

**Action explicitement PAS prise maintenant** : combler ce gap serait de la
persistance/instrumentation, pas de la conception — hors du "sans coder"
demandé pour cette période d'observation. À traiter soit comme un
prolongement naturel de la Brique 5 (persistance complète) déjà posée plus
haut, soit comme une petite extension additive de `record_observation()`
une fois Brique 3 elle-même validée — jamais avant, pour ne pas fusionner
deux briques dans un même changement.

### Directions futures une fois ce contrat de ligne complet (candidats, PAS de formule)

```
-> accélérations (variation du delta dans le temps, pas le delta seul)
-> persistance (le déséquilibre dure-t-il, ou s'épuise-t-il déjà)
-> retracement duration (combien de temps le repli dure avant reprise/mort)
-> higher lows (structure de la zone d'accumulation, déjà noté plus haut)
-> flow/liquidity interaction (le principe consolidé posé plus haut)
-> régime (position dans la trajectoire, déjà posé dans la roadmap complète)
-> signal (uniquement une fois tout ce qui précède validé empiriquement)
```

Chaque flèche reste une DIRECTION de recherche, jamais une formule ou un
seuil arrêté — cohérent avec le principe déjà gravé : les capteurs
produisent des primitives brutes, le feature engine découvre ensuite les
combinaisons, jamais l'inverse.

## Hypothèse de recherche post-backfill — attention sociale comme variable latente on-chain (29/08, formulation opérateur, AUCUN CODE)

**Reformulation de l'objectif** : ARIA ne doit pas chercher à prédire le
social ni à trouver "où l'attention est la plus forte". Elle doit mesurer
QUAND l'attention sociale devient économiquement visible on-chain, puis si
le déséquilibre qui en résulte est encore exploitable maintenant, avant
qu'il ne soit pleinement absorbé par le prix. Formulation qui s'aligne
directement sur l'objectif déjà gravé plus haut (auto-renforcement d'un
déséquilibre PvP, edge restant, exhaustion) — cette section l'étend, ne le
remplace pas.

**Chaîne causale visée** : attention sociale -> FOMO/découverte/imitation
-> nouveaux entrants -> swaps↑/buy flow↑/fréquence↑/taille des swaps↑ ->
accélération on-chain -> ARIA détecte -> le marché a-t-il encore de
l'edge ? -> exhaustion.

**Le test n'est pas trivial** : pas "volume élevé = attention élevée", mais
la TRANSITION `activité normale -> activité qui accélère -> nouveaux
wallets qui arrivent -> buy flow qui domine -> taille/fréquence des swaps
augmente -> prix commence à réagir`. C'est cette propagation dans le
comportement des participants qui serait potentiellement exploitable, pas
un niveau statique.

### Attention comme variable latente, jamais dépendante d'une plateforme

Au lieu de dépendre de X / Fomo / Telegram / Farcaster / Discord (donc de
leur disponibilité, de leurs quotas, de leur API), ARIA estimerait une
quantité d'attention injectée dans un marché à partir de signaux
observables on-chain déjà capturés ou capturables par les Briques
1-3/6 : `new_traders_rate`, `swap_frequency`, `buy_flow`, `flow_
acceleration`, `median_swap_size`, `unique_traders`,
`buyer/seller_concentration`, `liquidity`, `liquidity_trend`, `price_
acceleration`. But recherché : établir si ce proxy on-chain détecte
l'arrivée de l'attention suffisamment tôt pour rendre une dépendance
sociale externe secondaire — plus robuste qu'un scraper X/Fomo dont la
disponibilité n'est jamais garantie.

**Deux niveaux à garder strictement séparés, ne pas les fusionner
maintenant** :
- **Niveau 1 — on-chain** : ce que l'architecture déjà en construction
  (Briques 1-6) produit.
- **Niveau 2 — social** : utilisé PLUS TARD uniquement comme variable
  externe de VALIDATION (le lead/lag mesuré ci-dessous), jamais branché en
  amont d'un signal maintenant.

### Attention ≠ prix — trois cas à distinguer empiriquement (dataset A/B/C, Brique 6)

```
Cas 1 — attention ↑ / prix ↑ déjà réactif
  new traders ↑, buy flow ↑, swaps ↑, prix ↑
  -> mouvement déjà reconnu par le marché.

Cas 2 — attention ↑↑ / prix encore relativement plat
  new traders ↑↑, buy flow ↑↑, swap frequency ↑↑, prix faible réaction
  -> potentiellement le cas le plus intéressant : la pression n'est pas
     encore absorbée par le prix.

Cas 3 — attention ↑ mais flow qui se dégrade
  traders ↑, volume ↑, mais buy flow ↓, sell flow ↑, higher lows cassés
  -> l'attention existe encore mais le déséquilibre est en train de
     disparaître -- la composante "exhaustion" déjà posée plus haut.
```

### Expérience clé (une fois une trace sociale temporelle disponible pour certains tokens)

Aligner, pour les tokens où une trace sociale temporelle est récupérable,
les cinq séries suivantes sur une fenêtre `T-60m / T-30m / T-20m / T-10m /
T-5m / T0 / T+5m / T+10m / T+30m` : attention sociale, participation
on-chain, flow, liquidité, prix — puis chercher QUI bouge en premier.
Question probablement la plus importante de cette direction de recherche :
un lead structurel (`social -> +90s -> new traders -> +20s -> buy flow
acceleration -> +15s -> price acceleration`) rendrait le social exploitable
comme signal précoce ; un lag (`price -> traders -> social`) signifierait
que le social ne fait que confirmer un mouvement déjà démarré on-chain,
jamais le prédire. Reste une question expérimentale ouverte, pas une
conclusion — à trancher uniquement sur données réelles une fois Brique 6
possible.

### Priorité de recherche par chaîne pour le PvP meme trading (décision opérateur, révisée en cours de discussion)

```
1. ROBINHOOD  <- marché prioritaire
2. SOLANA     <- second laboratoire (généralité vs spécificité Robinhood)
3. BASE       <- infrastructure / contrôle / opportuniste, jamais un effort
                 dédié chasse-memecoin (les memes y sont rares)
```

Raison de la priorité Robinhood, pas seulement "plus de memes" : c'est déjà
la chaîne du pipeline EVM en cours de validation (Briques 1-3) — tester
l'hypothèse n'exige donc aucune nouvelle infrastructure parallèle. Solana
reste indispensable ensuite pour vérifier si le phénomène est général au
meme trading ou une propriété spécifique à Robinhood — distinction
scientifique importante, pas cosmétique. Base reste utile pour valider
l'architecture EVM générique, développer les primitives génériques, et
servir de contrôle contre l'hypothèse "notre signal ne marche que sur une
chaîne" — mais n'attire pas d'effort de recherche PvP dédié.

**Conséquence sur Brique 6 (backfill)** : une fois Brique 3 fermée sur les
deux chaînes EVM, Robinhood devrait probablement être le premier dataset
historique construit sérieusement — pas parce qu'on sait déjà qu'il est
meilleur, mais parce que c'est aujourd'hui le meilleur terrain pour tester
cette hypothèse avec l'infrastructure déjà construite.

**Noyau de features potentiellement commun, paramètres potentiellement
distincts par chaîne** : `participation`, `flow`, `accélération`,
`liquidité`, `concentration`, `exhaustion` comme noyau conceptuel partagé —
mais la microstructure et le comportement des participants peuvent différer
suffisamment entre Solana/Robinhood/Base pour que les distributions de
features et les seuils (jamais fixés a priori) diffèrent par chaîne. À
vérifier empiriquement, jamais supposé transférable sans preuve.

### Garde-fou explicite (répété par l'opérateur, absolu)

**Ne pas modifier la séquence des briques maintenant.** Cette section est
une priorité de RECHERCHE qui s'applique APRÈS la fermeture des capteurs
(Briques 1-5) et la construction du backfill (Brique 6) — jamais un
chantier actif, jamais du code, jamais une nouvelle "Brique Social". Le
backfill permettra de mesurer objectivement quelle chaîne produit
réellement le type de trajectoires recherchées, avant d'investir dans le
feature engine qui teste cette hypothèse.

## Extension — cohortes narratives et pistes de recherche complémentaires (29/08, formulation opérateur, AUCUN CODE)

Déclenché par un cas concret (un token "$AI" couplé à un token NVDA
tokenisé sur Robinhood, vault + burn/lock, position réelle d'un tiers
passée de ~3 651$ à une position bien plus importante) et par la question
"pourquoi CELUI-LÀ et pas un des ~15-20 clones nés au même moment avec le
même narratif (AI + chien + NVDA + Robinhood) ?". Complète, ne remplace
pas, la section précédente et le dataset A/B/C déjà posé plus haut.

### Nouvelle unité d'analyse : la cohorte narrative

Distincte du dataset A/B/C (qui compare des tokens indépendants) : un
groupe de tokens nés à peu près au même moment, avec le même narratif/meme,
traité comme UNE unité d'analyse. Question posée : parmi 15-20 clones
qui partagent la même recette au lancement, quelle variable commence à
diverger chez le futur leader dans les 5/10/20/30/60 premières minutes —
avant que l'écart ne soit visible dans le prix ? Feature candidate propre
à ce protocole (pas encore dans la liste précédente) : part du flux total
de la cohorte captée par chaque token dans le temps (`cohort_flow_share`),
plutôt qu'un volume absolu — un token peut devenir "le" narratif en
prenant une part croissante de l'attention totale disponible sur sa
cohorte, pas seulement en ayant un volume élevé dans l'absolu.

### Quatre pistes de recherche complémentaires, pas encore couvertes explicitement plus haut

1. **Renforcement du trader, pas seulement le token.** Pour une position
   gagnante réelle (plusieurs renforcements successifs), reconstruire
   l'état on-chain disponible dans les secondes précédant CHAQUE
   renforcement (pas seulement l'achat initial) — objectif : extraire un
   pattern de comportement des bons traders sans les copier, en isolant ce
   qui était objectivement observable à chaque décision. **Premier candidat
   trader concret banqué (29/08)** : le trader Fomo `gkisokay`, deux cas
   observés le même jour — $AI (13 achats étalés du 28/07 au 18/08 en zone
   de range $1.3M-$4.1M MC, vente partielle le 21/08 à $25.1M MC, +3537%)
   et $MEUGLEMENT/MOO (7 trades le 26/08 en zone $230-245K MC, position
   +737% observée le 29/08) — les deux avec une thèse publique écrite
   AVANT la confirmation du marché (narratif "board seat"/vault pour $AI,
   spéculation sur une promotion discrète pour $MOO), pas après coup.
   Wallet/pseudo à identifier par adresse on-chain (jamais par pseudo seul)
   avant tout usage réel en backfill.
2. **Anomalie relative au marché entier comme baseline dynamique.** Plutôt
   qu'un seuil absolu par variable (deviné à l'avance), comparer un token
   à la distribution de TOUS les tokens actifs suivis sur la même chaîne
   au même instant (ex. écart-type par rapport à la cohorte active du
   moment) — la question devient "ce token est-il anormalement différent
   du marché maintenant ?" plutôt que "dépasse-t-il un chiffre fixe ?".
3. **Faux leaders / concentration.** Volume et traders impressionnants
   mais concentrés sur une poignée de wallets = signal non sain — précise
   le Groupe C déjà posé (contrôles neutres) avec un critère de
   concentration explicite, pas un nouveau groupe.
4. **Absorption de la pression vendeuse.** Combien de sell flow le marché
   encaisse-t-il avant que le prix ne casse et que la liquidité ne
   s'érode — variante du principe déjà gravé (interaction flow/
   participation/liquidité), appliquée spécifiquement au côté vendeur.

### Raffinement majeur — "seconde couche" réflexive autour du token central (29/08)

Observation sociale en direct (compte X `@gkisokay`, activité du 29/08) qui
précise le concept de cohorte narrative : ce n'est pas seulement plusieurs
tokens INDÉPENDANTS qui se disputent l'attention d'un même narratif — un
système de tokens SATELLITES peut se former AUTOUR d'un token central déjà
établi, avec une relation de dépendance mutuelle explicite :

```
NVDA (action tokenisée sous-jacente)
  ↑
 AI  (token central, déjà établi, "centerpiece")
  ↑
AGI / SPACEHOOD / CLIPPY / MOO / ... (tokens satellites/"pairs")
```

Boucle réflexive décrite (par des participants du marché eux-mêmes, à
vérifier empiriquement sur le on-chain, jamais supposée) : un satellite qui
accélère (ex. AGI cassant son ATH) génère des achats sur le token central
(AI) par les détenteurs du satellite cherchant une exposition indirecte
plus établie -> AI monte -> l'attention nouvelle rejaillit sur le
satellite -> nouveaux satellites créés. Distinct du concept de "part de
flux de cohorte" déjà posé plus haut (`cohort_flow_share`) : ici la
relation n'est pas compétitive (qui gagne la cohorte) mais POTENTIELLEMENT
COOPÉRATIVE/RÉFLEXIVE entre un token central et ses satellites. Si
confirmé sur données réelles, une feature candidate serait une mesure de
CORRÉLATION DE FLOW à courte fenêtre entre un token central identifié et
ses satellites narratifs, distincte d'une simple corrélation de prix.

**Vigilance adresse (29/08)** : une adresse candidate transmise pour $AI
(`0x427d...3dba`) NE correspond PAS au contrat vérifié directement sur le
site officiel (`artificialinu.com`, qui affiche `0x2e8c31...111e18`,
identique à l'adresse déjà utilisée dans ce document) — écartée, jamais
intégrée. Rappel de la discipline déjà gravée : toujours vérifier par
clic/source primaire, jamais adopter une adresse transmise sans
recoupement, même informellement.

### Décision méthodologique actée (confirme, n'ajoute pas de nouvelle règle)

Reproduire ces hypothèses sur des trajectoires historiques DÉJÀ CONNUES
(via Brique 6) plutôt qu'attendre qu'un nouveau cas se produise en temps
réel — confirme la priorité déjà donnée au backfill et la discipline
anti-look-ahead déjà gravées plus haut, aucune règle nouvelle. Un
protocole qui échoue sur l'historique connu économise du temps et du
capital réel avant même d'être tenté en shadow/paper.

### Candidat de cohorte vérifié (29/08, adresses confirmées via DexScreener)

Contexte de marché confirmé (WebSearch, presse crypto) : depuis mi-juillet
2026, les launchpads Bankr/long.xyz permettent de créer des memecoins
adossés à la liquidité de >90 tickers d'actions tokenisées sur Robinhood
Chain (NVDA, AAPL, TSLA, SPY, TSM, MU, ...). Ce n'est PAS un cluster de
15-20 clones nés en un seul jour (hypothèse de départ infirmée) mais un
FLUX CONTINU de tels memecoins depuis cette date, sur lequel $AI a émergé
tôt et gardé une domination écrasante. Chaque ligne = contrat vérifié par
clic direct (jamais deviné) :

| Token | Contrat | Pool créé | Mkt Cap | Liquidité |
|---|---|---|---|---|
| AI (Artificial Inu) | `0x2e8c31162b855a2ffa90f6f8634643ad6f111e18` | 1mo15d | $123.4M | $3.8M |
| AIM (AI Man, NVDA-paired) | `0x621...1e18` (pool `0x334...3aff`) | 1mo14h | $19K | $19K |
| REALSTONK (NVDA-paired) | `0x237...8ba3` (pool `0xe49...8d73`) | 1mo9d | $64K | $45K |
| Aibo the robot (MU-paired) | `0x338...1e18` (pool `0x67a...26fb`) | 1mo4d | $21K | $21K |
| Robinhood AI (CORTEX/WETH) | `0x268...a07c` (pool `0x298...6751`) | 1mo28d | $19K | $16K |
| microduck (NVDA-paired) | `0xD5F...E725` | 2d10h | $14.6M | $334K |

**Le candidat le plus pertinent pour la question de recherche** : AIM,
né à ~1 jour d'écart de $AI, même quote token (NVDA), même thème IA —
écart de mkt cap de x6500 malgré un timing de lancement quasi identique.
Bien plus parlant que REALSTONK (6 jours d'écart) pour isoler ce qui
divergeait dans les toutes premières heures. **Adresses tronquées
ci-dessus à compléter (clic direct, jamais deviné) avant tout usage réel
en backfill** — ce tableau reste un candidat banqué, pas encore un
dataset prêt à l'emploi.

## Extension — LP dynamique piloté par le régime (30/08, formulation opérateur, AUCUN CODE)

**Hors des briques 1-6, jamais une brique à intercaler.** Piste de
recherche banquée pour APRÈS le dataset historique (Brique 6), à traiter
comme une branche parallèle distincte du moteur de trading, jamais
mélangée aux garde-fous/paramètres de stratégie réels.

### Le concept

Sur Uniswap V3/V4, la liquidité concentrée ne se déplace jamais
automatiquement avec le prix — une position sort de sa range et cesse de
gagner des frais tant qu'elle n'est pas repositionnée manuellement (ou
par un tiers). Le concept exploré ici : un LP dynamique dont la range se
déplace selon un état du marché mesuré, pas selon une règle fixe choisie
une fois pour toutes.

```
prix monte
   ↓
range actuelle approche de sa borne haute
   ↓
retirer/repositionner
   ↓
nouvelle range centrée plus haut
   ↓
continuer à capter les frais
```

### Pourquoi c'est pertinent pour ARIA spécifiquement

Les Briques 1-5 mesurent déjà exactement ce dont un tel pilotage aurait
besoin : prix instantané, buy/sell flow, participation (traders
distincts), liquidité et son évolution. Un LP piloté par le régime
consommerait les MÊMES primitives que le moteur de trading, sans capteur
supplémentaire :

```
                     MARCHÉ
                       │
          ┌────────────┼────────────┐
          │            │            │
       prix↑        flow↑↑       traders↑
          │            │            │
          └────────────┼────────────┘
                       ↓
              état du marché
                       ↓
             position LP active
                       ↓
       ┌───────────────┴───────────────┐
       │                               │
   range trop basse               range trop haute
       │                               │
   déplacer ↑                      déplacer ↓
```

### La vraie difficulté économique

Déplacer une range coûte réellement (remove liquidity + claim fees +
swap éventuel de rééquilibrage + add liquidity + gas) — repositionner à
chaque mouvement de prix serait probablement destructeur de valeur. La
question de recherche n'est donc jamais "faut-il suivre le prix" mais :
**à partir de quel déplacement de prix attendu et de quel niveau de frais
supplémentaires le repositionnement devient-il rentable ?** — rejoint
directement la notion de lead time / edge restant déjà présente dans la
roadmap du moteur plus haut.

### Ce qu'il faudrait tester historiquement (une fois Brique 6 dispo), jamais codé maintenant

LP statique vs LP à range fixe vs LP repositionné périodiquement vs LP
repositionné seulement au changement de régime détecté — comparés sur :

```
revenus de fees
− impermanent loss
− pertes liées aux mouvements hors-range
− coût des repositionnements
− gas
= rendement net
```

Piste la plus intéressante à l'intérieur de cette recherche : un LP qui
accepte volontairement de ne PAS être toujours actif — ne pas poursuivre
un pump violent, laisser la liquidité sortir progressivement, attendre
une consolidation/higher-lows/flow durable, puis recentrer. Le problème
devient alors quasiment celui du trading lui-même : détecter la phase de
marché et placer le capital là où le flux futur est probablement le plus
intéressant — avec une divergence intéressante : un token peut être un
mauvais achat spéculatif tout en étant un excellent marché pour un LP
(volatilité + volume + prix qui oscille dans une zone), et inversement.

### Point de vigilance — la concentration extrême n'est pas automatiquement supérieure

Une liquidité très concentrée en un point capte plus de frais par dollar
de capital, mais sort aussi plus vite de sa range et accumule fortement
un seul actif dès que le prix bouge. Le vrai problème de recherche :

```
CAPITAL × DENSITÉ DE LIQUIDITÉ × VOLUME FUTUR × PROBABILITÉ DE RESTER DANS LA RANGE
  − impermanent loss − coût de repositionnement
```

Une vraie question de recherche empirique, jamais une intuition à coder
directement.

### Où ça s'accroche à la roadmap

```
Briques 1-6
     ↓
dataset historique
     ├── branche A : moteur de trading
     └── branche B : LP dynamique
```

La branche B réutiliserait les mêmes primitives de Brique 5 (prix
instantané + liquidité + flow + participation) pour tester, sur
l'historique reconstruit, si une position concentrée dynamiquement
repositionnée bat réellement une position statique et le simple hold —
jamais assumé, toujours à vérifier empiriquement avant tout code.

## Extension — moteur de scénarios conditionnels / capacité d'extraction réelle (30/08, formulation opérateur, AUCUN CODE)

**Hors des briques 1-6, dépend directement du dataset de Brique 6.** Piste
de recherche banquée, jamais un code à écrire avant que le backfill
existe.

### Le problème central

Trois grandeurs distinctes, jamais interchangeables sur un memecoin peu
liquide : `market cap` (valeur théorique de tous les tokens au dernier
prix), `position value` (valeur affichée du portefeuille au dernier prix),
`exit value` (ce qui est réellement récupérable en vendant, compte tenu du
slippage et de la profondeur du pool). Un token à $20M de market cap avec
$300K de liquidité peut rendre une position de $2M presque impossible à
sortir proprement — la richesse affichée dépend de la continuité du jeu
PvP, pas d'une valeur figée.

### Ce que ça change pour ARIA

Plutôt que de chercher "jusqu'où ce token peut monter" (prédiction de
prix), la question utile devient une distribution empirique de
trajectoires comparables, PUIS pour chaque trajectoire, ce qu'un
participant aurait réellement pu extraire compte tenu de la liquidité
disponible à chaque étape :

```
INPUT à T (état observable, zéro look-ahead)
→ prix, liquidité, delta liquidité, buy/sell flow, participation, régime
        ↓
recherche de trajectoires historiques comparables (Brique 6)
        ↓
OUTPUT futur RÉEL de chaque trajectoire comparable
→ mcap atteint, durée, drawdown avant niveau,
  liquidité disponible à chaque palier,
  EXIT RÉELLEMENT RÉALISABLE (jamais juste le prix atteint)
```

### Capacité d'absorption — la feature la plus prometteuse de cette extension

À chaque instant, à partir de la liquidité courante + flow acheteur/
vendeur + variation de liquidité (déjà mesurés par Briques 2-3-5, zéro
nouveau capteur) : combien de pression vendeuse supplémentaire le marché
peut-il absorber avant une dégradation importante du prix ? Cette courbe
d'exit, comparée à la taille de position envisagée, ouvre la voie à un
sizing piloté par la capacité d'extraction réelle plutôt que par un
pourcentage fixe du capital.

### Où ça s'accroche à la roadmap

Directement à la séquence déjà posée dans "Architecture cible" plus haut
(`HISTORICAL BACKFILL -> EVENTS -> FEATURE ENGINE -> DISCOVERY RULES ->
REPLAY CAUSAL`) et au principe "exhaustion" du cadrage PvP : ARIA doit
détecter non seulement le déséquilibre exploitable, mais aussi le montant
réellement extractible à chaque étape avant qu'il ne disparaisse. Prochaine
étape méthodologique, une fois Brique 6 disponible : définir précisément
le contrat de données de cette mesure (input à T, output futur incluant
exit réalisable et exhaustion), puis vérifier empiriquement, out-of-sample,
si ces distributions ont une vraie valeur prédictive — jamais assumé avant
d'être testé sur le dataset réel.

### Étape A, prototype 9 pools -- DATASET BRUT GELÉ (30/08)

Backfill complet exécuté et vérifié sur les 9 pools validés (MSR/V2,
Copper Inu V3+V4, PDEX/TWO/MU/CHARITY/PAWHOOD/HOODHIM/V4) — le protocole
en 2 étapes de l'opérateur (dry-run coût/métadonnées, puis backfill
complet) a été suivi intégralement, jamais raccourci. Bilan final :

```
9 pools, ~156k events décodés, 106 704 appels RPC total, 0 fenêtre FAILED
(100% COMPLETE), 0 timestamp NULL après retry ciblé, 0 doublon,
buy+sell+undetermined == swap_count vérifié sur les 9 pools.
```

**Addendum (31/08/2026) — corrections découvertes après le gel du 30/08**

Le dataset brut `brique6_dataset_FROZEN_2026-08-30.db` reste strictement
immuable et n'est jamais corrigé en place. Les résultats et métriques du
bloc du 30/08 ci-dessus décrivent fidèlement l'état de la reconstruction
tel qu'il existait à cette date ; toutefois, deux défauts de reconstruction
ont été identifiés lors de l'audit d'intégrité du 31/08 et doivent être
considérés comme des écarts postérieurs au gel :

- **Bug de décodage V4 `token_is_currency0`** — 3 pools affectés : `MU`
  (A), `D227_1406fa` (C_EVENT) et `D51_73a6e5` (C_EVENT). Le code de
  production a été corrigé (`evm_swap_ws.py`, commit `2bb42f28`) avec des
  garde-fous de test supplémentaires. Les conséquences analytiques sont
  recalculées dans des bases séparées ; aucune donnée du FROZEN n'est
  modifiée.
- **Métadonnée `creation_block` incorrecte pour MSR (V2)** — le bloc
  enregistré initialement était `49930872` au lieu du bloc de création
  réel `48869561`, confirmé indépendamment par recherche `eth_getCode` et
  par `PairCreated`. Le backfill du 30/08 n'avait donc couvert qu'une
  portion terminale d'environ 57 minutes au lieu de la vie réelle du pool
  (~29,7 h). Une reconstruction complète séparée
  (`msr_metadata_fix_series.db`) donne 993 événements normalisés, un
  historique de ~1786,9 min et un `T0` réel à la minute 3. Le label
  historique de MSR reste B, conformément au protocole de labellisation
  figé avant tout calcul de feature.
- **Normalisation Mint/Burn V2/V3 incomplète** — `brique6_etape2_backfill.py`
  (script du 30/08) et son dérivé `msr_metadata_fix_backfill.py` décidaient
  d'insérer une ligne normalisée en comparant `pool.liquidity_added_raw`/
  `liquidity_removed_raw` avant/après chaque événement. Or les handlers de
  production (`evm_swap_ws.py`) mettent à jour un champ différent pour
  V2/V3 : `liquidity_added_quote`/`liquidity_removed_quote` (le champ
  `_raw` n'est utilisé que par V4, par conception -- unité L abstraite vs
  montant en quote-token, cf. le docstring du dataclass). Conséquence :
  chaque Mint/Burn V2/V3 était bien capturé en brut mais jamais normalisé.
  Ampleur mesurée par SQL pur (zéro RPC) : MSR (1 mint) et COPPERINU-V3
  (2819 mint + 2809 burn) -- les 11 pools C_EVENT sont tous V4, donc non
  exposés. Aucune primitive déjà calculée (T0, buy_share, mfe/mae,
  new_sender_share, price_recovery) ne dépend de cet axe -- impact nul sur
  les résultats déjà produits. Redécodé sans nouveau scan RPC (les
  événements bruts existaient déjà) dans
  `brique6_liquidity_redecoded_2026-08-31.db`, invariants vérifiés
  (Mint/Burn ne modifie jamais swap_count ni buy/sell_count -- 0 violation
  sur les 5629 événements), ModifyLiquidity V4 contrôlé par échantillon
  (chaque événement ne touche qu'un seul côté added/removed, magnitudes
  cohérentes en paires add/remove).

Conséquence méthodologique : les bases/analyses `v019`–`v021` produites
avant ces corrections restent conservées comme traces expérimentales
historiques, mais les conclusions A/B/C qui en dépendent ne doivent pas
être considérées comme finales tant que les reconstructions corrigées
correspondantes n'ont pas été recalculées.

Cette note ne réécrit pas le compte-rendu du 30/08 ; elle documente
explicitement les défauts découverts après son gel et la séparation entre
données brutes immuables et reconstructions corrigées.

### Integrity Audit Closure -- 31/08/2026

Checkpoint officiel de confiance du pipeline, à lire par toute session
future AVANT de reprendre ce chantier -- remplace toute supposition sur
l'état de fiabilité des données par un résultat vérifié et daté.

```
Niveau 1 -- Identity                              PASS
  MSR creation_block corrige (eth_getCode + PairCreated, 2 methodes independantes)
  9/9 pools A/B verifies (token-deploy proxy + Initialize log exact sur MU)

Niveau 2 -- Golden dataset                        PASS
  V2 (MSR)          buy/sell     -- hand-decode independant == production
  V3 (COPPERINU-V3) buy/sell     -- hand-decode independant == production
  V4 currency0-side (CHARITY, HOODHIM)  -- hand-decode independant == production
  V4 currency1-side (PDEX)              -- hand-decode independant == production
  Mint/Burn V2/V3   -- redecode complet, invariants verifies (0/5629 violation)
  ModifyLiquidity V4 -- controle par echantillon (un seul cote added/removed)

Niveau 3 -- Math invariants                       PASS
  buy+sell+undetermined == swap_count (A/B, C_EVENT, MSR-corrige)
  volume_quote_delta >= 0, price_quote > 0, liquidity added/removed >= 0
  exactement un cote (added XOR removed) non-nul par ligne modify_liquidity

Niveau 4 -- Temporal invariants                   PASS
  creation_block <= block_number <= scan_ended_at_block (0 violation)
  block_timestamp non-decroissant par pool (0 violation)
  T0 jamais negatif, jamais au-dela de l'historique disponible (0 violation)
  pool_age_at_t0_minutes jamais negatif (0 violation)

Niveau 5 -- Live <-> Backfill                     PASS
  bloc frais 50 886 336 (tip courant 50 886 696, hors horizon FROZEN 49 965 216)
  decodage manuel independant == decodeur de production appele en direct
  (side=buy, vol=48.789322, price=0.010917245806712609 -- match exact)
```

**Les trois défauts réels trouvés cette session** :

```
1. V4 token side / token_is_currency0
   symptome    : prix inverse/faux de plusieurs ordres de grandeur, buy/sell permutes
   cause       : script de sourcing passait un mauvais format d'argument a
                 add_pool() -- token_address devait etre le litteral
                 "currency0"/"currency1", pas une adresse hex, pour un pool V4
   pools       : MU (A/B), D227_1406fa (C_EVENT), D51_73a6e5 (C_EVENT)
   correction  : evm_swap_ws.py durci (_add_pool_v4 refuse desormais tout
                 token_address non-litteral), 3 pools re-decodes depuis les
                 RAW events FROZEN dans cage_currency0_bugfix_2026-08-31.db
   commit      : 2bb42f28 (code + tests de non-regression)
   impact      : T0 inchange (ne depend que du compte de swaps), mais
                 buy_share/mfe/mae/price_recovery des 3 pools etaient faux
                 dans v019 original -- recalcules dans v019_corrected

2. MSR creation_block
   symptome    : backfill original ne couvrait que les 57 dernieres minutes
                 de vie du pool -- MSR jamais un point de donnee valide
                 (t0_found=False, "no minute ever reached ratio>=5.0")
   cause       : creation_block == scan_started_at_block pour les 9 pools
                 A/B, jamais verifie independamment -- MSR seul avait un
                 ecart reel (bloc declare 49930872, bloc reel 48869561)
   pools       : MSR (A/B) -- seul pool touche, 8 autres verifies corrects
   correction  : backfill complet depuis le vrai creation_block (565 fenetres,
                 993 events) dans msr_metadata_fix.db/msr_metadata_fix_series.db
   commit      : aucun (script scratchpad, jamais commite -- reconstruction
                 de donnee uniquement, pas de code de production en cause)
   impact      : MSR ABSENT de v006-v021 (0 ligne dans abc_pool_primitives) --
                 pas une donnee fausse mais un point de donnee manquant.
                 Desormais disponible : T0=minute 3, 993 events, ~29.7h
                 post-T0. Label B confirme (protocole de labellisation,
                 independant des features). A noter pour le recalcul futur :
                 T0=3min est un outlier bas face aux 2 autres B (PAWHOOD
                 208min, HOODHIM 50min).

3. Normalisation Mint/Burn V2/V3
   symptome    : evenements Mint/Burn reels captures en brut, absents de
                 normalized_event_log
   cause       : brique6_etape2_backfill.py detectait un changement d'etat
                 via pool.liquidity_added_raw/removed_raw (champ V4 only) --
                 les handlers V2/V3 de production touchent en realite
                 liquidity_added_quote/removed_quote (par conception, cf.
                 docstring evm_swap_ws.py ligne 380)
   pools       : MSR (1 mint), COPPERINU-V3 (2819 mint + 2809 burn) -- 11
                 pools C_EVENT tous V4, non exposes
   correction  : redecode complet sans nouveau scan RPC (evenements deja
                 captures) dans brique6_liquidity_redecoded_2026-08-31.db,
                 invariants verifies (0/5629 violation)
   commit      : aucun (bug dans un script scratchpad, jamais commite --
                 evm_swap_ws.py de production etait deja correct par design)
   impact      : NUL sur v006-v021 (aucune primitive n'utilise cet axe) --
                 mais bloquant pour toute analyse future basee sur la
                 liquidite (absorption, exhaustion) tant que non integree
```

**FROZEN reste immuable** : `brique6_dataset_FROZEN_2026-08-30.db` et
`groupC_dataset_FROZEN_2026-08-30.db` n'ont jamais été réécrits. Toutes les
corrections ci-dessus vivent dans des bases séparées
(`cage_currency0_bugfix_2026-08-31.db`, `msr_metadata_fix.db`/
`msr_metadata_fix_series.db`, `brique6_liquidity_redecoded_2026-08-31.db`).

**Carte de contamination des analyses** :

```
v019 original        -> contamine par V4 (3 pools) + MSR absent (donnee
                         insuffisante, jamais un point de donnee valide)
v019_corrected        -> V4 corrige (3 pools) mais MSR toujours absent
v019_corrected_v2     -> CIBLE, PAS ENCORE CONSTRUITE : 9 A/B (MSR integre)
                         + 11 C_EVENT, toutes corrections integrees
v020/v021              -> recalcul necessaire une fois v019_corrected_v2
                         disponible (age-at-T0/support-overlap doivent
                         inclure MSR comme 9e pool A/B, T0=3min)
```

Normalisation Mint/Burn : aucun impact démontré sur v006–v021 (ces analyses
n'utilisent jamais cette primitive) ; la reconstruction corrigée
(`brique6_liquidity_redecoded_2026-08-31.db`) est néanmoins nécessaire avant
toute analyse future exploitant la liquidité.

**État pour toute future session** :

```
AUDIT      = CLOSED / PASS (5/5 niveaux)
FROZEN     = IMMUTABLE (jamais reecrit)
A/B        = v019_corrected_v2 CONSTRUIT (A=6, B=3 avec MSR) -- voir section ci-dessous
C_EVENT    = valide, inchange
C_AGE      = run seed 2026083113 CLOS -- voir section C_AGE ci-dessous
```

### v019_corrected_v2 -- base analytique finale (31/08/2026)

Dernier verrou analytique de Brique 6, construit après la clôture de
l'audit d'intégrité. **Composition : A=6, B=3 (MSR réintégré), C_EVENT=11.**
`compute_primitives` est IMPORTÉ depuis `brique6_analysis_v019_corrected.py`,
jamais réimplémenté (même doctrine qui a permis d'attraper le bug
currency0 : une convention re-dérivée est exactement comme deux générations
de données divergent silencieusement).

**Deux corrections intégrées** : (1) V4 `token_is_currency0` (MU /
D227_1406fa / D51_73a6e5), reprise verbatim de `v019_corrected` ; (2)
`creation_block` MSR — MSR est calculé pour la PREMIÈRE FOIS ici (il était
absent de v006–v021, 0 ligne), T0=minute 3, label B conformément au
protocole de labellisation figé.

**Correction délibérément EXCLUE** (instruction opérateur explicite) : la
normalisation Mint/Burn V2/V3. L'audit a confirmé que les primitives v019
n'en dépendent pas (axe swap uniquement) — l'injecter ici fabriquerait un
changement dont l'analyse n'a jamais dépendu. Elle reste corrigée dans la
couche de reconstruction (`brique6_liquidity_redecoded_2026-08-31.db`)
pour les futures features basées sur la liquidité uniquement.

**Tableau de stabilité des conclusions** — critères de classification figés
AVANT tout regard sur les résultats, et transition décomposée en deux
étapes (l'effet de la correction V4 seule, puis l'effet de MSR seul) plutôt
qu'un diff global qui masquerait laquelle des deux a causé quoi :

```
observation            correction V4        reintegration MSR    global                 discrimine A vs B ?
---------------------  -------------------  -------------------  ---------------------  -------------------
activity persistence   INVARIANT            CHANGED_NUMERICALLY  CHANGED_NUMERICALLY    HOLDS
new sender share       INVARIANT            CHANGED_NUMERICALLY  CHANGED_NUMERICALLY    FRAGILE
flow concentration     CHANGED_NUMERICALLY  CHANGED_NUMERICALLY  CHANGED_NUMERICALLY    FRAGILE
MFE                    CHANGED_NUMERICALLY  CHANGED_NUMERICALLY  CHANGED_NUMERICALLY    FRAGILE
MAE                    BECAME_INDETERMINATE CHANGED_NUMERICALLY  CHANGED_QUALITATIVELY  FRAGILE
price recovery         INVARIANT            INVARIANT            INVARIANT              NON_DISCRIMINANT
```

La colonne « discrimine A vs B ? » répond à une question DIFFÉRENTE des
autres (« l'observation a-t-elle changé ? » vs « sépare-t-elle réellement
A de B ? ») — ajoutée après avoir vu le diff et étiquetée comme telle,
jamais rétro-insérée dans les critères pré-enregistrés. `HOLDS` = médianes
différentes, plages disjointes, ordre survit au retrait du pool le plus
extrême. `FRAGILE` = plages qui se chevauchent, ou ordre qui bascule au
drop-top-1. `NON_DISCRIMINANT` = médianes identiques entre groupes.

**Ce qui survit réellement à l'audit de données** : **une seule observation
sur six**. `activity persistence` (A médiane 0,538 [0,526–0,584] vs B
médiane 0,124 [0,009–0,154], plages disjointes, survit au drop-top-1).
`price recovery` est INVARIANT mais pour une raison dégénérée — sa médiane
vaut 1 dans les trois groupes, elle ne sépare rien. Les 4 autres ont des
plages A/B qui se chevauchent.

**Fragilité structurelle à ne jamais oublier** : avec B à n=3, retirer un
seul pool déplace la médiane de B de 25 % à 47 % selon l'observation (et
celle de A jusqu'à 56 % sur le MFE — cohérent avec le corollaire déjà
mesuré : 1,8 % des trades portent 100 % du gain). Aucune de ces lectures
n'a la puissance statistique d'un résultat, ce sont des indications sur
9 pools.

**v020/v021 — recalcul CIBLÉ (`analysis_v020_v021_v2.db`)**, nécessité
établie avant d'écrire le code et non supposée : MSR a un T0 à 3 min, or
v021 avait conclu « zéro pool B ne chevauche la plage d'âge de C [1,18] ».
Le bug V4 n'est PAS re-appliqué ici (T0 ne dépend que du compte de swaps
par minute, jamais du prix/side/volume — vérifié pendant l'audit) : seule
l'absence de MSR importait.

```
groupe  version      n   mediane   plage      valeurs
A       inchange     6   100.5     [5,175]    [5,41,90,111,136,175]
B       original     2   129.0     [50,208]   [50,208]
B       v2 (+MSR)    3    50.0     [3,208]    [3,50,208]
C       inchange    11     3.0     [1,18]     [1,1,2,2,2,3,5,6,8,15,18]
```

Conséquence : la formulation littérale de v021 (« zéro pool B ») est
PÉRIMÉE (MSR à 3min est dans [1,18]), mais **la conclusion tient** — 2 des
9 pools A/B dans la plage de C (TWO à 5min, déjà connu, plus MSR à 3min)
reste très loin d'une distribution d'âge comparable, donc une comparaison
A/B-vs-C corrigée de l'âge demeure structurellement impossible. MSR ne
réhabilite PAS `new_sender_share`/`flow_concentration` — ils restent
déclassés « confondus par l'âge », seul le libellé exact de v021 est
obsolète.

### MINI-SPEC C_AGE v2 -- état à bloc fixe (31/08/2026, GO opérateur)

Résout le défaut de conception qui a rendu le premier run C_AGE insuffisant :
**on observe désormais un pool à un âge donné, même lorsqu'il est
silencieux.** Question posée : *à un âge donné du pool, indépendamment du
fait qu'un événement ait eu lieu à cet instant, les états observables des
pools A/B diffèrent-ils de ceux de contrôles neutres C_AGE ?* Cette brique
ne cherche plus un événement, elle cherche un **état de marché à un instant
déterminé par l'âge**.

**Convention figée** : à chaque âge cible H, l'observation C_AGE est définie
au **dernier bloc `reference_block` dont le timestamp est ≤
`creation_timestamp + H`**, avec état instantané lu à ce bloc et cumul
historique reconstruit depuis la création jusqu'à ce bloc. Aucun événement
requis à H. Aucune interpolation. Aucune direction token/quote. Aucun T0.
Aucun filtre d'activité. Aucun nouveau tirage.

Convention retenue *contre* « premier événement après H » délibérément : on
veut l'état du pool À H, pas l'état au prochain événement après H — sinon
l'observation est décalée artificiellement par un swap survenant 20
secondes plus tard.

**1. Âges cibles verrouillés** : 45 / 75 / 105 / 135 / 165 / 195 min.
Inchangés quels que soient les résultats.

**2. Champs temporels obligatoires** : `target_timestamp`,
`reference_block`, `reference_block_timestamp`, `actual_age`,
`age_error` (= `reference_block_timestamp - target_timestamp`).

**3. Mesures** — état instantané AU bloc (`price_state`,
`liquidity_level`) séparé du cumul depuis la création JUSQU'AU bloc
(`swap_count_cum`, `volume_total_cum`, `unique_senders_cum`,
`new_senders_cum`, `liquidity_added_cum`, `liquidity_removed_cum`).

**4. Aucune direction économique** (inchangé depuis le premier run) :
C_AGE n'a pas de `token_of_interest` défini expérimentalement, donc jamais
de `buy_share`/`sell_share`/`MFE`/`MAE`/`price_recovery`/`net_flow`.

**5. Absence réelle vs non-reconstructible — distinction stricte** :
`swap_count_delta = 0` signifie « aucun swap observé sur l'intervalle » ;
`price = NULL` signifie « impossible de reconstruire ce prix ». `None`
reste `None`, jamais une approximation (doctrine ARIA générale).

**6. Lecture d'état historique — FAISABILITÉ VÉRIFIÉE EMPIRIQUEMENT
AVANT de figer cette spec** (jamais supposée) :

```
Archive node Chainstack Robinhood : CONFIRME DISPONIBLE
  (valeurs historiques a ~2M blocs de profondeur differentes des valeurs
   courantes -- donc une vraie lecture d'archive, pas un fallback silencieux
   vers "latest")

V2  getReserves() @ block            -> OK, teste sur MSR
V3  slot0() @ block                  -> OK, teste sur COPPERINU-V3
V4  StateView de Base ABSENT sur Robinhood (eth_getCode = 0x)
    -> extsload(bytes32) sur le PoolManager Robinhood
       0x8366a39cc670b4001a1121b8f6a443a643e40951
       slot de base = keccak256(poolId ++ uint256(6))
       slot0     a l'offset +0 (sqrtPriceX96 = 160 bits de poids faible)
       liquidity a l'offset +3 (128 bits de poids faible)
    -> VALIDE EMPIRIQUEMENT 7/7 pools V4 du FROZEN : la valeur lue en
       storage au bloc == le sqrtPriceX96/liquidity du vrai event Swap
       de ce meme bloc
```

**Sémantique confirmée par un test dédié** : `eth_call` à un bloc N renvoie
l'état à la **FIN** du bloc N. Vérifié sur TWO, qui a 13 swaps dans le même
bloc — le storage correspond exactement au DERNIER (log_index 53), pas au
premier. C'est la sémantique voulue pour « état du pool à l'âge H ».

Si une primitive historique n'est pas reconstructible proprement pour une
famille : `None`, jamais une approximation.

**7. Population** : le tirage déjà réalisé (20 pools, 18 V4 / 1 V3 / 1 V2,
seed `2026083113`) reste la population expérimentale. **On ne refait pas le
tirage**, on reconstruit ses six états à bloc fixe.

**8. Contrôles qualité obligatoires avant toute analyse** :
- **A. Cohérence temporelle** : `creation_block <= reference_block`,
  `reference_block_timestamp >= creation_timestamp`.
- **B. Reproductibilité** : le même (pool, target_age) doit toujours
  produire le même `reference_block`.
- **C. Monotonie cumulative** : tous les compteurs cumulés doivent être
  non-décroissants avec l'âge, pour un pool donné.
- **D. Contrôle indépendant** : sur au moins un V2, un V3 et un V4,
  comparer l'état reconstruit à l'état lu directement au
  `reference_block` — particulièrement important après les trois bugs
  trouvés le 31/08.

**9. Statut de `activity persistence`** : après `v019_corrected_v2`, c'est
la seule observation qui survit aux corrections ET conserve une séparation
A/B. Statut explicite : **`candidate surviving preliminary controls`**,
JAMAIS `signal`. Trajectoire : v019 → corrections V4/MSR →
`v019_corrected_v2` → survit sur A/B → **C_AGE état-à-bloc-fixe →
confirmation ou disparition**. C_AGE est précisément le test manquant
avant de lui donner davantage de poids.

**10. Ce que C_AGE pourra enfin tester** — trois hypothèses concurrentes,
énoncées AVANT le calcul : (H1) à âge comparable, plus aucune différence →
les écarts v019 venaient du cycle de vie ; (H2) à âge comparable, certaines
différences survivent → signal réel ; (H3) différence seulement à certains
âges → interaction phase × comportement, plus intéressant qu'un seuil.

**EXÉCUTÉ le 31/08 — `cage_v2_fixed_block_2026-08-31.db`** : les 20 pools
reconstruits aux 6 âges, **120/120 observations exploitables** (contre
**8/120** avec l'ancien protocole événement-conditionné). Prix ET liquidité
reconstruits sur 120/120 — aucun `None`. 1556 appels RPC (résolution de
bloc en entonnoir : estimation depuis le débit de blocs propre à chaque
pool, puis marche locale jusqu'à la borne exacte, ~3-6 appels par cible au
lieu de ~25 pour une recherche binaire aveugle sur 50M blocs).

**Le trou méthodologique est comblé, chiffré** : **14 pools sur 20 ont zéro
swap sur toute la fenêtre ET un état parfaitement reconstruit** aux 6 âges.
Avec l'ancien protocole ils étaient invisibles (« aucune observation ») ;
ils sont désormais ce qu'ils ont toujours été — des pools observables,
silencieux.

**Contrôles qualité A/B/C/D : ALL PASS.** (A) cohérence temporelle 120/120,
`age_error <= 0` toujours ; (B) reproductibilité — re-résolution en direct
de 5 cibles, même `reference_block` à chaque fois ; (C) monotonie
cumulative — 10 compteurs × 20 pools, zéro violation ; (D) contrôle
indépendant — relecture fraîche V2/V3/V4 au même bloc, prix et liquidité
identiques au bit près.

**`age_error = 0` sur 120/120, vérifié et expliqué plutôt que présumé** :
Robinhood Chain produit ~7-10 blocs par seconde qui PARTAGENT le même
timestamp entier (mesuré : blocs 48004700→48004706 tous à 1787890424, puis
48004707 à 1787890425). Il existe donc quasi toujours un bloc exactement à
la seconde cible. Vérification supplémentaire au niveau du bloc individuel :
`reference_block + 1` a systématiquement un timestamp strictement supérieur
à la cible — le résolveur retient bien le DERNIER bloc de l'intervalle,
conformément à la convention figée, jamais le premier.

**Obstacle méthodologique identifié pour la comparaison A/B vs C_AGE, à
trancher AVANT de l'exécuter** : `activity persistence` — la seule
observation survivante de `v019_corrected_v2` — est définie comme la
fraction des minutes POST-T0 où `activity_ratio >= 1`. Elle dépend
structurellement de T0, que C_AGE n'a pas et ne doit pas avoir. Elle n'est
donc PAS directement transposable au protocole état-à-bloc-fixe. Comparer
A/B et C_AGE exigera soit une redéfinition explicite et symétrique de la
persistance (sans T0, par exemple sur une fenêtre d'âge fixe), soit de
restreindre la comparaison aux métriques réellement communes aux deux
protocoles. À ne jamais bricoler en silence — c'est une décision de
protocole, pas un détail d'implémentation.

### `activity_persistence_by_age` -- métrique symétrique (31/08/2026)

**Définie et COMMITÉE avant tout calcul sur A/B/C_AGE** — la trace git de
ce commit est la preuve que la formule n'a pas été choisie en regardant les
résultats. C'est la contrainte centrale posée par l'opérateur : « la
définition doit être faite sans voir les résultats, sinon on recréerait
exactement le problème que l'audit vient de nous faire corriger ».

**Étape 1 — l'ancienne métrique dépend structurellement de T0.** Relu dans
le code (jamais de mémoire), `brique6_analysis_v019_corrected.py` ligne 159
et `brique6_analysis_v003.py` lignes 126-128 :

```
activity_baseline(m) = mediane(swaps[max(0,m-15) .. m-1])     # causale, 15 min
activity_ratio(m)    = swaps(m) / activity_baseline(m)  si baseline non nulle
                     = None                              sinon
persistence_minutes  = |{ m dans [T0, fin] : ratio(m) != None ET ratio(m) >= 1.0 }|
persistence_share    = persistence_minutes / (available_post_t0 + 1)
```

La dépendance à T0 est double : borne INFÉRIEURE de la fenêtre (`m >= T0`)
et DÉNOMINATEUR (`available_post_t0 + 1`). C_AGE n'a pas de T0 et ne doit
pas en avoir — la métrique n'est donc pas transposable telle quelle.

**Étape 2 — la nouvelle métrique, transformation minimale et fidèle.**
Trois choses seulement changent, tout le reste est repris à l'identique :

```
activity_persistence_by_age(H) =
    |{ m dans [0, H] : ratio(m) != None ET ratio(m) >= 1.0 }| / (H + 1)
```

- **ancrage** : T0 -> minute 0 (création du pool)
- **borne supérieure** : fin de l'historique -> âge cible H
- **dénominateur** : `available_post_t0 + 1` -> `H + 1`
- **INCHANGÉ** : la formule du ratio (médiane causale sur 15 minutes), le
  seuil (`>= 1.0`), l'unité (fraction de minutes), le traitement de
  `None` (une minute dont la baseline est nulle ou absente n'est jamais
  comptée comme persistante).

Sémantique : *quelle fraction des minutes de vie du pool, jusqu'à l'âge H,
son activité s'est-elle maintenue au moins au niveau de sa propre baseline
récente ?* — une propriété de la trajectoire d'activité selon la maturité
du pool, là où l'ancienne mesurait ce que devient l'activité après un choc.

**Métriques d'accompagnement obligatoires, jamais des variables de
décision** — elles servent uniquement à distinguer « silence réel » de
« non mesurable », distinction gravée après les bugs du 31/08 :
`minutes_with_computable_ratio` (combien de minutes ont une baseline
exploitable) et `total_swaps_in_window`. Un pool à zéro swap obtient
légitimement `persistence = 0` (il ne maintient aucune activité) — ce n'est
PAS un `None`, et les deux ne doivent jamais être confondus.

**Les deux métriques coexistent, aucune ne remplace l'autre** (décision
opérateur explicite) :

```
activity_persistence_post_t0   -- que devient l'activite APRES un choc ?
                                  conservee intacte comme artefact historique,
                                  jamais recalculee ni renommee
activity_persistence_by_age    -- comment evolue l'activite avec la MATURITE
                                  du pool ? nouvelle, age-conditionnee
```

**Étapes 3 à 6, dans cet ordre strict** : (3) reconstruire les 9 pools A/B
aux 6 âges fixes avec le protocole état-à-bloc-fixe ; (4) réutiliser les 20
pools C_AGE déjà reconstruits ; (5) calculer la MÊME formule partout
(mêmes fenêtres, même unité, même traitement des manquants) ; (6) seulement
ensuite comparer A / B / C_AGE. Interdiction explicite de chercher à
maximiser la séparation A/B en ajustant la définition.

**RÉSULTAT (31/08, `persistence_by_age_2026-08-31.db`)** — 40 pools, 4
populations, une seule formule appliquée partout : A=6, B=3, C_EVENT=11,
C_AGE=20. Les séries minute de v003 restent des entrées valides malgré le
bug currency0 : `ratio(m)` ne dépend QUE du compte de swaps par minute,
jamais du prix/side/volume (vérifié pendant l'audit) — un bug de décodage
de prix ne peut pas déplacer un compte de swaps. Seul MSR a dû être
reconstruit, et il l'a été.

**La nouvelle métrique NE sépare PAS A de B** — médianes quasi identiques
à tous les âges, plages entièrement chevauchantes :

```
age   A (n=6)                        B (n=3)                        disjoint ?
 45   med 0.2283 [0.130,0.522]       med 0.1957 [0.130,0.565]       non
105   med 0.3679 [0.311,0.528]       med 0.4057 [0.104,0.538]       non
195   med 0.4184 [0.378,0.500]       med 0.4235 [0.082,0.526]       non
```

**Et le contrôle à condition de sélection ÉGALE ne sépare rien non plus** —
distinction logique décisive entre deux comparaisons qui n'ont pas la même
valeur probante :

```
A/B vs C_AGE    conditions de selection DIFFERENTES (A/B ont un T0, C_AGE non)
                -> plages disjointes des 105min, tient au drop-top-1
                -> MAIS confondu par la condition de selection elle-meme :
                   14/20 pools C_AGE ont zero swap, et un pool sans swap a
                   mecaniquement une persistance nulle. Meme restreint aux
                   6 pools C_AGE ACTIFS, la mediane reste 0.0000.
                   Cette separation ne demontre donc rien de plus que
                   "A/B ont ete selectionnes pour avoir eu un choc".

A/B vs C_EVENT  MEME condition de selection (les deux ont un T0)  <== decisif
                -> chevauchement a TOUS les ages, ne tient jamais au drop-top-1
```

**Découverte principale : les deux métriques mesurent des choses
réellement différentes, et l'écart entre elles est informatif.**
L'ancienne `activity_persistence_post_t0` sépare A de B nettement
(A [0.526, 0.584] vs B [0.009, 0.154], disjoint, facteur 3,4x, survit au
drop-top-1). La nouvelle, âge-conditionnée, ne les sépare pas du tout.
Deux tests de confondement, tous deux négatifs :

```
denominateur (available_post_t0+1) vs share : Spearman rho = -0.083  (n=9)
age au T0                          vs share : Spearman rho = +0.233  (n=9)
```

Visuellement sans ambiguïté : MSR (T0=3min) et PAWHOOD (T0=208min) sont
tous deux B avec une persistance faible ; TWO (T0=5min) et CHARITY
(T0=175min) sont tous deux A avec une persistance forte. **Ni le
dénominateur ni l'âge au T0 n'expliquent la séparation A/B.**

**Conclusion — le contrôle d'âge n'invalide pas la propriété, il la
PRÉCISE** : gagnants et perdants ont la même densité d'activité sur
l'ensemble de leur vie (by_age identique), mais des comportements très
différents APRÈS le choc (post_t0 disjoint). La réponse à la question
ouverte de l'opérateur (« la persistance post-T0 est-elle en réalité une
propriété plus générale du cycle de vie ? ») est donc **non, c'est
l'inverse** : la propriété est spécifiquement post-choc, et n'existe pas
comme propriété générale de maturité.

`C_EVENT` chevauche les deux groupes sur `post_t0` ([0.0063, 1.0], n=11) —
attendu et non contradictoire : un contrôle NEUTRE, ni gagnant ni perdant,
doit s'étaler sur toute la plage si la métrique capture bien un axe
gagnant/perdant.

**Réserve de puissance statistique, jamais à oublier** : n=6 contre n=3.
Ce résultat reste une indication sur 9 pools, pas une preuve. Statut de
`activity_persistence_post_t0` : `candidate surviving preliminary
controls` — le contrôle d'âge est passé, mais l'échantillon reste trop
petit pour parler de signal.

### C_AGE -- run seed 2026083113 (31/08/2026), CLOS

Contrôle âge-conditionné (jamais T0-conditionné), orthogonal à C_EVENT, conçu
après le reframe C2-bis. **Métriques symétriques uniquement, décision
opérateur explicite** : C_AGE n'a aucun token/quote "d'intérêt" prédéterminé
(contrairement à A/B et C_EVENT), donc aucune primitive directionnelle
(`buy_share`/`price_recovery`/`mfe`/`mae`) -- uniquement `swap_count`,
`volume0`/`volume1` (les deux côtés de la paire, jamais fusionnés),
`active_senders`/`new_senders`, `liquidity_added`/`removed` (par côté pour
V2/V3, en unité L abstraite pour V4), `price_ratio_1_per_0` (ratio brut
currency1/currency0, jamais orienté) et sa volatilité (log-return max
absolu, écart-type). `token0`/`token1`/`currency0`/`currency1`/`family`
restent dans le dataset -- disponibles mais sans signification économique
attribuée que la sélection n'a jamais établie.

**Population** : 20 pools tirés (seed `2026083113`) de l'univers structurel
multi-famille (49252 candidats V2/V3/V4, exclusion A/B et C_EVENT vérifiée
par identité réelle) -- composition **18 V4 / 1 V3 / 1 V2**, confirmée
conforme à la composition attendue. Cibles d'âge : **45/75/105/135/165/195
minutes**. Protocole par âge H : premier événement RÉEL (tout type, jamais
seulement swap) à `age >= H`, jamais d'interpolation, `actual_age` toujours
enregistré à côté de `H` cible.

**Préflight (`cage_preflight.py`)** : 20/20 PASS (identité/creation_block
plausible via `eth_getBlockByNumber`/famille/côtés bien formés/195min
d'historique réel disponible/au moins un événement on-chain réel). Première
version du check d'activité ne cherchait que le topic Swap (14/20 FAIL) --
corrigée en topic-agnostique (tout événement compte, cohérent avec le
principe "C_AGE ne conditionne jamais sur l'activité") après avoir confirmé
via un cas réel (`0xc89f36de...`) que l'échec initial était un bug de mon
propre script (ModifyLiquidity manqué), pas un vrai trou de donnée.

**Backfill (`cage_backfill.py`)** : 20/20 pools, 1787 appels RPC, 0 fenêtre
FAILED, 0 timestamp NULL, 0 violation `creation_block<=block_number`.
Décodage indépendant (jamais via le pool-state directionnel du feed de
production) -- 2 bugs trouvés et corrigés en relisant le code de production
avant tout lancement : offsets Mint/Burn V3 (le layout Mint a un champ
`sender` que Burn n'a pas, mon premier jet utilisait le même offset pour
les deux) et offset `liquidityDelta` V4 ModifyLiquidity (3e mot, pas le
2e). Écrit dans `cage_backfill_2026-08-31.db`, jamais dans le FROZEN.

**Résultat (`cage_age_observations_2026-08-31.db`)** -- vérifié technique
propre (0 `actual_age < target_age`, 0 compte négatif) puis confronté
manuellement à un timestamp on-chain réel (bloc de fin de fenêtre du pool
V2 : 254,68min réelles écoulées pour une cible de 255min -- la fenêtre de
scan était correcte, pas un bug de calcul) :

```
17/20 pools -- rafale d'activite initiale (souvent < 10min) puis silence
               total confirme jusqu'a la fin de la fenetre observee (255min)
3/20 pools  -- atteignent au moins 45min
2/20 pools  -- atteignent 135min
0/20 pools  -- atteignent 165 ou 195min
```

**Double lecture, décision opérateur explicite (31/08)** :
- **Comme contrôle d'âge** : couverture insuffisante pour comparer des
  états A/B à 165/195min sur cette population -- **échec de couverture**,
  pas un échec de pipeline.
- **Comme observation de marché** : résultat réel et intéressant en soi --
  confirme et amplifie la découverte C_EVENT du 20/08 (~4,3% des candidats
  atteignent un vrai choc d'activité), ici sans même conditionner sur
  l'éligibilité structurelle de C_EVENT. Consigné comme observation de
  survie/activité post-lancement, jamais comme feature de trading.

**Statut final** : `VALIDÉ COMME EXPÉRIENCE MÉTHODOLOGIQUE / INSUFFISANT
COMME CONTRÔLE D'ÂGE COMPLET`. La grille d'âge (45-195min) N'EST PAS
réduite après coup, aucun nouveau tirage n'est lancé pour "corriger" ce
résultat (biaiserait vers les pools qui survivent), aucune extension de
fenêtre de scan pour forcer une couverture -- les 3 mêmes garde-fous
anti-biais que C2-bis. Les 17 pools silencieux restent dans le dataset,
jamais traités comme "manquants".

**Défaut de conception trouvé, à corriger dans une future mini-spec, PAS
dans ce run** : le protocole actuel confond "état du pool à l'âge H" avec
"présence d'un événement à l'âge H" -- un pool silencieux à 105min a un état
réel (prix/liquidité) même sans événement à cet instant précis, mais le
protocole actuel ne peut l'observer que via le premier événement réel
suivant. Une observation d'état véritablement indépendante d'un événement
(lecture on-chain ciblée à un bloc proche de `creation_block + H`, dans
l'esprit de `resolve_cold`/`EVMSwapSnapshot` déjà existant pour V2/V3/V4)
résoudrait ça mais constitue une redéfinition de C_AGE, pas une correction
de ce run. Aucun nouveau sourcing C_AGE tant que cette distinction
(événement-conditionné vs état-à-bloc-fixe) n'est pas verrouillée dans une
mini-spec dédiée.

**Distinction désormais actée, à respecter par toute session future** :

- **Dataset brut gelé** — les événements tels que reconstruits on-chain
  (`raw_event_log`/`normalized_event_log`/`backfill_window`/
  `pool_metadata`), copie figée en lecture seule
  (`brique6_dataset_FROZEN_2026-08-30.db`, scratchpad de cette session,
  hash SHA-256 `6b2b1abf...082102a`) — **jamais réécrit**, quelle que soit
  l'expérimentation analytique qui suit.
- **Features analytiques** — tout ce qui se calcule PAR-DESSUS ce dataset
  (delta-log du prix, vitesse/accélération, régimes ACCUMULATION->
  EXPANSION->EXHAUSTION, market cap éventuel via `totalSupply()` — encore
  absent du dataset brut, à ajouter uniquement côté feature, jamais en
  rétro-modifiant le brut) — recalculable à volonté, jamais gravé.

Un bug de perf réel trouvé et corrigé pendant ce backfill (récupération
séquentielle des timestamps sans commit intermédiaire — 24 minutes de
travail perdues une fois avant le fix) : détail dans
`docs/HANDOFF_PIPELINE_MOMENTUM.md`. Prochaine étape, hors scope de cette
entrée : l'analyse gagnants/perdants proprement dite, sur ce dataset gelé
— pas avant une relecture de ce même document par la session qui l'entame.
