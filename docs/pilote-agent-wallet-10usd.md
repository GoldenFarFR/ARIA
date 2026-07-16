# Pilote agent-wallet réel ~10-15$ — EXCEPTION NOMMÉE (16/07)

> **Statut : §4 tranché par l'opérateur (16/07, explicite et répété).** Le modèle
> "plafond dur + wallet isolé + swap uniquement, vérifié après coup" est accepté
> pour CE pilote précisément borné — exécution réelle sans clic Telegram par
> transaction. Même rigueur que l'exception Sepolia : nommée ici, jamais une
> dérogation silencieuse à la règle absolue de validation humaine, qui reste
> pleinement en vigueur partout ailleurs (mainnet Vanguard ZHC, tout capital au-delà
> de ce pilote). Code construit : `packages/aria-core/src/aria_core/
> agent_wallet_pilot.py` (commit `0d3f593`), garde-fous §3 tous appliqués et
> testés (20 tests). **Encore en attente** : les identifiants CDP réels
> (`CDP_API_KEY_ID`/`CDP_API_KEY_SECRET`/`CDP_WALLET_SECRET`, wallet Coinbase
> Agentic créé par l'opérateur lui-même) et le branchement du vrai SDK CDP
> (balance_fn/swap_fn injectés, aucune clé ne transite jamais par une session
> cloud) — rien n'est activé (`ARIA_AGENT_WALLET_PILOT_ENABLED` reste OFF) tant
> que ce câblage réel n'est pas fait, testé sur le VPS.

## 1. Pourquoi ce document existe

L'opérateur propose (14-15/07) un test réel ~10$ sur un agent-wallet (MetaMask Agent
Wallet / Coinbase Agentic Wallets / Trust Wallet Agent Kit), avec le raisonnement :
montant trop petit pour avoir une vraie conséquence en cas d'erreur, sert à calibrer
avant le vrai palier. Ce n'est pas le montant qui compte mais le **précédent** : ce
serait la première fois que du code lié à ARIA déplacerait du capital réel mainnet
sans validation humaine par transaction (contrairement à Sepolia = testnet, Arena #60 =
infra tierce isolée qui ne passe jamais par notre code). Donc : traité avec la même
rigueur que l'exception Sepolia, nommé explicitement, jamais noyé dans « un test
tranquille ».

## 2. Choix du produit — décision opérateur requise

| Produit | Accès aujourd'hui (15/07) | Notes |
|---|---|---|
| **MetaMask Agent Wallet** | Accès anticipé demandé, PAS encore ouvert | Choix de fond de l'opérateur (DEX natif, self-custodial, ERC-7710/7715, compatible Claude Code nommément) |
| **Coinbase Agentic Wallets** | Semble accessible immédiatement (`npx awal`, email OTP, création gratuite/gasless sur Base) | Testable dès ~20$ USDC selon la doc officielle — à vérifier si 10$ suffit en pratique |
| **Trust Wallet Agent Kit** | Accessible via `portal.trustwallet.com`, accès non vérifié en détail | Couverture chaînes la plus large, moins creusé que les deux autres |

**Recommandation** : si l'opérateur veut tester CETTE semaine, Coinbase Agentic
Wallets est la seule option confirmée accessible sans attente. MetaMask reste le
choix cible à long terme — à activer dès que l'accès anticipé s'ouvre, sans repasser
par ce document (mêmes garde-fous, juste un autre wrapper).
**Décision opérateur actée (15/07) : Coinbase Agentic Wallets retenu pour ce pilote.**
CLI `npx awal` reconfirmé légitime (doc officielle `docs.cdp.coinbase.com`, repo
GitHub `coinbase/agentic-wallet-skills`) — création du wallet (email + code à 6
chiffres) gratuite et sans KYC, le KYC n'intervient QUE si le financement passe par
`npx awal fund` depuis un compte Coinbase classique (un transfert externe depuis un
wallet déjà détenu par l'opérateur évite complètement le KYC).

## 3. Garde-fous obligatoires (non négociables, quel que soit le produit choisi)

1. **Plafond dur vérifié dans le code**, pas dans le réglage UI de l'outil — avant
   CHAQUE transaction, interroger le solde réel du wallet et refuser si le montant
   engagé dépasserait le plafond fixé (ex. 10-15$ maximum, jamais plus). Ne jamais
   faire confiance au "session cap"/"transaction limit" natif du produit seul.
2. **Aucune capacité de transfert libre.** Seule l'action `swap` est autorisée
   (échange interne au wallet, aucun destinataire externe). Si un retrait est un jour
   nécessaire, UNE SEULE adresse pré-enregistrée en dur (celle de l'opérateur), jamais
   un champ libre — le transfert vers une adresse arbitraire est le vrai vecteur de
   vol, pas le swap.
3. **Slippage ≤10% explicite et codé en dur** (règle absolue déjà actée le 09/07) —
   jamais la valeur par défaut de l'outil, quel qu'il soit.
4. **Kill-switch = `/stop` existant** (`aria_core.outgoing_pause`, déjà testé,
   fail-closed). Le wrapper doit appeler `outgoing_pause.is_paused()` (ou
   `money_block_reason()`) avant CHAQUE tentative — si en pause, refuser et logger
   `status="blocked"`. Pas de mécanisme parallèle, même principe que Sepolia.
5. **Structurellement séparé de `wallet_guard.py`** — même doctrine que
   `sepolia_autonomous.py`/`bonding_trade_log.py` : ce pilote ne modifie ni ne
   contourne le garde-fou partagé qui protégera un jour tout capital réel à plus
   grande échelle.
6. **Journalisation complète** — CHAQUE tentative (réussie/échouée/bloquée) tracée
   via `aria_core.agent_wallet_log.record_transaction()` (déjà construit, #158/#159,
   15/07), lisible via `GET /api/aria/diagnostics/agent-wallet-ledger` (gate
   `ARIA_DIAGNOSTIC_TOKEN`, déjà en place, en attente de déploiement).
7. **Gate dédié, off par défaut** — nouveau flag (ex. `ARIA_AGENT_WALLET_PILOT_ENABLED`),
   séparé des flags Sepolia/Arena/wallet_guard existants.
8. **Wallet dédié et isolé** — jamais mélangé au wallet Vanguard ZHC principal (même
   principe qu'Arena #60), pour que ce pilote reste bornable et liquidable seul.

## 4. Question d'interprétation encore ouverte

Le modèle "plafond + liste blanche accordés une fois, puis autonomie dans ces bornes,
2FA seulement en sortie de cadre" (commun aux trois produits comparés, cf.
`aria-ops/docs/aria-learning-inbox/2026-07-14-agent-wallets-concurrents-metamask.md`)
satisfait-il la règle absolue ARIA de "validation humaine systématique" sur le
capital réel ? **Décision opérateur explicite requise avant toute activation** — ce
document formalise le fait que ce N'EST PAS une confirmation Telegram par trade
individuel, contrairement à la règle actuelle. Si l'opérateur valide ce modèle pour
CE pilote précisément borné (10-15$ max, swap uniquement, wallet isolé), ça devient
une exception nommée au même titre que Sepolia — pas une dérogation silencieuse.

## 5. Ce qui serait construit (une fois le "go" donné)

Nouveau module `aria_core/agent_wallet_pilot.py` (nom indicatif), structurellement
séparé de `wallet_guard.py` :
- Wrapper autour de l'API/CLI du produit choisi (ex. `npx awal` pour Coinbase, ou le
  futur client MetaMask `mm`).
- Fonction unique d'entrée (`attempt_swap(...)`) qui, dans l'ordre : vérifie le
  kill-switch → vérifie le plafond (solde réel) → force le slippage ≤10% → exécute →
  logge le résultat (`ok`/`failed`/`blocked`) via `agent_wallet_log.record_transaction`.
- Aucune fonction de transfert/retrait générique — seulement, si besoin plus tard, une
  fonction dédiée qui n'accepte AUCUN paramètre d'adresse (adresse pré-enregistrée en
  constante, jamais un argument appelant).
- Tests : plafond dépassé → `blocked` sans appel réel à l'API externe ; kill-switch
  actif → `blocked` sans appel réel ; slippage forcé même si l'appelant en fournit un
  autre ; chaque chemin loggé.

## 6. Portée (rappel)

Aucun capital réel engagé par ce document. Aucune adresse/clé ne vit ici (repo
public) — le wallet dédié du pilote, une fois créé, est documenté dans `aria-ops`
(même doctrine que Velvet Unicorn/MetaMask, cf. `docs/aria-learning-inbox/`).

## 7. Branches ouvertes

- Confirmer si 10$ est un montant testable en pratique (Coinbase mentionne "as little
  as $20 USDC" dans un cas d'usage vu — à vérifier avant de fixer le montant final).
- Une fois le produit choisi et le "go" donné : créer le wallet dédié (aria-ops),
  construire le wrapper (§5), tests, puis SEULEMENT ENSUITE nommer l'exception dans
  CLAUDE.md (même section que Sepolia/Arena).
- Revoir Guard Mode vs Beast Mode (MetaMask) ou l'équivalent chez Coinbase une fois
  le produit tranché — § 4 de ce document en dépend directement.
