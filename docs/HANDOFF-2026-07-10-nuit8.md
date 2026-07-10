# HANDOFF — 2026-07-10 (suite 8) — EMA/MACD livrés, seam entry_signals trouvé, CLAUDE.md nettoyé

Suite directe de `docs/HANDOFF-2026-07-09-nuit7.md`. Segment court, dans la continuité du
mandat opérateur "travaille 8h, creuse plus profond pour une meilleure analyse" — aucun
nouveau domaine réseau requis (tout le travail est du calcul pur, zéro appel externe).

## Écart CLAUDE.md/code fermé : EMA/MACD réels (#68)

`CLAUDE.md` annonçait depuis longtemps un "Moteur TA (RSI/MACD/EMA/fibo/divergences)" —
vérifié par grep avant d'écrire quoi que ce soit : MACD et EMA n'étaient calculés NULLE
PART dans le code. `skills/indicators.py` (`ema_series`/`macd_series`, déterministe,
7 tests) comble l'écart. Fibo/divergence RSI, eux, existaient bel et bien
(`skills/entry_signals.py`).

## Découverte en vérifiant : `entry_signals.detect_entry` totalement dormant

En creusant fibo/divergence pour vérifier l'affirmation CLAUDE.md, découverte qu'un
module complet et sophistiqué (`skills/entry_signals.py`, 171 lignes, 10 tests — le
setup "golden pocket Fibonacci 0,618–0,786 + divergence haussière RSI") n'était câblé
**nulle part** : ni dans le rapport `/vc` en prod, ni même dans la CI. Ne fait pas
doublon avec `ta_levels.suggest_entry_zone` (déjà câblé, générique, toujours renvoyé
support/résistance le plus proche) : `detect_entry` est un signal plus rare et de
meilleure qualité, complémentaire, avec R/R dérivé des niveaux réels.

**Livré ce segment** :
- `test_entry_signals.py` ajouté à `.github/workflows/ci.yml` (zéro risque, ferme un
  vrai trou de couverture).
- Seam documenté dans `docs/architecture-extensibilite.md` (point d'ancrage naturel :
  `acp_onchain_scan.py:721`, juste après `ctx.ta_entry = suggest_entry_zone(...)`).
- **Volontairement PAS câblé** dans le pipeline de scan réel — même prudence que #11/
  #59/#68 : une décision produit sur un rapport premium déjà approuvé revient à
  l'opérateur, pas un choix technique unilatéral.

## Incohérence CLAUDE.md trouvée et corrigée (relecture systématique après mise à jour)

Le bullet "Nuit 7" affirmait encore "la segmentation complète des 3 cycles reste hors
de portée du tier gratuit [CoinGecko], source alternative pas encore trouvée/vérifiée" —
en réalité déjà résolu **dans ce même segment nuit7** (tâche #62, `blockchain_info.py`)
et correctement documenté dans `docs/etat-systeme-cable.md`. Seul `CLAUDE.md` était
resté figé sur l'état intermédiaire. Corrigé pour ne pas re-présenter un point réglé
comme un manque encore ouvert à une future session.

## Backlog #56 (sourcing blue-chip via bot Telegram tiers) — toujours bloqué, pas retenté

Confirmé en relisant `docs/HANDOFF-2026-07-09-nuit2.md` : Telegram interdit
structurellement à un bot de lire les messages d'un autre bot. Solution nécessiterait
soit un userbot MTProto (session Telegram personnelle de l'opérateur — sensible,
décision qui lui revient), soit un relais manuel. Pas une tâche qu'une session
autonome peut débloquer sans l'opérateur — laissé de côté à raison, pas retenté ce
segment pour éviter de tourner en rond sur un blocage déjà connu.

## Sécurité — vérifié à chaque commit de ce segment

`detect-secrets scan` (commande documentée exacte, comparée au `.secrets.baseline`) :
0 nouvelle trouvaille sur chacun des 3 commits. `test_coherence.py` : 66/66 verts.
Suite complète : 4076 passed, 1 échec pré-existant connu (`test_web_verify_rugby.py`,
appel réseau live DuckDuckGo, hors périmètre).

## Domaines réseau — aucun nouveau ce segment

Liste cumulative inchangée depuis `HANDOFF-2026-07-09-nuit7.md` (tous déjà autorisés) :
`*.virtuals.io`, `degen.virtuals.io`, `whitepaper.virtuals.io`, `docs.game.virtuals.io`,
`basescan.org`/`api.basescan.org`, `sepolia.base.org`, `*.youtube.com`,
`shekel.xyz`/`*.shekel.xyz`, `x.com`/`twitter.com`, `geckoterminal.com`/
`*.geckoterminal.com`, `coingecko.com`/`*.coingecko.com`/`docs.coingecko.com`,
`ariavanguardzhc.com`/`*.ariavanguardzhc.com`, `cloudpanel.ionos.fr`/
`*.cloudpanel.ionos.fr`, `hyperagent.ch`, `coinrule.com`, `katoshi.ai`,
`blockchain.info`, `bitcoin.com`, `fred.stlouisfed.org`, `polymarket.com`/
`gamma-api.polymarket.com`/`clob.polymarket.com`.

## Ce qui reste en attente (priorité pour la prochaine session)

1. Décision opérateur sur le câblage réel d'EMA/MACD et `entry_signals.detect_entry`
   dans le rapport `/vc` (deux capacités testées prêtes, seams documentés).
2. Tâche #59 (Polymarket) : domaines dispo, code écrit et testé
   (`services/polymarket.py`), toujours volontairement pas branché dans `/vc` — même
   attente de décision produit.
3. Tâche #64 (audit B5, barres Potentiel-$) : contexte original perdu à une compaction
   antérieure, toujours en attente de clarification plutôt que de deviner.
4. Tâche #17 (durcissement SSH VPS) : nécessite un accès VPS que cette session cloud
   n'a pas.
5. Vérifier si l'opérateur a récupéré l'accès IONOS et créé le compte Shekel (nuit7).
6. Point sécurité mineur toujours ouvert : JWT non vérifié dans
   `skills/core/memory/ACP VIRTUAL PROTOCOL/20260628_1139_source.md:211`.

## Auto-critique honnête

Segment volontairement plus modeste que nuit7 (pas de nouvelle intégration externe,
juste deux capacités TA + une passe de cohérence documentaire) — assumé : après un
segment dense en nouvelles sources de données, une passe de vérification/nettoyage a
plus de valeur qu'empiler une nouvelle intégration mal digérée. Le motif qui se répète
(EMA/MACD puis entry_signals, tous deux "construits mais jamais câblés") suggère qu'il
pourrait y avoir d'autres capacités dormantes dans `skills/` — pas creusé plus loin ce
segment par manque de méthode fiable pour le faire vite sans risquer des faux positifs
(un grep brut sur les imports internes à `aria_core` rate tout ce qui n'est consommé
que par l'hôte `vanguard/backend`). Piste pour une prochaine session : un audit
dédié et méthodique, pas une vérification à la volée.
