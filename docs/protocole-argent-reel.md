# Protocole « feu vert argent réel » — le pacte

> **Pacte fondateur (Sylvain × l'analyste honnête d'ARIA).** L'argent réel n'entre
> QUE le jour où ARIA a **prouvé**, sur des critères fixés ICI et à l'avance, qu'elle
> mérite la confiance. Ce barème est **pré-engagé** : on ne l'assouplit jamais après
> coup pour « aller plus vite ». Le rôle de l'arbitre (l'analyste d'ARIA / l'assistant)
> est de dire NON tant que toutes les cases ne sont pas cochées — c'est précisément
> ce qui rend le OUI crédible le jour venu.
>
> Statut actuel : **AUCUN argent réel.** Le portefeuille d'ARIA est **suivi (paper)**,
> valorisé aux vrais prix on-chain, pour accumuler la preuve sans risque.

---

## 1. Le principe

- « Je lui fais confiance à 90 % les yeux fermés » ≠ all-in aveugle. Ça veut dire :
  **petit ticket de départ, œil toujours ouvert**, puis montée en taille au fur et à
  mesure que la preuve tient. On ne confie jamais d'un coup une somme qu'on n'est pas
  prêt à perdre entièrement (crypto = risque total).
- La signature finale reste **humaine** (Tangem). ARIA est autonome dans le cerveau
  (analyse, décision, stratégie suivie), **jamais** sur le bouton qui déplace l'argent.
- La stratégie suivie/documentée : **85 % VC moyen-long terme + 15 % spéculation
  small-cap filtrée on-chain** (jamais du hype à l'aveugle — cf. $HOLO = archétype du
  15 % *qui passe le filtre*, pas d'un pari au hasard).

## 2. Les 8 cases à cocher AVANT tout argent réel (toutes requises)

1. **Échantillon suffisant** : ≥ **80 verdicts résolus**, étalés sur ≥ **6 mois**.
   (En dessous, c'est de la chance, pas de la compétence.)
2. **Track record complet et inviolable** : **aucun** verdict effacé ou caché ; chaque
   appel horodaté + **empreinte SHA-256**, idéalement **ancrée on-chain** (hash sur Base).
   Reproductible par un tiers.
3. **Calibration prouvée** : la courbe de calibration est **monotone** (un « 8/10 »
   surperforme réellement un « 5/10 ») et le **hit-rate BUY est nettement supérieur au
   hasard APRÈS frais** (gas, slippage).
4. **Bat un benchmark honnête** : la stratégie 85/15 bat, sur la même période, à la fois
   (a) « hold ETH » et (b) une **sélection aléatoire** de tokens comparables.
5. **Robustesse anti-chance** : on **retire les 2 meilleurs coups** → la performance
   reste positive. Pas de survivorship, pas de cherry-pick, pas « un moonshot qui sauve
   tout ».
6. **Risque maîtrisé** : le sleeve 15 % **n'a pas explosé** ; drawdown maximum
   raisonnable ; et surtout les **AVOID ont réellement évité des pertes** (le token
   déconseillé a bien chuté).
7. **Proof engine cohérent** : l'auto-audit (juge adverse) est **bien calibré**, pas
   complaisant — il attrape réellement les erreurs d'ARIA.
8. **Feu vert avocat** sur la structure d'argent réel retenue (le tien vs. tiers ; cf.
   `docs/conformite-dossier-avocat.md`). Un fonds pour compte de tiers reste le mur le
   plus lourd et n'est pas le point de départ.

## 3. La montée en taille — en DEUX étapes séquentielles (décision opérateur, 09/07)

L'argent réel ne se déploie **pas d'un coup sur les deux poches**. Il se débloque
poche par poche, dans cet ordre, et **chaque étape rejoue le barème complet du §2**
sur son propre track-record (pas une seule fois au global) :

- **Étape A — VC réel (poche 85 %) débloqué en premier.** Une fois les 8 cases du §2
  cochées sur le track-record **paper**, l'argent réel démarre **uniquement** sur la
  poche VC (positions moyen/long terme). La poche spéculation (15 %) **reste en paper**
  pendant toute cette étape, même si le VC réel tourne déjà.
- **Étape B — Trading réel (poche 15 %) débloqué ensuite, jamais avant.** Ne s'ouvre
  que lorsque l'étape A a, à son tour, **rejoué les 8 cases du §2** — cette fois sur le
  track-record du **VC réel** (pas le paper d'origine). Même rigueur, deuxième passage,
  sur de l'argent réel cette fois. Tant que ce second barème n'est pas rempli, la poche
  15 % reste paper.
- La poche 15 % réelle, une fois débloquée, reste la **même poche** que celle documentée
  ailleurs (aucun capital séparé, aucune structure parallèle) — juste alimentée en réel.

**Palier de taille, à l'intérieur de chaque étape :**
- **Palier 0** : petit ticket (quelques centaines de $ **de ton capital**, jamais celui
  d'un abonné), œil ouvert, revue à chaque cycle.
- **Paliers suivants** : la taille n'augmente que si la preuve **continue** de tenir sur
  argent réel (la performance paper doit se confirmer en réel — le slippage réel est le
  juge de paix).
- **Clause d'arrêt** : toute rupture d'une case du §2 (drawdown anormal, track record
  trafiqué, calibration qui s'effondre) → **retour au paper sur l'étape concernée**,
  sans discuter. Une rupture sur l'étape B ne remet pas en cause l'étape A si celle-ci
  reste saine (les deux poches sont jugées indépendamment une fois débloquées).

## 4. Ce qui mesure tout ça

Le moteur de track-record (`aria_core/vc_predictions.py`, voûte 2) : il enregistre
chaque verdict (prix d'entrée, pool, poche 85/15), résout les issues au vrai prix OHLCV,
et calcule calibration + valeur du portefeuille suivi. **C'est cette machine qui produit
la preuve** sur laquelle le feu vert se décidera. Tant qu'elle n'a pas tourné assez
longtemps, la réponse est **non** — et ce non est une fonctionnalité, pas un défaut.
