# Gestion du risque du portefeuille papier — plan en 5 points (#186/#187)

> Réponse à la question opérateur du 15/07 (nuit) : « propose moi la meilleure façon
> de gérer ses choses là » (coupe-circuit perte, catastrophe, corrélation, custody des
> gains). Recherche + plan documentés dans `CLAUDE.md` (entrée 15/07 nuit) ; l'opérateur
> a donné le feu vert détaillé sur les points 2 et 3 (dispatch #187, VPS Secondaire) et
> sur le point 1 (dispatch #186, VPS Principal), en parallèle sur `paper_trader.py` --
> mergés ensemble le 16/07 (session cloud). Les points 4 et 5 restent au stade
> recherche — non dispatchés à ce jour.

Doctrine transversale aux 5 points, non négociable : **AUCUNE de ces protections
n'exécute jamais une vente/action automatique sur du capital RÉEL sans confirmation
opérateur.** En paper-trading, fermer automatiquement une position est sans risque —
ça teste la RÉACTION du mécanisme. Avec du capital réel, chaque déclenchement doit
suivre le même patron que `wallet_guard.py` : `escalate_spend` ne fait qu'alerter et
journaliser, seul un clic Telegram réel de l'opérateur déclenche l'exécution
(`resolve_spend`) — jamais de vente automatique.

## 1. Sizing ajusté au risque + coupe-circuit sur drawdown portefeuille (#186 — VPS Principal)

Constat vérifié dans `paper_trader.py` avant ce chantier : `ALLOC_PCT = 0.05`
(5 % du capital de départ par position, ~50 000 $ sur 1 M$) était **flat** --
appliqué identiquement quelle que soit la distance entre le prix d'entrée et
l'invalidation. Une position à stop LARGE risque proportionnellement plus
qu'une position à stop SERRÉ pour la même allocation en dollars. Aucun
coupe-circuit de portefeuille n'existait non plus (grep confirmé avant
construction : aucun `drawdown`/`circuit_breaker`/`kill_switch` dans
`paper_trader.py`).

Recherche à l'origine de ce chantier (Paul Tudor Jones, Ray Dalio/
Bridgewater) : ne jamais risquer plus d'une fraction fixe et faible du
capital sur un seul trade (indépendamment de la taille de position), et ne
jamais laisser un drawdown dépasser un seuil au-delà duquel la remontée
mathématique devient punitive (une perte de 50 % exige un gain de 100 %
pour revenir à zéro).

### 1.1 Sizing ajusté au risque (`risk_guard.size_position_by_risk`)

Fonction **pure**, aucun état persisté :

```
risked_usd = alloc_usd * (entry_price - invalidation_price) / entry_price
```

`RISK_CAP_PCT = 0.02` (2 % du capital total risqué au pire cas — entre le
1 % très conservateur de Paul Tudor Jones et le maximum actuel implicite du
flat `ALLOC_PCT` à 5 %). Si `risked_usd > RISK_CAP_PCT * capital_total`,
`alloc_usd` est réduit pour ramener le risque au pire cas exactement au
plafond (`capped_alloc = cap_usd / risk_fraction`). **Ne relève JAMAIS
`alloc_usd` au-delà de sa valeur d'entrée** — un plafond, jamais un bonus :
un stop très serré garde son allocation flat d'origine intacte, seul un
stop large voit son allocation réduite.

Sans `invalidation_price` connue (`None`, ou incohérente `>= entry_price`),
`alloc_usd` est renvoyé inchangé — le stop suiveur (`TRAIL_STOP_PCT` dans
`paper_trader.py`) reste alors le seul garde-fou, comme avant ce chantier.

**Câblage** : appliqué systématiquement dans `open_position()` (pas
seulement dans `run_paper_cycle`) — chokepoint de sécurité en profondeur qui
couvre TOUT appelant présent ou futur (commande manuelle, futur pilote de
capital réel réutilisant cette même fonction), pas seulement le cycle
heartbeat actuel. `capital_total` = `starting_capital()` (capital de
départ, la même base que `ALLOC_PCT`), pas l'équité courante — cohérent
avec la convention déjà en place pour le sizing flat, et évite de composer
la réduction de risque avec le palier souple du drawdown (§1.2) d'une
façon difficile à raisonner.

### 1.2 Coupe-circuit de drawdown portefeuille (`risk_guard.evaluate_portfolio_risk`)

**Plus haut d'équité persisté** : nouvelle colonne `equity_high_water_mark`
sur `paper_state` (migration à chaud idempotente, même patron que
`retry_count` sur `screened_token` — `PRAGMA table_info` puis `ALTER TABLE`
si la colonne est absente, jamais destructif). Initialisé au capital de
départ par `reset_portfolio()` ; sur une DB migrée sans valeur encore
écrite, `get_equity_high_water_mark()` retombe sur `starting_capital()`
(jamais `NULL` côté appelant).

Deux paliers, évalués une fois par cycle (`evaluate_portfolio_risk`, appelé
depuis `run_paper_cycle` APRÈS la gestion des positions déjà ouvertes —
qui continue toujours normalement — et AVANT toute tentative d'ouverture) :

- **Palier souple** (`SOFT_DRAWDOWN_PCT = 0.10`, -10 % depuis le plus haut) :
  réduit de moitié (`SOFT_ALLOC_MULTIPLIER = 0.5`) l'allocation des
  NOUVELLES entrées uniquement. Notifié une seule fois par transition
  (bande suivie via `last_alert_band` dans l'état persisté, pas de
  re-notification à chaque cycle tant que le drawdown reste dans la même
  bande — évite le bruit).
- **Palier dur** (`HARD_DRAWDOWN_PCT = 0.20`, -20 % depuis le plus haut,
  **OU** `HARD_CONSECUTIVE_LOSSES = 5` pertes consécutives sur les trades
  clôturés) : bloque TOUTE nouvelle entrée. Les positions déjà ouvertes
  continuent d'être gérées normalement par leur propre stop suiveur/prise
  de profit — rien n'est fermé de force.

**Flag dédié, jamais confondu avec `outgoing_pause`** : le palier dur arme
un état persisté dans un fichier séparé (`risk_guard_state.json`, PAS
`pause_state.json`) — `outgoing_pause.py` est un kill-switch global qui
coupe aussi des cycles sans rapport avec l'argent (ex. `knowledge_inbox`),
alors que ce coupe-circuit est scopé strictement à l'ouverture de nouvelles
positions (paper aujourd'hui, potentiellement réel plus tard).
`blocks_new_entries()` **respecte lui-même** `outgoing_pause` (une pause
globale bloque aussi les nouvelles entrées paper — cohérent avec le fait
que `heartbeat._tick()` ne tourne déjà plus du tout en pause) sans jamais
mélanger les deux raisons dans le message rapporté à l'appelant. État
illisible/corrompu → **fail-closed** (bloque), même doctrine « argent » que
`outgoing_pause.money_block_reason()`.

**Reprise JAMAIS automatique** : même si l'équité remonte au-dessus du
seuil dur entre-temps, le blocage reste armé tant que
`risk_guard.resume_new_entries(by=...)` n'a pas été appelé explicitement.
Portée de ce chantier : la fonction existe et est testée ; son câblage à une
commande Telegram (action humaine explicite au sens opérationnel, pas
seulement au sens fonction Python) est un suivi naturel non fait ici —
scope strictement limité aux deux mécanismes demandés.

**Notifications Telegram** sur déclenchement des deux paliers (`notifier`
déjà injecté dans `run_paper_cycle`, même canal que les alertes achat/
vente existantes) : `risk_guard.format_soft_drawdown_alert`/
`format_hard_circuit_breaker_alert`.

**Chokepoint en profondeur** : `blocks_new_entries()` est vérifié à la fois
dans `run_paper_cycle` (skip immédiat de la boucle d'ouverture si bloqué,
sans même tenter une analyse) ET dans `open_position()` lui-même — un
futur appelant direct (hors cycle heartbeat) reste protégé.

**Tests** : `packages/aria-core/tests/test_risk_guard.py` (23 tests) : sizing pur
(plafond appliqué/inchangé/jamais un bonus/entrées incohérentes),
persistance et robustesse du coupe-circuit dédié (corruption fail-closed,
jamais confondu avec `outgoing_pause`), intégration `evaluate_portfolio_risk`
(plus haut d'équité, palier souple, palier dur par drawdown ET par pertes
consécutives, reprise jamais automatique), câblage `open_position`/
`run_paper_cycle` (refus si bloqué, cap appliqué, positions déjà ouvertes
toujours gérées).

## 2. Surveillance continue des positions ouvertes (#187 — VPS Secondaire)

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
tour `paper_trade_cycle` existant), pour **chaque position ouverte** (pas
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
   jamais un signal fabriqué). **Même dégradation pour les positions sourcées par le
   pipeline momentum (#194)** : leur `sig` ne produit ni `category` ni
   `entry_security_json` (pas de `TokenScanContext` -- pipeline multi-chaînes, pas
   Base-only) -- ces positions ne comptent ni pour ni contre le plafond de
   concentration (§3) et leur re-scan est silencieusement sauté, exactement comme une
   position pré-#187. Le honeypot GoPlus est déjà vérifié UNE FOIS à l'entrée côté
   `momentum_entry._check_honeypot` (garde-fou dur séparé), simplement pas re-vérifié
   en continu par CE mécanisme pour ces positions -- un suivi possible serait
   d'étendre `capture_entry_snapshot` au pipeline momentum, pas fait ici.
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

## 3. Plafond de concentration/corrélation (#187 — VPS Secondaire)

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
Persisté dans la nouvelle colonne `category` à l'ouverture. **Positions sourcées par
le pipeline momentum (#194)** : `category` reste vide (le sig momentum n'a pas de
notion de `launchpad`) -- `open_position` ignore alors tout le bloc de plafond de
concentration pour ces positions (`if category:` seul garde), elles ne rentrent donc
ni dans l'exposition d'aucune catégorie ni sous aucun plafond -- dégradation honnête
documentée, pas un bug, suivi possible si le concept de catégorie est étendu au
pipeline momentum (ex. par chaîne) plus tard.

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

**Tests** : `packages/aria-core/tests/test_paper_trader_risk.py` (module isolé,
tout injectable/mocké) + tests d'intégration dans `test_paper_trader.py` (stockage de
`category`, plafond qui réduit/skip/n'affecte pas les autres catégories, fermeture sur
signal de sécurité nouveau, positions pré-#187 non affectées, blocage des nouvelles
entrées sur dépeg sans toucher à la gestion des positions déjà ouvertes).

## 4. Politique de custody des gains réels (non dispatché)

*Recherche seulement, rien codé.* Sweep vers réserve au-delà d'un seuil de gains
réalisés — pas encore écrite, en attente d'arbitrage opérateur.

## 5. Plafond dur % capital par position, indépendant de Kelly (non dispatché)

*Recherche seulement, rien codé.* Règle la plus universellement citée chez les
grands traders (Paul Tudor Jones : jamais plus de 1 % du capital par trade) — trou
identifié dans le plan initial, distinct du calcul d'allocation actuel
(`ALLOC_PCT=5%` fixe par position). En attente d'arbitrage opérateur sur le seuil
exact avant tout code.

## Interaction avec #194 (pipeline momentum multi-chaînes, mergé le 16/07)

Les mécanismes #186 (sizing/coupe-circuit) restent actifs **quelle que soit la source
des candidats** -- ils opèrent sur les positions ouvertes/le portefeuille dans son
ensemble, jamais sur le `sig` d'un analyzer particulier. Les mécanismes #187
(re-scan continu, plafond de concentration) dégradent honnêtement pour les positions
momentum (§2/§3 ci-dessus) plutôt que de planter ou de fabriquer une catégorie/un
instantané de sécurité inexistants. Voir `docs/pivot-momentum-1m-test.md` §6 pour la
limite symétrique côté #194 (le `price_lookup` du coupe-circuit ne propage pas encore
le kwarg `chain` pour les positions non-Base -- dégrade vers `cost_usd`, jamais un prix
inventé).
