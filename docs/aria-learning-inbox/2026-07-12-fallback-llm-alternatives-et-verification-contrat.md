[VPS Research]

# Alternatives au fallback LLM Groq, détection de dégradation de route, alternative à Blockscout pour la vérification de contrat

Suite au finding #117 (le fallback Groq/Llama-3.3-70b a pris une vraie
mauvaise décision sur un scénario à 8M$ de risque exposé — profondeur de
raisonnement dégradée vs Spark). Trois pistes ciblées, chacune vérifiée
contre le code réel avant proposition.

## Constat de départ (vérifié dans le code avant recherche)

- `llm.py::PROVIDER_URLS`/`DEFAULT_MODELS` — **`openrouter` est déjà un
  provider câblé** (`https://openrouter.ai/api/v1/chat/completions`,
  modèle par défaut `openrouter/free`), au même titre que `groq`/`xai`/
  `openai`. Il n'est simplement pas le fallback **actif** aujourd'hui
  (`llm_fallback_provider` = groq, confirmé par le contexte du finding #117).
- `llm.py::_fallback_route()` lit `llm_fallback_provider`/`llm_fallback_model`/
  `llm_fallback_api_key` — **le fallback est déjà générique par config**,
  pas câblé en dur sur Groq. Changer de fallback (ou en ajouter un second)
  est un changement de **configuration**, pas une nouvelle intégration de
  code — point anti-duplication important pour la suite.
- `llm.py::chat_with_context()` boucle sur les routes (`_resolve_routes`)
  et ne retourne **que le texte de la réponse** (`reply`) à l'appelant —
  le provider/modèle qui a effectivement répondu n'est jamais propagé
  au-delà d'un `logger.info` (non persistant, non lu par le code appelant).
  `record_llm_usage(provider=..., model=...)` persiste bien provider/modèle
  dans `data/llm-usage/YYYY-MM.jsonl`, **mais c'est de la télémétrie de
  coût**, pas un signal disponible en temps réel pour `vc_judge.py` ou tout
  code qui déciderait différemment selon la route ayant répondu.
- `services/blockscout.py::AddressInfo.is_verified` est la seule source de
  vérification de contrat utilisée aujourd'hui, dépendante à 100% de la
  disponibilité de `base.blockscout.com`.

---

## 1. Alternative gratuite/pas chère à Groq avec meilleure profondeur de raisonnement

**Pas de piste réellement gratuite trouvée pour du raisonnement profond —
honnêteté d'abord.** DeepSeek R1 était disponible gratuitement via
OpenRouter (`deepseek/deepseek-r1:free`) début 2026, **mais confirmé que
depuis juillet 2026 tous les modèles DeepSeek sur OpenRouter sont
payants** — la porte gratuite s'est refermée entre le moment où cette
piste a été suggérée et cette recherche.

**Ceci dit, "payant" ici veut dire extrêmement bon marché** : DeepSeek R1
coûte $0,55/1M tokens entrée + $2,19/1M sortie sur l'API officielle —
environ 27x moins cher que OpenAI o1 pour une classe de raisonnement
comparable (math/code/logique multi-étapes), et le modèle est
spécifiquement construit pour ce type de tâche (contrairement à
Llama-3.3-70b, généraliste). Recommandé comme **second fallback** (pas un
remplacement pur de Groq — la chaîne peut inclure les deux), routable via
le provider `openrouter` déjà câblé dans `llm.py` sans code nouveau — juste
`llm_fallback_provider=openrouter` + `llm_fallback_model=deepseek/deepseek-r1`
(ou l'inverse, ajouter DeepSeek comme fallback secondaire après Groq).

**Alternative explorée : modèles Qwen "thinking"** (disponibles via
OpenRouter/Alibaba Cloud Model Studio) — profondeur de raisonnement réelle,
mode hybride (thinking on/off par requête). **Mais un signal d'alerte
sérieux trouvé dans la littérature de sécurité** : activer le mode
"thinking" **augmente mesurablement le taux de réponses problématiques**
sur les variantes Qwen3 8B/14B/32B (ex. 35,4%→41,4% sur 8B) — l'effet
s'inverse seulement sur les très gros modèles (235B). Pour un usage
explicitement lié à des **décisions de sécurité** (le scénario même du
finding #117), c'est un risque à ne pas ignorer — Qwen thinking n'est pas
recommandé tel quel pour ce rôle sans validation empirique supplémentaire.

**Verdict : DeepSeek R1 (via OpenRouter, payant mais ~27x moins cher que
la référence premium, provider déjà câblé) recommandé comme second
fallback candidat pour la profondeur de raisonnement — pas de vraie
option gratuite trouvée, contrairement à l'espoir initial.** Qwen thinking
écarté pour ce rôle précis (risque de sécurité documenté sous mode
thinking), pourrait être reconsidéré pour des tâches non liées à la
sécurité.

## 2. Détecter qu'une réponse vient d'un fallback dégradé (suite #135)

**Vérifié : la donnée existe déjà dans le code, elle n'est simplement pas
propagée où elle serait utile.** `_post_chat`/`chat_with_context` savent
exactement quelle `route` (provider + modèle) a répondu à chaque itération
de la boucle — c'est déjà loggé (`logger.info("LLM fallback ok
provider=%s model=%s...")`) et déjà persisté en télémétrie de coût
(`record_llm_usage`). Ce qui manque : cette information n'est **jamais
retournée à l'appelant** (`chat_with_context` ne renvoie que la chaîne de
texte) — donc `vc_judge.py`/`vc_analysis.py` ne peuvent pas aujourd'hui
distinguer "réponse de Spark" vs "réponse de Groq" au moment de la
décision.

**Pattern déjà standard ailleurs (confirmé par la recherche)** : OpenRouter
expose nativement le champ `model` dans chaque réponse — c'est exactement
le mécanisme que `chat_with_context` pourrait reproduire en interne, sans
appel réseau supplémentaire, puisque `route.provider`/`route.model` sont
déjà connus au point d'appel.

**Coût de mise en œuvre (si dispatché) : faible.** Pas un nouveau
mécanisme de détection à construire — juste un changement de signature
(`chat_with_context` retourne `(reply, provider, model)` ou un objet
au lieu d'une simple chaîne), puis les appelants à enjeu (`vc_judge.py`)
peuvent conditionner un texte d'alerte explicite ("réponse produite par
un fallback dégradé, à valider avec plus de prudence") quand
`provider != "virtuals"`. **Recherche seulement, pas d'implémentation** —
mais le chemin est concret et court si "commandement" décide de le
dispatcher.

**Verdict : priorité moyenne-haute compte tenu du précédent #117.** Le
gap n'est pas un manque de donnée, c'est un manque de propagation d'une
donnée déjà présente — la correction est structurellement petite.

## 3. Alternative légitime à Blockscout pour la vérification de contrat (Volet C)

**Sourcify confirmé comme le complément le plus solide, pas un
remplacement.** Gratuit, **sans clé API, sans rate limit documenté**,
couvre toutes les chaînes EVM depuis une seule soumission ("submit once,
verify everywhere"). API v2 directement exploitable :
`GET /v2/contract/{chainId}/{address}` retourne le statut de vérification
(`match`/`creationMatch`/`runtimeMatch`/`verifiedAt`) pour une adresse
donnée — exactement le signal que `AddressInfo.is_verified` porte déjà
côté Blockscout, mais via une source **indépendante de la disponibilité de
`base.blockscout.com`**.

**Point important, trouvé en creusant** : Blockscout **auto-vérifie déjà
en interne les contrats trouvés vérifiés sur Sourcify** — donc
`is_verified` tel que lu aujourd'hui par ARIA bénéficie probablement déjà
indirectement de Sourcify quand Blockscout répond normalement. **Le vrai
gain de Sourcify n'est donc pas un signal different, c'est la
redondance** : si `base.blockscout.com` est indisponible/rate-limité (le
mode de dégradation déjà géré par `available=False` dans
`services/blockscout.py`), Sourcify offre un second chemin de lecture pour
`is_verified` spécifiquement, gratuit et sans clé.

**Alternative écartée : Basescan (Etherscan V2 API)** — fonctionnellement
comparable pour la vérification, mais nécessite une clé API et un rate
limit de 3 req/s / 100 000 req/jour en gratuit (contre aucun rate limit
documenté pour Sourcify) — pas de raison d'ajouter cette contrainte
opérationnelle quand Sourcify couvre le même besoin sans elle.

**Verdict : Sourcify (`/v2/contract/{chainId}/{address}`) recommandé comme
fallback de lecture pour `is_verified` uniquement**, à brancher dans
`services/blockscout.py` (ou un module sœur) selon le même patron de
dégradation douce déjà en place (`available=False`, jamais de donnée
inventée) — **ne remplace pas** les autres champs que Blockscout fournit
seul (holders_count, fonctions sensibles, solde) qui restent hors du
périmètre de Sourcify.

---

## Synthèse pour le Volet C / arbitrage

| Piste | Verdict | Effort si dispatché |
|---|---|---|
| DeepSeek R1 via OpenRouter (2e fallback) | Recommandé, payant mais ~27x moins cher que la référence premium — pas de vraie option gratuite trouvée | Config uniquement (`llm_fallback_provider`/`model`) |
| Qwen thinking (fallback) | Écarté pour un rôle sécurité — risque documenté d'augmentation de réponses problématiques en mode thinking | — |
| Détection de route dégradée (#135) | Priorité moyenne-haute, donnée déjà présente, juste pas propagée | Petit changement de signature `chat_with_context` |
| Sourcify (vérification contrat) | Recommandé comme fallback de lecture, pas remplacement | Nouveau module léger, patron de dégradation déjà en place |

Frontières confirmées respectées : aucune piste ne touche
`permission_mode`/`wallet_guard`/`config.toml`/capital réel/auto-modification.
Recherche uniquement — aucune modification de code effectuée.

## Sources

- [DeepSeek API Pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- [DeepSeek AI: R1 Reasoning, API & Local Deployment 2026](https://deepseek.ai/pricing)
- [OpenRouter Free Tier 2026: Rate Limits, Models, BYOK](https://klymentiev.com/blog/openrouter-free-tier)
- [DeepSeek R1 — OpenRouter](https://openrouter.ai/deepseek/deepseek-r1)
- [Qwen API and Models — OpenRouter](https://openrouter.ai/qwen)
- [When Models Outthink Their Safety: Unveiling and Mitigating Self-Jailbreak in Large Reasoning Models](https://arxiv.org/pdf/2510.21285)
- [SafeRBench: Dissecting the Reasoning Safety of Large Language Models](https://arxiv.org/pdf/2511.15169)
- [OpenRouter Model Fallbacks — docs](https://openrouter.ai/docs/guides/routing/model-fallbacks)
- [How OpenRouter Model Routing Works](https://openrouter.ai/blog/insights/model-routing/)
- [Sourcify APIv2: Getting Verified Contracts](https://docs.sourcify.dev/blog/apiv2-lookup-endpoints/)
- [Sourcify API docs](https://docs.sourcify.dev/docs/api/)
- [Blockscout vs Etherscan API: Free Tier, Pricing & Rate Limits Compared (2026)](https://www.blog.blockscout.com/blockscout-vs-etherscan-api-free-tier-pricing-rate-limits-compared-2026/)
- [Basescan rate limits](https://docs.basescan.org/support/rate-limits)
- Code ARIA vérifié : `packages/aria-core/src/aria_core/llm.py` (`PROVIDER_URLS`, `DEFAULT_MODELS`, `_fallback_route`, `chat_with_context`, `_post_chat`), `packages/aria-core/src/aria_core/llm_usage.py` (`record_llm_usage`), `packages/aria-core/src/aria_core/services/blockscout.py` (`AddressInfo.is_verified`)
