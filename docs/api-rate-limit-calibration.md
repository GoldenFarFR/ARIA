# Calibration des débits API — inventaire complet (21/07)

> Référencé depuis `CLAUDE.md` (Normes permanentes, « Débit calibré à 90% de la capacité
> réelle »). Décision opérateur explicite, 21/07 : chaque client d'API externe doit être
> throttlé à ~90% de sa vraie capacité soutenue — ni trop prudent, ni trop agressif.
> Méthode : doc officielle du fournisseur en premier, **vérification empirique en rafale
> contrôlée** quand la doc est absente, ambiguë ou contradictoire (deux fois nécessaire
> ce jour-là — GoPlus et DexScreener). Jamais un chiffre deviné ou recopié de mémoire.
>
> Ce fichier est un inventaire de référence, mis à jour à chaque recalibration. Il ne
> remplace pas le commentaire source à côté de chaque constante de throttle dans le
> code — les deux doivent rester cohérents.

## Tier 1 — pipeline momentum (fort volume, chemin critique du trading)

| Service | Débit réel (source) | Confiance | Throttle actuel | Cible 90% | Action |
|---|---|---|---|---|---|
| GeckoTerminal (Demo key) | 30 req/min | Confirmé (doc officielle, recherche 19/07) | 2,1s (28,6/min, 95%) | 2,222s (27/min) | Léger ralentissement |
| DexScreener (profils/boosts) | 60 req/min | Confirmé verbatim (doc officielle) | aucun → **1,111s implémenté** | 1,111s (54/min) | Fait (21/07) |
| DexScreener (pairs/tokens/search) | ~300 req/min | Probable, non confirmé verbatim (doc + rafale de 25 req/1,1s réussie sans erreur, mais ceiling réel non atteint) | aucun → **1,111s implémenté (même throttle client, pas de split)** | 0,222s (270/min) serait la cible SI confirmé, mais un seul point d'entrée (`_get_json`) sert tous les endpoints du module — calibré sur le chiffre le plus bas et le seul confirmé (60/min) plutôt que de risquer un dépassement sur les endpoints profils/boosts. | Fait (21/07), conservateur assumé |
| GoPlus (token_security) | **150 CU/min confirmé sur le dashboard réel du compte** (gopluslabs.io/dashboard, palier Free) -- mais GoPlus facture PAR TOKEN, pas par appel : Token Security API = 15 CU/token (EVM), 30 CU/token (Solana). `get_token_security()` interroge 1 seul contrat par appel -> 1 appel = 15 CU sur Base -> **10 req/min réelles**. Explique a posteriori le test empirique du même jour (bloqué à la 11e requête = 150/15 = 10 tokens exactement) | **Confirmé au plus haut niveau (dashboard du compte)** | 0,5s (120/min) initial -> 1,212s (calibrage erroné, basé sur une mauvaise lecture du test empirique) -> **6,667s (~9/min)** | 6,667s (9/min) | **Corrigé deux fois le même jour -- la structure de facturation par token, pas par appel, était l'angle mort** |
| Blockscout Pro | 5 req/s | Confirmé (doc + header `x-ratelimit-limit:5` vérifié en direct) | 0,2s (100%, zéro marge) | 0,222s (4,5/s) | Léger ralentissement |
| Blockscout gratuit (`base.blockscout.com`, chemin de repli, inactif tant que la clé Pro est valide) | 3 req/min documenté pour `api.blockscout.com` (produit différent, pas confirmé applicable à `base.blockscout.com`) | Non confirmé pour ce domaine précis | 0,35s (171/min) | Inconnu | Chemin mort en pratique — retester si la clé Pro venait à manquer |
| CoinMarketCap Pro (Basic) | 50 req/min | **Confirmé en direct** via `/v1/key/info` sur la vraie clé configurée | aucun | 1,333s (45/min) | Nouveau throttle |
| CoinGecko (Demo, `/simple/price`) | 100 req/min, 10 000 crédits/mois | Confirmé (2 sources officielles indépendantes) | 2,2s (27,3/min, 27% utilisé) | 0,667s (90/min) | **Accélération** (vérifier empiriquement avant déploiement, vu l'écart doc/réalité déjà observé ailleurs) |
| Mobula | 1 req/s, 10 000 crédits/mois | Confirmé (doc officielle) | 1,05s (95,2%) | 1,111s (0,9/s) | Léger ralentissement |

## Tier 2 — x402 et fournisseurs de données secondaires

| Service | Débit réel (source) | Confiance | Throttle actuel | Cible 90% | Action |
|---|---|---|---|---|---|
| Dune Execute SQL (Free) | 15 req/min (limite basse, contraignante) + 40/min (limite haute, compteur séparé) | Confirmé (doc officielle) | aucun | 4,44s (13,5/min) | Nouveau throttle |
| Tavily Search | **22/07, corrigé sur dashboard réel (billing) : PAS de rate-limit en req/min publié nulle part** — structure réelle = budget MENSUEL en crédits (plan "Researcher"/gratuit = 1000 crédits/mois, 1 crédit/recherche basique, 2/avancée). L'ancien chiffre "Dev=100/min, Prod=1000/min" de ce tableau était une confusion (mélange type-de-clé vs plan d'abonnement), jamais confirmé sur un vrai relevé | **Corrigé (dashboard réel, 22/07)** — le "confirmé" précédent était faux | 0,5s (120/min) — **chiffre sans fondement réel, aucun débit connu à respecter** | — | Pas un sujet de débit du tout — voir plutôt la note "usage mensuel" ci-dessous |
| RugCheck.xyz | Aucune limite publiée | Absence confirmée (Swagger officiel vérifié) | aucun | — | Backoff réactif seul, capacité inconnue |
| Farcaster/Warpcast | Aucune limite publiée | Absence confirmée | aucun | — | Backoff réactif seul, capacité inconnue |
| DefiLlama (gratuit) | Aucun chiffre publié (contrairement au tier payant, chiffré à 1000/min) | Absence confirmée | aucun | — | Backoff réactif seul, capacité inconnue |
| Polymarket Gamma | 4000 req/10s général, 500/10s `/events`, 300/10s `/markets` | Confirmé (doc officielle) | 2,0s (30/min) — très en dessous du plafond réel | 0,0222s (2700/min sur `/events`) | Marge énorme déjà, aucune urgence — usage trop faible pour que ça compte |
| twit.sh / Otto AI / Cybercentry (x402) | Aucune limite publiée — seul frein réel : le coût par appel | Absence confirmée (les 3) | — | — | Le plafond `x402_budget.py` (5$/semaine) sert déjà ce rôle, rien à ajouter |
| CDP x402 Discovery/Bazaar | Aucun chiffre officiel publié ; un rapport tiers non officiel suggère un seuil plus bas que le générique CDP (600/10s, probablement non applicable) | Faible — un seul rapport, non reproduit | aucun | — | Capacité inconnue, usage actuel faible/dormant |

## Tier 3 — API officielles bas-volume

| Service | Débit réel (source) | Confiance | Throttle actuel | Cible 90% | Action |
|---|---|---|---|---|---|
| GitHub REST (PAT fine-grained) | 5000 req/h | Confirmé (doc officielle) | aucun | 0,8s | Usage réel trop faible pour que ça compte, aucune urgence |
| Telegram Bot API (chat privé) | 1 msg/s par chat | Confirmé (doc officielle) | à vérifier (`host_hooks.check_rate_limit`, portée pas confirmée Telegram-spécifique) | 0,9s | Vérifier si un pic de notifications pourrait un jour dépasser ce rythme |
| Virtuals Protocol API | Aucune limite publiée | Absence confirmée | aucun | — | Capacité inconnue |
| x.ai Management API | Aucune limite publiée pour l'API elle-même | Absence confirmée | aucun | — | Usage horaire, capacité inconnue sans risque réel |
| Clanker API | Aucune limite publiée | Absence confirmée | aucun | — | Capacité inconnue |
| Blockchain.info | Chiffre historique "1/10s" non retrouvé sur la doc actuelle | Non confirmable | aucun | — | Usage quasi nul en pratique |
| Base RPC public (`mainnet.base.org`) | Aucun chiffre publié — doc officielle déconseille explicitement l'usage en production, recommande un fournisseur dédié | Absence confirmée + recommandation officielle de ne pas s'y fier | — | — | Feature qui l'utilise (graduation Virtuals) déjà gate OFF — recommandé de passer par un fournisseur RPC dédié si jamais activée |

## Méthode de vérification empirique utilisée (à réutiliser)

Pour GoPlus et DexScreener (doc absente/contradictoire), rafale de 20-25 requêtes
back-to-back (sans délai artificiel) contre l'endpoint réellement utilisé en production,
avec adresses de contrat variées (jamais la même, pour éviter un cache qui fausserait le
test). Observation : code de statut, corps de réponse (certains fournisseurs comme GoPlus
signalent leur rate-limit via un HTTP 200 avec un code d'erreur dans le corps, pas un vrai
429 HTTP), et pour GoPlus un test de récupération (attente puis nouvelles requêtes
espacées) pour mesurer le temps de reconstitution du quota.

## Deux familles de contrainte, jamais confondues (22/07, découverte Blockscout + Tavily)

Ce fichier a longtemps traité toute limite comme un DÉBIT (req/s ou req/min, protège
contre un blocage 429 en cas de rafale). Deux fournisseurs révèlent une famille
DIFFÉRENTE : un BUDGET CUMULÉ sur une période (crédits/jour pour Blockscout Pro,
crédits/MOIS pour Tavily) — un débit "sage" ne protège en rien contre l'épuisement de ce
budget si le VOLUME total dépasse ce qui est alloué sur la période. Pire, pour Tavily
("Researcher"/gratuit), la doc du fournisseur confirme explicitement "unused credits do
not roll over to the next month" -- un budget mensuel NON consommé est une capacité
définitivement perdue, jamais reportée. Deux implications opérationnelles distinctes :
un budget en CRÉDITS/JOUR (Blockscout) mérite un throttle proactif comme celui déjà
construit (`blockscout_credit_budget.py`) pour ne jamais le DÉPASSER ; un budget en
CRÉDITS/MOIS avec perte à la fin de période (Tavily) pose la question inverse -- s'assurer
qu'une VRAIE utilité disponible n'est pas laissée de côté par manque de câblage, jamais en
forçant une consommation artificielle sans valeur (bruit inutile, pollution de mémoire).
Réflexe à généraliser : avant de calibrer un débit pour un nouveau fournisseur, vérifier
D'ABORD sur son dashboard/sa doc de facturation s'il s'agit d'un débit instantané, d'un
budget cumulé avec reset, ou d'un budget cumulé SANS report -- les trois se calibrent
différemment.
