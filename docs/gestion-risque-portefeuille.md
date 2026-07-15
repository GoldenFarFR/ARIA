# Gestion du risque portefeuille (#186, 15/07)

## 1. Contexte et pourquoi

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

**Portée de ce chantier** : ces deux mécanismes uniquement, appliqués au
portefeuille papier 1 M$ (`ARIA_PAPER_TRADING_ENABLED`, aucun risque réel).
Aucun câblage vers un pilote de capital réel — ce dernier n'est pas encore
construit. `risk_guard.py` (nouveau module) est conçu comme un seam
réutilisable tel quel le jour où un pilote réel existera : ses deux
fonctions principales ne connaissent rien de « papier » vs « réel », elles
ne travaillent qu'avec des USD/prix/compteurs génériques.

## 2. Les deux mécanismes

### 2.1 Sizing ajusté au risque (`risk_guard.size_position_by_risk`)

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
la réduction de risque avec le palier souple du drawdown (§2.2) d'une
façon difficile à raisonner.

### 2.2 Coupe-circuit de drawdown portefeuille (`risk_guard.evaluate_portfolio_risk`)

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

## Tests

`packages/aria-core/tests/test_risk_guard.py` (23 tests) : sizing pur
(plafond appliqué/inchangé/jamais un bonus/entrées incohérentes),
persistance et robustesse du coupe-circuit dédié (corruption fail-closed,
jamais confondu avec `outgoing_pause`), intégration `evaluate_portfolio_risk`
(plus haut d'équité, palier souple, palier dur par drawdown ET par pertes
consécutives, reprise jamais automatique), câblage `open_position`/
`run_paper_cycle` (refus si bloqué, cap appliqué, positions déjà ouvertes
toujours gérées). `test_paper_trader.py` : une assertion existante mise à
jour (impact attendu et vérifié du nouveau plafond de risque sur un cas de
test à stop large), toutes les autres inchangées. Suite complète
`packages/aria-core` (5062) + `vanguard/backend` (108) vertes.
