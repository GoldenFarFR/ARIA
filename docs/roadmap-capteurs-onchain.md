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
