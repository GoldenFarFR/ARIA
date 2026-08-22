# Plan — la chaîne complète, de la découverte à la vente

> Écrit 2026.08.22 après la première nuit de trading réel : sept défauts, dont
> quatre trouvés par l'opérateur en regardant son wallet plutôt que les tableaux
> du système. Objectif : que chaque étape soit fiable seule ET que l'enchaînement
> ne puisse plus se casser en silence.
>
> Ce document est le plan d'ensemble. Le détail du coordinateur de débit vit
> dans `plan-throughput-coordination-solana.md`, l'historique par composant dans
> `HANDOFF_SOLANA_TRADE_PILOT.md`.

## La fondation : une seule porte vers Solana

Tout le reste en dépend, donc c'est le premier chantier.

**Constat de la diligence** (inventaire réel, 22/08) : 8 modules résolvent un
endpoint Solana, 6 font des appels directs, 13 appels au total, et **2 throttles
concurrents** s'ignorent (`pumpfun_curve_tracker` à 22 rps, `jupiter` à 0,35 s).

**Preuve que la discipline ne suffit pas** : le script de liquidation écrit
trois heures APRÈS avoir documenté la règle a reproduit le bug — il choisissait
un endpoint pendant que le module de portefeuille en résolvait un autre, d'où
`balance unreadable` sur les 32 ventes. Si l'auteur de la règle l'enfreint le
jour même, aucune convention ne tiendra.

**Ce qu'il faut donc :**

1. `services/solana_gateway.py` — le SEUL point qui parle à Solana. Il détient
   la liste des endpoints (payants puis publics), bascule automatiquement quand
   l'un meurt, applique le débit partagé (`solana_rpc_budget`, déjà construit et
   testé) et les priorités, et gère le recul sur 429 une fois pour tous.
2. Les 13 appels directs passent par lui.
3. Les 2 throttles concurrents sont **supprimés**, pas laissés à côté.
4. **Un test de cohérence qui échoue** si un appel RPC apparaît hors de la
   passerelle. Même patron que le garde-fou existant sur les actions externes.

Sans le point 4, ça régresse au premier ajout — c'est le seul point non
négociable de ce plan.

## Le parcours, étape par étape

### 1. Découverte

**Existe** : `pumpfun_curve_tracker` suit ~300 tokens par bandes de progression,
en sondage groupé (100 comptes par appel, 1 crédit).

**Fragile** : sur épuisement du fournisseur, `consider_candidate` refusait 472
candidats d'affilée sans jamais signaler que le fournisseur était mort. Corrigé
à chaud, mais le signalement est encore réparti dans chaque module.

**Cible** : la passerelle porte le signalement. La découverte n'a plus à savoir
quel fournisseur est vivant.

### 2. Évaluation d'un candidat

**Existe** : filtres de bonding (70-98,5 %), liquidité minimale, honeypot,
route de sortie vérifiée par un aller-retour Jupiter.

**Contrainte connue, non résolue** : la bande 50-70 % du suivi ne peut PAS être
ralentie — un token la traverse en ~26 s mesurées, et une cadence plus lente
fait échouer tous les candidats sur `MIN_DISTINCT_BUYERS`. C'est le poste le
plus coûteux et il est incompressible.

### 3. Achat

**Existe et fonctionne** : ~90 ms mesurés de bout en bout (prix en cache, devis,
décimales en cache, frais calibrés sur le réseau, construction, simulation,
signature, envoi sans attendre de slot).

**Acquis de la nuit** : le prix payé est enregistré avec ses décimales (bug ×10⁶
corrigé), le hash d'achat est stocké dans une colonne dédiée — c'est le seul
marqueur fiable qu'une position a coûté de l'argent.

### 4. Suivi du prix

**Existe** : prix lu depuis les réserves de courbe (source primaire), toutes les
2 s, avec repli DexScreener pour les tokens gradués. Mesuré à 385 ms de médiane.

**Acquis** : la boucle dort quand aucune position n'est ouverte — elle brûlait
43 200 appels/jour pour surveiller deux ou trois positions.

### 5. Vente

**Existe** : `sell_fn` sur les deux voies de sortie, réessai avec devis frais sur
échec de slippage, délai de 30 s par position après un échec.

**Acquis, chèrement** : une vente qui échoue ne doit jamais devenir une clôture
en base (piège FOMO, -82 % réels), et une position shadow ne doit jamais
atteindre le portefeuille (boucle infinie, 847 erreurs).

### 6. Réconciliation et nettoyage

**Existe** : réconciliation table/chaîne toutes les 45 s, récupération des
cautions toutes les 30 min (a rendu 0,39 $ en réel).

**Acquis** : seule la CHAÎNE peut déclarer un achat échoué. Déduire l'échec de
l'absence de tokens annulait 67 % des vrais achats.

## Les contraintes à trancher ensemble

### A0. `getProgramAccounts` est INTERDIT sur le plan gratuit — cause réelle

Mesuré sur le tableau de bord Chainstack (22/08) : quota à **2 % utilisé**, mais
**21 % des requêtes refusées** -- 11,19 % en 403 et 10,68 % en 429. Test direct :

    getHealth            -> 200
    getBalance           -> 200
    getProgramAccounts   -> 403

La méthode elle-même est bloquée sur le plan gratuit, indépendamment du volume.
Elle est appelée par `pumpswap_ws` pour résoudre le pool d'un token migré, et
elle échouait EN BOUCLE -- ces échecs ont été comptés comme de la saturation
pendant une bonne partie du diagnostic de la nuit.

**Conséquence** : une part importante des refus n'était pas un problème de
débit. Il faut soit remplacer cet appel (dériver l'adresse du pool plutôt que
la chercher, comme `pumpfun_curve_price.curve_address` le fait déjà pour les
courbes), soit le router vers un endpoint qui l'autorise. La passerelle rend le
second trivial : une méthode peut être épinglée à un endpoint donné.

### A. L'accès RPC — bloquant, à régler en premier

Helius : quota mensuel épuisé (429). Chainstack : 403. Les endpoints publics
répondent mais saturent vite.

Aucun code ne répare ça. **Trois options :**

| Option | Coût | Ce que ça implique |
|---|---|---|
| Attendre la réinitialisation Helius | 0 | trading à l'arrêt jusque-là |
| Comprendre le 403 Chainstack | 0 | peut-être une clé à régénérer |
| Ajouter un 3e fournisseur gratuit | 0 | plus de résilience, à évaluer |

Le budget mesuré est de ~49 000 appels/jour contre 100 000 offerts : **il n'y a
pas de problème de volume**, donc pas de raison de payer.

### B. La nervosité des sorties — à décider

L'actualisation à 1-2 s a fait passer la détention moyenne de **109 s à 8 s**.
Ce n'est pas un réglage choisi, c'est un effet de bord : voir le prix plus
souvent fait toucher le stop suiveur plus tôt.

Observé sur un petit échantillon : la perte de -8,3 % a été coupée en 1 seconde
(voulu), mais un +97 % n'a tenu que 24 s (peut-être coupé trop tôt).

**À trancher** : élargir le stop suiveur pour compenser la nouvelle vitesse
d'observation, ou accepter des sorties plus nerveuses. Décision opérateur, à
prendre sur un échantillon post-correctifs, pas sur les chiffres du 21/08 qui
sont périmés.

### C. Le shadow est structurellement optimiste — à garder en tête

Le shadow suppose un remplissage instantané. Le réel prend ~90 ms aujourd'hui
(contre 13 s avant), mais jamais zéro. Tout écart réel/shadow doit être lu avec
ça en tête, et les analyses du 21/08 sont à refaire sur des données récentes.

## Ordre d'exécution proposé

1. **Rétablir un fournisseur RPC** (contrainte A) — sans ça rien ne tourne.
2. **Construire la passerelle** + le test qui la rend obligatoire.
3. **Brancher un appelant à la fois**, du moins risqué (valorisation) au plus
   risqué (vente), en vérifiant le débit après chacun.
4. **Rallumer le trading** et accumuler un échantillon propre.
5. **Alors seulement** trancher la contrainte B, sur des données réelles.

Rien de tout ceci ne touche aux garde-fous : plafond de 0,10 $ par trade,
slippage à 10 %, kill-switch, gates. Ils restent inchangés.
