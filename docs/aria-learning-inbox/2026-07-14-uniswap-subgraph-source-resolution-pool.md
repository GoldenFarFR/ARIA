# Piste bancable — subgraph Uniswap officiel comme 4e source de résolution de pool

Contexte (14/07) : en creusant la faisabilité "prix depuis le swap on-chain"
(#157 suite), VPS Secondaire a mis en évidence que `geckoterminal.resolve_primary_pool`
peut sélectionner silencieusement le MAUVAIS pool pour un token à pools
multiples (ex. WETH) — écart de prix ~8x observé sur une vraie transaction,
sans qu'aucune erreur ne soit levée (`available=True` avec un prix faux, pas
un `available=False` honnête).

Pour une transaction précise déjà passée, le problème est déjà résolu par la
méthode prix-depuis-swap (le pool utilisé est nommé dans la transaction
elle-même, aucune ambiguïté). Mais pour la question générale "quel est LE pool
principal de ce token en ce moment" (utile pour `/vc`, ou en l'absence d'une
transaction précise à référencer), l'opérateur a soulevé un point valide :
Uniswap a son propre subgraph/API officiel qui liste les pools directement
depuis le protocole qui les crée — une source potentiellement plus autoritaire
qu'un indexeur tiers (GeckoTerminal) qui peut se tromper.

Limite honnête : ne couvrirait que Uniswap, pas Aerodrome (gros volume sur
Base) ni PancakeSwap (BNB) — un complément utile à la triangulation
existante (GeckoTerminal / DexScreener / CoinMarketCap en cours), pas un
remplacement à lui seul.

Pas creusé ce soir (doc officielle non consultée, pas de test en direct) —
piste à évaluer si la correction de `resolve_primary_pool` (sélection de pool)
devient un chantier à part entière.
