# agent-token-trader (#60 — marché « Jetons d'agent »)

Wrapper autour de la librairie npm `bondv5-trader` (`github:Virtual-Protocol/bondv5-trader`),
en aval de l'analyse pré-bonding déjà construite côté `aria-core` (#10 : `bonding_screen.py`,
`bonding_absorber.py`, `mint_authority.py`). **Ce composant ne fait aucune analyse** — il lit
des candidats déjà scorés et exécute, avec un garde-fou anti-slippage strict.

## Pourquoi un composant séparé

- `bondv5-trader` est une librairie npm brute (5 fonctions bas niveau, pas de skill packagée) —
  nécessite un vrai wrapper, distinct du code Python d'`aria-core`.
- Lecture des candidats via HTTP (`GET /api/aria/bonding-pool`, opérateur seulement), jamais
  d'accès SQLite direct à `aria.db` : deux process/langages ne doivent jamais taper le même
  fichier SQLite en continu.
- Wallet dédié, isolé du wallet Vanguard ZHC principal (même principe déjà appliqué au marché
  HL Perps / `dgclaw-skill` : exécution 100% autonome, hors `wallet_guard`/kill-switch Telegram
  d'`aria-core`).

## Faille corrigée par conception

`bondv5-trader` a un `minOutWei` par défaut de `1` (slippage non protégé). Ce wrapper
n'expose **aucun** chemin d'exécution qui accepte un `minOutWei` non dérivé d'un devis frais
(`quote.ts` : lecture des réserves de courbe juste avant l'appel, formule AMM produit constant,
tolérance plafonnée à 10% quel que soit le réglage — `config.ts`).

## Kill-switch

`ARIA_AGENT_TOKEN_TRADER_ENABLED` est relu à **chaque** itération du poller (et entre chaque
candidat d'un même cycle), jamais mis en cache au démarrage — couper la variable interrompt le
prochain trade sans redémarrer le process.

## Variables d'environnement

| Variable | Rôle |
|---|---|
| `ARIA_API_BASE_URL` | Base URL du backend vanguard (ex. `http://127.0.0.1:8000`) |
| `ARIA_ADMIN_SECRET` | Même secret que `admin_api_secret` côté backend (header `X-Admin-Secret`) |
| `ARIA_AGENT_TOKEN_TRADER_ENABLED` | Kill-switch — `true` pour autoriser l'exécution, `false`/absent = arrêt |
| `ARIA_AGENT_TOKEN_MAX_TRADE_USDC` | Taille max par trade (défaut 10) |
| `ARIA_AGENT_TOKEN_MAX_SLIPPAGE_BPS` | Tolérance slippage en bps, plafonnée à 1000 (10%) quoi qu'il arrive |
| `ARIA_AGENT_TOKEN_POLL_INTERVAL_MS` | Intervalle entre cycles (défaut 60000) |

## Ce qui reste à câbler avant tout trade réel

- Installer réellement `bondv5-trader` (`npm install`) et vérifier que sa signature correspond
  à l'interface `BondV5Trader` documentée dans `src/execute.ts` — aucune doc publique détaillée
  disponible au moment de l'écriture, à ajuster si divergence.
- Fournir un vrai `CurveReserveProvider` (`src/quote.ts`) : lecture on-chain des réserves de la
  courbe de bonding (RPC Base + adresse/ABI du contrat), aucune valeur simulée n'est acceptable
  ici — c'est la pièce qui manque avant un premier trade réel.
- Créer le wallet dédié (flux `app.virtuals.io/acp/new`) et décider du plafond de capital initial.
- `npm test` exécute les tests unitaires (math de slippage, kill-switch) sans dépendre de
  `bondv5-trader` ni du réseau.
