# HANDOFF — Doppler (Bankr token launches, Uniswap v4 bonding curve)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[CODE] Sujet    : Client Doppler minimal (lecture de prix Uniswap v4) — suite au test forcé opérateur sur des tokens Bankr
Date : 2026.07.24 / Probleme : demande operateur explicite (test forcé d'achat sur des tokens Bankr/Doppler, CLOWNS puis BANK) -- aucune API HTTP ni subgraph public n'existe pour Doppler (confirmé, seul un SDK TypeScript qui lit les contrats directement), et Blockscout ne donne meme pas la transaction de creation d'un token clone EIP-1167 -- impossible de forcer un achat sans inventer un prix.
Solution : nouveau `services/doppler.py` -- un pool Doppler EST un pool Uniswap v4 standard (le hook ne fait que planifier des frais anti-snipe degressifs sur ~14s, jamais le mecanisme de prix lui-meme, confirme via une vraie transaction) : lecture directe `sqrtPriceX96` via le contrat StateView officiel Uniswap. Le seul chainon Doppler-specifique (retrouver le pool d'un token) se fait via l'event `Initialize` du PoolManager (currency0/currency1 indexes), localise en trouvant d'abord le bloc de creation via Blockscout (`eth_getLogs` sur toute l'historique renvoie 413 Payload Too Large sur le RPC public — meme sur 20 000 blocs, confirme empiriquement) puis en scannant une petite fenetre RPC-safe autour de ce bloc. Formule de conversion validee par recoupement independant contre le vrai tick de la transaction CLOWNS (`1.0001**tick` == `(sqrtPriceX96/2**96)**2` a l'arrondi flottant pres) — un vrai bug d'inversion trouve et corrige avant commit (le token-est-currency0 donnait un prix a $31 000 milliards au lieu de ~$0.00000011). 21 tests dedies (test_doppler.py) — services/doppler.py (nouveau)

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Portée réelle de ce premier client
Date : 2026.07.24 / Probleme : —
Solution : couvre uniquement les pools appariés contre WETH (le seul numéraire observé sur les lancements Bankr/Doppler vérifiés à ce jour, CLOWNS/BANK) — un pool contre un autre numéraire retourne `None` plutôt qu'une conversion fausse. Pas encore câblé dans un pipeline d'entrée (bonding_entry.py reste scopé Virtuals) — utilisé pour l'instant en lecture ponctuelle (achats de test forcés par l'opérateur, thèse marquée comme tel, jamais une décision autonome d'ARIA).
