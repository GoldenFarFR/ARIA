# Reconnaissance marché ACP (Virtuals Protocol) — DONNÉES BRUTES

**Date du run :** 2026-07-06 (~07:07–07:11 UTC)
**Environnement :** session Claude Code cloud (egress policy restrictive)
**Objectif :** collecter des données brutes sourcées. Ne rien conclure, ne rien recommander.

> ⚠️ **Résultat global :** la collecte de données registre **n'a PAS pu être réalisée**
> depuis cet environnement. Les deux canaux d'accès au registre ACP sont bloqués
> (détails + preuves horodatées en §0). Aucune donnée d'agent, prix, ou job n'a été
> inventée. Ce document consigne : (a) les preuves d'inaccessibilité, (b) les rares
> faits vérifiables, (c) ce qui reste NON ACCESSIBLE.

---

## 0. ÉTAT D'ACCÈS AUX SOURCES (preuves horodatées)

### 0.1 Registre / visualizer / API live — NON ACCESSIBLE (bloqué egress policy)

Tentatives d'accès HTTPS aux hôtes Virtuals, toutes refusées par la gateway
d'egress de la session (`403 CONNECT rejected — policy denial`) :

| Hôte tenté | Horodatage (UTC) | Résultat | Source de preuve |
|---|---|---|---|
| `whitepaper.virtuals.io:443` | 2026-07-06T07:09:03.401Z | `connect_rejected` 403 | proxy `recentRelayFailures` |
| `os.virtuals.io:443` | 2026-07-06T07:09:03.780Z | `connect_rejected` 403 | proxy `recentRelayFailures` |
| `acpx.virtuals.io:443` | 2026-07-06T07:09:04.067Z | `connect_rejected` 403 | proxy `recentRelayFailures` |
| `https://acpx.virtuals.io/api/agents` | ~07:08 UTC | HTTP 403 (WebFetch) | tool WebFetch |
| `https://os.virtuals.io/acp/overview` | ~07:08 UTC | HTTP 403 (WebFetch) | tool WebFetch |
| `https://whitepaper.virtuals.io/llms.txt` | ~07:08 UTC | HTTP 403 (WebFetch) | tool WebFetch |

Vérifié aussi : l'egress bloque tout tiers testé (`example.com`, `dune.com`,
`x.com` → `403 CONNECT tunnel failed`). Seuls github/npm/pypi/anthropic sont
sur l'allowlist. Contournement interdit par la policy — non tenté.

### 0.2 acp-cli (Butler / browse_agents / SDK browse) — NON DISPONIBLE

- `@virtuals-protocol/acp-cli` **non installé** dans cet environnement
  (`which acp` → introuvable ; vérifié 2026-07-06 ~07:07 UTC).
- Le wrapper repo `packages/aria-core/src/aria_core/skills/acp_cli.py` appelle
  le binaire `acp browse` en subprocess. Ce binaire cible l'API sur `virtuals.io`
  → **serait de toute façon bloqué** par l'egress (§0.1), et exige login + wallet
  (adaptateur Alchemy/Privy, clé privée) absents ici.
- Conséquence : `browse_agents`, `list_offerings`, leaderboard = **non exécutables**.

### 0.3 WebSearch (signaux tiers) — INDISPONIBLE au moment du run

- ~6 requêtes WebSearch entre 07:07 et 07:11 UTC → toutes `API Error: 529 Overloaded`
  (saturation côté service Anthropic, transitoire). Aucune donnée tierce récupérée.

### 0.4 Captures registre présentes dans le repo — SANS DONNÉES EXPLOITABLES

Dossier `skills/core/memory/ACP VIRTUAL PROTOCOL/` (capturé 2026-06-28) :

| Fichier | Date capture | Contenu réel |
|---|---|---|
| `20260628_1122_source.md` | 2026-06-28 11:22 | **vide** (0 ligne) |
| `20260628_1139_source.md` | 2026-06-28 11:39 | whitepaper « About Virtuals » — CSS/thème, pas de data agent |
| `20260628_1141_source.md` | 2026-06-28 11:41 | coquille React « You need to enable JavaScript » (aucune data) |
| `20260628_1143_source.md` | 2026-06-28 11:43 | idem coquille React (aucune data) |
| `20260628_1146_source.md` | 2026-06-28 11:46 | doc SDK EconomyOS (mécanique protocole — voir §4) |

→ Aucun nom d'agent, prix, ni compteur de jobs n'est présent dans ces captures.

---

## 1. OFFRE EXISTANTE — agents PROVIDERS graduated actifs

**NON ACCESSIBLE.** La liste des providers graduated (nom, cluster, description,
prix USDC, format d'offering, nb jobs réussis, taux de succès, acheteurs uniques,
statut online, minutes depuis dernière activité) provient exclusivement du
registre/visualizer ACP, bloqué (§0.1) et non atteignable via CLI (§0.2).

- Nombre d'agents providers graduated : **non accessible**
- Détail par agent (métriques) : **non accessible**
- Aucune donnée inventée.

---

## 2. DEMANDE RÉELLE — jobs réellement initiés / complétés

**NON ACCESSIBLE.** Les compteurs de jobs complétés récents, la ventilation par
catégorie des transactions réelles, et l'identification des agents à fort volume
de jobs proviennent du registre / de l'on-chain indexé par le visualizer ACP,
bloqués (§0.1). Aucune source on-chain tierce (ex. Dune, BaseScan) n'a pu être
consultée (egress bloqué, §0.1 ; WebSearch 529, §0.3).

- Agents à nb élevé de jobs complétés récents : **non accessible**
- Catégories concentrant l'activité transactionnelle : **non accessible**
- Distinction offre listée vs jobs complétés : **non mesurable dans cet environnement**
- Aucune donnée inventée.

---

## 3. TROUS (catégories offre faible + signaux de demande)

**NON ACCESSIBLE.** Identifier les gaps exige de croiser (a) l'offre listée par
catégorie [registre, bloqué] avec (b) des signaux de demande — agents acheteurs
en recherche [registre/event stream, bloqué], posts X [egress `x.com` bloqué,
§0.1], clusters en croissance [visualizer, bloqué]. Aucun des trois flux n'est
atteignable ici.

- Catégories offre faible/absente : **non accessible**
- Signaux de demande (acheteurs, posts X, clusters) : **non accessible**
- Aucune niche déduite ni recommandée (conforme à la consigne).

---

## 4. FAITS VÉRIFIÉS (mécanique protocole, hors métriques marché)

Source : capture repo `skills/core/memory/ACP VIRTUAL PROTOCOL/20260628_1146_source.md`
(doc SDK EconomyOS « ACP SDK Getting Started », capturée 2026-06-28 11:46).
**Ce sont des faits de PROTOCOLE, pas des données de marché.** Ils n'apportent
aucune info sur l'offre/la demande réelle.

- SDK : `@virtuals-protocol/acp-node-v2` ; prérequis Node.js ≥ 18 + agent
  enregistré sur l'ACP Registry. [vérifié, capture]
- Point d'entrée `AcpAgent` : expose `browseAgents(keyword, params?)` pour
  chercher le registre, `createJobByOfferingName(...)`, `createJobFromOffering(...)`. [vérifié]
- Cycle de job (rôles/phases) : `setBudget` (Provider propose un prix) → `fund`
  (Client finance l'escrow) → `submit` (Provider livre) → `complete`/`reject`
  (Client/Evaluator). [vérifié]
- Paiement : `AssetToken.usdc(montant, chainId)` — USDC auto-résolu par chaîne. [vérifié]
- Événements système : `job.created | budget.set | job.funded | job.submitted |
  job.completed | job.rejected | job.expired`. [vérifié] — ce sont les états qui,
  s'ils étaient lus en live, permettraient de distinguer OFFRE de DEMANDE réelle.
- Chaînes supportées : Base Mainnet (8453), Base Sepolia (84532), BSC Testnet. [vérifié]
- Wallets non-custodiaux : adaptateurs Alchemy / Privy. [vérifié]

> Note : rien dans cette capture ne donne de nom d'agent, de prix affiché, ni de
> compteur de jobs réels. La distinction OFFRE (agents listés) vs DEMANDE (jobs
> complétés) demandée par la mission **n'est pas mesurable** sans accès live au
> registre / à l'event stream.

---

## 5. SÉPARATION VÉRIFIÉ / SUPPOSÉ / NON ACCESSIBLE

- **VÉRIFIÉ (dans cet environnement) :**
  - l'egress bloque `*.virtuals.io` et tout tiers testé (preuves §0.1) ;
  - `acp-cli` absent (§0.2) ;
  - les captures repo ne contiennent aucune donnée agent/job (§0.4) ;
  - la mécanique protocole ACP listée en §4 (source doc SDK datée).
- **SUPPOSÉ :** *(néant — aucune supposition marché n'est émise, conforme à la consigne).*
- **NON ACCESSIBLE :** toute la §1 (offre), toute la §2 (demande réelle),
  toute la §3 (trous). Cause unique et identique : pas d'accès live au registre ACP.

---

## 6. CE QU'IL FAUDRAIT POUR EXÉCUTER LA MISSION (non fait — pas d'action demandée)

*(Consigné pour info, aucune action entreprise.)*
- Soit autoriser `*.virtuals.io` dans l'egress policy de la session, soit exécuter
  la reco depuis un environnement local où `acp-cli` est installé **et loggé**
  (wallet/adaptateur configuré), pour lancer `acp browse --sort-by successfulJobCount`,
  `acp offering list`, et lire l'event stream.
- À défaut de registre : une source tierce indexée (ex. dashboard Dune ACP,
  BaseScan sur les contrats ACP) — mais elles aussi sont hors allowlist ici.
