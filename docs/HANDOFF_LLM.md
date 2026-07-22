# HANDOFF — LLM (provider, routage, identité)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[DEPLOYE] Sujet    : Bascule Spark → Grok/x.ai en urgence, 3 bugs trouvés
Date : 2026.07.17  /  Probleme : crédits gratuits Virtuals expirant le 18/07 — resolve_provider() ignorait LLM_PROVIDER du .env (ne lisait que le "vault" Windows, absent sur ce VPS) ; provider direct recevait l'ID catalogue Virtuals au lieu de son propre défaut ; GROK_API_KEY absente des deux classes Settings
Solution : env en premier pour resolve_provider ; llm_model réservé au provider "virtuals" ; champ grok_api_key ajouté aux deux classes. Vérifié 200 OK réel, sans repli Groq — aria_core/llm.py

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Provider LLM en prod
Date : 2026.07.22  /  Probleme : —
Solution : Grok/x.ai en primaire, fallback Groq (llama-3.3-70b-versatile) si x.ai tombe

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Le fallback LLM (Groq) dégrade réellement la profondeur de raisonnement
Date : 2026.07.12  /  Probleme : comparaison multi-modèles (Spark/Virtuals, Grok/x.ai, Groq) sur des prompts durs : résistance à l'injection de prompt tient sur les 3 providers, mais Groq (fallback par défaut, llama-3.3-70b) prend une décision opérationnelle erronée sur un scénario de sécurité one-shot, exactement l'option que la réponse Spark de référence réfute.
Solution : Constat documenté (n=2 prompts, signal réel mais pas une preuve statistique) — la validation humaine déjà obligatoire sur tout capital réel limite le risque immédiat ; amélioration non urgente identifiée (signaler visiblement dans la réponse quand elle vient du fallback) — cf. historique git 12/07 (#117/#135).

------------------------------------------------------------

[CODE] Sujet    : Provider direct DeepSeek ajoute + bug de resolution de modele/provider
Date : 2026.07.16  /  Probleme : _resolve_model() renvoyait pour tout provider direct sans modele explicite (xai/grok/deepseek/openai) l'ID catalogue Virtuals (ex. "x-ai-grok-4-3"), format inconnu de ces vraies API ; et spark_config.resolve_provider() force "virtuals" tant que VIRTUALS_API_KEY fait >=10 caracteres — changer LLM_PROVIDER seul ne bascule rien.
Solution : provider DeepSeek ajoute (independant de Virtuals) ; bug _resolve_model() corrige ; bascule reelle exige de vider VIRTUALS_API_KEY en plus de poser LLM_PROVIDER — jamais effectuee telle quelle, la bascule Grok/x.ai du 17/07 a pris le relais (voir entrees suivantes de ce fichier pour l'etat actuel) — llm.py (cf. historique git 16/07)
