# [VPS Research] Diligence — IMDA Singapour, premier cadre gouvernemental complet dédié à l'IA agentique (22/01/2026)

## Contexte et périmètre

Veille juridique/gouvernance pure, pas une proposition de structure — même
doctrine que les fiches `2026-08-06-veille-juridique-responsabilite-agents-ia-trading.md`
et `2026-08-15-veille-reglementaire-consolidee-aout2026.md` (déjà existantes,
ne pas dupliquer : celles-ci couvrent des PRÉCÉDENTS/POURSUITES, ici c'est un
CADRE DE GOUVERNANCE national complet, jamais catalogué sous cette forme).
Pertinent pour `docs/conformite-dossier-avocat.md` (dossier à valider par un
avocat avant tout encaissement réel) et pour le mandat permanent
"forces/faiblesses d'une IA trader" (15/07). Banque le repère et une première
comparaison point par point, ne tranche rien, aucun code touché.

## Le cadre (vérifié WebSearch 21/08/2026, sources IMDA/MDDI + cabinets)

Le "Model AI Governance Framework for Agentic AI" (MGF), publié par l'IMDA
(Infocomm Media Development Authority) le 22/01/2026 lors du Forum économique
mondial, est présenté comme le premier cadre de gouvernance national au monde
spécifiquement dédié aux agents IA autonomes (ceux qui planifient/raisonnent/
agissent de façon indépendante pour le compte d'un utilisateur). Conformité
volontaire, mais les organisations restent légalement responsables du
comportement de leurs agents — s'applique à toute organisation déployant de
l'IA agentique à Singapour, qu'elle développe l'agent en interne ou utilise un
agent tiers.

Quatre dimensions structurantes :
1. **Évaluer et borner les risques en amont** (avant déploiement, pas après).
2. **Rendre l'humain réellement responsable** ("meaningfully accountable" —
   pas une supervision de façade).
3. **Contrôles techniques et processus nommés** : moindre privilège sur
   l'accès en écriture aux bases de données, whitelisting des serveurs/outils
   de confiance, sandboxing de l'exécution de code, identité numérique
   vérifiable + piste d'audit obligatoire pour chaque agent (qui a agi, sous
   quelle autorisation).
4. **Responsabilité de l'utilisateur final** activée (l'utilisateur garde un
   rôle actif, pas un simple spectateur).

## Comparaison point par point avec l'architecture ARIA déjà en place

| Contrôle nommé par le MGF | Équivalent côté ARIA aujourd'hui |
|---|---|
| Identité vérifiable + piste d'audit par agent | `agent_wallet_log.py` (ok/failed/blocked par tentative), `system_issues.py`, journalisation systématique déjà en place |
| Moindre privilège sur l'accès en écriture aux données | Pas un contrôle formalisé en tant que tel — `wallet_guard`/`agent_wallet_pilot.py` restent structurellement séparés (`test_coherence` verrouillé), mais aucun inventaire explicite "quel processus a un accès en écriture à quelle table" n'existe |
| Whitelisting des serveurs/outils de confiance | Partiel : `research-loop` tourne avec `--allowedTools`/`--disallowedTools` hard-codés ; pas de whitelisting équivalent pour une session Claude Code de commandement classique (accès shell complet) |
| Sandboxing de l'exécution de code | Absent pour les sessions de commandement (accès direct au VPS de prod, cf. mandat #192/MOSAIC déjà catalogué) |
| Supervision humaine non-décisionnelle mais réelle | Doctrine ARIA déjà explicite (gouvernance stricte, décision finale opérateur, `docs/protocole-argent-reel.md`) |
| Bornage des risques en amont | Partiel : garde-fous (`wallet_guard`, plafonds 10-25$, slippage ≤10%) bornent l'EXÉCUTION, mais aucune évaluation de risque formalisée AVANT le déploiement d'un nouveau mécanisme (le processus reste "Analyser → Proposer → go → Implémenter") |

## Potentiel concret pour ARIA

Premier cadre gouvernemental complet (pas un principe généraliste comme les
guidelines déjà cataloguées) à comparer point par point plutôt qu'à résumer —
deux gaps réels et non triviaux ressortent de la comparaison ci-dessus : (1)
aucun inventaire explicite de moindre-privilège par processus/table, (2)
aucun sandboxing de l'exécution de code pour les sessions de commandement
(accès shell complet au VPS de prod, déjà noté comme surface de risque via
MOSAIC/#192 mais jamais formalisé comme un gap de gouvernance spécifique).
Aucune action de code — repère à verser au dossier avocat et à la prochaine
revue du mandat #192, décision de durcissement (sandboxing, whitelisting)
hors du périmètre de cette veille (toucherait potentiellement l'architecture
d'exécution elle-même, donc validation opérateur explicite requise).

## Branches ouvertes (banquées, pas creusées)

- Mise à jour de juin 2026 du MGF (citée par Baker McKenzie) — vérifier ce
  qui a changé depuis la V1.0 de janvier avant toute citation future.
- Aucune organisation basée à Singapour dans le périmètre ARIA aujourd'hui —
  vérifier si `ariavanguardzhc.com` crée une exposition Singapour (utilisateurs
  potentiels) similaire à la question déjà ouverte sur l'AI Act UE.
- Comparer ce MGF au rapport FINRA 2026 déjà catalogué (fiche du 15/08) —
  les deux nomment "human in the loop"/accountability, mais le MGF est plus
  précis sur les contrôles TECHNIQUES (moindre privilège, sandboxing) que
  FINRA ne le formalise.

## Sources

- [Singapore Introduces New Model AI Governance Framework for Agentic AI (Bird & Bird)](https://www.twobirds.com/en/insights/2026/singapore/singapore-introduces-new-model-ai-governance-framework-for-agentic-ai)
- [Singapore: Governance Framework for Agentic AI Launched (Baker McKenzie)](https://www.bakermckenzie.com/en/insight/publications/2026/01/singapore-governance-framework-for-agentic-ai-launched)
- [Singapore Launches New Model AI Governance Framework for Agentic AI (MDDI, communiqué officiel)](https://www.mddi.gov.sg/newsroom/singapore-launches-new-model-ai-governance-framework-for-agentic-ai--/)
- [Singapore: IMDA updates Model AI Governance Framework for Agentic AI (Baker McKenzie, mise à jour 06/2026)](https://www.bakermckenzie.com/en/insight/publications/2026/06/singapore-imda-updates-model-ai-governance-framework-for-agentic-ai)
- [IMDA — communiqué de presse officiel](https://www.imda.gov.sg/resources/press-releases-factsheets-and-speeches/press-releases/2026/new-model-ai-governance-framework-for-agentic-ai)
