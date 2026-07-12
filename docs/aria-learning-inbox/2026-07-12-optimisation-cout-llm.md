[VPS Research]

# Optimisation coût LLM — approfondissement (résout les points laissés ouverts par #9 et #11)

Suite au constat déjà fait sur `llm_usage.py` (appels `depth="develop"` =
72,5 % des tokens d'entrée pour 28 % des appels). Deux pistes de la passe 3
avaient été banquées avec un point explicitement laissé ouvert ("donnée non
disponible dans cet audit" pour #9, "à faire avant de considérer ce point
actionnable" pour #11) — cette note les résout avec des données réelles,
plus une évaluation neuve du batching. Aucune des deux pistes n'est
reproposée depuis zéro : c'est un approfondissement, pas un doublon.

---

## Données réelles vérifiées (juillet 2026, `/opt/aria-data/llm-usage/2026-07.jsonl`, lecture seule)

```
depth="develop"  : 109 appels — 1 289 788 tokens entrée — 69 556 tokens sortie
depth=None/none  : 358 appels —   595 335 tokens entrée — 235 527 tokens sortie
```

**Grep complet des call sites qui passent explicitement `depth="develop"`** :
exactement **2 fichiers, 2 lignes** :
- `skills/vc_analysis.py:1031`
- `skills/vc_judge.py:484`

**Conclusion directe, importante** : `depth="develop"` n'est PAS mal utilisé
— il est déjà et uniquement réservé aux deux tâches que l'opérateur a
explicitement demandé de protéger (VC, garde-fous/juge). La concentration
de coût observée n'est donc pas un problème de mauvais routage à corriger,
c'est le signe que la discipline actuelle est déjà correcte. **Aucune
recommandation de downgrade sur `vc_analysis.py`/`vc_judge.py`** — le
faire dégraderait exactement les deux tâches à protéger.

---

## 9bis. Résolution de #9 (cascade coût/difficulté) — le bon périmètre est le bucket "none", pas "develop"

Le seam identifié en passe 3 restait valide (`depth` traverse tout `llm.py`
mais n'est lu que pour le logging, jamais pour choisir une route) — donnée
manquante alors : le volume réel des appels "faciles". Maintenant connue :
**358 appels/mois, 595k tokens d'entrée**, répartis sur ~18 fichiers
appelants (`community_feedback.py`, `x_voice.py`, `brain.py`,
`wallet_guard.py`, `tweet_compose_workflow.py`, `proactive.py`, `avatar.py`,
`relay_conversation.py`, `avatar_style_refresh.py`,
`knowledge/epistemic_critic.py`, `knowledge/x_insight_relevance.py`,
`skills/comms_skill.py`, `qi_self_judge_shadow.py`,
`knowledge/web_verify.py`, `knowledge/epistemic.py`,
`knowledge/app_idea_poll.py`, `gateway/x_engagement.py`) — aucun d'eux ne
passe `depth="develop"`, donc **un routage cascade scopé exclusivement au
cas `depth is None` ne peut structurellement pas toucher `vc_analysis.py`/
`vc_judge.py`** — garantie par construction (grep, pas une promesse), pas
seulement par discipline de code.

**Proposition concrète.** Faire lire `depth` par `_resolve_model`/
`_route_for_provider` (seam déjà confirmé, `llm.py`) : quand `depth is None`
(le cas par défaut de ces 18 appelants), autoriser un modèle moins cher que
le modèle primaire (ex. router vers `groq`/`llama-3.3-70b-versatile`, déjà
dans `DEFAULT_MODELS`, au lieu du modèle premium primaire) ; quand
`depth="develop"`, comportement strictement inchangé (route primaire
actuelle, aucune modification). Changement additif et réversible par un
flag (`ARIA_LLM_CASCADE_ENABLED`, gated OFF par défaut comme les autres
mécanismes sensibles) — première activation observée en shadow (logger
seulement quelle route *aurait* été choisie, sans changer le comportement
réel) avant bascule effective, pour mesurer l'écart de qualité avant
d'agir en production.

**Upside concret.** 358 appels/mois à un tarif "premium" alors qu'ils ne
sont explicitement pas les tâches critiques — le gain dépend du tarif
réel du modèle premium actuellement utilisé pour ces appels (non
déterminable depuis ce VPS, pas de credentials LLM live ici, cf.
contrainte déjà connue) mais la structure du gain (jusqu'à 358 appels/mois
déplaçables) est confirmée par les données réelles, pas supposée.

## 11bis. Résolution de #11 (prompt caching) — vérifié, mais bute sur un vrai inconnu (le proxy Virtuals)

**Ordre du prompt — vérifié, déjà correct.** Lu `chat_with_context` en
entier (`llm.py:245-311`) : `messages = [{"role": "system", "content":
system_context}, ...history, {"role": "user", "content": user_content}]` —
le contenu stable (`system_context` = `_SYSTEM_PROMPT` + directive de
langue) est **déjà** en première position, le contenu variable (données du
scan) en dernier. Rien à changer ici — le point resté ouvert en passe 3 est
résolu : l'ordre est déjà celui qui permettrait un caching de préfixe s'il
était disponible.

**Taille du bloc stable — mesurée, pas estimée.** `_SYSTEM_PROMPT` dans
`vc_analysis.py` (lignes 63-100, avant la clause anti-clichés ajoutée en
#120) fait ~38 lignes, quelques centaines de tokens — petit mais
**identique à chaque appel `depth="develop"`**, donc un candidat réel si le
caching était actif.

**Le vrai blocage, confirmé cette fois (pas supposé).** `PROVIDER_URLS`
(`llm.py:13-19`) confirme que la route qui compte en production passe par
`virtuals` → `https://compute.virtuals.io/v1/chat/completions` (un proxy
Virtuals Protocol, surface compatible OpenAI chat/completions), pas
directement Anthropic ni OpenAI. Recherche faite : **aucune documentation
publique trouvée** sur le comportement de caching de ce proxy précis
(prompt caching natif Anthropic type `cache_control` ? passthrough
transparent ? aucun caching du tout ?). Impossible de conclure depuis le
code ou une recherche web publique — **à vérifier directement auprès du
support/documentation développeur Virtuals**, pas quelque chose qu'un audit
de code peut trancher. Tant que non confirmé, ce chantier reste "prêt côté
ARIA, bloqué côté fournisseur inconnu" plutôt que "actionnable".

**Verdict : structure déjà optimale côté ARIA (rien à changer dans l'ordre
du prompt), gain réel conditionné à une réponse du support Virtuals sur le
comportement de caching de leur proxy — à poser comme question, pas à
coder en aveugle.**

## 12bis. Batch API (nouveau — évalué et jugé peu applicable en l'état)

**Légitimité.** Réelle et significative : Anthropic Message Batches API
(-50 %, 24h SLA), OpenAI Batch API (-50 %, même structure), Groq (Batch +
prompt caching cumulables, jusqu'à ~25 % du tarif à la demande). Aucun
projet crypto, fonctionnalité native des providers.

**Vérifié avant de proposer.** `chat_with_context` est un `await` unique,
synchrone, utilisé indifféremment par les chemins interactifs
(`brain.py`, `relay_conversation.py` — un humain ou Claude Code attend la
réponse en direct) et les tâches de fond (`proactive.py`, `avatar.py`,
déclenchées via `heartbeat.py`). **Aucun des deux ne peut absorber une
latence de plusieurs heures à 24h** sans une réécriture structurelle :
même les tâches de fond aujourd'hui appellent et attendent la réponse dans
le même cycle heartbeat (pas de mécanique "soumettre maintenant, relire au
prochain passage" — `HeartbeatTask` n'a pas de notion de job différé).

**Verdict : écarté pour l'instant, pas un gain rapide.** Le blocage n'est
pas le manque de légitimité de l'API (elle est solide et le rabais est
réel) mais l'absence, dans l'architecture actuelle d'ARIA, d'un mécanisme
de soumission/relecture asynchrone — un chantier structurel bien plus
lourd que le gain immédiat ne le justifie tant que le volume par tâche de
fond reste modeste (aucune tâche heartbeat n'envoie aujourd'hui de gros
lots de prompts en une fois — le pattern d'usage réel est "un appel, une
tâche", pas "traiter 500 items ce soir"). À réévaluer si un jour une tâche
de fond nécessite un traitement par lot volumineux (ex. si la
consolidation mémoire de #28 devait un jour traiter d'un coup un grand
volume historique plutôt qu'incrémentalement au fil de l'eau, comme conçu).

---

## Verdict global de cette note

Les deux pistes de la passe 3 sont maintenant tranchées avec des données
réelles au lieu d'un signal incomplet : #9 devient une proposition concrète
et sûre par construction (le cascade ne peut pas toucher `develop`, grep à
l'appui), #11 est bloquée sur une seule question précise à poser au support
Virtuals plutôt que sur du travail de code côté ARIA. Le batch API,
sujet neuf de cette note, est écarté honnêtement — solide comme technique,
mais l'architecture actuelle d'ARIA (100 % synchrone) n'a pas de point
d'ancrage pour en tirer parti sans un chantier plus lourd que le gain.
