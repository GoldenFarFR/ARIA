# Diligence — Moni API vs CabalSpy (sourcing wallet-scoring), 01/08/2026

> Instantané daté, pas une fiche vivante. Suite directe de la diligence CabalSpy du
> 23/07 (`docs/HANDOFF_WALLET_SCORING.md`), qui avait écarté Moni sur la seule base de sa
> doc publique ("aucun des deux n'a de labels smart money"). L'opérateur a fourni une
> vraie clé API Moni (`profile.moni.ai`, palier gratuit) le 01/08 — cette fiche est le
> premier test réel avec authentification, jamais fait avant. **Aucune clé/valeur secrète
> n'apparaît ici**, conforme à la doctrine repo public.

## Ce que l'API propose réellement (vérifié en direct, pas la doc seule)

Base URL confirmée : `https://api.discover.getmoni.io/api/v3/` (distincte du domaine
principal `moni.ai` — trouvée via `apiguide`/`llms.txt`, jamais assumée). Authentification :
header `Api-Key`. Latence excellente et stable : 80-130ms sur 5 appels successifs (moyenne
~92ms). Palier gratuit avec un vrai rate-limit (429 "Rate limit exceeded" observé après
2-3 appels rapprochés — non chiffré précisément, un délai de ~15s a suffi à débloquer).

Endpoints réels testés (`accounts/{username}/info/full/`, `projects/raw/`) — données
retournées par compte X/Twitter :
- `moniScore` : score composite continu (pas une catégorie)
- `smartsCount` / `smartMentionsCount` : volume d'engagement par des comptes "smart"
- `smartTier` / `smartTags` : **catégorisation** (ex. "Exchange") -- confirmée PRÉSENTE et
  remplie sur un compte de projet/exchange (`RobinhoodCrypto`), mais **toujours vide/null**
  sur les 3 wallets individuels testés ci-dessous
- `mlProjectPrediction` : score ML de probabilité qu'un compte soit un vrai "projet" crypto
  (vu à 95% sur un exemple réel du flux `projects/raw/`)

## Comparaison directe sur les mêmes entités (CabalSpy wallets "kol" -> Moni via leur handle X)

3 wallets tirés au hasard du flux réel `cabalspy.list_wallets("base", wallet_type="kol")`
(238 wallets reçus), chacun avec un handle X déjà attaché par CabalSpy -- interrogés ensuite
sur Moni avec ce même handle :

| Wallet (CabalSpy type="kol") | Handle X | Moni `moniScore` | Moni `smartsCount` | Moni `smartTier`/`smartTags` |
|---|---|---|---|---|
| Kaz1m | KZMKBL | 81 | 8 | `null` / `[]` |
| blanker | 0xblanker | 1850 | 165 | `null` / `[]` |
| milady | milady | 57 | 3 | `null` / `[]` |

**Constat net, sur preuve directe (pas la doc seule)** : pour les 3 wallets individuels
labellisés "kol" par CabalSpy, Moni fournit un score continu et un compteur d'engagement,
mais **jamais de catégorisation exploitable** (`smartTier`/`smartTags` vides) -- ce champ
semble réservé aux comptes de PROJETS/exchanges (confirmé positif sur `RobinhoodCrypto`),
pas aux wallets/KOLs individuels que `/walletqueue`/`/walletscore` ont besoin de
catégoriser.

## Verdict

**La diligence du 23/07 se confirme, cette fois sur preuve directe plutôt que la doc
seule** : CabalSpy reste le bon choix pour le sourcing wallet-scoring (catégorie directe
"kol"/"smart"/"whale" par wallet, exploitable sans transformation). Moni ne remplace pas
ce rôle -- son intérêt potentiel serait ailleurs (score de momentum social continu par
compte X, découverte de projets récents via `projects/raw/` + `mlProjectPrediction`),
non exploré plus loin ici, hors scope de cette diligence.

## Branches ouvertes (jamais creusées, juste banquées)

- `projects/raw/` (flux de projets récents + score ML "vrai projet") pourrait être une
  source de découverte complémentaire à `launchpad_discovery.py` -- angle non évalué.
- Rate-limit précis du palier gratuit Moni jamais mesuré rigoureusement (juste observé
  qu'il existe et qu'un court délai suffit) -- à calibrer si un usage réel était envisagé.
