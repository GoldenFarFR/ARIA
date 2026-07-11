# Archive détail — entrées 10/07 compactées dans CLAUDE.md le 11/07

> Ce fichier n'est PAS un nouveau segment de travail — c'est une archive créée le 11/07
> pour préserver mot pour mot le détail complet d'entrées `CLAUDE.md` compactées à cette
> date (compaction demandée par l'opérateur pour garder `CLAUDE.md` lisible). Rien n'a été
> perdu : chaque section ci-dessous est le texte intégral original, non résumé.

## Scorecard « feu vert argent réel » (#70, 10/07) — EN LIGNE

Question directe de l'opérateur ("tu ferais confiance à ARIA pour 100k$ ?") répondue
honnêtement NON, puis outillée plutôt que laissée en simple avis : `skills/
real_money_readiness.py` calcule objectivement, depuis le vrai journal `vc_predictions`,
les 8 cases pré-engagées de `docs/protocole-argent-reel.md` — jamais un jugement
subjectif. Commande `/feuvert` (Telegram, admin-only — jamais public).

Correction du 10/07 (même segment) : cette entrée affirmait à tort le paper-trading
"gaté OFF par défaut, aucune preuve d'un run" — jamais vérifié contre l'état réel du VPS,
seulement supposé depuis la doc. Vérifié en direct via `GET /api/pulse` : `paper_trading:
true`, cycle `paper_trade_cycle` déjà exécuté (le run de preuve tourne réellement, pas
seulement câblé). `sample_size` reste très probablement `fail` aujourd'hui (échantillon
encore jeune : 4 pronostics au total, 0 clôturé, vu sur `/cockpit` le 10/07) mais le
compteur avance désormais pour de vrai. `integrity` `ok` par garantie structurelle
(`close_prediction` ne réécrit jamais, aucune fonction de suppression) ; `robustness`
calculable dès 3 BUY clôturés. Le reste (`benchmark` hold-ETH, `risk` vérif a posteriori
des AVOID, `judge` méta-audit, `lawyer`) reste honnêtement `unknown` — la donnée ou
l'action humaine manque encore pour même MESURER ces cases, pas seulement pour les
remplir. Leçon retenue : toujours vérifier l'état réel (API publique, `/pulse`) avant
d'affirmer un statut de gate, même documenté ailleurs comme "OFF par défaut".

## Sentiment de marché continu (#71, 10/07) — EN LIGNE, gate OFF

Demande opérateur (image Wall St Cheat Sheet — psychologie du cycle de marché) : scanner
en continu, sans expiration, les paires principales. Livré : `skills/indicators.py`
gagne `bollinger_bands` (même patron qu'`ema_series`/`macd_series`) ; nouveau
`skills/candlestick_patterns.py` (doji/marubozu/hammer/shooting_star/engulfing, 171
lignes, testé, PAS câblé à l'époque — nécessite de vraies bougies OHLC, absentes pour
BTC/ETH via CoinGecko `market_chart`, réservé aux tokens Base via `ohlcv.py` — câblé
depuis dans `/vc`, cf. entrée Nuit 9) ; nouveau `skills/market_sentiment.py`
(`classify_sentiment` : RSI+Bollinger+momentum+retracement → 6 régimes + repli neutre,
PAS les 13 émotions fines du cheat sheet — aucune signature numérique ne distingue
"colère" de "dépression", simplification assumée et documentée). Tâche heartbeat
`market_sentiment_cycle` (60min, gate OFF `ARIA_MARKET_SENTIMENT_ENABLED`) rafraîchit
BTC + ETH (`PRINCIPAL_PAIRS`, liste de départ extensible — pas "toutes les paires" au
sens large). Persistance SQLite `market_sentiment` : `upsert_reading` écrase TOUJOURS la
lecture précédente — "sans expiration" veut dire aucun TTL de lecture, la fraîcheur
dépend uniquement du dernier cycle heartbeat réussi. Commande `/sentiment` (Telegram,
admin-only). Complète `btc_cycles.py` (halving, pluri-annuel) par une lecture
court/moyen terme, ne le remplace pas.

## Backlog #11/#64 résolu (10/07) — barres « échelle commune » des scénarios + thèse enrichie

Contexte de l'audit original perdu à une compaction antérieure (tâche restée bloquée deux
sessions) ; reconstruit en lisant le code réel plutôt qu'en devinant : la barre de
PROBABILITÉ de chaque carte bull/base/bear (`vc_report.py`) était déjà correctement à
l'échelle (0-100% par carte, indépendante) — mais rien ne comparait l'AMPLEUR des cibles
entre elles (`cible` est de la prose LLM libre, jamais un nombre). Ajout d'un champ
structuré `cible_multiple` (optionnel, jamais fabriqué si le LLM ne l'a pas chiffré) →
barre supplémentaire à largeur PARTAGÉE entre les 3 scénarios, omise si moins de 2 sont
chiffrés (dégradation douce, même doctrine que le reste du rapport). Thèse (`these`)
enrichie au même commit : 3-5 phrases, doit s'ancrer sur ≥2 signaux concrets déjà
fournis (sécurité, liquidité, R/R, TA, contexte marché) — jamais une généralité
interchangeable. 8 tests (vc_analysis + vc_report).

## Sentiment de marché → décision LLM réelle (#75, 10/07) — EN LIGNE (partie résolue)

Demande opérateur explicite, après avoir vu le cockpit vide : « je veux que ses données
soit utiles pour aria et toi pas pour moi... pour que vous puissiez ajuster votre
stratégie ». Distinction architecturale trouvée en creusant `vc_analysis.py` : l'overlay
macro halving (#14, `_attach_market_context`/`_attach_extras`) s'exécute APRÈS la
réponse LLM — pure décoration de rapport, n'a JAMAIS influencé le raisonnement, malgré
les apparences. Corrigé pour le sentiment BTC/ETH en le branchant sur le chemin PRÉ-LLM
(`_fetch_sentiment_readings` → `_build_untrusted_context`, même patron qu'EMA/MACD/
golden pocket #74) : le régime (doute_accumulation, euphorie, capitulation_peur...)
atteint désormais le prompt AVANT que le LLM ne tranche potentiel/thèse/recommandation.
Régime `donnees_insuffisantes` jamais affiché (silence). Dégradation douce (DB
absente/gate OFF/erreur → liste vide, jamais bloquant). 6 tests ajoutés.

## INCIDENT SÉCURITÉ MAJEUR (10/07) — délégation autonome à « Cursor » trouvée vivante et RETIRÉE

Déclenché par un message Telegram alarmant reçu par l'opérateur (« Feu vert reçu — je
cadre le chantier xprofile... je délègue à l'ouvrier Cursor ») en réponse à `/feuvert`,
qui n'a RIEN à voir avec ce texte (commande déterministe, zéro coût LLM). Investigation
(4 agents parallèles + vérification manuelle) : un sous-système entier —
`aria_worker_queue.py`, `capability_gap.py`, `operator_readiness.py`,
`skills/community_worker_skill.py` — committé le 05/07 **par Cursor lui-même**
(co-auteur `cursoragent@cursor.com` sur les commits), câblé dans
`brain.py`/`heartbeat.py`/`telegram_bot.py`, jamais documenté nulle part dans CLAUDE.md
malgré la doctrine explicite « Cursor/Grok abandonnés ». Reachable SANS validation
opérateur : heartbeat auto (6h et 15min), mots du quotidien en Telegram (« go », « vas-y »,
« lance », « nettoie le répertoire »), et même un formulaire PUBLIC du site
(`/api/aria/community-feedback`, visiteur anonyme). Preuve GitHub réelle que ça avait
déjà agi : issue #1 + PR #2 auto-générées le 03/07, jamais traitées, la réponse publique
aux visiteurs affirmait littéralement « transmis à l'ouvrier Grok/Cursor ».
`operator_go_ahead.py:136-138` (texte « Feu vert reçu — je reprends notre échange et
j'avance ») confirmé comme la source exacte du message alarmant. Retiré en entier
(`aria_worker_queue.py` + `community_worker_skill.py` supprimés, `capability_gap.py`
réduit à une notification Telegram locale sans écriture GitHub ni délégation,
`brain.py`/`operator_readiness.py`/`operator_go_ahead.py`/`community_feedback.py`/
`health_watch.py` nettoyés de tout appel externe). Gardé : la synchro bannière X
(`self_maintenance.py`) et la surveillance santé (`health_watch.py`), qui notifient
désormais Telegram au lieu de déléguer. `suggestion_feedback.py` supprimé au passage
(zéro appelant en prod, uniquement lié au worker queue mort). Garde-fou mécanique
ajouté : `test_coherence.py::test_external_write_actions_registered_in_allowlist` —
toute fonction de production qui écrit réellement à l'extérieur (GitHub/X/email) doit
être déclarée dans une allowlist explicite ; un nouvel appel non déclaré fait échouer la
CI immédiatement, sans dépendre d'un audit périodique ou d'une mémoire humaine. Testé
positif (un faux appel simulé fait bien échouer le test).

**Suite (10/07, même jour) — résidu PROMPT nettoyé** : la première passe avait retiré le
CODE (skills/queue), pas le NARRATIF. ARIA a redit en Telegram « je rédige
`sessions/ARIA-WORKER.md` pour déléguer à l'ouvrier Cursor » — root cause trouvée dans
`directives.md` (chargé dans son prompt via `directives.py`, sections « mode
débranchement Grok » + « Community → ouvrier Cursor » qui lui ORDONNAIENT de déléguer à
un skill `worker_delegate` supprimé). Réécrit → doctrine à jour (Claude Code construit,
ARIA propose via `aria_directives`/issue). Mentions mortes aussi nettoyées dans
`public_mode.py` (liste), `llm_economy.py`/`operator_go_ahead.py` (bouts de prompt),
`community_feedback.py`/`qi_auto_judge.py` (docstrings). Gardé intact : le « ouvrier »
LÉGITIME de `spark_config`/`ecosystem_config` (nom du tier LLM Spark/Virtuals, sans
rapport avec Cursor) et le commentaire d'historique de `capability_gap.py`. Leçon : après
avoir retiré un système, grep AUSSI les fichiers de prompt/knowledge (`directives.md`,
persona, YAML), pas seulement le code exécuteur.

## Vision (images en chat Telegram) — EN LIGNE, gate ON, testé en conditions réelles (10/07)

Déclenché par un vrai bug trouvé en capture d'écran : l'opérateur a envoyé un graphique
DexScreener avec « juge cette situation », ARIA n'a rien répondu. Cause racine
confirmée : `telegram_bot.py` n'enregistrait AUCUN handler photo
(`MessageHandler(filters.PHOTO, ...)` absent) — toute image envoyée à ARIA était ignorée
en silence. Pire, une fonction `_handle_avatar_photo` existait déjà pour le flux
`/avatar` mais n'avait, elle non plus, jamais été enregistrée (même bug que le reliquat
Cursor/collision `/directive` : une fonction écrite mais jamais câblée au bon endroit).
Corrigé en un seul point d'entrée `_handle_photo` (nouveau dispatcher, seul handler photo
enregistré) qui route selon la légende : légende vide ou mots-clés avatar → flux
`/avatar` existant ; légende normale (question, « juge cette situation ») → nouvelle
lecture visuelle générale (`_handle_vision_photo`), admin-only, gate OFF par défaut
(`ARIA_VISION_ENABLED`, coût LLM par image, décision produit volontairement pas encore
ouverte au public — un visiteur reçoit un refus court sans jamais déclencher d'appel
LLM). Sous le capot : `llm.chat_with_context` gagne un paramètre `image_data_uri`
optionnel (bascule le message utilisateur en contenu multimodal
`[{"type":"text",...},{"type":"image_url",...}]`, forme chat-completions
OpenAI-compatible — comportement texte strictement inchangé sans image, tous les
appelants existants intacts) ; `AriaBrain._llm_response` le reçoit et l'ajoute au prompt
système final (`chat_with_context`), avec une règle anti-hallucination dédiée : ARIA ne
lit un chiffre précis (prix, %, volume) que si elle peut réellement le voir net sur
l'image, sinon elle le dit explicitement au lieu de l'inventer — même doctrine
facts-only que le reste du système. Confirmé en direct sur le VPS (10/07, même segment) :
gate activé (`ARIA_VISION_ENABLED=1` dans `vanguard/backend/.env` — PAS
`/opt/aria/.env`, piège vécu : le vrai fichier chargé par le conteneur est pointé
explicitement par `deploy.sh`), premier test réel (graphique DexScreener B20/ETH +
« juge cette situation ») → réponse de qualité, chiffres lus correctement sur l'image
(liquidité, FDV, %, volumes, ratio buy/sell), raisonnement sur le ratio liquidité/FDV
comme signal, distinction rug vs dump post-hype, limites honnêtes explicites (lecture
d'image ≠ vérification on-chain). Grok (via la passerelle Virtuals/Spark) accepte donc
bien la vision — plus une inconnue. Limite v1 assumée : le message image ne passe pas
par `repertoire_db.save_message` (appelé seulement dans le gros dispatcher texte, pas
dans `_llm_response` directement) — une image envoyée n'entre donc pas dans l'historique
conversationnel pour un suivi ultérieur en texte. 25 tests ajoutés (llm/brain/telegram),
suite complète verte.

## Identité visuelle ARIA — prompts de portrait renforcés (10/07), frontière de goût gravée dans le prompt

Déclenché par une vraie demande opérateur (rendre ARIA visuellement au niveau de
réalisme d'une référence externe montrée en capture — un agent IA nommé « Lily Turner »
sur Virtuals). Décision opérateur explicite tranchée via AskUserQuestion (le contenu de
cette référence est un agent NSFW, bio « Hold me, unlock me, earn with me », token
$LILY — vérifié en direct sur son profil X) : ARIA reprend UNIQUEMENT le niveau de
réalisme technique (peau/lumière/cohérence du personnage), jamais le registre sexualisé
ni le modèle d'abonnement/monétisation. `portrait_scene.py` (3 prompts de génération :
scène, style, bannière legacy) enrichi avec des descripteurs de réalisme photo (85mm,
lumière naturelle, texture de peau, mise au point sur les yeux) + présence/charisme
(« commanding presence », regard direct) + une frontière de goût écrite en dur dans le
prompt (« never suggestive, never revealing, never sexualized ») plutôt que laissée à la
seule discipline de qui rédige la légende — un vrai garde-fou structurel, pas juste une
bonne intention. Brief identité (`_extract_identity_brief`, vision-model) moins tronqué
qu'avant (120→220 caractères) pour une cohérence de personnage plus fidèle d'une
génération à l'autre. `generate_banner_creative` (bannière X text-to-image, sans photo
source) inchangé — exclut déjà tout humain, la frontière de goût n'y a pas de sens. 5
tests ajoutés. Portée du reste de la demande opérateur, non câblée à l'époque :
"stories" façon influenceuse (ni X ni Telegram n'ont cette fonctionnalité nativement
pour un bot — vérifié : Telegram bot = photo de profil carrée uniquement, AUCUN concept
de bannière contrairement à X, confirmé par le code existant `x_banner.py` lui-même
scopé "Bannière X" sans équivalent Telegram) ; voix/TTS (aucune infra existante,
référence Angèle/mélodieuse notée pour plus tard) ; studio 3D (jugé disproportionné —
recommandé un outil "talking avatar" image+audio→vidéo pour les futurs AMA à la place).
Objectif final rappelé par l'opérateur : ce travail sert un moment de lancement
commercial futur avec une vidéo forte — mais « preuve avant promesse » reste la
priorité réelle avant ce moment (pacte `docs/protocole-argent-reel.md` inchangé, rien ne
le contourne).
