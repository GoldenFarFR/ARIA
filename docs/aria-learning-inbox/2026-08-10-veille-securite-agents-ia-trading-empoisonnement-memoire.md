# [Veille sécurité] Empoisonnement de mémoire chez les agents IA de trading — vérifié contre l'architecture réelle d'ARIA

## Contexte et périmètre

Mandat permanent "forces/faiblesses d'une IA trading" (CLAUDE.md, 15/07,
boucle continue) : recherche externe sur les faiblesses spécifiques aux IA
(hallucination, surapprentissage, injection de prompt adversariale), puis
vérification systématique contre le code réel d'ARIA — jamais un simple
portrait de vainqueurs, toujours une preuve comparative.

## Ce que la recherche externe confirme (vérifié WebSearch, sources datées)

- **Incident réel documenté** (OECD.AI, base d'incidents IA officielle,
  04/05/2026) : exploit d'injection de prompt réussi contre un modèle Grok
  via un payload encodé en morse, ayant fait transférer ~175 000$ de tokens
  DRB vers le wallet de l'attaquant.
- **Incident Step Finance** (2026) : ~27M$ déplacés sans autorisation
  humaine via des appareils exécutifs compromis + un protocole d'agent trop
  permissif.
- **Taux de réussite mesurés** (étude académique multi-institutions, citée
  dans plusieurs sources sécurité 2026) : injection directe réussie 79% du
  temps, injection indirecte (via page web/email/flux marché tiers) entre
  41,7% et 68,2%.
- **Point architectural central** : un modèle ne distingue pas
  nativement une instruction système fiable d'une donnée adverse injectée —
  les deux passent par le même flux, sans séparation structurelle.
- **Vecteur "empoisonnement de mémoire"** : une instruction malveillante
  injectée dans la mémoire long terme d'un agent reste dormante ("sleeper
  agent") jusqu'à un déclencheur qui la fait influencer une décision/action
  réelle bien plus tard — distinct de l'injection directe déjà couverte par
  le dôme `<donnees_non_fiables>` (mandat #192).

## Vérification contre le code réel d'ARIA (pas supposé, grep direct)

Question posée : ARIA a un vrai système de mémoire long terme
(`aria-brain`, `knowledge/*.yaml`, `truth_ledger`, table `knowledge` via
`knowledge/cognitive.py::add_knowledge`/`get_approved`) — une connaissance
empoisonnée qui y entrerait pourrait-elle un jour influencer une VRAIE
décision de trading (momentum/VC/scalping) ?

Recherché tous les consommateurs réels de `knowledge.cognitive.get_approved`
dans `aria_core/` : **`grounding.py`, `curiosity.py`,
`tweet_compose_workflow.py`, `skills/builder_skill.py`,
`skills/ingest_repo_skill.py`, `skills/tavily_learning.py`** — zéro
recoupement avec `momentum_entry.py`, `vc_analysis.py`, `vc_judge.py`,
`paper_trader.py`, `risk_guard.py`, `agent_wallet_pilot.py`. Le pipeline de
décision financière ne lit JAMAIS ce magasin de connaissances.

**Conclusion : le vecteur "empoisonnement de mémoire → trade non
autorisé" est réel en théorie pour tout agent IA à mémoire, mais
n'est PAS atteignable dans l'architecture réelle d'ARIA aujourd'hui** — la
mémoire cognitive (comms/marketing/apprentissage) et le pipeline de trading
sont architecturalement cloisonnés, sans point de jonction. Aucune action
corrective nécessaire ; le cloisonnement existant EST déjà la défense,
pas un oubli à combler. À revérifier si un futur chantier fait un jour
lire une connaissance mémorisée par un chemin de décision financière — ce
jour-là, le dôme anti-injection déjà standard (mandat #192) devrait
s'appliquer à cette lecture aussi, pas seulement aux données web/on-chain
brutes.

## Branches ouvertes (banquées, pas creusées maintenant)

- Le taux 79%/41-68% de réussite d'injection mesuré sur d'AUTRES agents ne
  dit rien de la résistance propre du dôme d'ARIA (jamais mesurée
  empiriquement contre un corpus d'attaques standardisé) — un test
  adversarial dédié (rejouer des payloads connus contre
  `sanitize_untrusted_text`/les system prompts des gates) serait la seule
  vraie preuve, pas une extrapolation depuis les chiffres d'autrui.
- Le patron "Query Monitor / cross-turn drift" de FinHarness (déjà comparé
  10/08, `docs/HANDOFF_PIPELINE_MOMENTUM.md`) redevient pertinent SI ARIA
  construit un jour un agent multi-tour à appel d'outils réel — lien direct
  avec cette même veille, pas une redite.
