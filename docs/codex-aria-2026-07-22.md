> **Note ajoutée le 28/07/2026, avant commit** : ce document a été produit le 22/07/2026 (session
> externe, jamais committé au repo jusqu'ici — retrouvé sur demande opérateur le 28/07). Il
> s'auto-qualifie déjà comme un instantané figé ("si le code a bougé depuis, il peut avoir
> dérivé... revérifier plutôt que de citer cette page indéfiniment") — le contenu ci-dessous est
> donc conservé TEL QUEL pour les parties non encore rafraîchies (fidélité historique), avec
> seulement les divergences déjà connues signalées ici plutôt que corrigées dans leur corps de
> texte :
> - **Partie 2.3** : corrigée directement dans le corps du texte (passe du 28/07 sur les Parties
>   1-3, voir plus bas) — l'adresse de transfert autorisée est désormais à jour
>   (`0x584b2B35dac347B2317da0d21b95063de51257Ef`, vérifiée en direct dans `agent_wallet_pilot.py`).
> - **Partie 10** (non rafraîchie dans cette passe) : `ARIA_BONDING_DISCOVERY_ENABLED` — le document dit "resté OFF (sourcing jugé
>   de mauvaise qualité)". CLAUDE.md a depuis corrigé ce point (24/07, audit 5-agents) : le gate
>   est en réalité `true` en prod.
> - **Parties 4, 5, 6 et nouvelle 5bis — RAFRAÎCHIES le 28/07/2026** (cette même session) : chaque
>   constante/formule reverrifiée directement contre le code réel (`safety_screen.py`,
>   `acp_onchain_scan.py`, `momentum_entry.py`, `risk_guard.py`, `paper_trader.py`,
>   `bonding_entry.py`). Corrige notamment l'ordre réel des garde-fous durs momentum (honeypot
>   déplacé en dernier le 21/07, concentration holders déplacée après le calcul R/R le 26/07), la
>   plage RSI absolue [20,40] (25/07), le plancher RVOL 2 500$, l'architecture à 3 poches
>   (scalping/swing/vc, 27/07) avec son coupe-circuit par poche + macro (-15%), et ajoute la
>   nouvelle Partie 5bis dédiée au pipeline bonding (score composite 35/35/15/15, paliers de sortie
>   Take-Seed/Tier2/Tier3/moonbag, stop de perte 3 volets) — y compris la trouvaille empirique du
>   28/07 non encore corrigée (Item #167 : le crible bonding ne laisse passer quasiment aucun
>   candidat réel sur un échantillon d'environ 380 tokens).
> - **Parties 1, 2 et 3 — RAFRAÎCHIES le 28/07/2026** (session distincte, même journée) : chaque
>   constante/formule/gate/cadence reverrifiée directement contre le code réel (`brain.py`,
>   `grounding.py`, `llm.py`, `heartbeat.py`, `gateway/telegram_bot.py`, `wallet_guard.py`,
>   `outgoing_pause.py`, `agent_wallet_pilot.py`/`agent_wallet_pilot_cycle.py`, `x402_budget.py`,
>   `x402_executor.py`, `onchain/sepolia_autonomous.py`, `onchain/attestation.py`,
>   `services/smart_money.py`, `services/smart_money_leaderboard.py`, `skills/candidate_ranking.py`).
>   Principales corrections : Mistral retiré comme provider LLM (code mort supprimé le 27/07) ; un
>   6e ancrage anti-confabulation (refus de fermeture manuelle de position, 24/07) s'intercale dans
>   la cascade `AriaBrain.process` ; le verrouillage par défaut de la conversation Telegram publique
>   (24/07 — un non-admin reçoit désormais un message figé, plus un accès grounded au LLM, sauf gate
>   explicite) ; plusieurs cadences heartbeat déplacées depuis le 22/07 (découverte momentum
>   60→30min, surveillance agent-wallet 10→30min, `aria_brain_cycle` devenu une cadence organique
>   16-24h) ; trois cycles dont le déclenchement réel était resté cassé jusqu'au 24/07
>   (`smart_money_leaderboard_discovery_cycle`, `token_holder_extraction_cycle`,
>   `trade_devils_advocate_cycle` — le gate existait mais n'était jamais lu par le tick heartbeat) ;
>   trois nouveaux cycles apparus depuis (plancher quotidien de trades, Polymarket paper-trading,
>   sourcing CabalSpy). Corrections annotées inline (⚠️) aux endroits concernés dans les Parties 1-3
>   ci-dessous plutôt que regroupées en fin de section.
>
> Pour toute décision qui compte, revérifier contre le code réel plutôt que de citer cette page —
> exactement la norme que ce document énonce lui-même en conclusion.

---

# Référence interne — ne quitte pas l'opérateur
# ARIA — Codex

Chaque formule, seuil et variable ci-dessous a été relu directement dans le code réel du dépôt
ARIA le 22 juillet 2026 — jamais recopié de mémoire. Ce document fige un instantané : si le code
a bougé depuis, il peut avoir dérivé. En cas de doute sur un chiffre précis, revérifier plutôt que
de citer cette page indéfiniment.

Portée — cerveau, argent réel, smart money, VC, momentum, risque, mémoire, infra
Langage — français, vulgarisé mais chiffré
Statut — instantané daté

## Sommaire
Glossaire express
1. Le cerveau
1.1 Routage d'un message
1.2 Le client LLM
1.3 Anti-hallucination
1.4 Le heartbeat
1.5 Commandes Telegram
2. Argent réel
2.1 wallet_guard
2.2 Kill-switch /stop
2.3 Pilote agent-wallet
2.4 x402
2.5 Sepolia et Kelly
2.6 Le smart contract
2.7 Récapitulatif
3. Smart money
3.1 Signal token-centrique
3.2 Moteur /walletscore
3.3 candidate_ranking
3.4 /topwallets
4. Pipeline VC
4.1 safety_screen
4.2 Score de sécurité
4.3 mint_authority
4.4 dev_wallet
4.5 liquidity_depth
4.6 vc_analysis (LLM)
5. Pipeline momentum
6. Risque de portefeuille
7. Mémoire et identité
7.1 dna.yaml
7.2 test_coherence.py
8. Infrastructure
8.1 Déploiement bleu-vert
8.2 Chiffrement PDF
8.3 Cockpit et dossier
9. Qui décide quoi
10. Index des variables
11. Stress-test du pipeline VC
12. Recherche VC crypto — succès et échecs
13. Corrections codées le 22/07

## Avant de commencer
### Glossaire express
Ces mots reviennent partout dans la suite. Une seule lecture ici évite de les re-expliquer à
chaque section.

**Gate / variable d'environnement** — Un interrupteur nommé `ARIA_XXX_ENABLED` qui active ou
coupe une capacité précise sans toucher au reste du code. La valeur par défaut est presque
toujours désactivée (OFF).

**Fail-open / fail-closed** — Ce qui se passe quand une donnée manque ou qu'un service tombe en
panne. Fail-open = on continue quand même (dégradation douce). Fail-closed = on bloque par
prudence (utilisé uniquement là où une erreur coûterait cher : honeypot, argent réel).

**Honeypot** — Un token piégé qui laisse acheter mais empêche de revendre (ou taxe la revente à
un niveau confiscatoire). Détecté par le service GoPlus.

**Slippage** — L'écart toléré entre le prix visé et le prix réellement exécuté sur un échange
(swap). Plus il est haut, plus on accepte un mauvais prix.

**Smart contract** — Un programme déposé sur la blockchain, immuable une fois publié — personne
ne peut le modifier après coup, pas même son auteur (sauf fonctions explicitement prévues pour ça).

**Racine de Merkle** — Une empreinte unique (32 octets) qui résume un ensemble de données :
changer une seule virgule dans les données change entièrement l'empreinte. Sert à prouver après
coup qu'un enregistrement n'a pas été trafiqué.

**Percentile** — La position d'une valeur dans un classement, en pourcentage. Percentile 90 =
meilleur que 90% de la population comparée.

**Critère de Kelly** — Une formule financière qui calcule la fraction optimale du capital à
risquer sur un pari, à partir du taux de réussite et du ratio gain/perte moyen.

**ATR (Average True Range)** — Une mesure de la volatilité réelle d'un actif — l'amplitude
normale de ses mouvements de prix.

**RSI** — Indicateur de force relative (0-100) : au-dessus de 70 le marché est jugé suracheté,
en-dessous de 30 survendu.

**R/R (risque/récompense)** — Le ratio entre ce qu'on peut gagner (distance jusqu'à la cible) et
ce qu'on peut perdre (distance jusqu'à l'invalidation).

**Kill-switch** — L'interrupteur général `/stop` sur Telegram — coupe toute action sortante
d'ARIA (dépenses, publications) instantanément.

**Paper trading** — Un portefeuille 100% fictif qui applique les vraies analyses au vrai prix du
marché, sans jamais toucher un centime réel.

**Smart money** — Ici, un comportement de trading mesurable (pas une identité) — cohérence dans
le temps, entrées précoces, sorties disciplinées.

**x402** — Un protocole de micropaiement (quelques centimes) pour payer un accès API à la volée,
sans abonnement.

**CDP** — Coinbase Developer Platform — la plateforme qui héberge le wallet réel utilisé par le
pilote agent-wallet.

## Partie 1 — Le cerveau : LLM, routage, heartbeat

Deux couches distinctes travaillent ensemble : un LLM (modèle de langage) qui rédige/tranche les
cas ambigus, et une logique déterministe (règles mathématiques fixes, sans IA) qui fait le plus
gros du travail vérifiable. Le LLM n'intervient qu'en dernier recours.

### 1.1 Comment ARIA lit un message
Point d'entrée unique : `AriaBrain.process()`. Chaque message traverse une cascade ordonnée — la
première étape qui matche court-circuite toutes les suivantes :

1. Ancrages anti-confabulation — 5 détecteurs déterministes (jamais d'appel LLM), chacun né d'un
   incident réel : identité du modèle, méthodologie d'analyse, "pourquoi pas acheté", périmètre
   du scan, statut d'aria-brain.
2. Suivi de la dernière analyse `/vc` (opérateur).
3. **Refus de fermeture manuelle de position** (`is_manual_position_close_command`, ajouté le
   24/07 après un incident réel : "ferme la position autono" était mal classé par le routeur
   d'intention générique et répondait à côté de la plaque). Réponse déterministe fixe — le
   protocole hebdomadaire (§ plus bas) est un test délibérément sans intervention humaine, y
   compris pour les sorties ; redirige vers `/feedback`/`/ledger` et vers les mécanismes de
   correction existants (Devil's Advocate, revue par lot des trades perdants).
4. Questions langage naturel sur l'état d'un trade.
5. Workflows conversationnels (composition de tweet, auto-maintenance, préparation matinale).
6. Affirmation externe injectée → vérification web/GitHub plutôt que refus aveugle.
7. Message de bienvenue (template).
8. Classifieur d'intention (regex à score) → route vers l'un des ~16 skills (portefeuille,
   répertoire, launchpad, FAQ, marketing, entrepreneur...).
9. Si un skill répond → reformulation LLM (sauf skills strictement factuels, jamais reformulés
   pour ne pas risquer une dérive).
10. Sinon → réponse générale : FAQ directe → vérité déjà établie → recherche web calibrée → LLM en
    dernier recours.

Post-traitement systématique sur toute réponse : correction du texte, détection de "faux succès
technique", vérification épistémique (opérateur), journalisation dans le registre de vérité, note
de coût LLM.

En mode public (visiteur non reconnu), un mode "grounded" strict s'active : température très
basse (0.1), et le LLM ne peut répondre qu'à partir d'un bloc de faits vérifiés (FAQ +
connaissance approuvée + registre de vérité) — jamais d'improvisation libre.

### 1.2 Le client LLM
ARIA peut parler à plusieurs fournisseurs de modèles de langage, jamais un seul verrouillé en dur :

| Fournisseur | Modèle par défaut |
|---|---|
| xai / grok | grok-4.3 |
| openai | gpt-5-mini |
| groq | llama-3.3-70b-versatile |
| virtuals (Spark) | configuré séparément |
| deepseek | deepseek-chat |
| gemini | gemini-3.5-flash |
| anthropic | claude-haiku-4-5-20251001 |

⚠️ **Mistral retiré le 27/07** (commit `6c7df9f8`, "remove Mistral provider support") — provider
jamais réellement utilisé en prod, supprimé comme code mort (URL, modèle par défaut, clé d'auth
dédiée, tout le câblage associé). La ligne du tableau ci-dessus a disparu en conséquence.

Un appel essaie d'abord le provider primaire, puis un éventuel fallback fixé par l'appelant, puis
un fallback global (Groq) — le premier qui répond gagne, et si c'est un fallback qui a servi,
c'est journalisé pour que l'opérateur seul le sache. Depuis le 18/07, un disjoncteur dédié
(`llm_circuit_breaker.py`) peut aussi basculer le routage PAR DÉFAUT (aucun provider explicite
fourni par l'appelant) vers OpenRouter si le provider primaire échoue en série — mécanisme
distinct de ce fallback générique, non détaillé ici. Chaque provider a sa propre clé API (aucun
repli croisé pour virtuals/anthropic, pour éviter un rejet d'authentification) — vérifié inchangé
dans `_auth_key_for_provider`.

Paramètres par défaut d'un appel (`chat_with_context`) : `max_tokens = 400`, historique de
conversation tronqué aux 12 derniers messages, timeout HTTP 90 secondes (120s pour un modèle
local). Le piège du "budget de raisonnement" invisible consommant tout le quota de tokens sans
laisser de place à la vraie réponse (documenté ici à l'origine sur Mistral, désormais retiré) reste
une leçon actionnable pour tout futur fournisseur qui présenterait le même comportement.

### 1.3 Anti-hallucination
```
FAQ_DIRECT_SCORE = 4      # score minimum pour répondre depuis la FAQ sans LLM
FAQ_LLM_MIN_SCORE = 3     # score minimum FAQ pour même autoriser un appel LLM
LEDGER_LLM_MIN_SCORE = 4  # idem côté registre de vérité
budget faits vérifiés = 6000 caractères max injectés au LLM
```

Le bloc "faits vérifiés" assemblé avant chaque réponse combine : FAQ (top 5), présentation de la
holding, connaissance approuvée, contexte épistémique, et les 4 meilleurs échanges déjà vérifiés
du registre de vérité. Six règles fixes sont injectées en tête de tout prompt "grounded" :
répondre uniquement depuis ce bloc, dire explicitement l'absence d'info plutôt que d'inventer, ne
jamais inventer un prix/revenu/métrique/date, pas de conseil financier personnalisé, précision
avant éloquence, toujours citer la source.

Détecteurs nés d'incidents réels — chacun bloque une confabulation déjà survenue une fois : ARIA
avait affirmé "je tourne sur Claude Opus 4.8" (faux, invérifiable) ; avait confondu bonding et
momentum sur son propre périmètre de scan ; avait prétendu une capacité non vérifiée sur son
propre journal aria-brain. Chacun est désormais une réponse template figée, jamais une
improvisation.

### 1.4 Le heartbeat — tous les cycles
Toutes les 60 secondes, une boucle vérifie quelles tâches sont dues (comparaison à leur dernière
exécution persistée) et les lance, chacune bornée à un timeout dur de 300 secondes — une tâche
qui dépasse ou plante n'affecte jamais les autres du même passage. Le kill-switch est vérifié en
tout premier : aucune tâche ne tourne en pause.

| Cycle | Cadence | Gate / état |
|---|---|---|
| portfolio_scan | 30 min | actif, aucun gate |
| paper_trade_cycle | 15 min | `ARIA_PAPER_TRADING_ENABLED` — surveillance des positions ouvertes |
| momentum_discovery_cycle | 30 min ⚠️ | même gate — découverte de nouveaux candidats (abaissé de 60 à 30 min le 23/07, le WebSocket #196 couvre déjà la détection ~30s) |
| paper_weekly_review_cycle | 60 min | même gate — reset hebdomadaire (agit si 7j écoulés) |
| daily_trade_floor_cycle **(nouveau, 23/07)** | 60 min | gate maître `ARIA_PAPER_TRADING_ENABLED` + `ARIA_DAILY_TRADE_FLOOR_ENABLED` — force jusqu'à 2 ouvertures/passage si <5 trades ouverts ce jour civil, gardes de sécurité intacts |
| polymarket_paper_cycle **(nouveau, 26/07)** | 60 min ⚠️ | `ARIA_POLYMARKET_PAPER_ENABLED` — cadence d'observation TEMPORAIRE (burn-in, 1 candidat/passage) ; cadence nominale prévue 720 min/12h une fois quelques cycles propres confirmés (Item #133) |
| agent_wallet_pilot_cycle | 60 min | `ARIA_AGENT_WALLET_PILOT_ENABLED` — capital réel |
| agent_wallet_monitor_cycle | 30 min ⚠️ | `ARIA_AGENT_WALLET_MONITOR_ENABLED` — remonté de 10 à 30 min le 23/07 (passage à 8h puis retour à 30 min le même jour, décision opérateur) |
| sepolia_autonomous_cycle | 60 min | `ARIA_SEPOLIA_AUTONOMOUS_ENABLED` |
| vc_crawl | 6h | actif |
| vc_resolve | 24h | actif |
| vc_weekly_forecast | 2j | actif |
| vc_self_report | 7j | actif |
| vc_radar_x | 12h | actif |
| vc_thesis_review | 24h | actif |
| wallet_scan_queue_cycle | 20 min | `ARIA_WALLET_SCAN_QUEUE_ENABLED` + `ARIA_WALLET_SCORING_ENABLED` |
| wallet_candidate_sourcing_cycle | 3h | triple gate wallet-scoring |
| cabalspy_candidate_sourcing_cycle **(nouveau, 23/07)** | 3h | triple gate wallet-scoring (+ `ARIA_CABALSPY_SOURCING_ENABLED`) — sourcing des wallets KOL labellisés CabalSpy, resynchronisation complète au plus 1×/semaine |
| smart_money_leaderboard_discovery_cycle | 3h | triple gate wallet-scoring ⚠️ resté un no-op silencieux jusqu'au 24/07 (gate lu mais jamais appliqué à `task.enabled` — corrigé, commit `fbad4842`) |
| token_holder_extraction_cycle | 3h | `ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED` ⚠️ même bug, même correctif 24/07 |
| market_sentiment_cycle | 60 min | `ARIA_MARKET_SENTIMENT_ENABLED` |
| market_alerts_cycle | 60 min | `ARIA_MARKET_ALERTS_ENABLED` (Otto AI, OFF) |
| relay_conversation_cycle | 15 min | relais ARIA ↔ Claude Code |
| knowledge_inbox_cycle | 6h | `ARIA_KNOWLEDGE_INBOX_ENABLED` |
| claude_mentor_cycle | 60 min | revue de performance par Claude |
| pump_dump_autopsy_cycle | 3h | `ARIA_PUMP_DUMP_AUTOPSY_ENABLED` |
| aria_brain_cycle | organique 16-24h ⚠️ | une page/jour visée, mémoire libre — plus une horloge fixe 24h depuis le 24/07 : centre 20h ±4h de jitter déterministe (seedé par le dernier passage réel) |
| trade_devils_advocate_cycle | 3h | `ARIA_TRADE_DEVILS_ADVOCATE_ENABLED` ⚠️ jusqu'au 24/07 le gate existait mais n'était jamais lu par le tick heartbeat (`enabled=False` figé en dur) — corrigé le même commit que les deux lignes ci-dessus |
| canonical_facts_sync_cycle | 3h | `ARIA_CANONICAL_FACTS_SYNC_ENABLED` |
| x_curiosity / x_mentions_learn / x_profile_sync / tweet_schedule | 3h / 90 min / 24h / 1 min | publication X, gatée séparément |
| bonding_discovery_cycle | 3h | `ARIA_BONDING_DISCOVERY_ENABLED` — **OFF par défaut dans le code**, mais confirmé `true` en prod (audit 5-agents du 24/07, via le conteneur réel) ; pipeline lui-même largement retravaillé depuis (score composite, paliers de sortie Take-Seed) — hors du périmètre de cette Partie, voir Partie 5bis et `docs/HANDOFF_PIPELINE_MOMENTUM.md` |
| … (~32 autres cycles, sur 66 tâches déclarées au total) | — | showcase PR, ACP (dormant), exam pédagogique, mémoire consolidée, X balance monitor + disjoncteur LLM, directive proposal, exposition/culture — chacun avec son propre gate étroit |

### 1.5 Commandes Telegram
Trois niveaux d'accès : public (aucune vérification), admin (ID Telegram dans la liste des
administrateurs), propriétaire (un seul ID, plus strict qu'admin — seul lui touche au
kill-switch).

| Commande | Niveau | Rôle |
|---|---|---|
| `/start` | public | Bienvenue ; lève la pause si propriétaire |
| `/whoami` | public | Identité Telegram (jamais la liste admin à un non-admin) |
| `/stop` | propriétaire | Coupe toute action sortante (kill-switch) |
| `/resume` | propriétaire | Relève la pause |
| `/riskresume [poche]` | propriétaire | Lève le coupe-circuit portefeuille (drawdown/pertes) — depuis le 27/07 (plan à 3 poches), sans argument lève les 3 poches (scalping/swing/vc) à la fois, ou cible une poche précise en argument |
| `/vc <contrat>` | admin | Analyse VC complète d'un contrat |
| `/walletscore` | admin | Note un wallet, analyse immédiate |
| `/topwallets` | admin | Classement des meilleurs investisseurs |
| `/feedback` | admin | Bilan du paper-trading |
| `/ledger` | admin | Détail position par position |
| `/agentwallet` | admin | Solde réel du wallet agent CDP |
| `/funnel` | admin | Cumul des rejets momentum (48h) |
| `/regime` | admin | Performance par régime macro |
| `/counterfactual` | admin | Que seraient devenus les candidats rejetés |
| `/sentiment` | admin | Dernière lecture de sentiment marché |
| `/watchlist` | admin | Top candidats du pool VC screené |
| `/feuvert` | admin | Scorecard avant argent réel (8 cases) |
| `/mode [standard\|scalping]` **(nouveau, 26/07)** | admin | Bascule le mode d'entrée du test Milly ($1M) — sans argument, affiche le mode courant ; scalping remplace entièrement swing/VC sur ce portefeuille, jamais un mélange, jamais automatique |
| `/polymarket` **(nouveau, 26/07)** | admin | Aperçu du portefeuille papier dédié (100k$) sur les marchés de prédiction Polymarket |
| … (~25 autres, sur 44 commandes enregistrées au total) | admin | langue, thèses, calibration, X, avatar, répertoire, GitHub, canal directives… |

⚠️ **Correction 28/07** : en texte libre (hors commande) sur Telegram, un non-admin ne passe plus
par une réponse publique "rate-limitée" du cerveau — depuis le 24/07 (décision opérateur
explicite, "verrouille aria"), l'espace est verrouillé par défaut : un visiteur non-admin reçoit un
message figé ("cet espace est réservé à l'équipe") et ne déclenche plus du tout d'appel LLM,
sauf si l'opérateur réactive `ARIA_TELEGRAM_PUBLIC_CONVERSATION_ENABLED` (OFF par défaut). `/start`
et `/whoami` restent inchangés (zéro coût LLM, jamais concernés par ce verrou). Ce verrou est
strictement scopé à la surface Telegram : le mode "grounded" public décrit plus haut (§1.1) reste
pleinement actif sur le widget de chat du site web (`vanguard/backend`, visiteur ET membre connecté
via Privy), qui appelle toujours `aria_brain.process(public_mode=True)` sans ce gate. Un admin
traverse une cascade de détecteurs déterministes en langage naturel (approbation/rejet de
connaissance, vote, questions de statut) avant de retomber sur le cerveau général si rien ne
matche.

## Partie 2 — L'argent réel : wallets, garde-fous, smart contract

Règle absolue du projet : jamais de trade automatique sur du capital réel sans validation humaine
— sauf trois exceptions nommées, bornées, et documentées ci-dessous.

### 2.1 wallet_guard — l'escalade Telegram (chemin historique)
Le mécanisme d'origine, pensé pour trois actions seulement : `client_fund_job`, `trade_tokens`,
`onchain_anchor_sepolia`.

1. Vérifie le kill-switch avant toute création de demande.
2. Crée une demande d'approbation en base, à l'état `pending`.
3. Envoie un prompt Telegram à 3 boutons : Oui / Non / Explique-moi pourquoi.
4. Si l'envoi Telegram échoue, la demande reste `pending` indéfiniment — aucune dépense n'a lieu.
5. Aucun timeout n'existe. Un état "expiré" est défini dans le code mais jamais atteint — une
   demande reste en attente tant qu'aucun clic ne survient.

Anti-double-clic : la résolution utilise une transition de base de données atomique (le premier
clic "gagne la course", le second reçoit "déjà traitée") — une double exécution est
structurellement impossible, pas seulement improbable.

Ce chemin est aujourd'hui périmé pour le trading réel : le pilote agent-wallet (§2.3) tourne sur
un chemin séparé qui ne l'appelle jamais.

### 2.2 Le kill-switch /stop
État persisté dans un fichier dédié, réécrit de façon atomique (jamais de fichier à moitié
écrit). Comportement volontairement asymétrique : les tweets/publications continuent même si
l'état est illisible (dégradation douce), mais toute dépense d'argent est bloquée par défaut si
cet état ne peut pas être lu avec certitude (prudence maximale sur l'argent). Le kill-switch ne
bloque jamais la messagerie Telegram elle-même — `/stop` et `/resume` restent toujours joignables.

### 2.3 Le pilote agent-wallet réel (Coinbase CDP)
Le seul endroit du système où ARIA décide et exécute seule sur du vrai capital mainnet (10-15$),
sans clic Telegram par transaction.

```
plafond dur      = 15.0 $ (swap ET transfert)
slippage         = 10% forcé, toujours — jamais la valeur demandée par l'appelant
sizing           = min(solde_réel × 3%, 15.0 $)
cooldown panne   = 60 minutes (échec transitoire : RPC, slippage)
cooldown SDK     = 7 jours (signature d'un bug structurel du SDK)
candidats/cycle  = 5 maximum
chaîne           = Base uniquement
```

La détection de position déjà ouverte se fait en lisant les vrais tokens détenus (pas un seuil de
solde ambigu qui confondrait un token réel avec de la poussière de frais). Adresse de transfert
autorisée gravée en dur dans le code (`ALLOWED_TRANSFER_ADDRESS`, `agent_wallet_pilot.py`), jamais
une variable modifiable sans revue de code — valeur vérifiée en direct le 28/07 :
`0x584b2B35dac347B2317da0d21b95063de51257Ef` (a déjà changé une fois, cf. CLAUDE.md 23/07 —
revérifier dans le code avant de la citer à nouveau plutôt que de la recopier de mémoire). Ce
chemin n'importe jamais `wallet_guard` — c'est une séparation structurelle, vérifiée par des tests
automatiques dédiés (`test_agent_wallet_pilot_never_uses_wallet_guard_and_gated_off` et deux autres
dans `test_coherence.py`).

Le pilote réutilise le pipeline momentum déjà construit pour le paper-trading (§5) comme moteur
de décision — même honeypot, même R/R, même alignement technique — appliqué ici à un montant sans
conséquence en cas d'erreur.

### 2.4 x402 — les micropaiements
```
plafond hebdo = 5.0 $ (semaine calendaire, remise à zéro chaque lundi 00h00 UTC)
réseaux autorisés = {base, eip155:8453}
actif accepté = USDC uniquement
```

Chaque appel qui reçoit un code "paiement requis" (402) traverse une cascade en 11 étapes
fail-closed : kill-switch vérifié en premier (avant même de connaître le prix), actif/montant/
réseau vérifiés, plafond hebdomadaire vérifié, solde réel vérifié, signature, puis nouvelle
tentative — seules les dépenses réussies comptent contre le plafond (un blocage ou un échec ne
consomme jamais le budget).

### 2.5 Sepolia — le rehearsal testnet et la formule de Kelly
Un testnet où la monnaie n'a aucune valeur réelle — ARIA y décide et agit en totale autonomie,
comme entraînement avant le mainnet.

```
b        = gain_moyen% / |perte_moyenne%|
brut     = taux_de_réussite − (1 − taux_de_réussite) / b
tempéré  = brut × 0.5                    # demi-Kelly
fraction = max(0, min(0.20, tempéré))    # jamais plus de 20%

taille_position = 10 000 $ (capital FICTIF de répétition) × fraction
```

Sous 5 trades clôturés : fraction fixe de repli = 1%.

Coupe-circuit après 4 échecs consécutifs. Plafond de 12 transactions/jour. Chaîne verrouillée en
dur (Sepolia, chain_id 84532) — toute demande sur une autre chaîne est refusée avant même de
toucher la clé. Le swap de test porte sur une paire dédiée, jamais le token réellement analysé
(chaîne différente du mainnet, aucun contrat en commun).

### 2.6 Le smart contract AriaLedger.sol
Un coffre-fort on-chain minimal : aucun fonds n'y transite jamais. Il stocke une empreinte de 32
octets à chaque appel — jamais un montant, jamais une clé.

| Fonction | Rôle |
|---|---|
| `anchor(root)` | Ajoute une nouvelle empreinte, horodatée par le bloc — réservé au propriétaire |
| `anchorCount()` | Nombre total d'empreintes déposées |
| `anchorAt(index)` | Lit une empreinte précise |
| `latest()` | Dernière empreinte déposée |
| `transferOwnership(x)` | Transfère la propriété du contrat |

À quoi ça sert, en clair : ARIA (ou l'opérateur) résume tous les verdicts d'une période en une
seule empreinte (une racine de Merkle, calculée hors chaîne), et publie cette empreinte sur Base.
La blockchain horodate l'opération et ne peut plus être modifiée — personne, pas même ARIA, ne
peut ensuite prétendre qu'un verdict a été écrit à une autre date ou le retoucher discrètement :
toute modification changerait l'empreinte. C'est une preuve d'intégrité, jamais un transfert de
valeur.

Calcul de la racine : SHA-256 avec séparation de domaine (préfixe différent pour une feuille et
pour un nœud interne, contre une attaque de "second pré-image"), sérialisation JSON canonique
(deux machines produisent toujours la même empreinte). Le serveur ARIA ne signe jamais rien sur
mainnet — la signature reste toujours locale, sur un wallet opérateur, jamais sur le VPS. Sepolia
est la seule exception (testnet, clé dans l'environnement du VPS).

### 2.7 Récapitulatif

| Mécanisme | Plafond / seuil | Gate |
|---|---|---|
| Pilote agent-wallet — transaction | 15.0 $, slippage 10% forcé | `ARIA_AGENT_WALLET_PILOT_ENABLED` |
| Pilote agent-wallet — sizing | 3% du solde réel | — |
| Pilote agent-wallet — transfert | adresse unique en dur | `ARIA_AGENT_WALLET_TRANSFER_ENABLED` + pilote |
| x402 — hebdomadaire | 5.0 $/semaine calendaire | actif dès le premier appel payant |
| Sepolia — Kelly | demi-Kelly, plafond 20% | `ARIA_SEPOLIA_AUTONOMOUS_ENABLED` (triple gate) |
| Sepolia — coupe-circuit | 4 échecs consécutifs | — |
| AriaLedger — ancrage mainnet | aucun plafond $ (aucune valeur transférée) | `ARIA_ONCHAIN_ANCHOR_ENABLED` |

## Partie 3 — Smart money et scoring de wallets

« Smart money » = un comportement mesurable, jamais une identité ou une taille de portefeuille.
Deux moteurs distincts vivent dans le même fichier `services/smart_money.py` : un signal de
confirmation par token, et un moteur de notation complet par wallet (`/walletscore`).

### 3.1 Signal token-centrique (confirmation sur un token précis)
Jusqu'à 8 plus gros détenteurs analysés (hors pool de liquidité et adresse zéro). Chaque wallet
est jugé sur 4 critères :

```
cohérent_dans_le_temps  = ≥2 jours distincts d'activité ET ≥2 transactions
entrée_précoce_contrôlée = 1er achat ≤3 jours après création de la paire
                            ET ≥2 achats ET aucun achat isolé >70% du total
sortie_disciplinée       = ≥2 ventes, OU 1 vente avec ≥1 achat déjà fait
wash_trading_suspecté    = une seule contrepartie concentre ≥60% des échanges
                            (nécessite ≥3 échanges hors pool pour juger)

wallet_qualifié = disponible ET NON wash_trading ET NON contrat
                  ET (critères remplis parmi les 3 premiers) ≥ 2
```

Il faut au moins 2 wallets qualifiés pour qu'un signal se déclenche — un seul wallet convergent
ne fait jamais bouger le score. La magnitude du bonus dépend désormais du meilleur score de
scoring réel connu sur ces wallets, pas d'un forfait fixe :

```
bonus_convergence = min(nb_wallets_qualifiés − 1, 3) × 3.0     # plafond 9 points
signal_qualité    = min(100, meilleur_score_connu + bonus_convergence)
delta_score       = arrondi(signal_qualité / 100 × 15)          # plafond 15 points

Exemple : 2 wallets qualifiés, meilleur score connu = 90
  → bonus = min(1,3)×3 = 3 → signal = min(100,93) = 93 → delta = arrondi(93/100×15) = 14
```

Deux wallets à gros score dominent donc toujours dix wallets à score faible — l'inverse de
l'ancien système qui donnait le même forfait quel que soit le niveau réel des wallets convergents.

### 3.2 Le moteur complet /walletscore
Disqualifiants durs : un wallet est écarté d'office s'il est un contrat, s'il est suspecté de
wash-trading (même formule 60% ci-dessus, mais tous tokens confondus, avec une exclusion de
l'infrastructure DEX — une contrepartie revenant sur ≥2 tokens distincts est traitée comme un
pool/routeur, jamais un partenaire de wash-trading), ou si sa source de financement est confirmée
malveillante (vérification GoPlus AML).

```
échantillon suffisant = âge du wallet ≥90 jours ET ≥100 swaps réels
                        (exclut les allers-retours wrap/unwrap et stable↔stable)
```

Seuil anti-chance qui scale avec l'échantillon — corrige un biais réel où un seul trade chanceux
pouvait dominer un petit échantillon :

```
retrait = max(1, arrondi(N × 10%))          # 10% de l'échantillon, aux deux extrémités
si N < 30 trades clôturés → indisponible
PnL_robuste_positif = somme(PnL des trades restants après retrait) > 0

Exemple : 30 trades → 3 retirés de chaque côté. 300 trades → 30 de chaque côté.
```

Plancher de liquidité confirmée : 30 000 $ — sous ce seuil, un pool n'est pas assez fiable pour
valoriser un PnL. Volontairement asymétrique : ne bloque que la jambe d'achat, jamais la vente
(sinon une vraie perte sur un pool devenu illiquide après un rug pull disparaîtrait des
statistiques au lieu d'y apparaître).

Ratio de confiance de prix : proportion de jambes (achat+vente) dont le prix vient d'une
exécution réelle plutôt que d'un repli approximatif — affiché à côté du score, jamais masqué ; en
dessous de 30%, le wallet est aussi exclu de la population utilisée pour comparer les autres.

```
percentile(valeur, population) = 100 × (nb_en_dessous + 0.5×nb_ex_æquo) / taille_population

Calculé séparément sur 4 axes : taux de réussite, ratio de Sortino,
PnL réalisé en $, ratio de diversification (tokens profitables/tokens total).

composite_percentile = moyenne des 4 percentiles disponibles
```

C'est ce `composite_percentile` qui alimente à la fois le bonus smart money (§3.1) et le
classement `/topwallets` (§3.4).

Formules financières sous-jacentes : PnL par trade = quantité × (prix de vente − prix d'achat),
méthode premier-entré-premier-sorti. Ratio de Sortino = rendement moyen ÷ racine carrée de la
moyenne des rendements négatifs au carré (nécessite ≥5 trades et au moins une perte, sinon
indéterminé — jamais un ratio inventé). Drawdown maximum mesuré uniquement sur le PnL réalisé
(les positions encore ouvertes ne sont pas marquées au marché — limite honnêtement documentée).

Un drapeau séparé "suspect positif" (jamais fondu dans le score) s'allume si au moins 3 signaux
extrêmes coïncident : taux de réussite ≥70%, Sortino ≥1.5, diversification ≥3 tokens à ≥60% de
réussite, récurrence d'entrée précoce ≥3 fois — un signal d'alerte à examiner manuellement, pas
une pénalité automatique.

### 3.3 candidate_ranking — le classement du pool VC
```
score_classement = points_sécurité + points_liquidité + points_concentration + points_verdict

points_sécurité      = score de sécurité (§4.2), borné [0,100]
points_liquidité     = 0 si liquidité ≤30 000$
                       sinon min(25, 11 × log10(liquidité / 30 000))
                       (exemples : 100k$→+6, 1M$→+17, plafond 25)
points_concentration = 0 si inconnu ; +10 si top-holder ≤10% ;
                       −10 si ≥30% ; linéaire entre les deux
points_verdict       = SAFE +12 / CAUTION 0 / DANGER −40
```

### 3.4 /topwallets — le classement des meilleurs investisseurs
Sourcing gratuit : des adresses détenant une position notable sur au moins 3 tokens distincts
déjà analysés par ARIA, en excluant toute adresse taguée comme infrastructure (exchange, hot
wallet, contrat brûlé). Le classement lui-même est directement le `composite_percentile` réel
(§3.2) — aucune formule composite séparée.

```
capacité maximale = 600 wallets
éviction PERMANENTE si composite_percentile < 30       (ne revient jamais)
éviction par capacité si hors du top 600               (réversible, peut revenir)
éviction pour inactivité si aucune activité on-chain depuis 90 jours
```

## Partie 4 — Le pipeline VC — builders précoces (poche 85%)

Structurellement différent du momentum (§5) : ici le LLM juge sur des faits riches, un seul veto
déterministe (danger confirmé) peut annuler sa décision. Ce pipeline reste dormant sur le test
1M$ historique 100% momentum (15/07), mais reste actif pour toute analyse manuelle `/vc`.

⚠️ **Mise à jour 28/07** : le sourcing VC réel existe désormais dans le code (plan à 3 poches,
27/07) — une poche `"vc"` dédiée est câblée dans `paper_trader._run_paper_cycle_locked` et source
ses candidats via `candidate_ranking.top_candidates` avec `_default_analyzer` (ce même pipeline
`safety_screen`/`vc_analysis`), en parallèle des poches `scalping`/`swing` (momentum). Gate
dédié `ARIA_MULTI_POCKET_SOURCING_ENABLED`, OFF par défaut dans le code au 28/07 — à VÉRIFIER en
direct (pas supposer) avant de considérer que la poche VC ouvre réellement des positions en prod.
Détail : `docs/HANDOFF_PAPER_TRADING.md`.

### 4.1 Le crible de découverte (safety_screen)
```
liquidité minimum      = 30 000 $
score de sécurité min  = 70 (sur une échelle 0-95)
concentration max      = 30% (un wallet hors pool/burn)
taxe de vente max      = 15%
```

Barrières prioritaires (avant tout jugement de marché) : contrat vérifié obligatoire ; mint
contrôlé par un dev — bloquant seulement si l'autorité n'est ni renoncée, ni un launchpad connu,
ni un contrat (une autorité inconnue reste bloquante, jamais un bénéfice du doute) ; pas de
fonction blacklist ; pas de désactivation des transferts possible ; concentration inconnue =
rejet (jamais "OK par défaut").

Barrières honeypot dynamiques (GoPlus) : honeypot confirmé, revente totale bloquée, taxe de vente
>15%, owner caché, reprise de propriété possible — chacune bloquante seulement si positivement
confirmée ; une donnée absente n'a jamais d'effet.

Distinction importante : un rejet définitif (mécanisme malveillant confirmé) est traité
différemment d'un rejet mou (liquidité/concentration qui peuvent évoluer avec la maturité du
projet) — un candidat en rejet mou reste éligible à un nouvel essai plus tard, un rejet définitif
ne l'est jamais.

### 4.2 Le score de sécurité — formule complète (0 à 95)
```
score = 50   (base)

Ajustements MARCHÉ (une fois qu'une paire existe) :
  liquidité < 500$    → −25   |   liquidité < 5 000$  → −12   |  sinon → +10
  volume 24h < 1 000$ → −10   |   sinon → +5
  ratio ventes/(achats+ventes) >0.7 → −8  |  <0.35 avec ≥20 tx → +5
  variation prix 24h ≤−40% → −15  |  ≤−20% → −8

Ajustements ON-CHAIN (additifs, une panne réseau ne dégrade jamais) :
  mint détecté −30 SEULEMENT si mint_authority ∈ {eoa, unknown, non résolu}
    (0 si ∈ {renounced, launchpad, contract} — corrigé le 22/07, voir Partie 13)
  blacklist détectée −30 | transferts désactivables −30
  top holder (hors pool/burn) >50% → −20

Ajustements FONDAMENTAUX (CoinGecko, si demandés) :
  ratio valorisation-diluée/capitalisation ≥3.0 → −10

Ajustements HONEYPOT (GoPlus, si demandés) :
  honeypot confirmé −60 | vente totale impossible −40 | achat impossible −40 (24/07, symétrique
    de cannot_sell_all — aucun émetteur légitime, même stablecoin réglementé, n'a besoin de
    bloquer l'achat)
  taxe de vente ≥10% −20 | taxe d'achat ≥10% −10
  owner caché −20 | reprise de propriété possible −20
  taxe/slippage modifiable après coup −15 (22/07, pouvoir dissimulé distinct de l'owner caché)
  owner peut modifier le solde d'un wallet −40 (22/07, vecteur de perte totale, trouvé sur la
    position momentum réelle CNX — distinct du honeypot classique qui ne bloque que la revente)

Verdict forcé à DANGER, quel que soit le score, si l'un de ces 4 signaux est CONFIRMÉ : honeypot,
vente totale impossible, achat impossible, owner peut modifier le solde d'un wallet.

Quatre autres signaux GoPlus (contrat proxy upgradeable, mint actif, capacité blacklist,
transferts pausables) sont CONTEXTUALISÉS (24/07) plutôt que pénalisés mécaniquement dans un
sens ou l'autre : normaux et attendus chez un émetteur stablecoin réglementé déjà reconnu
(Circle/Tether — ex. USDC/USDT sont légitimement proxy + mintable + blacklist-capable), un vrai
point d'attention chez un déployeur anonyme/inconnu. Jamais de malus/bonus automatique — seul
`cannot_buy` (ci-dessus) reste un véto dur sans exception, aucun cas légitime trouvé.

score final = borné entre 5 et 95

Verdict :
  SAFE si score≥70 ET liquidité≥5 000$ ET volume≥1 000$
  DANGER si score<35 OU liquidité<500$
  sinon CAUTION
```

Cas particulier d'un token encore en phase de "bonding" (pré-graduation, ex. Virtuals) : aucune
paire DEX normale n'existe encore, donc aucune pénalité de liquidité — le score se base plutôt
sur la progression réelle vers la graduation et le nombre de détenteurs.

La détection de mint (`has_mint`) ne regarde que les fonctions réellement appelables de l'ABI du
contrat (jamais le code source brut) — corrige un vrai bug passé où la fonction interne standard
`_mint`, présente dans presque tout token même à offre fixe, produisait un faux positif
systématique.

### 4.3 mint_authority — qui contrôle le mint
```
pas de mint externe          → sans_objet
déployeur = launchpad connu  → launchpad
owner = adresse morte        → renoncé
owner = un contrat           → contrat
owner = un wallet externe    → EOA (dangereux)
indéterminable               → inconnu
```

Normes par launchpad, vérifiées et documentées : Virtuals (allocation équipe 15-20%, courbe de
bonding) ; Flaunch (pas de bonding, frais 1% créateur) ; Zora (taxe anti-sniper 99% décroissante
sur 10 secondes, vesting créateur 5 ans) ; Clanker (pas de bonding).

### 4.4 dev_wallet — builder engagé ou farmer
```
seuil détention haute = 40%   (risque de dump si dépassé)
seuil revente lourde  = 50%   (a déjà revendu la majorité de sa dotation)
```

Deux compteurs, "préoccupation" et "aligné", sur 3 dimensions (détention / mode d'acquisition /
comportement de vente) :

```
concern≥2 ET concern>aligned    → "préoccupant"
aligned≥2 ET concern==0         → "aligné"
concern>0 ET concern≥aligned    → "préoccupant"
sinon                           → "neutre"
```

Le code documente honnêtement que c'est une heuristique "à calibrer en conditions réelles", pas
une mesure garantie exacte.

### 4.5 liquidity_depth — profondeur de liquidité
```
ratio_minimum = liquidité / capitalisation ≥ 30%
sur courbe de bonding (ex. Virtuals) → neutralisé, jamais de pénalité
(liquidité exponentielle, mince par construction en tout début de courbe)
```

Purement informationnel : un ratio insuffisant ajoute un signal d'alerte mais n'affecte pas
directement le score de sécurité — contrairement aux autres signaux qui, eux, le modifient.

### 4.6 Le jugement LLM (vc_analysis)
```
plafond dur de taille = 10% du capital, quel que soit le modèle
si recommandation ≠ BUY → taille forcée à 0%
```

Un seul appel LLM (profondeur "develop", jusqu'à 1800 tokens, température 0.2) produit un verdict
structuré : résumé, potentiel (0-10), niveau de risque, recommandation (BUY/WATCH/SELL/AVOID),
taille suggérée, niveaux entrée/invalidation/cible, scénarios haussier/central/baissier avec
probabilités, liste explicite des données manquantes. Toute valeur hors schéma retombe sur un
défaut prudent (recommandation → AVOID, risque → EXTRÊME) plutôt que de planter.

Le seul veto déterministe, non contournable : si le scan de sécurité frais (recalculé à chaque
analyse, jamais une donnée périmée) classe le token DANGER, la recommandation est forcée à AVOID
et la taille à 0% — quoi que dise le LLM. Ce garde-fou est documenté comme rempart direct contre
l'incident public d'un autre agent IA (AIXBT) vidé par une injection sans contrôle non-LLM.

Si le LLM est désactivé, en panne, ou renvoie une sortie illisible : repli déterministe, jamais de
BUY — DANGER→AVOID, SAFE→WATCH, sinon WATCH également.

## Partie 5 — Le pipeline momentum — le test 1M$ en cours

C'est le pipeline qui tourne réellement sur le portefeuille papier de 1 000 000 $ (poche
"swing" depuis le passage à une architecture à 3 poches, voir note en fin de section).
Contrairement au VC (des seuils déterministes décident presque seuls, le LLM n'intervenant qu'en
second avis), c'est un pari technique sur un token déjà liquide et en mouvement.

**Garde-fous durs à l'entrée, dans l'ordre RÉEL d'exécution** (21/07, du plus rapide/gratuit au
plus rare/coûteux — l'ordre a bougé depuis, honeypot et concentration se sont tous deux déplacés,
voir les notes sur chaque ligne) :
```
liste noire (contrat déjà confirmé piégeux)   → rejet immédiat, aucun appel réseau
liquidité minimum        = 50 000 $  (doublée à 100 000 $ en régime macro Peur,
                             15 000 $ en mode scalping)
volume 24h minimum       = max(500 $, 1% de la liquidité du pool)
ratio volume/liquidité   = rejet si >20× de façon SOUTENUE (≥75 secondes)
mouvement 24h déjà fait  = rejet si >+200% (levé en régime Euphorie confirmé) — entre 200% et
                             350%, une convergence smart money CONFIRMÉE peut "sauver" le rejet
                             (task #3, 22/07) ; au-delà de 350%, rejet dur sans exception
profil projet établi     = DexScreener payant OU listing CoinGecko, sinon rejet
honeypot (GoPlus)        = déplacé EN DERNIER parmi les garde-fous durs (21/07) — c'est la
                             ressource la plus RARE de tout le pipeline (~55 requêtes/min
                             soutenues, cf. `docs/api-rate-limit-calibration.md`), jamais dépensée
                             sur un candidat qui allait de toute façon être rejeté gratuitement
                             plus haut ; reste le SEUL garde-fou fail-closed sur panne réseau
```

**Signal technique et décision**
```
Setup recherché : golden pocket Fibonacci (retracement 0.618-0.786)
                   + divergence haussière RSI, sur ≤25 bougies — le pivot RSI récent doit lui-même
                   tomber dans la plage ABSOLUE [20, 40] (25/07, incident réel ZEN : un achat sur
                   "RSI remonte 39 → 40" avait été validé par le seul critère relatif — prix plus
                   bas + RSI plus haut que le creux précédent, sans aucun plancher/plafond sur le
                   RSI lui-même — hors de toute vraie zone de survente)

R/R franc ≥2.0 ET alignement technique ≥2/3 signaux  → achat DIRECT
R/R entre 1.0 et 2.0                                  → un seul appel LLM tranche
                                                          (confirmation + sécurité fusionnées)
sinon                                                 → HOLD
```

Concentration des holders (top 10 hors pool/burn/contrats vérifiés ≥80%) : déplacée APRÈS ce
calcul de R/R (26/07, audit du pipeline complet — 333 paiements x402 Blockscout réels/0,666$
trouvés depuis le 21/07, dont 31% de purs doublons sur des candidats déjà rejetés gratuitement à
l'étape R/R juste au-dessus). Le seuil (80%) reste inchangé, seul le MOMENT du check a bougé —
il ne se déclenche plus qu'une fois un vrai setup golden pocket + RSI confirmé.

Volume relatif (RVOL) de la bougie déclenchante : rejet dur si un vrai volume est disponible et
confirme <3× la moyenne des 10 bougies précédentes ; rejet aussi si le ratio atteint 3× mais que
la bougie déclenchante représente <2 500$ en valeur absolue (un ratio élevé sur une moyenne
effondrée n'est pas un vrai flux de capital) ; si la donnée est structurellement absente, jamais
de rejet mais un malus de conviction s'applique à la taille.

**Plancher quotidien de trades (diagnostique, 23/07 → 27/07)** : un cycle heartbeat additif et
indépendant force jusqu'à 5 ouvertures par passage pour atteindre `DAILY_TRADE_FLOOR` = 30
positions ouvertes par jour civil (relevé depuis un chiffre initial de 5 — le texte de CLAUDE.md
n'a pas encore été recalé sur cette valeur, écart noté dans `docs/trading-thresholds-calibration.md`)
sur des candidats qui échouent SEULEMENT les gates de QUALITÉ (volume, profil projet, R/R, RVOL)
— jamais les gates de SÉCURITÉ (liste noire, honeypot, liquidité, wash-trading, concentration,
plafond parabolique), qui restent tous appliqués intégralement. Le coupe-circuit de risque reste
vérifié en premier, prioritaire sur cet objectif de volume. Sizing désormais identique à un trade
normal (formule risque/ATR ci-dessous, plus de taille fixe à 1% du capital — décision opérateur
du 25/07, "enlève le truc qui force les positions avec 1% du capital", pour ne plus plafonner
artificiellement l'upside d'un candidat réellement fort).

**Sizing — composition complète**
1. Budget de risque par palier de conviction : FORT 1.5% / MODÉRÉ 1.0% / FAIBLE 0.5%
2. Divisé par la largeur du stop suiveur ATR (2.5 × ATR, borné [5%, 40%]) → allocation de base,
   plafonnée au maximum historique du même palier (5%/3.5%/2%)
3. × multiplicateur de rythme hebdomadaire (0.5 si l'objectif +10% est déjà atteint)
4. × multiplicateur de régime macro (0.5 en régime Peur confirmé)
5. × multiplicateur de drawdown portefeuille (0.5 si -10% à -20% depuis le plus haut)
6. Plafond dur final : jamais plus de 2% du capital risqué au pire cas
7. Plafond par impact de prix : réduit encore si CET ordre ferait trop bouger CE pool
8. Prix de remplissage simulé : toujours ≥ prix spot (un achat pousse le prix vers le haut)

**Gestion de la position**
```
Stop suiveur     : largeur = 2.5×ATR (borné [5%,40%]), ne se resserre jamais en dessous
Point mort verrouillé (Breakeven Hard Floor) : dès que le prix touche et TIENT
                   (≥75 secondes) au moins 50% de la distance vers la 1ère cible
                   (plancher absolu 8%), le stop remonte irrévocablement au prix d'entrée
Prise de profit  : 3 paliers dynamiques ancrés sur la cible technique
                   (TP1 = cible ; TP2 = 2× la distance TP1 ; TP3 = 3×),
                   1/3 de la quantité initiale vendue à chaque palier
Régime Peur      : le 3e palier est écrasé (sortie accélérée)
Régime Euphorie  : le 3e palier est neutralisé (moon bag pur, guidé par le stop seul)
```

Reset hebdomadaire : clôture forcée au prix médian des 5 dernières bougies (anti-mèche),
archivage complet, verdict contre l'objectif +10%, remise à 1 000 000 $ chaque semaine — que la
précédente ait réussi ou non. C'est une boucle d'entraînement répétée, jamais une porte de sortie
unique. Fonctions désormais scopées par poche (`weekly_cycle_due(wallet=...)`/
`run_weekly_reset(wallet=...)`, défaut `"swing"`, 27/07) — voir la note ci-dessous.

⚠️ **Architecture à 3 poches (27/07, en cours de déploiement)** : le portefeuille 1M$ décrit
ci-dessus devient progressivement 3 poches indépendantes de 1M$ chacune (scalping/swing/vc),
sourcées et gérées séparément — la poche `"swing"` correspond à ce qui est décrit dans cette
Partie 5, `"scalping"` reprend la même discipline avec les paramètres dédiés déjà notés dans
CLAUDE.md (bougies 15-30min, RSI period=10, golden pocket [20,40]), `"vc"` source désormais
réellement le pipeline VC de la Partie 4 (voir sa note de mise à jour). Gate dédié
`ARIA_MULTI_POCKET_SOURCING_ENABLED`, OFF par défaut dans le code au 28/07 — état réel en prod à
vérifier en direct, jamais supposer. Le reset hebdomadaire ET le coupe-circuit de risque (Partie
6) sont désormais tous les deux PAR POCHE, plus un coupe-circuit MACRO agrégé sur les 3 poches
combinées (voir Partie 6). Détail complet : `docs/HANDOFF_PAPER_TRADING.md`.

Le pipeline bonding (Virtuals, tokens pré-graduation) tourne sur ce même test 1M$ mais suit des
règles entièrement différentes, largement retravaillées entre le 24/07 et le 28/07 (Items
#152-167) — voir la Partie 5bis ci-dessous plutôt que de chercher cette information ici.

### 5bis. Le pipeline bonding (Virtuals)

Moteur de décision SÉPARÉ (`bonding_entry.py`) pour un token Virtuals encore sur sa courbe de
bonding (aucun pool DEX n'existe encore) — branché sur le même test 1M$ actif (feu vert
opérateur, 24/07), mais qui ne partage presque aucune règle avec le momentum standard ci-dessus :
ni DexScreener ni GeckoTerminal n'existent encore pour ce token, donc toute la mécanique
liquidité-DEX/OHLCV classique est structurellement inapplicable.

**Ce qui a été retiré du pipeline standard, et pourquoi** :
- Le check honeypot GoPlus (orienté DEX/Base) — pas de logique de contrat séparée à exploiter ici
  au-delà du contrat protocole Virtuals lui-même, déjà utilisé par tous les tokens Virtuals.
- Le plancher de liquidité 50 000$ — protège contre un retrait de LP externe, qui ne s'applique
  pas à une réserve de bonding détenue par le PROTOCOLE, jamais un dev individuel.
- Le golden pocket/RSI calculé sur des bougies DexScreener/GeckoTerminal (inexistantes) —
  reconstruit à la place depuis l'historique RÉEL de trades individuels
  (`services/virtuals.py::fetch_recent_trades`, endpoint `vp-api.virtuals.io`, confirmé en direct
  et sans authentification) agrégé en bougies par NOMBRE FIXE de trades (5 trades/bougie, jamais
  un intervalle de temps fixe — la densité de trading varie trop sur une bonding curve).

**Champs natifs Virtuals repris comme garde-fous durs** (trouvés dans le même endpoint déjà
appelé pour lister les tokens, jamais captés avant) :
```
détention équipe (dev_holding_pct)     ≤ 5%   — inconnue ou dépassée = rejet (fail-closed)
concentration top10 (top10_holder_pct) ≤ 80%  — appliquée SEULEMENT si ≥50 holders réels
                                                  (relevé de 15→50 le 28/07 : un échantillon réel
                                                  de 50 tokens en bonding n'a trouvé qu'1/50 avec
                                                  ≥15 holders, et celui-là restait à 100% de
                                                  concentration — 15 n'était jamais un vrai seuil
                                                  d'échantillon suffisant)
liquidité de la réserve de bonding     ≥ 10 000$  — proxy de market cap (les deux suivent de très
                                                      près sur une bonding curve, observation
                                                      opérateur)
prix réellement convertible en USD     — sinon HOLD (jamais un prix inventé)
```
Sous le seuil de 50 holders, la concentration n'est plus neutre (moitié du poids du pilier) mais
quasi nulle (20% du poids, 28/07) — un signal jugé non informatif à ce stade, pas un bénéfice du
doute mécanique.

**Le score composite (24/07, remanié 28/07)** — remplace ce qui était au départ des rejets durs
sur dev_holding/concentration : testé en direct contre les 100 vrais prototypes disponibles le
jour du lancement, TOUS rejetés sur la concentration (même le token avec le plus de holders,
NISTIC, 33 holders, encore à 100% de concentration). Cause racine vérifiée contre le whitepaper
officiel Virtuals ET le vrai formulaire de lancement (`app.virtuals.io/create`) : le module de
vesting d'équipe est désactivé par défaut et, même activé, verrouillé 1 an post-TGE — donc
`dev_holding_pct=0%` et une concentration mécaniquement écrasée par un petit pool d'acheteurs
sont des faits STRUCTURELS de ce stade de marché, pas des signaux de risque. Conclusion opérateur
(revue croisée LLM externe confirmée avant codage) : sur un token aussi jeune, le vrai edge est un
pari sur le PRODUIT/ÉQUIPE/adoption, pas sur des métriques on-chain qui ne veulent encore rien
dire.

```
score composite = poids_dev_sécurité + poids_produit_conviction
                   + poids_setup_technique + poids_concentration

poids_dev_sécurité (35 pts max)       = 35 × (1 − dev_holding_pct / 5%)
poids_produit_conviction (35 pts max) = potentiel/10 × 35
                                          (potentiel=None -- équipe discrète mais réelle -- reste
                                          neutre à 17,5/35, jamais pénalisé pour manque de buzz)
poids_setup_technique (15 pts max)    = 0 si pas de setup golden pocket/RSI (28/07, item #152 --
                                          N'EST PLUS un rejet dur, voir plus bas) ; sinon marge de
                                          R/R (9 pts, référence R/R=5.0) + marge d'alignement
                                          technique (6 pts)
poids_concentration (15 pts max)      = 15 × (1 − top10_holder_pct / 80%) si ≥50 holders réels,
                                          sinon 15 × 0,2 (quasi nul, non informatif)

BUY si score composite ≥ 60/100 (seuil de départ, choix opérateur explicite -- "on commence à
soixante sur cent et on la laisse trader, si mauvais résultats on ajustera")
```

**28/07 — le setup technique n'est plus un rejet dur (item #152)** : jusqu'à cette date,
l'absence de golden pocket/divergence RSI rejetait AVANT même que le score composite soit
calculé — un cas réel (HOLO) a été rejeté uniquement pour cette raison, son équipe/produit
jamais évalués. Un token aussi jeune manque structurellement d'historique de trades pour qu'un
setup Fibonacci/RSI veuille dire quelque chose ; recherche du 28/07 en accord avec l'instruction
opérateur explicite ("les tokens en bonding sont tradés par potentiel, grâce au facteur équipe,
produit, fondamentaux"). Un setup absent note désormais simplement 0/15 sur ce pilier, le score
composite tranche seul, même doctrine déjà appliquée à la concentration/dev security.

**R/R et fallback (28/07, item #152)** : sans setup technique détecté, la cible/l'invalidation
retombent sur des multiples fixes du prix d'entrée — cible ×2.0, invalidation ×0.35 (perte max
65%), ancrés directement sur le design de sortie ci-dessous (Take-Seed / stop total) plutôt
qu'inventés séparément, pour que le R/R affiché reste cohérent avec la gestion réelle de la
position.

**Devise** : un trade bonding est coté en $VIRTUAL par token, jamais en USD directement — chaque
niveau de prix renvoyé (entrée/cible/invalidation) est converti via `virtuals.virtual_usd_rate()`
juste avant d'être remis au portefeuille (100% USD). `entry_atr_pct` (un ratio ATR/prix, dans la
même unité $VIRTUAL des deux côtés) reste volontairement NON converti — le facteur d'échange
s'annule algébriquement, le convertir serait un point de défaillance en plus pour rien.

**Sizing** : même formule risque/ATR que le momentum standard (`paper_trader.compute_entry_alloc`),
plus une réduction supplémentaire dédiée `BONDING_SIZE_REDUCTION = 0.5×` — risque structurellement
plus élevé (pas de check honeypot, marché plus mince), demande explicite opérateur pour une taille
plus prudente que le palier momentum standard.

**Sortie — paliers de prise de profit dédiés (item #154, 28/07)**, distincts du système momentum
générique ci-dessus (multiples de PRIX fixes, jamais une cible technique — une position bonding
peut ne pas en avoir) et avec un VRAI reliquat jamais vendu mécaniquement :
```
BONDING_TP_STAGES          = (+100%, +400%, +1150%)  = 2×/5×/12,5× le prix d'entrée
                              (Take-Seed / Tier2 / Tier3)
BONDING_TP_STAGE_FRACTIONS = (45%, 25%, 20%) de la quantité INITIALE — ~10% jamais vendu
                              mécaniquement (moonbag pur, géré uniquement par le stop suiveur ATR)
```
Recherche à l'origine de ces paliers (28/07) : des cas réels de lancements bonding gagnants vont
de 100× à ~11 900× au pic, mais TOUS ont ensuite rendu 92% à 99,8% de ce pic dans l'année qui a
suivi — la discipline de sortie compte plus que la conviction sur cette classe d'actif, intuition
opérateur ("elle peut vendre en plusieurs paliers pour sécuriser") directement confirmée par la
recherche.

**Stop de perte à 3 volets (item #155, 28/07)**, ADDITIF — jamais un remplacement du stop suiveur
ATR générique, qui continue de s'appliquer en parallèle sur une position bonding :
```
Volet 1 (statique)  : le clamp cible/invalidation du fallback ci-dessus (×0.35, perte max 65%) --
                       élargi (jamais resserré) si un niveau technique existait et était plus
                       serré. Un cas réel (HOLO, projet actif non-fantôme) a montré des swings
                       -55%/+122% comme du bruit NORMAL sur cette classe d'actif -- un stop
                       technique plus serré sortirait sur ce bruit, pas sur un vrai échec.
Volet 2 (vélocité)  : référence de prix glissante, réancrée toutes les 30 min
                       (BONDING_VELOCITY_WINDOW_MINUTES) -- une chute de 40%
                       (BONDING_VELOCITY_DROP_PCT) ou plus depuis cette référence force une
                       sortie complète immédiate, indépendamment du stop ATR (qui ne réagit qu'à
                       un NOUVEAU plus-haut, jamais à une chute rapide depuis un niveau déjà sous
                       le dernier plus-haut confirmé).
Volet 3 (liquidité) : même patron défense-en-profondeur que la poche VC (Formule B, plus bas) --
                       plancher absolu 10 000$ (BONDING_LIQUIDITY_FLOOR_USD, miroir volontaire du
                       plancher d'entrée) + chute cumulée 50% depuis l'entrée
                       (BONDING_LIQUIDITY_DROP_CUMULATIVE_PCT) + chute soudaine 30% entre deux
                       cycles consécutifs (BONDING_LIQUIDITY_SUDDEN_DROP_PCT) -- une réserve de
                       bonding qui se vide est le même signal qu'un pool DEX qui se vide.
```

**⚠️ Trouvaille empirique du 28/07, PAS ENCORE CORRIGÉE (Item #167 du backlog)** : testé contre
un échantillon réel d'environ 380 candidats bonding vivants, les garde-fous durs actuels de ce
pipeline — essentiellement le plancher de liquidité (10 000$) et la concentration holders —
ne laissent passer PRESQUE AUCUN candidat réel : un taux d'acceptation proche de 0% sur le flux de
tokens réellement en train de bonder aujourd'hui. Trou trouvé, pas encore comblé au moment de la
rédaction de cette mise à jour du Codex — prochaine étape probable : recalibrer un ou plusieurs de
ces planchers une fois davantage de données réelles en main, même doctrine "mesurer avant de
resserrer/assouplir" que le reste de ce pipeline. Détail :
`docs/HANDOFF_PIPELINE_MOMENTUM.md`, `docs/trading-thresholds-calibration.md`.

## Partie 6 — Gestion du risque de portefeuille

Deux mécanismes séparés, jamais confondus : le sizing par trade (une fonction pure, sans mémoire)
et le coupe-circuit de portefeuille (un état persisté qui survit aux redémarrages).

```
Plafond de perte au pire cas   = 2% du capital total, par position
                                  (basé sur la distance jusqu'à l'invalidation technique)

Drawdown SOUPLE (−10% depuis le plus haut d'équité) → allocation ÷2 sur les NOUVELLES entrées
Drawdown DUR (−20%) OU 5 pertes consécutives (tous contrats) → bloque toute nouvelle entrée
                                  (positions déjà ouvertes continuent d'être gérées normalement)

Pertes consécutives PAR CONTRAT ≥2 (mode standard/swing)
                        OU ≥3 (mode scalping, 26/07, item #101) → re-entrée suspendue sur CE
                                  contrat seul (reset à chaque nouveau cycle hebdomadaire)
```

La reprise après un coupe-circuit dur n'est jamais automatique — elle exige une action humaine
explicite (commande `/riskresume`), même si le drawdown s'est entre-temps résorbé tout seul.

⚠️ **Architecture à 3 poches (27/07)** : ce coupe-circuit, historiquement unique pour tout le
portefeuille, est désormais calculé PAR POCHE (`risk_guard.evaluate_portfolio_risk(wallet=...)`,
`resume_new_entries(wallet=...)`, `/riskresume` scopé par poche) — une série de pertes sur la
poche scalping seule ne bloque plus les poches swing/VC, chacune a son propre plus-haut d'équité
et son propre état persisté. Un second coupe-circuit MACRO (`risk_guard.evaluate_macro_risk`),
délibérément plus drastique, agrège l'équité COMBINÉE des 3 poches contre son propre plus-haut :
une chute de **-15%** (`MACRO_CIRCUIT_BREAKER_LOSS_PCT`) déclenche un arrêt total et immédiat de
TOUTES les nouvelles entrées d'un coup, sur les 3 poches à la fois — couvre l'angle mort qu'une
coupure par poche seule laisserait ouvert (un krach réellement corrélé sur les 3 poches en même
temps, chacune juste sous son propre seuil individuel). Vérifié une fois par cycle, AVANT tout
coupe-circuit par poche. Détail complet : `docs/HANDOFF_PAPER_TRADING.md`.

## Partie 7 — Mémoire, identité et gouvernance

### 7.1 dna.yaml — l'identité structurée d'ARIA
Depuis le 21/07, quatre anciens fichiers séparés (valeurs, objectifs, réflexion, persona) ont été
fusionnés en un seul arbre. Quatre branches :

| Branche | Contenu |
|---|---|
| racine | Identité stable (nom, mission, autonomie, règles de sécurité, langue selon la surface) |
| personnalité | Traits de caractère, les 5 grands traits de personnalité (chacun décrit en texte, pas un score chiffré), voix |
| valeurs | Liste priorisée (0-100), ex. "autonomie progressive" (100), "vérité avant buzz" (90) |
| objectifs | Liste avec horizon/priorité/statut (en cours/en attente/terminé) |
| réflexion | Cadence hebdomadaire (7 jours), questions de réflexion posées à ARIA |

Ce fichier n'est lu que par ARIA elle-même — jamais par Claude Code. Ce qui façonne le
comportement d'ARIA vit ici et dans sa base de connaissance ; ce qui façonne le travail de Claude
Code vit dans `CLAUDE.md`. Les deux ne se confondent jamais.

### 7.2 test_coherence.py — le garde-fou mécanique
Une suite de tests (une quarantaine) qui tourne dans la CI à chaque changement et qui DOIT rester
verte — elle rend certaines dérives structurellement impossibles plutôt que de compter sur la
vigilance d'une session future. Un échantillon représentatif :

- Aucune IP publique en clair dans le code ou les tests.
- Le honeypot GoPlus reste actif sur le chemin d'analyse VC — jamais dormant.
- Le cycle papier-trading est bien déclaré dans le heartbeat, jamais une tâche orpheline.
- Le rehearsal Sepolia et le pilote agent-wallet réel n'appellent jamais `wallet_guard` —
  séparation structurelle du garde-fou partagé au capital réel.
- Le pilote agent-wallet ne possède aucune fonction de transfert GÉNÉRIQUE (le test CI ne vérifie
  que l'absence de `def transfer(`/`def withdraw(` à destination libre) — correction 22/07 : ce
  n'est PAS "swap uniquement" depuis le 16/07, `attempt_transfer()` existe bel et bien (Exception
  nommée #4, transfert USDC réel vers une seule adresse codée en dur `ALLOWED_TRANSFER_ADDRESS`).
  Le garde-fou porte sur "pas de destination libre", pas sur "aucun transfert possible".
- Le smart contract `AriaLedger.sol` ne contient jamais de fonction qui accepte ou envoie de la
  valeur — l'ancrage ne déplace jamais de fonds.
- L'endpoint public `/api/pulse` est bien dans la liste blanche publique ; l'endpoint
  `/dossier/{contrat}` exige toujours l'authentification opérateur et reste strictement lecture
  seule.
- Le rapport PDF est toujours chiffré avant envoi, avec un mot de passe généré à la volée, jamais
  codé en dur.
- Toute fonction qui écrit réellement à l'extérieur (GitHub, X, email) doit être déclarée dans un
  registre dédié — garde-fou né d'un incident réel d'écriture externe non tracée.

## Partie 8 — Infrastructure et déploiement

### 8.1 Le déploiement bleu-vert
Le backend alterne entre deux ports internes (8000 et 8001). À chaque déploiement : l'image en
cours est retaguée comme filet de secours, la nouvelle image est construite et démarrée sur le
port "en attente" pendant que l'ancien conteneur continue de servir le trafic réel. Un contrôle de
santé (jusqu'à 10 tentatives, 3 secondes d'intervalle) vérifie que le nouveau conteneur répond ET
que son empreinte de commit correspond à ce qui vient d'être déployé. La bascule nginx n'a lieu
qu'après cette confirmation ; un second contrôle vérifie ensuite le trafic réel à travers nginx
(pas juste le port direct en local). L'ancien conteneur n'est supprimé qu'après cette double
confirmation — s'il y a le moindre échec, il est conservé et la bascule est annulée, sans aucune
coupure de service.

### 8.2 Le chiffrement des rapports PDF
Le rapport `/vc` en PDF est protégé après génération : mot de passe d'ouverture vide (aucune
friction pour le destinataire), mais un mot de passe "propriétaire" généré à la volée (jamais
réutilisé, jamais codé en dur) est requis pour lever les permissions — seule l'impression basique
est autorisée, copier-coller/modification/assemblage sont refusés. Le code documente
honnêtement que ce n'est pas un chiffrement inviolable (un outil dédié peut lever ces permissions
en quelques secondes) : c'est un frein dissuasif, la vraie protection anti-fuite étant un
filigrane nominatif (destinataire + empreinte) sur la version HTML.

### 8.3 Cockpit et dossier
`GET /api/pulse` — endpoint public, retourne un signal grossier : statut, commit déployé, pouls du
heartbeat, si le paper-trading tourne, si l'exécution réelle est active (toujours faux à ce jour),
l'état de l'ancrage on-chain. Aucun secret, aucune donnée de candidat, aucune information
personnelle.

`GET /api/aria/dossier/{contrat}` — gaté opérateur, agrège en lecture pure tout ce qu'ARIA a
consigné sur un contrat donné (analyses VC, carnet de bord, suivi de thèse, positions papier) en
une seule chronologie. Aucun appel réseau, aucune écriture — un contrat jamais analysé renvoie
simplement un dossier vide.

## Partie 9 — Qui décide quoi

- **L'opérateur (toi)** — décision finale sur tout ce qui compte réellement : argent, garde-fous,
  stratégie. Seul détenteur du kill-switch.
- **Claude Code** — construit et exploite le système sous validation, sauf sur un périmètre étroit
  et déjà autorisé explicitement (nettoyage GitHub : code mort, docs qui dérivent, branches
  orphelines).
- **ARIA** — décide et agit seule uniquement là où c'est structurellement fictif ou déjà borné par
  une exception nommée : le portefeuille papier (100% fictif), le rehearsal Sepolia (testnet), et
  le pilote agent-wallet (10-15$ réels, plafonds durs vérifiés à chaque tentative).

Chaque exception au principe "jamais de trade automatique sur du capital réel" est nommée,
bornée, documentée, et structurellement séparée du garde-fou partagé (`wallet_guard`) qui
protégera tout capital réel futur au-delà de ce périmètre.

## Partie 10 — Index des variables d'environnement

Chaque capacité d'ARIA a son propre interrupteur, jamais un seul bouton général (à l'exception du
kill-switch `/stop`, qui coupe tout ce qui est sortant d'un coup).

| Variable | Rôle |
|---|---|
| `ARIA_LLM_ENABLED` | Coupe tout appel LLM (mode "faits vérifiés uniquement") |
| `ARIA_GROUNDED_MODE` | Mode strict "faits vérifiés" pour les visiteurs publics |
| `ARIA_PAPER_TRADING_ENABLED` | Gate commun au portefeuille papier 1M$ (découverte, surveillance, reset hebdo) |
| `ARIA_AGENT_WALLET_PILOT_ENABLED` | Pilote agent-wallet réel — capital mainnet, ACTIF |
| `ARIA_AGENT_WALLET_TRANSFER_ENABLED` | Capacité de transfert USDC du pilote (en plus du swap) |
| `ARIA_AGENT_WALLET_MONITOR_ENABLED` | Surveillance lecture-seule du wallet agent |
| `ARIA_SEPOLIA_WALLET_ENABLED` / `ARIA_SEPOLIA_AUTONOMOUS_ENABLED` | Rehearsal testnet autonome |
| `ARIA_ONCHAIN_ANCHOR_ENABLED` | Ancrage réel sur AriaLedger.sol (mainnet) |
| `ARIA_WALLET_SCAN_QUEUE_ENABLED` / `ARIA_WALLET_SCORING_ENABLED` | File de fond du wallet-scoring et son moteur de notation |
| `ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED` | Extraction de détenteurs pour `/topwallets` |
| `ARIA_MARKET_SENTIMENT_ENABLED` | Lecture continue du régime macro (Peur/Neutre/Euphorie) |
| `ARIA_CONVICTION_RESEARCH_ENABLED` | Diligence X/web/GitHub avant sizing (jamais un gate d'achat) |
| `ARIA_KNOWLEDGE_INBOX_ENABLED` | Propositions de connaissance vers GitHub (jamais un commit automatique) |
| `ARIA_VISION_ENABLED` | Lecture de photos envoyées sur Telegram (admin seulement) |
| `ARIA_BONDING_DISCOVERY_ENABLED` | Découverte pré-graduation Virtuals — ⚠️ voir note de tête, périmé (en réalité `true` en prod depuis avant le 24/07) |
| `ARIA_ACP_ENABLED` | Routage conversationnel ACP — abandonné, resté OFF |
| `ARIA_TIKTOK_PUBLISH_ENABLED` | Publication TikTok — client prêt, jamais activé (pas encore de vraie valeur à y publier) |
| `ARIA_DIRECTIVE_CHANNEL_ENABLED` | Canal ARIA → Claude Code — pilote en ligne, resté OFF |

## Partie 11 — ajoutée le 22/07/2026
### Stress-test du pipeline VC rectifié

> Correction du 22/07 (vérification factuelle) : les « 3 briques » ci-dessous n'ont JAMAIS été
> codées. Une vérification exhaustive contre le dépôt réel (grep sur
> MEV/deguis/disguise/private_sale/raise_history, lecture de
> `vc_analysis.py::_build_untrusted_context`, `dev_wallet.py`, `acp_onchain_scan.py`) ne trouve
> AUCUNE trace de ces 3 mécanismes dans le pipeline — ni câblés, ni même en contexte consultatif
> LLM. Seule trace réelle : une idée non implémentée dans `knowledge/improvement_seeds.yaml`
> (ligne 140, module `services/deployer_history.py` — fichier inexistant), et la protection MEV
> correspond exactement au backlog CLAUDE.md #216, explicitement noté DIFFÉRÉ. La version
> précédente de cette page affirmait à tort qu'elles étaient « ajoutées » et « fonctionnaient » —
> c'était une confusion entre une RECOMMANDATION issue du stress-test et un fait accompli. Ce qui
> suit est corrigé pour refléter ce statut réel : propositions évaluées HYPOTHÉTIQUEMENT dans les
> scénarios, jamais construites.

Après avoir comparé notre pipeline VC à une thèse externe sur l'investissement institutionnel en
small-cap crypto, 3 briques ont été proposées (réputation du wallet déployeur + activité GitHub
pré-lancement, protection MEV pour le pilote agent-wallet, signal renforcé de « sortie de
liquidité déguisée » — aucune codée à ce jour, voir correction ci-dessus) et le reste explicitement
rejeté (deals OTC/SAFT, dispersion des propres achats sur des wallets multiples, accords market
maker/CEX/KOL — hors de portée et/ou contraires à la doctrine de transparence d'ARIA). Un workflow
de 5 agents a ensuite généré 24 scénarios et appliqué mentalement les formules RÉELLES du pipeline
(parties 4 et 6 ci-dessus, telles qu'elles existent aujourd'hui) pour juger, scénario par
scénario, si le verdict rendu est correct — les 3 propositions ci-dessus étaient évaluées
séparément, de façon hypothétique, jamais comme si elles étaient déjà actives.

#### Points forts confirmés

| Constat | Preuve (scénario) |
|---|---|
| Défense en profondeur réelle sur le honeypot pur | Trois mécanismes indépendants (barrière dédiée, malus de score, verdict DANGER forcé) convergent avant même d'atteindre mint_authority/dev_wallet/LLM. |
| Les barrières discrètes couvrent une zone grise plus large que le score seul | Un mint EOA ou une concentration à 60% resteraient en zone ambiguë "CAUTION" pour le score composite seul — les règles dédiées, elles, rejettent net. |
| dev_wallet distingue financement légitime échelonné et extraction malveillante | Deux scénarios de revente en petites tranches documentées (financement d'audit, embauche) jugés correctement neutres/alignés, jamais sur-flagués. |
| Sourcing sain sans sur-filtrage sur des profils standards | Un fair launch communautaire et un launchpad reconnu passent sans friction et capturent un x15,6 et un x6,3 respectivement. |

#### Points faibles trouvés (13, classés par gravité)

Le plus grave, déjà connu — CORRIGÉ le 22/07 (voir Partie 13) — le malus de score de -30 dès
qu'une fonction mint existe ne lisait jamais mint_authority, contrairement au crible dur qui
neutralise correctement un mint timelocké/contrat. Un projet avec un vesting sain (deployeur
réputé, 11 mois de code avant le token) se retrouvait à 35/95 et rejeté du sourcing automatique —
un x50 raté. C'était exactement le défaut #164 déjà noté dans ce document (§ « moteur de
légitimité »), confirmé ici concrètement plutôt qu'en théorie, et corrigé le jour même.

| Gravité | Point faible | Correctif proposé |
|---|---|---|
| grave — corrigé 22/07 | Malus mint -30 ignorait mint_authority | Neutraliser/réduire le malus quand l'autorité est contract/launchpad/renounced — fait, voir Partie 13. |
| grave — corrigé 22/07 | Aucun garde-fou anti-wash-trading côté VC (un volume 65x liquidité est même récompensé, +5) | Réutilise `momentum_entry.MAX_VOLUME_TO_LIQUIDITY_RATIO`/`_wash_trading_ratio_confirmed` — fait, voir Partie 13 (item 5). |
| grave | Manipulation temporaire synchronisée sur la fenêtre de scan (liquidité/volume gonflés puis retirés après achat) | Exiger une confirmation de stabilité sur plusieurs heures, même patron que `HIGH_WATER_CONFIRMATION_SECONDS`. |
| grave | dev_wallet ne surveille que le wallet officiellement étiqueté, pas un second wallet non rattaché | Étendre le signal sortie-déguisée à tout wallet ayant reçu une part significative au TGE. |
| grave | Aucune détection de cluster Sybil — une distribution factice (40 wallets, 78% cumulé) retourne un signal positif | Chantier déjà connu et non résolu : analyse de graphe de financement commun, prioritaire avant réactivation VC, y compris sur le bonding. |
| grave | Vente insider via dépôt CEX intermédiaire — angle mort structurel on-chain | Flag dédié sur transfert vers une adresse de dépôt CEX connue — couverture partielle seulement, limite reconnue. |
| grave | Le PnL papier affiché ne reflète jamais la profondeur réelle du pool (x50 fictif possible sur la vitrine publique) | Appliquer une décote de liquidité au PnL affiché des positions ouvertes, comme l'impact de prix déjà simulé à l'achat. |
| grave | Tension structurelle : la thèse « builders sub-1M$ » et le seuil liquidité/mcap 30% s'excluent mutuellement pour une petite équipe honnête à faible trésorerie | Pas un correctif silencieux — un arbitrage thèse/sécurité à trancher explicitement avec l'opérateur. |
| grave | Détection blacklist par sous-chaîne, contournable par un pattern proxy (fonction cachée, activable après coup) | Activer Webacy (déjà committé, bloqué par une clé API manquante) comme second avis indépendant. |
| modérée — corrigé 22/07 | Plancher de liquidité absolu (30 000$) déconnecté des paliers internes du score (5 000$/500$) — pénalise une équipe qui verrouille sa liquidité en vesting anti-rug | Assoupli si score≥70 ET verdict SAFE ET mint propre — fait, voir Partie 13 (item 7). |
| modérée — corrigé 22/07 | Le champ `slippage_modifiable` de GoPlus est récupéré mais jamais exploité | Malus -15 + barrière hard_fail ajoutés — fait, voir Partie 13 (item 6). |
| modérée | Un futur signal GitHub jugé sur la fréquence des commits (pas leur substance) reproduirait le même faux positif que le LLM actuel | Concevoir le signal pour juger la substance des diffs, pas seulement leur volume. |
| modérée | Fréquence réelle du re-scan de liquidité en continu sur une position VC ouverte non confirmée aussi explicitement que côté momentum | Vérifier dans le code réel (pas supposer) et aligner sur le mécanisme momentum si besoin. |

#### Valeur hypothétique des 3 briques proposées (jamais codées)

> Correction 22/07 — cette section évaluait un scénario hypothétique ("si ces 3 briques
> existaient"), pas un résultat réel : réputation déployeur/GitHub, protection MEV et signal de
> sortie de liquidité déguisée n'existent dans AUCUN fichier du dépôt à ce jour (cf. avertissement
> en tête de Partie 11). Le "verdict" du workflow original portait donc sur une simulation de leur
> effet, jamais sur un code réellement exécuté.

Reformulé honnêtement : SI ces 3 briques étaient construites telles que décrites, le workflow
estimait qu'aucune n'aurait fait basculer un seul verdict final dans les 21 scénarios où elles
s'appliquaient — un signal purement consultatif pour le LLM n'aurait jamais pesé plus lourd que le
crible dur qui décide réellement du sourcing automatique. La protection MEV, à l'échelle actuelle
(10-15$), aurait un bénéfice financier estimé quasi nul (le slippage 10% borne déjà le pire cas à
3 centimes). Conclusion révisée : ces 3 idées restent des PROPOSITIONS non construites — si elles
sont un jour codées, les câbler dans la décision automatique plutôt qu'en contexte narratif serait
nécessaire pour qu'elles aient un effet réel.

#### Priorités de correction (dans l'ordre proposé par le workflow)

1. Corriger le malus mint -30 pour qu'il lise `mint_authority` — fait le 22/07, voir Partie 13.
2. Ajouter un garde-fou anti-wash-trading au pipeline VC (réutiliser le mécanisme momentum
   existant). — fait le 22/07, voir Partie 13 (item 5).
3. Exiger une confirmation de stabilité temporelle avant de faire confiance à un scan de
   liquidité/volume instantané côté VC.
4. Décider explicitement si les 3 signaux proposés (réputation déployeur/GitHub, MEV, sortie
   déguisée — aucun codé, cf. correction en tête de Partie 11) méritent d'être construits, et si
   oui câblés dans le score déterministe plutôt que consultatifs.
5. Construire un signal anti-sortie-déguisée réel (proposition non codée à ce jour) étendu à tout
   wallet ayant reçu une part significative au TGE, pas seulement celui étiqueté dev.
6. Lire et exploiter `slippage_modifiable` déjà récupéré via GoPlus. — fait le 22/07, voir Partie
   13 (item 6).
7. Appliquer une décote de liquidité au PnL affiché des positions ouvertes.
8. Escalader à l'opérateur la tension entre la thèse « builders sub-1M$ » et le seuil
   liquidité/mcap 30%.
9. Prioriser le chantier de clustering Sybil déjà identifié comme non résolu, y compris en phase
   de bonding.
10. Concevoir le futur signal GitHub pour juger la substance des commits, pas seulement leur
    fréquence.
11. Vérifier dans le code réel la fréquence effective de re-scan de liquidité sur une position VC
    ouverte.
12. Activer Webacy comme second avis de sécurité indépendant (déjà committé, juste une clé API
    manquante).
13. Étudier un correctif pour le plancher de liquidité absolu déconnecté des paliers internes du
    score. — fait le 22/07, voir Partie 13 (item 7).
14. Ajouter un flag pour un transfert vers une adresse CEX connue en quantité significative
    (priorité basse, couverture restera partielle).
15. Construire le relais MEV privé (proposition non codée à ce jour) — pas construit, coût
    réel/priorité pas encore évalués.

Méthode : 5 agents (4 générateurs de scénarios en parallèle + 1 synthèse), 24 scénarios simulés
avec application mentale des formules réelles vérifiées dans le code. Aucun scénario n'a été
exécuté sur du vrai capital — c'est un exercice de simulation, pas un test en conditions réelles.

## Partie 12 — ajoutée le 22/07/2026
### Recherche VC crypto — succès et échecs

Recherche menée en 2 passes : un premier run (skill de recherche approfondie native) a été arrêté
après 53 agents pour respecter le plafond du projet (5 max, révisé à 3 max depuis le 24/07) — ses
résultats déjà collectés (84 affirmations extraites de 25 sources, 7 déjà passées au vote
adversarial complet à 3 voix) ont été récupérés directement depuis les transcripts, puis
réinjectés dans un second workflow, cette fois strictement à 5 agents, qui a vérifié le reste et
synthétisé.

> Biais de survivant, reconnu explicitement par le rapport lui-même — les fonds cités comme
> disciplinés sont connus précisément parce qu'ils ont survécu et sont couverts par la presse.
> Impossible d'exclure que d'autres fonds aient appliqué les mêmes pratiques (refus de levier,
> diligence rigoureuse) et aient quand même échoué pour des raisons non documentées ici. Le corpus
> est aussi structurellement déséquilibré : beaucoup plus de matière vérifiée sur les échecs de
> 2022 que sur des succès chiffrés en small-cap — les exemples de réussite portent sur des
> pratiques et des évitements, pas des gains vérifiés sur un pari small-cap précis.

#### Ce qui a fait tomber les fonds (affirmations confirmées par vote adversarial)

- **Three Arrows Capital** — ~10 milliards $ d'actifs en mars 2022, effondré en quelques mois.
  Exposition Terra/Luna concentrée et levée (200 à 560 M$ selon la source), position Aave à effet
  de levier (211 999 aWETH, ~235 M$ de collatéral contre 183 M$ de dette, seuil de liquidation à
  1 014 $) — le mécanisme exact : capital emprunté + collatéral volatil + seuil de liquidation
  atteint.
- **Celsius et 3AC** — retraits simultanés de 50 000 stETH chacun vers FTX les 8-9 juin 2022,
  vendus en catastrophe via un seul canal OTC — la contagion s'est propagée par un point de sortie
  commun, pas par hasard.
- **FTX/Alameda** — bilan resté opaque jusqu'à une fuite médiatique (CoinDesk) ; dépenses de
  prestige (immobilier de luxe, +100 M$ de dons politiques) lues à tort comme des signes de
  solidité plutôt que des signaux d'alerte.
- **Galois Capital** — thèse pourtant saine (critique précoce et documentée de Terra, short
  correct sur LUNA avant l'effondrement) mais a quand même perdu jusqu'à 45 M$ : un pur risque de
  contrepartie (logé chez FTX), structurellement distinct de la qualité de sa thèse
  d'investissement.

Trois affirmations plus englobantes n'ont PAS résisté à la vérification et sont explicitement
écartées : l'auto-justification de Su Zhu présentée comme diagnostic indépendant ; la description
de la stratégie 3AC comme un simple pari de redéploiement ; et le récit d'une cascade « directe »
et strictement séquentielle Terra→Celsius/3AC/Voyager→BlockFi/FTX→Genesis/DCG, qui blur en réalité
deux vagues causalement distinctes.

#### Ce qui distingue les fonds disciplinés

- **Polychain Capital** — refus structurel de tout levier (confirmé, citation directe du
  fondateur) ; a refusé plusieurs pitchs de Sam Bankman-Fried avant l'effondrement FTX ; sortie
  anticipée d'un pari stablecoin algorithmique avant qu'il ne tourne mal.
- **a16z crypto** — ~90% des investissements en phase précoce, avant toute liquidité publique du
  token ; ticket moyen passé de 4,5 M$ à 10,4 M$ (2024→S1 2026) pendant que le nombre de deals
  baissait — concentration croissante sur moins de paris, plus gros.
- **Paradigm** — sourcing « thèse d'abord » : une équipe de recherche interne identifie les
  prochaines vagues technologiques puis va chercher les équipes qui construisent dessus, plutôt
  que d'attendre le deal flow entrant.
- **Framework Ventures** — engagement actif post-investissement (fournisseur de liquidité,
  staking, vote de gouvernance) plutôt qu'une détention passive.
- **1confirmation** — 125 M$ levés pour seulement 59 sociétés en portefeuille depuis 2017 :
  concentration délibérée par la petite taille du fonds, pour forcer un nombre de paris limité à
  haute conviction.
- **Multicoin / Dragonfly** — structure de ticket adaptée à la maturité réelle du projet (seed à
  Series D, equity ou token direct), diligence technique internalisée par des associés au profil
  ingénieur.

#### Traduction concrète pour ARIA

À adopter :
1. Interdiction structurelle et permanente du levier — à graver, aucune exception même si le
   capital réel grossit.
2. Séparer strictement, dans `vc_analysis.py`, le risque de contrepartie (où le capital est logé)
   du risque de thèse (qualité du projet) — jamais fondus dans un seul score composite.
3. Plafond de dépendance corrélée au niveau du portefeuille entier (même pont, même stablecoin de
   règlement, même routeur DEX) — au-delà du simple plafond par chaîne/catégorie (40%,
   `CONCENTRATION_CAP_PCT`) déjà dans `paper_trader_risk.py` (correction 22/07 : pas
   `risk_guard.py`, qui ne contient aucune constante de ce type).
4. Vérification de vesting on-chain systématique avant tout score de conviction élevé (cliff
   ≥6-12 mois investisseurs, ≥1 an équipe) — nouveau module candidat `vesting_check.py`.
5. Preuve on-chain systématique plutôt qu'assurance déclarative pour toute allégation de sécurité
   — étendre le principe déjà appliqué au mint (`mint_authority.py`) à toute affirmation projet.
6. Journaliser explicitement la raison structurelle de chaque rejet AVOID/WATCH dans
   `thesis_journal.py` — historique consultable de ce qu'ARIA a évité et pourquoi.
7. Plafond dur sur le nombre de positions `vc_thesis` ouvertes simultanément (5-8 max), pas
   seulement sur leur taille individuelle.
8. Re-scan de sécurité en continu pendant la détention d'une position VC, pas seulement à
   l'entrée.
9. Vérifier la profondeur de sortie, symétrique à la profondeur d'entrée déjà vérifiée par
   `liquidity_depth.py`.

À éviter structurellement : jamais de levier ; jamais de dépendance de sortie concentrée sur une
seule contrepartie/pont/exchange ; jamais accepter une affirmation non vérifiable on-chain comme
suffisante ; jamais laisser une opacité de trésorerie passer inaperçue ; jamais laisser des
dépenses de prestige faire monter un score de sécurité ; jamais dimensionner une position sans
vérifier la profondeur de sortie réelle du pool.

Honnêtement non transposable : l'accès à des deals privés à gros ticket, les relations CEX pour
des sorties OTC, le rejet de pitch par jugement interpersonnel — ARIA n'a ni relations humaines ni
accès deal-flow privé. Son équivalent mécanique reste le veto LLM sur données vérifiables déjà en
place, à améliorer plutôt qu'à singer sous une forme humaine.

Méthode : 1er run (skill natif) arrêté à 53 agents, données récupérées depuis les transcripts (84
affirmations, 7 déjà votées 3x) ; 2e run strictement à 5 agents (2 vérifications groupées + 1
recherche complémentaire + 1 traduction ARIA + 1 synthèse). Limites reconnues par le rapport
lui-même : biais de survivant, corpus déséquilibré échecs/succès, second passage de vérification
plus léger que l'original sur les items déjà couverts.

## Partie 13 — ajoutée le 22/07/2026
### Corrections codées suite au stress-test et à la recherche VC

Huit correctifs au total (déclenchés par le stress-test Partie 11 + une recherche externe
recoupée + un second plan de renforcement + une découverte en observant une position momentum
réellement ouverte, le même jour) — TOUS codés et testés. Suite complète finale : 6801 passed, 17
skipped, `test_coherence.py` vert. Rien commité au moment de cette entrée (statut au 22/07 —
possiblement committé depuis, à vérifier via `git log` si besoin).

#### 1. Malus mint conditionné à mint_authority — CORRIGÉ
Détail complet en Partie 4.2 (formule mise à jour) et `docs/HANDOFF_MOTEUR_LEGITIMITE.md`. Résumé
: `_apply_onchain_signals` (`acp_onchain_scan.py`) ne consultait jamais `mint_authority` — pire,
celui-ci n'était résolu qu'APRÈS le calcul du score (ordre d'appel inversé dans
`scan_base_token`). Les deux corrigés : `SAFE_AUTHORITIES` (renommé public, source unique dans
`mint_authority.py`) neutralise désormais le malus pour renounced/launchpad/contract, et la
résolution de l'autorité a été déplacée avant le calcul du score.

#### 2. Reset hebdomadaire conditionnel (poche satellite) — CORRIGÉ
Décision opérateur explicite tranchée : option 3 (poche satellite). La demande initiale (exempter
une position du reset forcé si R/R>1.5, stop ATR non touché, régime Euphorie) entrait en conflit
direct avec la règle gravée le 18/07 : « ARIA repart à 1M$ CHAQUE semaine ». Trois options avaient
été proposées ; l'opérateur a confirmé l'option 3 : une poche SÉPARÉE et PLAFONNÉE (5% du capital
de départ par défaut), hors du verdict hebdomadaire principal, ni bonus ni pénalité.

Éligibilité (`_satellite_pocket_eligible`, `paper_trader.py`) — TOUT requis :
```
  strategy == "momentum" (Formule B/vc_thesis pas encore couverte)
  régime ratchet (entrée, maintenant) == Euphorie
  prix > stop actif (_compute_active_stop, stop ATR pas touché)
  R/R RESTANT = (cible − prix) / (prix − stop actif) ≥ 1.5
```

```
Plafond dur : 5% du capital de départ fixe (1M$) = 50 000$, cumulé sur toutes
les positions satellite (anciennes + nouvelles) -- meilleurs R/R restants
admis en premier si plusieurs candidats se disputent la place.

Verdict hebdomadaire : end_equity = cash + coût immobilisé en poche satellite
(jamais l'équité complète qui inclurait la valorisation flottante de la
poche satellite) -- neutralise TOTALEMENT son effet sur validated/return_pct,
ni bonus ni pénalité, jamais un moyen de repousser artificiellement un échec.
```

Nouvelle colonne `pocket` ('main'/'satellite') sur `paper_position`/`paper_position_archive` —
une position satellite n'est JAMAIS wipée par l'archivage hebdomadaire, ni réévaluée une fois
promue (sort uniquement par sa propre clôture normale, sur son propre tempo).
`_compute_active_stop` extrait de la boucle de gestion (lecture seule, aucun effet de bord) pour
être réutilisé sans dupliquer une logique qui pourrait diverger — même philosophie que la
réutilisation du détecteur wash-trading (item 5 plus bas). Limite connue (v1), documentée plutôt
que cachée : `risk_guard` lit l'équité COMPLÈTE (poche satellite incluse) pour son coupe-circuit
de drawdown — une poche satellite qui perd de la valeur peut donc influencer un déclenchement de
drawdown la semaine suivante, même si son résultat n'a pas compté dans LE verdict. Plafond bas
(5%) pour borner cet impact ; séparer les deux poches dans `risk_guard` resterait un chantier
distinct si le besoin se confirme en conditions réelles.

16 nouveaux tests (`test_paper_weekly_cycle.py`) : fonctions pures
(`_remaining_reward_risk`, `_satellite_pocket_eligible`) + intégration (promotion, exclusion du
verdict, plafond avec priorité au meilleur R/R, position portée d'une semaine précédente jamais
réévaluée, cas inéligibles inchangés).

#### 3. Sauvetage smart money sur mouvement parabolique — CORRIGÉ
Pipeline momentum (`momentum_entry.py`). Le plafond de +200%/24h (§ Partie 5) rejetait aussi de
vrais breakouts légitimes, pas seulement des pump-and-dump.

```
200% < mouvement ≤ 350% (régime non-Euphorie) :
  appel analyze_smart_money (déjà construit pour /vc, réutilisé tel quel)
  score_delta > 0 (≥2 wallets qualifiés convergents) → gate levé, achat possible
  sinon → rejet inchangé (comme avant ce correctif)

mouvement > 350% : rejet dur SANS exception, sauvetage jamais tenté
  (plafond absolu, pas un 3e palier négociable)
```

Coût : un appel Blockscout holders dédié, uniquement pour les candidats déjà dans cette tranche
rare (jamais sur tous les candidats). Couverture limitée à Base à ce jour (même limite que le
reste du signal smart money).

#### 4. Monitoring post-entrée d'une position VC — CORRIGÉ
Jusqu'ici, une position `vc_thesis` n'était re-vérifiée que sur la liquidité (Formule B, Partie
6) — jamais sur le comportement du wallet déployeur pendant la détention. Deux nouveaux signaux
SELL d'urgence, dans `paper_trader.py`, tournant sur le même cycle de gestion déjà existant (15
min) :

```
Signal #1 — vente récente du déployeur :
  delta = sold_pct_actuel − sold_pct_a_l_entrée (dev_wallet.py, déjà construit)
  delta ≥ 10 points de % → sortie immédiate ("vente déployeur détectée")
  (seuil bien plus bas que HEAVY_SELL_PCT=50% de dev_wallet.py, qui juge une
  fois à l'entrée -- ici c'est une DÉGRADATION pendant la détention qui compte)

Signal #2 — chute de liquidité SOUDAINE entre deux cycles :
  last_liquidity_usd (mis à jour à CHAQUE cycle, contrairement à
  entry_liquidity_usd qui reste figé à l'entrée) chute de ≥30% par rapport
  au cycle précédent → invalidation fondamentale
  (complète, sans jamais remplacer, le check -50% cumulé depuis l'entrée déjà
  en place -- un retrait de LP étalé en petites tranches peut ne jamais
  franchir 50% cumulé à aucun instant T, mais représenter un vrai retrait)
```

Deux nouvelles colonnes persistées par position : `entry_dev_sold_pct` (instantané à l'ouverture)
et `last_liquidity_usd` (mis à jour chaque cycle). Fail-open sur les deux si la donnée d'entrée
n'a jamais été résolue — jamais une alerte inventée sans base de comparaison réelle.

Suite complète revérifiée après ces quatre premiers correctifs : 6758 passed, 17 skipped,
`test_coherence.py` vert. Second lot ci-dessous (5-7), issu d'un plan de renforcement distinct
proposé le même jour.

#### 5. Anti-wash-trading réutilisé dans le crible VC — CORRIGÉ
Le crible VC (`safety_screen.py`) n'avait jusqu'ici AUCUN garde-fou anti-manipulation de volume —
seul le pipeline momentum en avait un. Réutilise TEL QUEL le détecteur déjà construit
(`momentum_entry.MAX_VOLUME_TO_LIQUIDITY_RATIO` + `_wash_trading_ratio_confirmed`, fenêtre de
confirmation soutenue partagée par contrat/chaîne), jamais une deuxième constante qui pourrait
diverger.

```
volume_24h / liquidité > 20x, SOUTENU ≥75s (même clé (contrat, "base")
  que le pipeline momentum -- même contrat = même réalité de marché)
  → échec MOU (jamais hard_fail -- comportement de marché, pas un
  mécanisme malveillant confirmé dans le contrat)
```

#### 6. Malus + barrière slippage/taxe modifiable après coup (GoPlus) — CORRIGÉ
GoPlus expose un pouvoir caché supplémentaire (`slippage_modifiable`), distinct de
`hidden_owner`/`can_take_back_ownership` : le dev peut changer taxe/slippage après coup, sans
jamais reprendre la propriété visible — un token "propre" au moment du scan peut devenir extractif
plus tard sans qu'aucun autre signal GoPlus ne le détecte. Jamais consulté avant ce correctif.

```
slippage_modifiable == True :
  malus -15 sur security_score (acp_onchain_scan._apply_honeypot_signals)
  + barrière HARD_FAIL dans safety_screen (même famille que hidden_owner/
    can_take_back_ownership -- un pouvoir jamais réparé par le temps)
```

#### 7. Découplage du plancher de liquidité VC — CORRIGÉ
Le plancher unique (30 000$) pénalisait à tort un token dont le score ET le verdict sont déjà
propres — le risque scam/rug est alors déjà écarté par le scoring lui-même. Assoupli SEULEMENT si
tout le reste est irréprochable, jamais un blanc-seing générique sur la liquidité.

```
liquidité < 30 000$ ET security_score ≥ min_score (70) ET verdict == SAFE
  ET mint propre (renounced/launchpad/contract ou pas de mint)
  → tolérée, jamais silencieuse (raison "liquidité faible ... tolérée"
    visible même sur un passage réussi, pas seulement sur un rejet)

Sinon (un seul de ces critères manque) → rejet inchangé
```

Suite ciblée (`safety_screen.py` + `test_goplus.py`) : 82 passed après les items 5-7.

#### 8. owner_change_balance (GoPlus) jamais consulté — vecteur de perte totale non couvert — CORRIGÉ
Trouvé en observant une position momentum RÉELLEMENT ouverte en prod (CNX, entrée R/R 4.5). GoPlus
signalait `owner_change_balance=True` sur ce token (l'owner du contrat peut modifier directement
le solde de n'importe quel wallet, y compris celui d'ARIA) — un pouvoir DISTINCT du honeypot
classique (qui bloque la revente, pas le solde lui-même). Ce champ était déjà capté par le client
GoPlus depuis le début (`services/goplus.py::TokenSecurity.owner_change_balance`), mais n'était
exploité NULLE PART dans la décision — ni score, ni barrière VC, ni pipeline momentum. Sa seule
utilisation existante (`ownership_verifiably_renounced`) ne sert qu'à calibrer la durée du cache,
jamais à bloquer un token.

```
Côté crible VC (safety_screen.py) : même famille que hidden_owner/
can_take_back_ownership/slippage_modifiable :
  owner_change_balance == True → malus -40 (acp_onchain_scan.py,
  verdict DANGER forcé -- même gravité que is_honeypot/cannot_sell_all)
  + barrière hard_fail dans safety_screen.py

Côté pipeline momentum (_check_honeypot, momentum_entry.py) : rejoint
le SEUL garde-fou dur déjà en place (décision opérateur 15/07 inchangée
-- mint_authority/dev_wallet restent hors scope momentum) :
  is_honeypot OU cannot_sell_all OU owner_change_balance → rejet
  (même nature -- pouvoir technique de vol direct des fonds, pas un
  signal de conviction/thèse -- coût ZÉRO appel réseau supplémentaire,
  même lecture GoPlus déjà faite pour le honeypot)
```

Décision de portée : ce signal REJOINT le garde-fou honeypot du momentum plutôt que d'élargir la
doctrine "seul le honeypot" à d'autres signaux GoPlus (hidden_owner/mint restent hors scope
momentum, cohérent avec la décision opérateur du 15/07) — argumenté par le fait qu'
`owner_change_balance` est de MÊME NATURE que le honeypot (perte totale directe des fonds), pas un
signal de conviction comme `mint_authority`/`dev_wallet`.

4 nouveaux tests (`test_goplus.py` x2, `test_momentum_entry.py` x1, `test_safety_screen.py` via la
barrière). Suite complète finale : 6801 passed, 17 skipped. Rien commité au moment de cette entrée
pour l'ensemble de la Partie 13.

---

Fin du Codex. Sources : lecture directe du code à `/opt/aria`, dépôt `GoldenFarFR/ARIA`, 22
juillet 2026. Ce document n'est jamais une autorité au-delà de sa date — pour toute décision qui
compte, revérifier contre le code réel plutôt que de citer cette page.
