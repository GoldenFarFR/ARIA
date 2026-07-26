# HANDOFF — LLM (provider, routage, identité)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[DEPLOYE] Sujet    : Bascule Spark → Grok/x.ai en urgence, 3 bugs trouvés
Date : 2026.07.17  /  Probleme : crédits gratuits Virtuals expirant le 18/07 — resolve_provider() ignorait LLM_PROVIDER du .env (ne lisait que le "vault" Windows, absent sur ce VPS) ; provider direct recevait l'ID catalogue Virtuals au lieu de son propre défaut ; GROK_API_KEY absente des deux classes Settings
Solution : env en premier pour resolve_provider ; llm_model réservé au provider "virtuals" ; champ grok_api_key ajouté aux deux classes. Vérifié 200 OK réel, sans repli Groq — aria_core/llm.py

------------------------------------------------------------

[CODE] Sujet    : ARIA_OUVRIER_CLOUD vide retombe sur "spark" (defaut cache du registre), pas "grok" -- 3e facteur jamais documente
Date : 2026.07.25 / Probleme : trouve en diagnostiquant pourquoi le canal relay Claude<->ARIA ne repondait plus du tout (les 2 fallbacks -- Virtuals sans cle, Groq/llama a sa limite quotidienne -- echouaient tous les deux). LLM_PROVIDER=grok et VIRTUALS_API_KEY vides etaient bien poses dans le .env (les 2 facteurs deja connus, cf. entree du 16/07 juste en dessous), mais `resolve_provider()` verifie aussi `ARIA_OUVRIER_CLOUD` -- vider cette 3e variable ne la "desactive" pas, `os.environ.get(...) or ""` la fait retomber sur `merged.get("ARIA_OUVRIER_CLOUD")`, qui vient du registre `ecosystem_registry.yaml` (defauts injectes par `propagate_operator_env`) et vaut encore "spark" -- des que `ouvrier_cloud in ("spark","virtuals")`, `resolve_provider()` force le retour a "virtuals" QUEL QUE SOIT `LLM_PROVIDER`. Verifie empiriquement (`resolve_spark_runtime().provider == "virtuals"` malgre `LLM_PROVIDER=grok` confirme dans l'environnement du conteneur) -- jamais suppose. Consequence : le "Grok/x.ai en primaire" affirme par l'entree ETAT ACTUEL precedente etait faux depuis la bascule du 22/07 -- chaque appel LLM passait en realite par Virtuals (echec silencieux, pas de cle) puis par le fallback Groq/llama, qui a fini par epuiser son quota gratuit quotidien (100k TPD).
Solution : `ARIA_OUVRIER_CLOUD` doit recevoir une valeur EXPLICITE differente de "spark"/"virtuals" (ex. "grok") pour desactiver reellement le mode spark -- le vider ne suffit jamais, meme combine aux 2 autres facteurs deja connus. Verifie apres correction : `resolve_spark_runtime().provider == "grok"` en conditions reelles sur le conteneur redeploye. Aucun changement de code (le defaut "spark" du registre reste une decision produit valide pour le cas general Virtuals/Spark) -- uniquement une lecon de configuration, gravee ici pour la 4e fois qu'une bascule de provider est tentee.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Provider LLM en prod
Date : 2026.07.22 (corrige 2026.07.25)  /  Probleme : —
Solution : Grok/x.ai en primaire (verifie reellement le 25/07 -- l'affirmation du 22/07 etait fausse en pratique depuis un moment, voir entree juste au-dessus), fallback Groq (llama-3.3-70b-versatile) si x.ai tombe. Pour toute bascule de provider future : verifier LES 3 facteurs (LLM_PROVIDER, VIRTUALS_API_KEY, ARIA_OUVRIER_CLOUD) sont explicitement poses, jamais juste vides.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Le fallback LLM (Groq) dégrade réellement la profondeur de raisonnement
Date : 2026.07.12  /  Probleme : comparaison multi-modèles (Spark/Virtuals, Grok/x.ai, Groq) sur des prompts durs : résistance à l'injection de prompt tient sur les 3 providers, mais Groq (fallback par défaut, llama-3.3-70b) prend une décision opérationnelle erronée sur un scénario de sécurité one-shot, exactement l'option que la réponse Spark de référence réfute.
Solution : Constat documenté (n=2 prompts, signal réel mais pas une preuve statistique) — la validation humaine déjà obligatoire sur tout capital réel limite le risque immédiat ; amélioration non urgente identifiée (signaler visiblement dans la réponse quand elle vient du fallback) — cf. historique git 12/07 (#117/#135).

------------------------------------------------------------

[CODE] Sujet    : Provider direct DeepSeek ajoute + bug de resolution de modele/provider
Date : 2026.07.16  /  Probleme : _resolve_model() renvoyait pour tout provider direct sans modele explicite (xai/grok/deepseek/openai) l'ID catalogue Virtuals (ex. "x-ai-grok-4-3"), format inconnu de ces vraies API ; et spark_config.resolve_provider() force "virtuals" tant que VIRTUALS_API_KEY fait >=10 caracteres — changer LLM_PROVIDER seul ne bascule rien.
Solution : provider DeepSeek ajoute (independant de Virtuals) ; bug _resolve_model() corrige ; bascule reelle exige de vider VIRTUALS_API_KEY en plus de poser LLM_PROVIDER — jamais effectuee telle quelle, la bascule Grok/x.ai du 17/07 a pris le relais (voir entrees suivantes de ce fichier pour l'etat actuel) — llm.py (cf. historique git 16/07)

------------------------------------------------------------

[CODE] Subject  : Same class of bug recurred with a different Virtuals catalog ID (anthropic-claude-opus-4-8)
Date : 2026.07.26 / Problem : operator report ("aria ne trade pas en scalping") led to a real prod-log finding: `provider=grok model=anthropic-claude-opus-4-8` -- HTTP 400 "Model not found" on EVERY call reaching this state, silently forcing every LLM call in the system (conversation AND automated decisions) onto the Groq fallback, which then hit its own daily token quota (98.6%/100000 tokens observed). `llm_economy._spark_model_for_depth` already had a guard for the conversation path (rejects a Virtuals catalog ID when Virtuals isn't the active provider, verified live to correctly return `None` here) -- an exhaustive search across the codebase found NO caller reaching `_resolve_model` with this exact explicit model, meaning some path still bypasses that guard (root cause never fully identified despite the search).
Solution : rather than leave every other `chat_with_context` caller exposed to the same silent failure mode, the same "reject a Virtuals catalog ID for a non-Virtuals provider" check was added directly inside `_resolve_model` itself -- the single funnel point every real-provider call goes through regardless of which caller resolved the model, so this class of bug can no longer reach a real third-party API no matter where it originates. Verified live in the prod container: `_resolve_model("grok", "anthropic-claude-opus-4-8")` now returns `"grok-4.3"` (DEFAULT_MODELS["grok"]) with a warning logged, instead of the 400. `llm.py` -- 1 new dedicated test (`test_llm_fallback.py`), full suite + `test_coherence.py` green.
