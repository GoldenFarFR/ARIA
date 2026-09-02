# Registre Phase 1 — ce que les 10 poches contenaient réellement

    Établi 2026-09-02. Source : research/pockets/POCKET-CONSTANTS-SNAPSHOT.md
    (121 constantes capturées avec leur justification, avant toute suppression).
    Méthode : une passe de classification, puis une passe adversariale chargée
    de l'attaquer. Les corrections de la seconde sont intégrées ici, y compris
    quand elles portent sur mon propre outillage.

## Le résultat principal

**121 constantes ne sont pas 121 hypothèses. Elles recouvrent environ 16
phénomènes distincts.** C'est le seul chiffre qui compte pour la suite : sans
ce dédoublement, une future correction de multiplicité compterait 121 tests
indépendants là où il y en a une quinzaine, et fabriquerait de la
significativité à partir de répétitions du même signal.

Le facteur de dédoublement est d'environ **7,5x**. Une hypothèse « confirmée
sur 4 poches » est le plus souvent la même constante recopiée quatre fois, pas
quatre confirmations.

## Statuts (après correction adversariale)

| Statut | Sens | Note |
|---|---|---|
| `VALIDATED_RESULT` | survit hors échantillon | **0** — et c'est le résultat honnête |
| `EXPLORATORY_RESULT` | mesuré, mais sur l'échantillon qui l'a calibré | le gros du corpus |
| `PARAMETER` | valeur choisie après avoir regardé les résultats | circularité potentielle |
| `SAFETY_RULE` | protection contre une donnée aberrante / un incident | **jamais une hypothèse économique** |
| `ORIGIN` | intuition ou consigne opérateur, jamais mesurée | |
| `OBSERVATION` | fait constaté | |

**`VALIDATED_RESULT` est vide, et il doit le rester tant qu'aucun test hors
échantillon n'a été fait.** Les 121 commentaires ont été relus : aucun ne porte
de validation hors échantillon positive. Le seul élément réellement hors
échantillon trouvé dans tout le corpus est une **réfutation** — le trailing à
8 % de late_bonding, optimum de 360 combinaisons rejouées sur les 100 meilleurs
trades, s'est effondré en 3 h de production réelle. Deux commentaires disent
eux-mêmes l'inverse d'une validation : `solana_pump.MIN_RESERVE_USD_AT_ENTRY`
(« UNVALIDATED out-of-sample… textbook overfitting risk ») et
`late_bonding.MAX_TOP_BUYER_SHARE` (« the PnL gain does NOT survive
cross-validation »). Cohérent avec le verdict global **NO EVIDENCE OF EDGE**.

## Trois découvertes structurantes

### 1. Un parent statistique commun, jamais déclaré

La mesure « 1,8 % des trades portent 100 % du gain » (46 trades sur 2522, dôme
Solana entier) est l'argument porteur d'**au moins cinq décisions classées
séparément** : `HOLDER_CONCENTRATION_REJECT_PCT` (dans fast_discovery ET
ws_exit), `TRAILING_STOP_ARM_PEAK_PCT`, `HARD_STOP_PCT_DEFAULT`, et
`late_bonding.MIN_LIQUIDITY_USD`.

Ces cinq constantes ne sont pas indépendantes : **si cette statistique est un
artefact d'échantillon, elles tombent ensemble.** C'est le cas d'école que le
champ `MULTIPLICITÉ` existe pour attraper, et il n'était écrit nulle part.

### 2. Deux hypothèses frontalement contradictoires coexistent

Neuf constantes posent un **plancher** de liquidité à l'entrée (de 1 000 $ à
25 000 $ selon la poche). Une seule pose un **plafond** :
`ws_exit.MAX_LIQUIDITY_USD_ENTRY = 5000`.

Une poche cherche donc la liquidité haute pendant qu'une autre cherche la
basse, sur des populations qui se recouvrent. Ce ne sont pas deux réglages
d'un même paramètre : ce sont deux hypothèses de marché opposées, et au moins
l'une des deux est fausse. Aucune n'a jamais été confrontée à l'autre.

### 3. Bonding progress et liquidité sont entanglés — confirmation d'un
falsificateur déjà pré-enregistré

Le code de late_bonding le documente lui-même : « the SAME liquidity floor that
was supposed to protect this band was ALSO lowered 5500$ -> 4000$ the same day
-- so the one thing the test's justification leaned on never actually held ».

Les deux constantes ont bougé les mêmes jours et ont été re-vérifiées sur les
mêmes échantillons (578 puis 1609 clôtures). **Leurs gains respectifs ne
s'additionnent pas et aucun des deux n'est attribuable seul.**

C'est la confirmation directe, par le code lui-même, du falsificateur n°4
inscrit dans [H-BONDING-PROGRESS.md](H-BONDING-PROGRESS.md) avant d'avoir lu
ce commentaire (« Effect is actually a liquidity proxy in disguise — not yet
decomposed »). Ce falsificateur passe donc de « à tester » à « déjà
partiellement démontré » : toute reprise de cette hypothèse doit décomposer les
deux effets avant de revendiquer quoi que ce soit.

## Les regroupements (18 groupes, ~16 phénomènes après fusion)

Groupes principaux, avec le nombre de constantes qu'ils absorbent :

| Phénomène | Constantes | Remarque |
|---|---|---|
| Plancher de liquidité à l'entrée | 9 | valeurs irréconciliables (1k$ → 25k$) |
| Prise de profit étagée (scale-out) | 9 | |
| Gate de régime marché | 12 | dont 4 copies de `REGIME_WINDOW` |
| Trailing stop | 8 | |
| Timeout de détention | 8 | |
| Fenêtre d'âge du pool | 6 | valeurs irréconciliables (5 → 120 min) |
| Sortie sur effondrement de liquidité | 5 | même valeur, jamais calibrée nulle part |
| Concentration des holders | 4+ | échantillons qui se recouvrent |
| Surge M5 à l'entrée | 4 | tous issus de la même passe Dune du 16/08 |
| Simulation de friction d'exécution | 6 | |
| Garde-fous de sanité de prix | 3+ | **jamais des hypothèses** |
| Stop dur / stop fixe | 4 | un seul contrefactuel (304 clôtures) réutilisé partout |

**Fusion supplémentaire imposée par l'audit** : les groupes « trailing »,
« scale-out » et « timeout » traitent séparément des constantes que le code
déclare dans **un seul bloc**, sous un commentaire unique (« The CALIBRATED
exit rule itself, 16/08 Dune backtest »). C'est UNE règle de sortie, pas trois
mécanismes indépendants.

**Groupes manquants ajoutés par l'audit** : la maturité « post-bonding » de
dip_recovery_v2 (`MIN_MARKET_CAP_USD` + `MAX_MARKET_CAP_USD` +
`MIN_POOL_AGE_DAYS` = une seule hypothèse de stade), et la migration/graduation
comme meilleur résultat (`MAX_BONDING_PROGRESS` +
`EXEMPT_GRADUATED_FROM_MAX_HOLD`).

## Erreurs de classification corrigées

L'audit a trouvé 15 erreurs. Les plus importantes :

**Type 1 — un garde-fou promu en hypothèse** (l'erreur nommée d'avance par
l'opérateur) : `solana_late_bonding.REGIME_WINDOW = 30`, classé
`D_MOVEMENT / EXPLORATORY_RESULT`, est en réalité le plancher de taille
d'échantillon du capteur de régime. Vérifié dans le code
(`solana_late_bonding_shadow.py:823`) : `if len(usable) < REGIME_WINDOW: return
None` — en dessous de 30 échantillons, aucun verdict n'est rendu. Un garde-fou
anti-circularité s'était vu attribuer le résultat mesuré de sa constante
voisine. Reclassé `NONE / SAFETY_RULE`.

**Type inverse — une règle de sortie déguisée en filet** :
`dip_recovery_v2.MAX_HOLD_HOURS = 168`, classé `SAFETY_RULE`. Mais cette poche
n'a **pas de stop-loss** : une position ne peut se clore que par take-profit
+25 % ou par ce timeout. Ce n'est pas un filet, c'est la moitié de la règle de
sortie et un déterminant direct du PnL. Reclassé `A_TIMING / ORIGIN`, avec son
volet protecteur conservé en note.

**Défaut d'outillage, le mien** : mon script de capture lisait le commentaire
contigu à chaque *ligne*, alors que ce code déclare ses constantes en *blocs*
sous un commentaire unique. 37 constantes étaient donc rapportées comme non
justifiées. Après correction : 84 ont leur propre commentaire, 35 sont couvertes
par un commentaire de bloc, **2 seulement** n'ont rien (`CHAINS`,
`CHECKPOINT_TABLE`, toutes deux techniques). Aucune constante de stratégie
n'est non documentée. Le chiffre faux avait déjà été diffusé ; il est corrigé
partout.

## Ce que ce registre ne dit pas

Il classe des intentions, pas des résultats. Aucune ligne ici ne mesure si un
seuil gagne de l'argent — la couverture temporelle réelle des poches interdit
d'ailleurs de le prétendre (mesuré le même jour : `fast_discovery` a 2021
clôtures mais sur **3 jours distincts**, `ws_exit` 1078 sur 3 jours, et
`solana_late_bonding` 37 sur 1 seul jour). Un n élevé n'y est pas une
couverture.

La suite n'est pas d'optimiser un de ces seuils. C'est de prendre **une**
hypothèse déjà suffisamment documentée, lui donner son falsificateur, son
contrefactuel, sa cohorte gelée et son `config_hash`, puis la faire passer dans
le laboratoire de Phase 2. La première est déjà figée :
[H-BONDING-PROGRESS.md](H-BONDING-PROGRESS.md) — et la découverte n°3 ci-dessus
vient d'en durcir un falsificateur avant même qu'il soit testé.
