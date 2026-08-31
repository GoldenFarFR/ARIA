# Vision — Historical Social/On-chain Attribution Engine (31/08/2026)

**Statut : vision banquée, RIEN construit. Chantier explicitement SÉQUENCÉ
APRÈS l'optimisation RU (décision opérateur : « une fois que tu as optimisé
à 100% et que je t'ai confirmé, tu passes sur ce chantier en parallèle de
l'on-chain en cours »).** Ne démarrer qu'après confirmation explicite.

## Doctrine centrale, non négociable

> Tout doit fonctionner **par justification, jamais par hasard** — exactement
> la méthode qui vient de révéler `token_is_currency0`, `creation_block` et
> Mint/Burn avant qu'ils ne contaminent la suite.

Interdit : `API disponible -> on la branche -> on regarde si ça marche`.
Obligatoire : `hypothèse -> justification de la mesure -> protocole aveugle
-> données brutes -> contrôle -> analyse -> validation -> intégration`.

**Les API ne dictent jamais la méthodologie.** On part de
`QUESTION -> VARIABLE NÉCESSAIRE -> SOURCE QUI PEUT LA MESURER -> TEST DE
QUALITÉ`, jamais de « voilà tout ce que l'API peut rendre, trouvons un
usage ». `tweet_count` n'a aucune valeur en soi tant qu'on n'a pas énoncé
POURQUOI il représenterait quelque chose.

## Différence fondamentale on-chain / social

```
ON-CHAIN  archive publique quasi native -> exploration retrospective
          exhaustive possible, "montre-moi tout ce qui s'est passe"

SOCIAL    surfaces d'acces externes : API, quotas, cout, profondeur
          historique, pagination, disponibilite. On ne peut PAS dire
          "recuperons tout Internet et on verra".
```

Conséquence architecturale directe : **l'on-chain devient le détecteur qui
déclenche la dépense sociale**, jamais l'inverse — cohérent avec la
doctrine déjà figée (X n'est jamais un déclencheur, seulement une couche de
caractérisation en aval d'un événement on-chain).

```
ON-CHAIN RADAR -> token detecte interessant -> SOCIAL QUERY (ciblee,
fenetre temporelle bornee) -> persistance -> analyse
```

Ordre de grandeur : ~20 tokens intéressants/jour × une recherche X ciblée
sur une fenêtre précise, au lieu d'une surveillance permanente de milliers
de comptes. `twitterapi.io` (`advanced_search`, opérateurs `since:`/
`until:`, ~0,15$/1000 tweets) permet exactement ce ciblage.

## Deux niveaux de collecte, budget contrôlé

- **Niveau 1 — Social Discovery** (peu coûteux) : pour un token qui
  déclenche l'on-chain, compter mentions / auteurs / accélération /
  engagement. Pas d'analyse profonde.
- **Niveau 2 — Social Deep Dive** (seulement si le niveau 1 montre quelque
  chose) : sources, tweets originaux, quotes, auteurs, thèses, liens —
  recherche des sources réellement causales.

## Persister TOUT ce qui est payé — même discipline que le FROZEN on-chain

```
API externe -> RAW SOCIAL FROZEN -> normalisation -> features -> replay
```

Champs minimaux : `tweet_id, timestamp, token, author, text, URL, likes,
reposts, replies, query_used, retrieved_at`. Raison : ne JAMAIS dépendre de
l'API pour refaire une expérience historique.

Couches strictement séparées, mêmes noms que côté on-chain :
`RAW` (exactement ce que l'API a rendu) -> `NORMALIZED` -> `PRIMITIVE`
(mesure directement justifiable) -> `FEATURE` (combinaison à tester) ->
`SIGNAL` (seulement après validation). Empêche `tweet_count` de devenir un
« signal » juste parce qu'il bouge.

## Les 4 types de source à distinguer

```
ORIGIN     la premiere personne qui introduit l'information
AMPLIFIER  n'apporte rien de neuf mais donne de la portee
CONVERTER  transforme l'attention en comportement (thesis + achat +
           renforcement) -- particulierement observable via FOMO
NOISE      beaucoup de volume, peu ou pas de consequence mesurable
```

Distinction des trois surfaces : `X` = attention publique ; `FOMO` =
comportement social + conviction affichée ; `ON-CHAIN` = action réellement
exécutée.

## Graphe de propagation plutôt qu'une liste de tweets

```
Source A -> Source B -> FOMO Trader X -> 20 nouveaux wallets
         -> activity shock -> price
```

Question posée au graphe : *quelle chaîne d'événements apparaît le plus
souvent avant les vrais pumps ?* Et symétriquement pour l'exhaustion :
`pump -> attention -> FOMO -> nouveaux entrants -> peak -> activite ralentit
-> theses changent -> sell flow -> dump`.

## Le contrôle est obligatoire — le social doit être testé contre un témoin

Ne jamais tester `social -> price` seul : un changement social peut SUIVRE
le prix (`prix +20% -> 100 tweets` ne prouve rien). Il faut
`social vs on-chain vs price`, et surtout comparer **gagnants ET
perdants** :

```
premier dataset social : 10 winners / 10 pump-and-dumps / 10 neutres
fenetre : T-60m -> T+60m autour des changements de regime
```

Le but n'est PAS « trouver les comptes qui parlent des winners », c'est
**comparer la propagation sociale qui précède un winner à celle qui précède
un loser**.

## Hypothèses falsifiables, figées avant lecture

```
H0 : le social n'a pas de lead systematique
H1 : le social precede l'on-chain
mesures : lead_social_to_onchain, lead_social_to_price
```

**Même discipline de gel qu'on-chain** : dataset, période, requêtes API,
règles de filtrage, définition des timestamps, fenêtres, métriques et
règles d'inclusion sont figés AVANT de regarder le résultat. En
particulier `T_social` (première occurrence ? première thesis ? premier
cluster ?) se décide à l'avance — jamais la définition qui s'aligne le
mieux après coup, exactement comme `T0`.

## Auditer la source sociale elle-même

Même vigilance que celle qui a trouvé le `creation_block` faux de MSR —
sinon on construira un `social T0` tout aussi faux :

```
X     requete reellement executee, pagination complete, doublons, tweets
      supprimes/indisponibles, timezone, retard de collecte, resultats
      tronques, limites historiques
FOMO  ordre reel, timestamp reel, position ouverte avant/apres thesis,
      capital reellement engage, PnL au moment de l'observation
```

## Evidence chain, jamais un score opaque

Le mot important est **evidence**, pas score :

```
14:03:11  X post #123
14:03:48  FOMO thesis
14:04:02  wallet A achat
14:04:15  new wallets +7
14:04:22  activity acceleration
14:05:01  price +8.2%
```

L'alerte cible ressemble à : *« On-chain : activité +340%, nouveaux wallets
+61%. Social : 3 nouvelles sources indépendantes en 4 minutes. FOMO :
2 thèses avec capital engagé. Séquence historique similaire : 7/10 runners,
1/10 losers, 2/10 neutres. Première divergence : social → FOMO → on-chain.
Sources : [liens]. Risques : liquidité faible, marché global faible. »* —
une justification vérifiable, pas une machine qui prétend savoir.

**Règle absolue** : ARIA ne doit jamais avoir « une opinion » sans pouvoir
montrer quelles données l'ont amenée à cette opinion, quand ces données
sont apparues, et quelles observations historiques justifient leur
importance.

## Barrière d'entrée d'une source dans le moteur

Aucune source ne gagne automatiquement le droit de devenir une feature,
même si elle semble excellente :

```
1. disponibilite  2. fiabilite  3. synchronisation temporelle
4. repetabilite   5. lead/lag mesurable
6. comparaison winners/losers/controls   7. OOS   8. integration
```

## Premier outil concret : `Social Event Collector`

Pas de score, pas de FOMO complet, pas de classement d'influence.

```
Entree : token, timestamp T0
Sortie : mentions X T-60m -> T+60m, auteurs, engagement, URLs, texte,
         premiere apparition
```

Puis comparaison avec `swaps / new traders / flow / price`, pour répondre à
une seule question : *qu'est-ce qui était visible sur X AVANT le mouvement,
et qu'est-ce qui a réellement été suivi d'une réponse on-chain ?* Une fois
20-50 épisodes documentés, on saura si X, FOMO ou certaines chaînes de
propagation valent leur coût d'intégration.

## Les 5 phases

```
PHASE 1  "collectons presque tout" (dans la fenetre d'un evenement)
PHASE 2  "qu'est-ce qui a bouge en premier ?"
PHASE 3  "qui a reellement provoque une reponse ?"
PHASE 4  "quelles sources reproduisent ce phenomene ?"
PHASE 5  "on ne surveille plus tout : on surveille les sources qui
          demontrent un impact"
```

Sortie finale visée, un **Social Impact Rank** par source
(`lead-time median`, `on-chain response %`, `price response %`,
`originality`), jamais une liste de comptes à suivre décidée a priori.

Lien : ce chantier alimente `docs/vision-aria-trader-assistant.md`
(NARRATIVE ENGINE / FOMO INTELLIGENCE), qui reste lui aussi non démarré.
