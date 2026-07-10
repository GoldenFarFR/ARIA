# HANDOFF — 2026-07-10 (suite 9) — Pivot Cursor audité, régressions /vc corrigées, bug bonding-phase Virtuals résolu

Suite directe de `docs/HANDOFF-2026-07-10-nuit8.md`. Segment déclenché par l'opérateur qui a
travaillé avec **Cursor** pendant que Claude Code était à quota (Claude MAX 5x) — puis une
longue série de corrections réelles trouvées en vérifiant honnêtement le travail de Cursor et
en testant `/vc` en conditions réelles sur de vrais contrats.

## Pivot Cursor — audité, pas un incident

L'opérateur a utilisé Cursor directement (confirmé via `experiments/exp-20260710-1958/README.md`,
créé par ARIA elle-même : *"aria je travaille avec cursor car claude code j'ai consommer
tout"*) pendant l'indisponibilité de Claude Code. **Différence fondamentale avec l'incident
délégation-autonome retiré plus tôt ce même jour** : ici c'est l'opérateur qui pilote et merge
lui-même (3 PR, tous `author_association: OWNER`) — pas une IA qui délègue seule.

3 PR revues en détail (diff complet lu, pas juste le résumé de Cursor) :
- **#20** — câble enfin Polymarket dans `/vc` (mon propre tracker de tâches l'avait marqué
  "complété" à tort — vrai seam dormant comme `entry_signals` avant lui).
- **#21** — fix faux positifs `detect-secrets` sur mes propres tests vision/miner Telegram.
- **#22** — mémoire de suivi `/vc` (`vc_session_context.py`) : après un `/vc`, "+515 pourquoi ?"
  répond maintenant depuis le rapport plutôt que de repartir sur le web.

Aucun garde-fou touché, aucun secret exposé, style cohérent avec la doctrine existante.
`main` fusionné en fast-forward (54 commits, la branche de session désignée était déjà
entièrement mergée dans `main` — restart propre suivant la procédure documentée).

## Régression réelle trouvée dans le PR #22 (corrigée)

`_vc_analyze_and_reply` (telegram_bot.py) appelle `repertoire_db.save_message()` sans filet
après un `/vc` — un échec local (table absente, DB verrouillée) faisait planter tout le flux
APRÈS l'envoi de l'ordre au client, y compris l'email et le track-record. Détecté via 9 tests
existants cassés par le merge (CI du PR l'avait déjà signalé — vérifié via `get_job_logs`,
même trace exacte). Entouré d'un `try/except` best-effort.

## Faux positif de routage corrigé

`operator_readiness.py` : la regex de confirmation "ok tout est prêt" matchait n'importe quel
message "ok" + "maintenant" à moins de 40 caractères — a détourné une vraie demande opérateur
("ok trouve moi maintenant un jeton qui répond à tes critère BUY") vers l'audit de readiness
au lieu du vrai routage. `maintenant` retiré de l'alternance, test de non-régression ajouté
avec la phrase exacte reproduite.

## Câblage `candlestick_patterns.py` dans `/vc`

Module déjà construit et testé (10/07, jamais branché). Câblé dans `acp_onchain_scan.py`
(`ctx.ta_candle_patterns`, 3 derniers patterns sur les vraies bougies OHLC déjà récupérées
pour EMA/MACD, aucun appel réseau supplémentaire), exposé dans `vc_analysis.py`.

## Diligence produit légère (site + GitHub) — nouveau

Trou identifié par ARIA elle-même en conditions réelles (capture opérateur : *"et pour la
qualité du projet, tu fait des recherches ?"* → réponse honnête d'ARIA : *"non, pas encore
[...] c'est précisément le trou que je veux fermer"*). Livré :
- `services/site_snapshot.py` (nouveau) : titre + meta-description + texte visible de la page
  d'accueil du projet (regex, pas de nouvelle dépendance), tronqué, best-effort.
- `services/project_activity.py` : `website_url_from_links` + `fetch_github_diligence_snapshot`
  (description/étoiles/issues/fraîcheur, réutilise le client GitHub public existant).
- `vc_analysis.py` : `_fetch_product_diligence` (même patron pré-LLM que sentiment/Polymarket),
  site marqué explicitement DÉCLARATIF, GitHub présenté comme vérifiable. Hérite de la garde
  anti-injection générique du bloc `<donnees_non_fiables>`.
- **Limite connue, pas comblée ce segment** : ne lit que la page d'accueil déclarée — l'équipe
  doxxée et la tokenomics d'un token Virtuals (vérifié en direct sur XOE) vivent sur la fiche
  Virtuals elle-même (`virtuals.io`), pas le site officiel du projet. Piste ouverte pour une
  prochaine session : exploiter le payload Virtuals déjà récupéré (`description`, `tokenomics`,
  `additionalDetails`) plutôt qu'une seconde source de scraping.

## Grâce période pour les candidats "direct" fraîchement découverts

Dry-run manuel de `bonding_discovery_cycle` (script `dry_run_bonding_discovery.py` créé ce
segment, même patron que `simulate_lifecycle.py`) : **18 candidats Clanker/Bankr sur 20
bannis à vie** pour la seule raison "aucune paire DEX trouvée" — un token tout juste déployé
n'a souvent pas encore de paire indexée, ce n'est pas un signal de scam.

Décision opérateur explicite (recadrage du principe, pas juste ce cas précis) : *"le bonding
doit être recalculé plusieurs fois comme tous les autres tokens [...] jeter ce qui ne sert à
rien ok mais ce qui peut avoir un potentiel grâce à leur technologie doit être gardé et
rescanné"* + *"si il y a une super technologie mais des failles dans le contrat on jette,
aucun risque, il existe énormément d'autres projets"*.

Traduit en code (`safety_screen.py`, `bonding_screen.py`) : `hard_fail` (rejet définitif) ne
couvre plus QUE les mécanismes malveillants confirmés dans le contrat (mint dev, blacklist,
disable-transfers, honeypot, cannot-sell, sell-tax extractif, owner caché, reprise de
propriété) + adresse invalide. Liquidité insuffisante / pas de paire / contrat non vérifié /
concentration → échec MOU (`pending`, retenté). **`passed` (le seuil d'entrée réel) est
inchangé** — seule la classification définitif/à-réessayer change. Les 18 candidats à tort
bannis ont été réévalués en direct sur le VPS après déploiement (retombés en `pending`, comme
attendu).

## Bug plus profond trouvé en vérifiant sur deux vrais contrats (accès réseau ajouté par l'opérateur)

L'opérateur a fourni deux contrats Virtuals réels (`0x6f8c2Eb5...` "HoloStudio AI Ecosystem",
`0xB455C23d...` "XOE") où `/vc` ne détectait aucun contexte bonding malgré une vraie courbe de
bonding visible sur virtuals.io. Domaines `api.virtuals.io` + `www.clanker.world` ajoutés par
l'opérateur (effectif en quelques secondes, comme documenté) → vérifié en direct contre l'API
réelle (pas de simulation) :

- **Hypothèse casse d'adresse, testée et écartée** : les deux contrats matchent identiquement
  en casse mixte et minuscule. Fix quand même appliqué par précaution (`virtuals.py`,
  `clanker.py` — défensif, sans coût, sur TOUS les launchpads comme demandé par l'opérateur).
- **Vraie cause trouvée** : `tokenAddress` reste `null` tant qu'un token Virtuals n'a pas
  gradué — structurel, pas une panne. L'adresse de contrat visible pendant le bonding (celle
  que l'opérateur colle dans `/vc`) vit dans le champ `preToken`. `fetch_by_address` ne
  cherchait QUE `tokenAddress` → ne pouvait STRUCTURELLEMENT jamais trouver un token encore en
  bonding, exactement la catégorie que `_resolve_bonding_phase` doit détecter.
- Fix : `build_token_by_pretoken_url` + repli automatique dans `fetch_by_address` (tokenAddress
  d'abord, preToken en repli — un seul appel réseau sur le chemin heureux).
- **Vérifié end-to-end** via `scan_base_token` sur le contrat réel : `bonding_phase=True`,
  `bonding_holder_count=306` (conforme à la capture opérateur), score recalculé 55/CAUTION au
  lieu du générique précédent. Déployé (`8962bb1`) et reconfirmé après déploiement.
- **Limite honnête non résolue** : `graduation_progress()` reste `None` — le payload réel
  inspecté en direct ne contient AUCUN des noms de champ supposés (`virtualRaised` etc.), et le
  "56,94%" affiché par l'UI Virtuals ne correspond exactement à aucune formule simple testée
  (`mcapInVirtual`/42000 s'en approche mais pas exactement). Pas de proxy inventé plutôt qu'un
  chiffre non soutenu — commentaire du code mis à jour pour refléter cette vérification réelle
  (l'ancien commentaire "confiance moyenne, à revérifier sur le VPS" était daté).

## CLAUDE.md — doctrine réseau réaffirmée

Consigne opérateur explicite et répétée : *"a chaque fois que tu a besoin d'une api ou dun
environnement tu me demande GRAVE LE dans le claud.md"*. Section existante renforcée plutôt
que dupliquée (elle disait déjà l'essentiel depuis le 09/07, juste pas assez martelée).

## Connecteurs MCP explorés (Base MCP, Crypto.com, Gmail, Stripe, Massive Market Data)

À la demande de l'opérateur ("regarde ce qui t'intéresse"). Résumé :
- **Base MCP** : lecture (portefeuille/historique/recherche tokens) + écriture réelle
  (`send`/`swap`/`sign`/x402). Wallet connecté à 0,00$, aucun wallet agent délégué
  (`agentWallets: []`) — rien d'actionnable actuellement. **Aucun outil d'écriture utilisé**,
  conforme à la règle absolue capital réel.
- **Crypto.com** : données de marché, probablement redondant avec GeckoTerminal/DexScreener
  déjà en place pour ce qu'ARIA screene réellement.
- **Gmail/Stripe** : pas d'usage identifié ce segment (Stripe hors-scope tant que le narratif
  produit payant reste abandonné).
- **Massive Market Data** : API stocks/options/forex/indices (pas spécialisé memecoins
  onchain). Connecteur activé côté opérateur mais **jamais chargé dans cette session précise**
  malgré plusieurs vérifications (`ListConnectors`, nouveaux messages) — confirmé fonctionnel
  dans une session fraîche ouverte en parallèle par l'opérateur (bug de propagation côté
  plateforme, pas une mauvaise config). Piste ouverte, non câblée : complément macro (BTC/ETH
  spot, forex) pour `btc_cycles.py`/contexte marché, jamais un remplacement des sources
  onchain existantes.

## VPS — nettoyage disque + déploiements multiples

Disque VPS à 79,8%/86GB avant nettoyage — diagnostic (`docker system df`) : images Docker (35GB,
89% récupérable) + cache de build (31,7GB, 91% récupérable) = quasi tout l'usage réel, pas de
la vraie donnée (`deploy.sh` ne nettoie jamais après un build). `docker image prune -f` +
`docker builder prune -f` → 80% → 11%. Plusieurs déploiements réussis ce segment, dernier en
date : commit `8962bb1` (fix preToken), health check OK à chaque fois.

## Sécurité — vérifié à chaque commit de ce segment

Suite complète systématiquement verte après chaque changement (4283 → 4318 tests au fil du
segment), seul échec constant = `test_web_verify_rugby.py` (réseau live DuckDuckGo, hors
périmètre, pré-existant, sandbox-only). Aucun garde-fou touché. Aucun secret exposé.

## Domaines réseau ajoutés ce segment

`api.virtuals.io`, `www.clanker.world` — ajoutés par l'opérateur en direct, effectifs en
quelques secondes, confirmé de nouveau (déjà vérifié 09/07 avec d'autres domaines).

## Ce qui reste en attente (priorité pour la prochaine session)

1. Décision opérateur : activer `ARIA_BONDING_DISCOVERY_ENABLED` maintenant que le pipeline
   direct/bonding est sain (grâce période + bug preToken corrigés, vérifiés en réel).
2. Diligence produit — exploiter directement le payload Virtuals (`tokenomics`,
   `additionalDetails`, `description`) pour les tokens lancés sur Virtuals plutôt que de
   re-scraper un site externe qui n'a souvent pas cette info.
3. `graduation_progress()` reste honnêtement `None` — formule exacte de l'UI Virtuals non
   identifiée, à revisiter si un vrai besoin produit apparaît (pas de valeur ajoutée à deviner
   sans confirmation).
4. Examen pédagogique (`exam.py`) : le pool de 67 concepts se répète nécessairement avant la
   fin des 20 jours (aucun suivi cross-jour des concepts déjà posés) — fix simple identifié,
   pas implémenté (l'opérateur n'a pas encore confirmé vouloir ce changement).
5. Connecteur Massive Market Data : vérifier si le bug de propagation se résout tout seul ou
   nécessite un signalement à Anthropic.
6. Items déjà en attente depuis nuit8 (SSH VPS #17, accès IONOS/Shekel, JWT non vérifié dans
   un fichier de mémoire) — toujours non traités, aucun accès/contexte nouveau ce segment.
7. `deploy.sh` ne purge jamais le cache Docker (images + build cache) après un build — cause
   confirmée du remplissage disque VPS à 79,8% traité ce segment (nettoyage manuel one-shot
   80%→11%, pas une correction à la source). Proposé d'ajouter un `docker image prune -f` +
   `docker builder prune -f` automatique en fin de script — jamais confirmé par l'opérateur,
   donc pas fait. Sans ce fix, le disque se remplira de nouveau au fil des prochains
   déploiements.

## Auto-critique honnête

Segment dense et à forte valeur réelle (deux vrais bugs de production trouvés et corrigés
grâce à des contrats réels fournis par l'opérateur, pas des cas synthétiques), mais j'ai
perdu du temps au début à chercher la mauvaise cause (casse d'adresse) avant de vérifier
empiriquement contre l'API réelle — le réflexe "demande l'accès réseau d'abord" aurait dû
arriver plus tôt dans l'investigation plutôt qu'après plusieurs allers-retours de captures
d'écran. Leçon actée : dès qu'une hypothèse technique est vérifiable en direct et qu'un
accès réseau existe ou peut être demandé, le faire immédiatement plutôt que de raisonner
dans le vide sur du code seul.
