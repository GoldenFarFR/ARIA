# Gestion du risque du portefeuille papier — plan en 5 points (#186/#187)

> Réponse à la question opérateur du 15/07 (nuit) : « propose moi la meilleure façon
> de gérer ses choses là » (coupe-circuit perte, catastrophe, corrélation, custody des
> gains). Recherche + plan documentés dans `CLAUDE.md` (entrée 15/07 nuit) ; l'opérateur
> a donné le feu vert détaillé sur les points 2 et 3 (dispatch #187, VPS Secondaire) et
> sur le point 1 (dispatch #186, VPS Principal), en parallèle sur `paper_trader.py`.
> Les points 4 et 5 restent au stade recherche — non dispatchés à ce jour.

Doctrine transversale aux 5 points, non négociable : **AUCUNE de ces protections
n'exécute jamais une vente/action automatique sur du capital RÉEL sans confirmation
opérateur.** En paper-trading, fermer automatiquement une position est sans risque —
ça teste la RÉACTION du mécanisme. Avec du capital réel, chaque déclenchement doit
suivre le même patron que `wallet_guard.py` : `escalate_spend` ne fait qu'alerter et
journaliser, seul un clic Telegram réel de l'opérateur déclenche l'exécution
(`resolve_spend`) — jamais de vente automatique.

## 1. Coupe-circuit sur drawdown portefeuille (#186 — VPS Principal)

*À compléter par la session en charge de #186.* Palier souple + palier dur sur le
drawdown global du portefeuille papier, réutilise `outgoing_pause` existant.

## 2. Surveillance continue des positions ouvertes (#187 — ce document)

**Fichier** : `packages/aria-core/src/aria_core/paper_trader_risk.py`
(`rescan_open_position`, `capture_entry_snapshot`, `EntrySecuritySnapshot`,
`usdc_depeg_pct`/`is_usdc_depegged`) — module séparé de `paper_trader.py` pour limiter
la collision avec #186 sur ce même fichier. `paper_trader.py` n'y gagne que 2 colonnes
DB additives (`category`, `entry_security_json`) et 2 kwargs optionnels sur
`open_position`.

### Constat de départ

GoPlus (`services/goplus.py`) et Blockscout (`services/blockscout.py`) ne
vérifiaient la sécurité d'un token QU'À L'ENTRÉE, via `scan_base_token`. Rien ne
re-vérifiait une position déjà ouverte pendant qu'elle était détenue — un token
propre à l'achat peut devenir un honeypot ou voir son ownership repris APRÈS l'entrée
(rug tardif), sans que rien ne le détecte avant le prochain déclenchement de stop/TP
basé sur le prix seul.

### Mécanisme

À chaque cycle `run_paper_cycle` (aucune nouvelle cadence heartbeat — réutilise le
tour `paper_trade_cycle` existant, 180 min), pour **chaque position ouverte** (pas
seulement les nouveaux candidats) :

1. **Instantané à l'entrée** (`capture_entry_snapshot`) : capturé une seule fois, à
   l'ouverture. Réutilise les champs déjà calculés par `scan_base_token`
   (`ctx.is_honeypot`, `ctx.cannot_sell`, `ctx.hidden_owner`,
   `ctx.can_take_back_ownership`, `ctx.contract_verified`) — **aucun appel GoPlus ou
   Blockscout dupliqué à l'entrée**. Seul `blockscout_client.read_owner` est un appel
   réseau nouveau, car `TokenScanContext` n'a pas d'adresse owner. Sérialisé en JSON
   dans la nouvelle colonne `entry_security_json`.
2. **Re-scan** (`rescan_open_position`) : à chaque cycle, refait un appel GoPlus
   (`get_token_security` — honeypot, revente bloquée, owner caché, reprise de
   propriété possible) et Blockscout (`check_contract_flags` — vérification du
   contrat, `read_owner` — adresse owner courante), et compare contre l'instantané
   d'entrée. **Seul un signal NOUVEAU (absent à l'entrée, présent maintenant)**
   déclenche — un token qui avait déjà des taxes élevées ou un owner non-renoncé dès
   le départ n'est pas re-jugé après coup, ce n'est pas le rôle de ce mécanisme.
   Positions ouvertes AVANT ce mécanisme (pas d'`entry_security_json`) : aucune
   référence à comparer, le re-scan est silencieusement sauté (dégradation honnête,
   jamais un signal fabriqué).
3. **Fermeture** (`paper_trader.run_paper_cycle`, pas le module de risque lui-même —
   séparation lecture/décision) : si un signal dur est détecté, `close_position(...,
   reason="sécurité re-scan")` immédiatement, avant toute gestion par stop
   suiveur/prise de profit ce même tour. ⚠️ **Capital réel : ceci deviendrait une
   ALERTE Telegram seule**, jamais une fermeture automatique (doctrine `wallet_guard`
   ci-dessus).

### Dépeg USDC

Réutilise `CoinGeckoClient.get_simple_price(["usd-coin"], vs_currencies=["usd"])`
(`usdc_depeg_pct`). Seuil : **écart absolu au peg $1 > 1 %** (pratique standard de
gestion de risque crypto). Le pricing de tout ce portefeuille papier suppose un USD
stable — un dépeg bloque les **nouvelles entrées** du cycle (les positions déjà
ouvertes continuent d'être gérées normalement, stop/TP inclus). Fail-open : une panne
CoinGecko ne bloque jamais le cycle (doctrine dôme), et le dépeg n'est même pas vérifié
si aucun candidat n'a été proposé ce tour (pas d'appel réseau superflu).

## 3. Plafond de concentration/corrélation (#187 — ce document)

### Constat de départ

`MAX_POSITIONS=15` plafonne uniquement le NOMBRE de positions, pas la corrélation
entre elles. 10 positions « Base bonding-phase » qui chutent ensemble comptent comme
diversifiées dans ce compte simple alors qu'elles ne le sont pas — le risque réel du
portefeuille dépend de combien de capital est concentré sur un même TYPE de pari, pas
du nombre de lignes.

### Catégorie

`derive_category(launchpad, bonding_phase=...)` → label `launchpad` (déjà résolu par
`scan_base_token`, champ plus fin que `network` qui n'existe pas sur
`TokenScanContext` et ne varie de toute façon pas dans ce portefeuille Base-only)
suffixé `-bonding` si `bonding_phase` — ex. `virtuals_bonding`, `clanker`, `unknown`.
Persisté dans la nouvelle colonne `category` à l'ouverture.

### Plafond

**Jamais plus de 40 % du capital de poche (`STARTING_CAPITAL_USD`, l'enveloppe fixe de
la preuve — pas le sous-ensemble actuellement déployé, qui varie avec le nombre de
positions ouvertes et donnerait une fausse impression de diversification sur un
portefeuille peu rempli) concentré sur une seule catégorie ouverte simultanément.**

Comportement à l'ouverture (`open_position`, `fit_alloc_to_concentration_cap`) :
- Place suffisante sous le plafond → l'allocation est **réduite** pour tenir
  exactement dessous, la position s'ouvre quand même (capital-efficient plutôt qu'un
  refus sec).
- Place restante **< 20 % de l'allocation normale** de position → la position est
  **skippée** (`None`) plutôt qu'ouverte en position poussière qui encombrerait le
  portefeuille pour un montant dérisoire.
- Plafond déjà atteint (place ≤ 0) → skip immédiat.

## 4. Politique de custody des gains réels (non dispatché)

*Recherche seulement, rien codé.* Sweep vers réserve au-delà d'un seuil de gains
réalisés — pas encore écrite, en attente d'arbitrage opérateur.

## 5. Plafond dur % capital par position, indépendant de Kelly (non dispatché)

*Recherche seulement, rien codé.* Règle la plus universellement citée chez les
grands traders (Paul Tudor Jones : jamais plus de 1 % du capital par trade) — trou
identifié dans le plan initial, distinct du calcul d'allocation actuel
(`ALLOC_PCT=5%` fixe par position). En attente d'arbitrage opérateur sur le seuil
exact avant tout code.
