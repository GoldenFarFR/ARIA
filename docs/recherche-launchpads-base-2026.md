# Recherche — Launchpads Base à courbe de bonding (niche 15 % « pré-bonding »)

> Synthèse factuelle produite le 06/07/2026 (6 agents de recherche, 0 erreur).
> **Statut de fiabilité (mis à jour 10/07) : l'endpoint Clanker est confirmé
> testé en direct depuis le VPS** (`services/clanker.py`, réponse réelle de
> l'API, énumération `sortBy` obtenue de l'API elle-même). **Les autres
> endpoints (Virtuals bonding, Flaunch, Zora) restent non testés en direct**
> (403 en session cloud), validation encore basée sur code public + docs +
> résumés de recherche. Chantier restant : `curl` de confirmation depuis le
> VPS sur les endpoints non-Clanker, et re-vérification des adresses de
> contrats sur BaseScan.

---

## Constat central (contre-intuitif)

La thèse ARIA — « détecter les tokens encore en courbe de bonding, avant
graduation DEX » — ne s'applique proprement qu'à **un seul launchpad Base
majeur : Virtuals Protocol**.

- **Clanker** et **Zora** (les deux plus gros en volume sur Base) **sautent la
  courbe de bonding** : liquidité déployée directement sur Uniswap dès t=0.
  Aucune fenêtre pré-bonding à exploiter.
- **Flaunch** n'est pas une courbe « pump.fun » mais a une **vraie fenêtre
  pré-DEX de 30 min** (Fair Launch à prix fixe), détectable — l'analogue le plus
  proche après Virtuals.
- La « vraie » niche courbe-de-bonding vit massivement **sur Solana**
  (pump.fun), pas sur Base — et **son edge se dégrade** (taux de graduation
  pump.fun effondré à ~0,26 % mi-juin 2026).

Conséquence : sur Base, la niche pré-bonding stricte est **étroite** (Virtuals +
Flaunch). Pour Clanker/Zora, le seul « early » possible est la détection des
**premières minutes post-déploiement** — utile, mais c'est une autre thèse
(sniping de nouveaux déploiements, pas de bonding).

---

## 1. Classement des launchpads Base à couvrir

| Rang | Launchpad | Vraie phase pré-bonding ? | Détection gratuite | Priorité |
|------|-----------|---------------------------|--------------------|----------|
| **1** | **Virtuals Protocol** | OUI — courbe classique (Prototype→Sentient) | OUI (API REST publique sans auth) | **Cœur de la niche** |
| **2** | **Flaunch** | Partielle — Fair Launch prix-fixe 30 min | OUI (SDK + on-chain) | **Haute** |
| **3** | **Clanker** | NON — liquide sur DEX dès t=0 | OUI (event `TokenCreated` + API) | Moyenne (nouveau déploiement seulement) |
| **4** | **Zora** | NON — pool Uniswap V4 dès t=0 | OUI (event Factory + API) | Basse (bruit >30k coins/jour) |

**Non retenus (faible confiance)** : Hook.bid (bonding permanente, pas de
graduation), Base.meme (rien de vérifié), Cliza (1 tweet), **Larry / Apez
(introuvables — ne pas affirmer leur existence)**.

---

## 2. Détection concrète par launchpad

### Virtuals Protocol — PRIORITÉ 1

**A) API REST publique (gratuite, sans clé)** — endpoint Strapi :
```
GET https://api.virtuals.io/api/virtuals?filters[status]=UNDERGRAD&filters[chain]=BASE&sort[0]=createdAt:desc&pagination[pageSize]=100
```
- `status=UNDERGRAD` = Prototype = **encore en bonding**. `AVAILABLE` = gradué (Sentient).
- Filtre par token : `filters[tokenAddress][$eq]=0x…` ; détail : `…/api/virtuals/{id}`.
- Gratuité vérifiée *par usage* (dizaines de repos GitHub 2026, simple `accept: application/json`). Pas de rate-limit documenté → prévoir back-off sur 429.

**B) On-chain (temps réel)** — contrat Bonding sur Base, events `Launched` /
`Graduated`. Un token `Launched` sans `Graduated` = encore en courbe.
Graduation à **42 000 VIRTUAL** accumulés.
Adresse Bonding rapportée : `0xF66DeA7b3e897cD44A5a231c61B6B4423d613259`
(**confiance moyenne — revérifier sur BaseScan**).

### Flaunch — PRIORITÉ 2

**A) SDK `@flaunch/sdk` (gratuit)** :
```
flaunchRead.trustedPoolKeySignerStatus(coinAddress)
→ { isFairLaunchActive, fairLaunchStartsAt, fairLaunchEndsAt }
```
`isFairLaunchActive = true` ⇒ token encore en pré-graduation (fenêtre prix-fixe
30 min). Équivalent direct du « encore en bonding ».

**B) On-chain** — PositionManager `0x51Bba15255406Cfe7099a42183302640ba7dAFDC`,
Uniswap v4 PoolManager `0x498581fF718922c3f8e6A244956aF099B2652b2b`.
⚠️ Adresses/signatures issues de résumés de recherche — **re-vérifier sur
docs.flaunch.gg et BaseScan**.

### Clanker — PRIORITÉ 3 (détection « nouveau déploiement », PAS bonding)

- On-chain : event **`TokenCreated`** (deployer v4 rapporté
  `0xE85A59c628F7d27878ACeB4bf3b35733630083a9` — confiance moyenne).
- API gratuite : `GET https://www.clanker.world/api/tokens` (adresse, symbol,
  timestamp, trust status) + `…/api/search-creator`.
- Rien à « graduer » : liquide sur Uniswap dès la création.

### Zora — PRIORITÉ 4 (bruit extrême)

- On-chain : ZoraFactory `0x777777751622c0d3258f214F9DF38E35BF45baF3`, events
  `CoinCreatedV4` / `CreatorCoinCreated`.
- API gratuite (~120 req/min sans clé) : SDK `@zoralabs/coins-sdk` →
  `getCoinsNew()` ; REST `https://api-sdk.zora.engineering`.
- Pas de graduation + >30 000 coins/jour ⇒ le goulot est le **filtrage**, pas la
  détection. Faible intérêt pour la thèse pré-bonding.

---

## 3. Signaux de qualité mesurables (grille de scoring commune)

Surtout pertinents sur **Virtuals** (courbe) et **Flaunch** (fenêtre prix-fixe).
**Statut : logiquement solides et convergents, mais aucun validé sur données
réelles** — à calibrer empiriquement sur les vrais flux.

1. **Vélocité de bonding** — progression vers le seuil / temps (VIRTUAL
   accumulés / 42 000 ; % d'allocation Fair Launch vendue avant 30 min). Meilleur
   prédicteur de graduation. (Recherche pump.fun : les **3 premières minutes**
   sont discriminantes.)
2. **Acheteurs uniques vs concentration** — beaucoup de wallets distincts = bon ;
   courbe portée par 1-2 wallets = red flag (bundler/rug).
3. **Répartition du supply** — part créateur / top holders faible = mieux.
4. **Volume + ratio buy/sell** soutenus sur la fenêtre précoce.
5. **Identité / équipe doxxée** — Virtuals (produit/agent fonctionnel via
   framework GAME) ; Zora (profil social réel) ; Flaunch (Twitter/Farcaster via
   Privy) ; Clanker (réputation on-chain via `search-creator`).
6. **Produit réel vs pur memecoin.**
7. **Ancienneté courte (`createdAt`) + traction = « early ».**
8. **Contrôles anti-arnaque** — GoPlus / honeypot.is (surtout Clanker v4 à frais
   dynamiques via hooks), croisement DexScreener/BaseScan.

---

## 4. Recommandation cross-chain : **rester Base-first**

**Garder Base comme cœur d'exécution ; ajouter Solana uniquement en couche de
SIGNAL lecture-seule ; ne construire l'exécution Solana que si les données
prouvent un edge durable.**

- **Coût asymétrique** : la *détection* Solana = simple ajout d'API (semaines).
  L'*exécution* = stack entièrement neuf et non-transférable (wallets ed25519,
  gas SOL, priority fees, bundles Jito, RPC co-localisé ~50 ms) → mois de dev +
  capital immobilisé en SOL.
- **Timing défavorable** : graduation pump.fun effondrée à ~0,26 % (−80 % en
  3 mois), revenus quotidiens ~800 k$, frais réseau Solana −84 %.
- **Base garde de vrais atouts** : Virtuals = vraie courbe, détectable
  gratuitement sans clé, avec produit sous-jacent (agents IA) → meilleur
  signal/bruit que les memecoins Solana purs.

Nuance : Solana reste plus **profonde** (volumes, market caps, ~30k tokens/jour),
mais l'edge y est cher à capter et en détérioration → **monitoring d'abord**.

---

## 5. Trous et incertitudes (à ne pas surestimer)

- **Endpoint Clanker confirmé testé en direct depuis le VPS (10/07)** — les
  autres endpoints (403 en session cloud) restent non testés en direct. →
  `curl` de confirmation depuis le VPS avant intégration pour ceux-ci.
- **Adresses de contrats à confiance moyenne** (revérifier BaseScan) : Bonding
  Virtuals `0xF66D…3259`, deployers Clanker v4 `0xE85A…83a9` / `0x375C…2c5E`,
  PositionManager Flaunch `0x51Bb…AFDC`. *(ZoraFactory `0x7777…baF3` = mieux
  corroborée.)*
- **Mapping numérique du `status` Virtuals** (1=prototype…) inféré du frontend →
  préférer la forme texte `UNDERGRAD` / `AVAILABLE`.
- **Taux de graduation propre à Base** introuvable pour Virtuals (traiter «
  faible par défaut » ; seule donnée = lancement Solana fév. 2026, **8,3 %**
  gradués — 13/156).
- **Chiffres d'adoption 2026** (13k tokens/jour Clanker, >30k coins/jour Zora) =
  sources médias → ordres de grandeur, pas des mesures.
- **Piège Virtuals** : `acpx.virtuals.io/api/agents` = registre ACP (agents de
  service), **différent** de `api.virtuals.io/api/virtuals` (index des tokens) —
  c'est ce dernier pour le pré-bonding.

---

### TL;DR opérationnel

Couvrir **Virtuals en priorité 1** (seule vraie courbe sur Base, API gratuite
sans clé), **Flaunch en priorité 2** (fenêtre Fair Launch 30 min via
`isFairLaunchActive`). Clanker/Zora = détection de *nouveaux déploiements*
(Clanker > Zora à cause du bruit). **Rester Base-first**, Solana en monitoring
lecture-seule. Avant prod : **`curl` de confirmation depuis le VPS** +
re-vérification des adresses sur BaseScan.
