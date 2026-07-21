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
| GoPlus (token_security) | Doc officielle : 100-150 CU/min (deux pages contradictoires). **Rafale réelle : bloqué dès la 11e requête en 2,7s, ~11s pour récupérer** | **Empirique > doc** | 0,5s (120/min) | **1,212s (~49,5/min)** | **Ralentir significativement — la doc est trompeuse** |
| Blockscout Pro | 5 req/s | Confirmé (doc + header `x-ratelimit-limit:5` vérifié en direct) | 0,2s (100%, zéro marge) | 0,222s (4,5/s) | Léger ralentissement |
| Blockscout gratuit (`base.blockscout.com`, chemin de repli, inactif tant que la clé Pro est valide) | 3 req/min documenté pour `api.blockscout.com` (produit différent, pas confirmé applicable à `base.blockscout.com`) | Non confirmé pour ce domaine précis | 0,35s (171/min) | Inconnu | Chemin mort en pratique — retester si la clé Pro venait à manquer |
| CoinMarketCap Pro (Basic) | 50 req/min | **Confirmé en direct** via `/v1/key/info` sur la vraie clé configurée | aucun | 1,333s (45/min) | Nouveau throttle |
| CoinGecko (Demo, `/simple/price`) | 100 req/min, 10 000 crédits/mois | Confirmé (2 sources officielles indépendantes) | 2,2s (27,3/min, 27% utilisé) | 0,667s (90/min) | **Accélération** (vérifier empiriquement avant déploiement, vu l'écart doc/réalité déjà observé ailleurs) |
| Mobula | 1 req/s, 10 000 crédits/mois | Confirmé (doc officielle) | 1,05s (95,2%) | 1,111s (0,9/s) | Léger ralentissement |

## Tier 2 — x402 et fournisseurs de données secondaires

| Service | Débit réel (source) | Confiance | Throttle actuel | Cible 90% | Action |
|---|---|---|---|---|---|
| Dune Execute SQL (Free) | 15 req/min (limite basse, contraignante) + 40/min (limite haute, compteur séparé) | Confirmé (doc officielle) | aucun | 4,44s (13,5/min) | Nouveau throttle |
| Tavily Search | Dev = 100/min, Prod = 1000/min (10x d'écart selon la clé) | Confirmé mais **tier réel de la clé à vérifier** | 0,5s (120/min — dépasse déjà le tier Dev si c'est le tier actif) | 0,667s (90/min) si Dev, 0,067s (900/min) si Prod | **Vérifier le tier réel avant de calibrer** |
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
