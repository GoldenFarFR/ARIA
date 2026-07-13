[VPS Research]

# Scan large-spectre — cinq branches (mode par défaut, décision opérateur 12/07)

## Contexte et méthode

Retour au mode par défaut (scan large-spectre, pas une tâche ciblée) sur
décision opérateur du 12/07. Grounding utilisé pour juger la
"connectabilité" de chaque piste : `docs/architecture-extensibilite.md`
(lu directement depuis `GoldenFarFR/ARIA`, raw GitHub) — les seams
`include_*` du `TokenScanContext`, les couches Gateways/Skills/Services/
LLM routing/persistance, et le tableau des intégrations déjà planifiées
ou dormantes (`include_ta` → `services/ohlcv.py` planifié,
`include_factcheck` → `services/factcheck.py` planifié, `include_social`
→ X radar planifié, cache court TTL planifié autour de `scan_base_token`).

**Deux critères de jugement, comme toujours** : (1) légitimité — doc
réelle, maintenance active, adoption réelle, pas du vaporware ; (2)
connectabilité sans dupliquer l'architecture existante — quel seam
précis, quel upside concret. Cinq branches retenues cette fois, toutes
jugées vertes sur les deux critères. Aucune touchée aux garde-fous
(`permission_mode`/`wallet_guard`/règles-uniques/`config.toml`), aucun
capital réel, aucun secret, aucune exécution autonome, aucune
auto-modification du système — toutes ces frontières confirmées non
approchées dans les cinq branches ci-dessous.

---

## 1. GeckoTerminal OHLCV API — brique directe pour le seam `include_ta` déjà planifié

**Légitimité** : API publique de GeckoTerminal (groupe CoinGecko),
documentation officielle à jour (`apiguide.geckoterminal.com`), 1 500+
DEX et 200+ réseaux couverts, endpoint OHLCV dédié pour les pools
(bougies multi-timeframe). Gratuit sans clé sur le plan public. Wrapper
Python tiers maintenu existant (`dineshpinto/geckoterminal-api`) si un
client Python est préféré à un appel HTTP direct.

**Limites réelles à documenter honnêtement** : rate limit du plan gratuit
= **30 appels/minute** (relevé le plus récent, en hausse par rapport aux
10/minute d'une version antérieure de la doc — signal de fréquence de mise
à jour active). Chaque appel ne couvre qu'une fenêtre de 6 mois max ; au-
delà, pagination via `before_timestamp`. Suffisant pour un usage
conversationnel "quelques requêtes/heure" comme les autres services déjà
câblés (Frankfurter).

**Connectabilité** : c'est très précisément le seam `include_ta` déjà
identifié comme "planifié" dans `architecture-extensibilite.md`, avec le
fichier cible déjà nommé (`services/ohlcv.py`). Aucune duplication —
remplit un seam vide existant, pas une nouvelle architecture. Upside
concret : ARIA pourrait citer des niveaux techniques réels (support/
résistance à partir de vraies bougies) plutôt que de s'appuyer
uniquement sur des faits fondamentaux/on-chain comme aujourd'hui.

---

## 2. Loki (OpenFactVerification) — brique open-source pour le seam `include_factcheck` déjà planifié

**Légitimité** : projet open-source (`Libr-AI/OpenFactVerification` sur
GitHub), licence MIT, "niveau commercialement utilisable" revendiqué par
les auteurs, publication associée (arXiv 2410.01794). Pipeline en 5
étapes documenté : découpage d'un texte long en affirmations
individuelles → évaluation de leur "check-worthiness" → génération de
requêtes → récupération de preuves → vérification — explicitement conçu
pour être rapide et peu coûteux, pas seulement pour la recherche
académique.

**Alternative notée pour comparaison** : Google Fact Check Tools API
(officielle, gratuite, mais limitée à une recherche dans les
ClaimReview déjà publiées par des fact-checkers tiers — utile en
complément, pas en remplacement, car elle ne vérifie rien elle-même, elle
cherche si quelqu'un d'autre l'a déjà fait).

**Connectabilité** : correspond exactement au seam `include_factcheck`
("Fact-check" dans le tableau des intégrations planifiées,
`services/factcheck.py` ou "2e dimension de juge"). Upside concret :
un pipeline déjà pensé pour découper des affirmations et les vérifier
contre des sources récupérées, potentiellement réutilisable comme brique
pour renforcer le juge adverse déjà en place (`protocole-argent-reel.md`,
case 7 : "proof engine cohérent, pas complaisant") — piste à évaluer plus
en détail, pas décidée ici.

---

## 3. Défense en profondeur contre l'injection de prompt (pattern CaMeL) — renforcement informationnel du "dôme", pas une modification

**Note de cadrage explicite** : cette branche documente une **technique
de sécurité publiée**, elle ne propose et ne touche à aucun fichier de
garde-fou existant. La règle du dôme "les données non fiables sont
toujours assainies avant d'atteindre le LLM" est déjà en place — cette
branche ne fait qu'apporter un vocabulaire et une référence académique
pour, un jour, évaluer si cette règle est appliquée aussi rigoureusement
que l'état de l'art le permettrait.

**Légitimité** : papier de recherche "Defeating Prompt Injections by
Design" (CaMeL), largement cité dans la littérature 2026 sur la défense
contre l'injection de prompt, repris comme référence par plusieurs
analyses indépendantes (Vectra, Techglock, guides de sécurité agentique
2026). Pas un produit à installer, un **pattern architectural** :
séparation stricte entre un LLM "privilégié" qui génère un plan
d'exécution à partir de la requête utilisateur de confiance, et un LLM
"mis en quarantaine" qui traite les données non fiables sans jamais avoir
accès aux outils — un interpréteur personnalisé trace la provenance de
chaque donnée et applique des politiques de sécurité avant chaque appel
d'outil, de sorte que les données récupérées ne peuvent jamais influencer
le flux de contrôle du programme.

**Limite documentée honnêtement** : le papier lui-même reconnaît une
lourdeur d'implémentation réelle (interpréteur Python personnalisé,
politiques spécifiques au domaine) — pas un correctif "plug-and-play".

**Connectabilité** : le point d'ancrage le plus proche dans l'architecture
ARIA est la frontière déjà nommée entre les Services (données
potentiellement non fiables : on-chain, web, X) et le LLM routing
(`aria_core/llm.py`) — le motif "séparer contrôle et données" est déjà,
en substance, la logique du dôme. Upside concret : un vocabulaire et une
référence externe pour, un jour, faire auditer ou renforcer cette
frontière existante par quelqu'un d'autre que ARIA elle-même — sans
toucher à rien maintenant.

---

## 4. Cache sémantique — brique de réduction de coût pour `aria_core/llm.py`, seam déjà nommé ("cache court TTL, planifié")

**Légitimité** : **GPTCache** (Zilliz), bibliothèque open-source établie,
intégrations documentées avec LangChain et llama_index, gains mesurés de
2 à 10x sur les cache hits. Alternative citée : **LMCache** (réutilisation
de KV-cache à travers GPU/CPU/disque, jusqu'à 7x plus rapide au premier
token) — pertinent seulement si ARIA passait un jour à de l'inférence
auto-hébergée, pas pertinent tant que le routing LLM reste vers des
fournisseurs API externes (cas actuel).

**Chiffres cités (à prendre comme ordre de grandeur, pas une promesse
transposable telle quelle)** : jusqu'à 73% de réduction de coût sur des
charges à forte répétition (implémentation Redis LangCache), 47-80% de
réduction combinée en associant cache de prompt + cache sémantique +
routage de modèle.

**Connectabilité** : le tableau des intégrations planifiées mentionne
déjà un "cache court (TTL perf)" comme wrapper autour de
`scan_base_token` — cette branche est une extension naturelle et déjà
anticipée de ce même seam, appliquée spécifiquement aux appels LLM
(`aria_core/llm.py`) plutôt qu'aux seuls appels de services on-chain.
Upside concret : deux requêtes quasi identiques (même token, questions
reformulées différemment) pourraient réutiliser une réponse LLM déjà
calculée au lieu de repayer un appel complet — pertinent vu le volume
déjà mentionné dans une veille antérieure de ce dépôt
(`2026-07-12-optimisation-cout-llm.md`, non re-lue en détail ici, à
recouper avant toute décision pour éviter un doublon).

---

## 5. Alternatives à l'API X officielle pour le seam `include_social` déjà planifié

**Constat de marché confirmé** : l'API X officielle est désormais
facturée au crédit sans palier gratuit (environ 0,005$ par post lu,
0,010$ par lecture d'auteur) — aucun "tier gratuit" resté disponible en
2026, contrairement à ce qu'une recherche plus ancienne aurait pu
suggérer.

**Alternatives tierces identifiées, avec économie chiffrée** : plusieurs
fournisseurs tiers de lecture seule revendiquent des coûts très inférieurs
au tarif officiel — un exemple chiffré cité (à vérifier indépendamment
avant toute décision, source commerciale donc à prendre avec réserve) :
un cas d'usage fintech ayant migré son "read path" complet vers un
fournisseur tiers, passant d'environ 4 800$/mois (plan Pro officiel) à
199$/mois. D'autres fournisseurs facturent au volume (par exemple de
l'ordre de 0,05$ pour 1 000 tweets chez l'un d'entre eux).

**Réserve de légitimité explicite, à ne pas minimiser** : ces
fournisseurs tiers reposent structurellement sur du scraping/accès non
officiel à X — **statut exactement analogue au motif déjà rencontré
avec Clanker/Virtuals/Stooq dans ce même dépôt** (source non officielle,
sans garantie contractuelle de stabilité, risque de rupture à tout
moment si X change ses défenses anti-scraping). Cette branche n'est donc
**pas** un signal aussi vert que les quatre précédentes — c'est un
compromis coût/risque à trancher explicitement par le commandement, pas
une recommandation.

**Connectabilité** : seam `include_social` déjà nommé dans le tableau des
intégrations planifiées ("X radar"). Upside concret : si un fournisseur
tiers durable était retenu malgré la réserve ci-dessus, le motif déjà
documenté ailleurs dans ce dépôt (détection de pics de mentions sur une
fenêtre glissante, ex. cron 5 minutes) serait directement applicable au
filtre "établi + actif, pas juste pompé" déjà recommandé dans les
diligences précédentes (DexScreener, Zora Coins) — un signal social
pourrait renforcer ce filtre, pas le remplacer.

---

## Branches ouvertes (banquées, pas creusées maintenant)

- **Bifrost (AI gateway open-source, Maxim AI)** : passerelle unifiée
  vers 20+ fournisseurs LLM via une API compatible OpenAI — pertinent
  seulement si `aria_core/llm.py` devait un jour gérer plus de
  fournisseurs simultanés que son fallback actuel ; pas creusé, juste
  noté comme piste de simplification potentielle du routing.
- **Redis LangCache** comme implémentation de référence du cache
  sémantique (section 4) — chiffres les plus documentés du secteur, mais
  introduirait une dépendance Redis nouvelle sur le VPS ; à comparer à
  GPTCache (sans dépendance serveur supplémentaire) avant tout choix.
- **ARGUS / benchmarks "firewall" pour l'injection de prompt
  context-aware** (arXiv 2605.03378, cité en section 3) — approche
  concurrente à CaMeL, plus légère à mettre en œuvre (pas d'interpréteur
  personnalisé) mais pas encore comparée en détail ici — à évaluer
  seulement si la piste CaMeL est jugée trop lourde le jour où le
  commandement s'y intéresse.
- **Veracity** (alternative open-source à Loki pour le fact-check,
  section 2, score numérique de véracité + interface multilingue) — non
  comparé en détail à Loki, juste noté comme second candidat.
- **Recoupement à faire avant toute décision cache LLM** : vérifier le
  contenu de `docs/aria-learning-inbox/2026-07-12-optimisation-cout-llm.md`
  (déjà dans le dépôt, pas relu en détail dans cette veille) pour éviter
  de proposer deux fois la même chose sous un angle différent.

## Sources

- [GeckoTerminal API Docs — FAQ](https://apiguide.geckoterminal.com/faq)
- [GeckoTerminal API Docs — Introduction](https://apiguide.geckoterminal.com/)
- [Pool OHLCV chart by Pool Address — CoinGecko API docs](https://docs.coingecko.com/reference/pool-ohlcv-contract-address)
- [dineshpinto/geckoterminal-api (GitHub)](https://github.com/dineshpinto/geckoterminal-api)
- [Libr-AI/OpenFactVerification — Loki (GitHub)](https://github.com/Libr-AI/OpenFactVerification)
- [Loki: An Open-Source Tool for Fact Verification — arXiv 2410.01794](https://arxiv.org/html/2410.01794v1)
- [Google Fact Check Tools API](https://developers.google.com/fact-check/tools/api)
- [Veracity: An Open-Source AI Fact-Checking System — arXiv 2506.15794](https://arxiv.org/html/2506.15794v1)
- [Defeating Prompt Injections by Design (CaMeL) — MIT 6.5660 reading](https://css.csail.mit.edu/6.5660/2026/readings/camel.pdf)
- [The Prompt Injection Problem: A Guide to Defense-in-Depth for AI Agents](https://manveerc.substack.com/p/prompt-injection-defense-architecture-production-ai-agents)
- [Indirect Prompt Injections: Are Firewalls All You Need? — arXiv 2510.05244](https://arxiv.org/pdf/2510.05244)
- [ARGUS: Defending LLM Agents Against Context-Aware Prompt Injection — arXiv 2605.03378](https://arxiv.org/pdf/2605.03378)
- [GPTCache — OpenReview](https://openreview.net/forum?id=ivwM8NwM4Z&noteId=kNvh8Qmekg)
- [Top Semantic Caching Solutions for AI Applications in 2026 — getmaxim.ai](https://www.getmaxim.ai/articles/top-semantic-caching-solutions-for-ai-applications-in-2026/)
- [How to Cut LLM Token Costs in 2026 — wavect.io](https://wavect.io/blog/reduce-llm-token-costs-2026/)
- [Is the Twitter (X) API Free in 2026? The Honest Answer — Sorsa](https://api.sorsa.io/blog/is-twitter-api-free)
- [X (Twitter) API Pricing 2026: Tiers & Real Costs — Sorsa](https://api.sorsa.io/blog/twitter-api-pricing-2026)
- [7 Best Twitter/X API Alternatives in 2026 — xpoz.ai](https://www.xpoz.ai/blog/comparisons/best-twitter-api-alternatives-2026/)
- Document de référence interne : `docs/architecture-extensibilite.md`
  (`GoldenFarFR/ARIA`, lu directement via raw GitHub pour cadrer la
  connectabilité de chaque branche, 2026-07-13)

## Frontières confirmées respectées

Aucun code touché, aucun client câblé, aucun fichier de garde-fou
(`permission_mode`/`wallet_guard`/règles-uniques/`config.toml`) approché
ni même ouvert. Aucun capital réel, aucun secret, aucune exécution
autonome, aucune auto-modification du système dans les cinq branches —
la section 3 (injection de prompt) est explicitement de la veille
informationnelle sur une frontière déjà existante, pas une proposition de
modification. Décision d'intégration pour chacune des cinq branches
laissée entièrement au commandement.
