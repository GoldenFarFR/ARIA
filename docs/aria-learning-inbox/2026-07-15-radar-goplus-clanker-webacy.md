[VPS Research]

# Radar large-spectre — Clanker (launchpad Base), GoPlus Security & Webacy (risk-scoring)

## Contexte

Passe de scan large-spectre en rôle par défaut (pas un dispatch d'ouvrier).
Angle 2 du dispatch du 15/07 : nouvelles technologies/approches on-chain
utiles à ARIA — sourcing/découverte de tokens, analyse de risque, launchpads
Base non encore couverts. Vérification préalable (grep) : `Bankr` et
`Flaunch`/`Zora` sont déjà diligenciés (`2026-07-11-bankr-diligence-approfondie.md`,
`2026-07-13-diligence-flaunch-zora-coins.md`) — pas refaits ici. Aucune note
existante sur Clanker, GoPlus Security ou Webacy — trois pistes réellement
nouvelles.

**Méthode** : pour Clanker et GoPlus, un vrai appel `curl` en direct a été
fait (pas une lecture de doc seule), avec le résultat brut ci-dessous —
même discipline que la doctrine « jamais un résumé sans vérification directe ».

---

## 1. Clanker — launchpad de tokens Base via Farcaster, racheté par Farcaster/Neynar

**Mécanisme** : bot IA sur Farcaster — un utilisateur tague `@clanker` avec
la description d'un token, et le bot déploie le contrat ERC-20 + pool de
liquidité sur Base automatiquement. Lancé fin 2024, activité en forte
croissance début 2026.

**Rachat confirmé** : Farcaster a racheté Clanker (token CLANKER +360% à
l'annonce), puis transféré la gestion à **Neynar** (infrastructure
principale de Farcaster) en janvier 2026 — transition d'un token
spéculatif vers un modèle **buy-back-and-burn** utilitaire. Volume cumulé
revendiqué : **7,62 milliards $ depuis le lancement** (début février 2026),
frais protocole journaliers record >600 000$.

**API publique testée en direct — AUCUNE clé requise, données confirmées
fraîches** :
```
curl https://www.clanker.world/api/tokens?page=1
```
Résultat réel obtenu à l'instant du test (2026-07-15, ~18:53 UTC) : un
token nommé "CodeAgent" (`CODEAIP05`), `chain_id: 8453` (Base confirmé),
`deployed_at: 2026-07-15T18:53:23.000Z` — **déployé quelques secondes avant
l'appel**, donc le flux est bien temps réel, pas un cache figé. Champs
retournés utiles à un crawler de découverte : `contract_address`,
`pool_address`, `pair` (ex. USDC), `starting_market_cap`, `supply`,
`social_context` (plateforme Farcaster, `requestor_fid`), `warnings`,
`tags.verified`, structure de frais (`extensions.fees`, cascade de
destinataires comme chez Flaunch).

**Signal de légitimité** : rachat par une entité identifiée (Farcaster/
Neynar), pas un projet anonyme — contraste positif avec plusieurs
launchpads déjà diligenciés (BullX, équipe floue).

**Réserve non résolue ce soir** : le champ `tags.verified: false` sur le
token observé montre que **tous les tokens Clanker ne sont pas vérifiés
par défaut** — un filtre de découverte devrait explicitement exclure ou
pondérer différemment `verified: false`, comme déjà recommandé pour
Zora/Flaunch (filtrer le bruit des lancements fraîchement snipés).

**Verdict Clanker : signal vert pour usage en découverte** — API réelle,
sans clé, données confirmées live par un vrai appel, rachat par une
entité identifiée. À ajouter à la liste des sources de découverte
indépendantes de Base (aux côtés de DexScreener, GeckoTerminal, et
l'endpoint `/v1/dex/pairs/{chain}` de Dune déjà noté dans
`docs/dune-integration-plan.md`).

---

## 2. GoPlus Security — API de détection de risque token/adresse, gratuite, sans clé

**Test en direct, deux appels réels** :
```
curl https://api.gopluslabs.io/api/v1/supported_chains
curl https://api.gopluslabs.io/api/v1/token_security/8453?contract_addresses=0x4200...0006
```
- `supported_chains` confirme **Base listé explicitement** (`{"name":"Base","id":"8453"}`),
  aux côtés d'Ethereum, BSC, Arbitrum, Polygon, Solana, Optimism, etc.
  (~30 chaînes).
- `token_security` sur l'adresse WETH Base a renvoyé un objet réel et
  détaillé sans aucune clé API : `holder_count` (5 198 597), liste des
  principaux détenteurs avec pourcentages, `is_honeypot`, `is_mintable`,
  `is_open_source`, `is_blacklisted`, `is_anti_whale`,
  `can_take_back_ownership`, `hidden_owner`, etc. — **aucune authentification
  requise pour ce test**, réponse HTTP normale (`"code":1,"message":"OK"`).

**Endpoints disponibles selon la doc** (non tous testés ce soir) : Token
Security API (EVM + Solana), Malicious Address API, NFT/approval security,
Phishing Site Detection API, simulation de transaction. Rate limits et
éventuels paliers payants non confirmés précisément (page d'aperçu vague
sur ce point) — à vérifier avant un usage à haut volume, mais l'accès de
base fonctionne sans inscription.

**Verdict GoPlus : signal vert net** — c'est exactement le type de
détection honeypot/mintable/ownership qu'un filtre de sécurité pré-trade
devrait vérifier avant qu'ARIA n'envisage un token candidat, en
complément (pas remplacement) de toute analyse interne existante. Accès
gratuit sans clé confirmé par test réel, contrairement à Webacy (voir
ci-dessous) et à Dune (bloqué ce soir, voir
`docs/dune-integration-plan.md` §7).

---

## 3. Webacy — risk-scoring wallet/token, mais accès verrouillé par clé API

**Produits identifiés (lecture doc, pas testés en profondeur)** : Smart
Contract Analysis API (détection de menace), Wallet Screening API (KYW —
liste noire, sanctions, spam/sybil, AML), Transaction Simulation API,
SafetyScore (score global de vulnérabilité wallet), CLI Webacy annoncé en
2026 pour agents IA (« real-time digital asset risk intelligence directly
in the terminal »). +15 fournisseurs de données agrégés selon leur propre
communication.

**Test en direct — confirmé verrouillé** :
```
curl "https://api.webacy.com/addresses/0x4200...0006?chain=base"
→ HTTP 401 {"message":"Unauthorized"}
```
Contrairement à GoPlus et Clanker, **Webacy exige une clé API** dès le
premier appel — pas de palier de test anonyme. Pas de détail de pricing
public trouvé ce soir (recherche web n'a rien remonté de spécifique sur
les tarifs) — nécessiterait une inscription pour évaluer le coût réel, non
fait ce soir (pas de compte créé, conforme aux frontières de la mission).

**Verdict Webacy : piste réelle mais non vérifiable sans inscription** —
produit visiblement plus riche que GoPlus sur le volet wallet (KYW,
sanctions/AML), mais coût et conditions d'accès à évaluer avant tout usage
— pas de recommandation ferme ce soir, juste banqué comme piste à
approfondir si un besoin de screening wallet plus poussé que GoPlus
apparaît.

---

## Synthèse

| | Accès sans clé confirmé par test réel | Couverture Base confirmée | Signal de légitimité |
|---|---|---|---|
| Clanker (découverte) | Oui | Oui (`chain_id: 8453` dans la réponse) | Racheté par Farcaster/Neynar |
| GoPlus Security (risque token) | Oui | Oui (`supported_chains` liste Base) | Service établi, multi-chaînes, cité par Alchemy/CoinDesk |
| Webacy (risque wallet) | Non — 401 sans clé | Annoncée dans leur doc, pas testée | Produit réel mais accès non vérifié |

**Aucun signal rouge disqualifiant** sur les trois. Recommandation :
Clanker et GoPlus méritent d'être considérés comme sources
complémentaires réelles (découverte + filtre sécurité pré-trade),
vérifiées par appel direct plutôt que supposées ; Webacy reste une piste
ouverte non tranchée par manque d'accès gratuit testable.

## Branches ouvertes (banquées, pas creusées)

- Rate limits précis et éventuel palier payant de GoPlus au-delà d'un
  usage ponctuel — non trouvés ce soir, à vérifier avant un usage à volume
  soutenu.
- Pricing réel de Webacy (nécessite une inscription pour le voir) — non
  fait ce soir, hors frontière de la mission sans un « go » explicite.
- Filtrage `tags.verified` sur l'API Clanker comme signal de bruit à
  exclure d'un crawler de découverte, dans le même esprit que le
  garde-fou anti-snipe déjà recommandé pour Zora Coins.

## Sources

- [Clanker — Public API (clanker.world)](https://www.clanker.world/api)
- [Clanker Documentation (GitBook)](https://clanker.gitbook.io/documentation)
- [The Defiant — Farcaster Acquires Clanker (tokenbot)](https://thedefiant.io/news/nfts-and-web3/farcaster-acquires-clanker-tokenbot)
- [KuCoin — Clanker Weekly Protocol Fees Hit $8M](https://www.kucoin.com/news/articles/clanker-surging-activity-in-base-ecosystem-drives-weekly-protocol-fees-to-record-8m-high)
- [GoPlus Security — API Overview](https://docs.gopluslabs.io/reference/api-overview)
- [GoPlus Security — Getting Started](https://docs.gopluslabs.io/docs/getting-started)
- [GoPlus Security — Token Security API](https://gopluslabs.io/en/token-security-api)
- Appels `curl` réels effectués ce soir : `api.gopluslabs.io/api/v1/supported_chains`,
  `api.gopluslabs.io/api/v1/token_security/8453`, `www.clanker.world/api/tokens`,
  `api.webacy.com/addresses/...` (tous horodatés 2026-07-15 ~18:53 UTC)
- [Webacy — Products](https://www.webacy.com/products)
- [Webacy — Wallet Watch Risk Scoring](https://www.webacy.com/blog/new-to-webacy-wallet-watch-risk-scoring-why-continuous-security-is-the-next-big-thing)
- Contexte local vérifié : `docs/aria-learning-inbox/2026-07-11-bankr-diligence-approfondie.md`,
  `2026-07-13-diligence-flaunch-zora-coins.md` (grep-avant-proposer, pas
  refaits ici)

## Frontières confirmées respectées

Aucun compte créé (ni Webacy, ni Clanker, ni GoPlus). Aucune clé API
achetée ou générée. Aucun code ARIA modifié — recherche externe +
appels `curl` en lecture seule sur des endpoints publics, aucun capital
réel engagé, aucune approche de `wallet_guard`/`permission_mode`/
`config.toml`/auto-modification.
