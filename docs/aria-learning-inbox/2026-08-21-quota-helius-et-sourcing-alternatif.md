# Quota Helius epuise en 2 jours -- recherche de fond sur le sourcing

> 21/08. Demande operateur : "laisse en l'etat et trouve une solution a long
> terme, un autre service, un autre RPC... explore toutes les pistes connues
> et inconnues", puis "fais un vrai test simule avant de cabler, si ca resout
> toute la chaine et si ca tiendra". Cette fiche est le resultat. Rien n'a ete
> cable : le test a montre qu'aucune piste ne resout tout sans contrepartie.

## Le declencheur

Plan Helius GRATUIT (1M credits/mois). 801k consommes en DEUX jours, dont
579k le seul 21/08, a 92% par le WebSocket. Environ 8 heures de quota
restantes au moment de la mesure. Le plan gratuit n'offre par ailleurs NI
Staked Connections (SWQoS) NI LaserStream gRPC.

## Le diagnostic, et il n'est pas celui qu'on croit

La consommation vient d'UN seul abonnement : `logsSubscribe` program-wide sur
pump.fun, qui recoit **tous les trades de toute la plateforme** -- environ
5,7 millions d'evenements par jour pour en exploiter quelques centaines.

**Changer de fournisseur ne resout RIEN.** Verifie par le calcul : chez
Alchemy (30M CU gratuits, facturation a la bande passante, ~40 CU par 1000
octets), ce meme flux epuiserait le quota en **1 a 4 heures** -- soit bien
pire que Helius qui tient 1,7 jour. Le probleme est le VOLUME, pas le prix.

## Ce que le flux coûteux apporte reellement -- mesure, pas suppose

| usage | role reel |
|---|---|
| `active_mints()` | DECOUVERTE des candidats -- critique |
| `distinct_buyers` | filtre `MIN_DISTINCT_BUYERS=1` |
| `top_buyer_share` | filtre `MAX_TOP_BUYER_SHARE=0.95` |
| buyer_acceleration, sell_pressure, sol_velocity, cohorte fondatrice | COLLECTE seule |

**Sur 3948 rejets en 6 heures, ZERO venait des deux filtres alimentes par ce
flux** : leurs seuils sont si relaches qu'ils ne rejettent jamais rien. Le
flux coute donc 92% du quota pour la decouverte plus des metriques qui ne
decident de rien aujourd'hui.

## Pistes testees EN DIRECT

- **PumpPortal** (gratuit, sans cle, deja cable) : mesure live, **3058
  creations/heure, 42 Mo/jour**, champs `mint`/`bondingCurveKey`/
  `marketCapSol`/`initialBuy`. Donne la CREATION, jamais la progression. Or
  seuls ~3% de ces tokens atteindront la bande d'achat : les suivre tous
  demanderait des milliers d'abonnements, pire que le probleme actuel.
- **Alchemy** : cle deja presente dans le projet et fonctionnelle, mais
  `SOLANA_MAINNET is not enabled` sur l'app (une case a cocher). 30M CU
  gratuits, dont 7,4M deja consommes. Inutilisable pour CE flux (cf. calcul
  ci-dessus), **utile en revanche pour les appels RPC ponctuels** et comme
  seconde jambe si un fournisseur tombe.
- **REST DexScreener / GeckoTerminal** : teste en direct, ne sait PAS lister
  par progression de courbe. DexScreener renvoie 1 paire pumpfun sur 30 pour
  une recherche "pump" ; GeckoTerminal ne donne que les nouveaux pools.
- **Autres niveaux gratuits** releves : QuickNode 10M/mois, Syndica 10M,
  Chainstack 3M, Alchemy 30M. Tous se heurtent au meme mur du volume.

## LA piste retenue : un fournisseur specialise qui filtre A LA SOURCE

**Bitquery** expose directement la progression de courbe, avec la formule
`100 - (((Pool_Base_Balance - 206900000) * 100) / 793100000)`. Le
`793 100 000` est **exactement notre `INITIAL_CURVE_TOKENS`** : notre calcul
local et le leur sont identiques, donc les valeurs sont comparables sans
conversion. Une requete d'exemple "tokens entre 95% et 100%" existe deja dans
leur IDE ; notre bande est 70-98,5%. Acces REST, WebSocket, gRPC ou Kafka,
filtrable par le programme `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`.

Alternatives de meme famille, non testees faute de compte : **Anaxer** (flux
creations + graduations gratuits, <450ms, filtres de liquidite integres,
payant a partir de 39$/mois) et **Solana Tracker** (2500 requetes REST
gratuites/mois, trop peu pour du continu).

## Ce qu'on perdrait, et c'est la contrainte posee par l'operateur

Sa consigne : "aucune degradation n'est toleree par rapport a ce qui est deja
branche". Bilan honnete :

- **Decisions d'entree/sortie : AUCUNE degradation** (les filtres concernes
  ne filtrent rien).
- **Collectes exigeant d'observer un token DES SA NAISSANCE : perdues** --
  cohorte fondatrice, detection de bundle Jito, vitesse de courbe. Elles ne
  decident de rien aujourd'hui, mais ont ete construites le 21/08 et n'ont
  pas encore livre de verdict.

Il n'existe donc **aucune option gratuite sans contrepartie**. Trois voies :
1. fournisseur specialise (preserve la decouverte, perd les metriques de
   naissance) ;
2. plan Developer 49$/mois (ne tient que 17 jours au rythme actuel, donc
   reduire reste necessaire -- et 49$ depasse le seuil operateur de 10-15$
   pour un outil payant) ;
3. flux Helius FILTRE sur les seuls tokens deja suivis (le dashboard confirme
   `accountInclude` cote serveur), avec une decouverte venue d'ailleurs.

## Etat au moment d'ecrire

Operateur cree un compte Bitquery. Rien de cable, rien de degrade, systeme en
marche. Prochaine etape : verifier EN DIRECT avec la cle que la progression de
courbe est exploitable et a quelle cadence, avant tout branchement.

## Bitquery tested live (2026-08-21)

Free account created. Real limits read from the dashboard, not from docs:
1000 API points/month, 17 min of streaming/month, 0.2 GB traffic, 10 req/min,
2 concurrent streams. Endpoint for Solana: `https://streaming.bitquery.io/eap`.

**Our bonding-progress formula is independently validated.** On mint
`3t4NqcEJPxyc3Vm8Zja7accyPFSeFxse122X9LmDpump`, still on the curve:
ARIA read 82.900% at 17:15:44, Bitquery 83.307% at 17:16:53 -- a 0.41 point
gap over 69 seconds, in the direction of the rise. No scale bias, no constant
mismatch. Query latency 0.19 s. Bitquery exposes no quota-cost header; point
consumption is only visible on the dashboard.

**Paid plans do not clear our bar.** Personal 49$/mo has streaming DISABLED
(unusable regardless of the rest). Pro 99$/mo is the first with streaming:
100 000 min/month, i.e. two 24/7 streams (43 200 min each), but caps traffic
at 5 GB -- and that ceiling is unmeasured. The current Helius flow carries
~5.7M events/day; server-side filtering would cut that a lot, but nobody can
say today whether we land at 1 GB or 40 GB per month.

**The free 17 minutes are worth keeping for exactly one purpose**: measuring
the real byte rate of the filtered stream, to extrapolate GB/month and decide
whether 99$/mo is viable. That measurement is free and has not been run yet.
Quota resets 21 September.

**Measured point cost**: 4 requests consumed 10 of the 1000 monthly points,
i.e. ~3 points per bonding-progress query. That caps the free tier at roughly
330 queries/month (~11/day) -- far too few to serve as a fallback when the
Helius quota runs dry (current usage is thousands of calls/day). Bitquery
free is a spot-verification instrument, a few times a month, nothing more.
