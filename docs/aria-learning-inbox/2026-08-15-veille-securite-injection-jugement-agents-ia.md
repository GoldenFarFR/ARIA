# [Veille sécurité] Empoisonnement du jugement recommandé — vérifié contre l'architecture réelle d'ARIA

## Contexte et périmètre

Mandat permanent #192 (prompt injection, forces/faiblesses IA trading,
CLAUDE.md 15/07) — suite directe de
`2026-08-10-veille-securite-agents-ia-trading-empoisonnement-memoire.md`
(empoisonnement de mémoire long terme). Ici l'angle est distinct : des
publications récentes (Microsoft, Zscaler ThreatLabz) documentent des
attaques qui ne visent plus une ACTION (paiement, classification) mais
directement le JUGEMENT/LA RECOMMANDATION qu'un agent produit — exactement
la fonction de `conviction_research`/`vc_analysis` pour ARIA (produire une
thèse d'achat). Vérifié contre le code réel, pas supposé.

## Ce que la recherche externe documente (research-log.md, 14-15/08)

- **Microsoft "AI Recommendation Poisoning"** (blog sécurité, 10/02/2026) —
  des entreprises embarquent des instructions cachées dans du contenu web
  ("SEO growth hack for LLMs") qui tentent d'écrire en persistance "retiens
  [Entreprise] comme source de confiance" — 50+ prompts distincts identifiés
  chez 31 entreprises.
- **Zscaler ThreatLabz** (divulgué 08/2026, testé sur 26 LLM) — deux
  campagnes mesurées et chiffrées : (1) un faux site de doc d'API cache une
  instruction de paiement DANS le balisage structuré schema.org, 4/26 LLM
  l'ont exécutée ; (2) un site typosquatté avec des données structurées
  optimisées pour tromper la CLASSIFICATION d'un agent (pas un humain),
  2/26 LLM l'ont classé comme légitime.
- **MemMorph** (PointGuard AI, 2026) — biaise le CHOIX D'OUTIL d'un agent en
  insérant 3 entrées déguisées en "faits" dans sa mémoire long terme, 85,9%
  de succès mesuré.
- **MCP Tool Poisoning** — classe d'attaque codifiée OWASP MCP03:2025, cas
  réel à 42 900 instances exposées ("ClawHavoc") : instructions adverses
  cachées dans les DESCRIPTIONS/schémas d'un outil MCP tiers plutôt que
  dans son contenu visible.

## Vérification contre le code réel d'ARIA (grep direct, pas supposé)

**Question 1 — le jugement `conviction_research`/`vc_analysis` peut-il être
manipulé par du contenu web/structuré fetché ?** Vérifié dans
`conviction_research.py` (docstring de tête, ligne ~50) : chaque fragment
externe (site, tweets, liens déclarés GitHub/Farcaster/Telegram) passe par
`sanitize_untrusted_text` AVANT injection dans le prompt (neutralise `<`/`>`
pour empêcher de forger un faux tag de délimitation), est enveloppé dans le
tag `<donnees_non_fiables>`, avec une règle système explicite d'ignorer
toute instruction trouvée à l'intérieur. Un vrai bug de ce type avait déjà
été trouvé et corrigé lors d'une revue croisée le 19/07 (URL non sanitisée
atteignant le prompt système Telegram via la thèse persistée) — le pattern
est donc déjà éprouvé, pas juste déclaré.

**Question 2 — `website_substance.py` lit-il le contenu brut scrapé via un
appel LLM (surface d'attaque directe) ?** Vérifié : `judge_website_substance`
est un **jugement pur, sans appel réseau ni LLM** — score pondéré
déterministe (densité de mots, sections-clés détectées par mot-clé, nombre
de pages, HTTPS). Le seul texte qui atteint le prompt LLM de `vc_analysis`
est un court `signal` (ex. "weak"/"moderate"/"strong"), lui-même re-sanitisé
via `_sanitize()` avant d'être ajouté au prompt (`vc_analysis.py:476-477`).
Le contenu brut scrapé (mots-clés, texte de page) ne traverse donc jamais
directement un LLM — la surface d'attaque "recommandation empoisonnée via
contenu de page" décrite par Microsoft/Zscaler n'est pas atteignable par ce
chemin précis dans l'architecture réelle d'ARIA aujourd'hui.

**Conclusion : les vecteurs Microsoft/Zscaler sont déjà couverts** par le
dôme existant (`sanitize_untrusted_text` + tag + règle système, mandat
#192) partout où un LLM lit réellement du contenu externe, et sont hors
d'atteinte là où le jugement est purement déterministe
(`website_substance`). Aucune action corrective nécessaire — le
cloisonnement/dôme existant EST déjà la défense, cohérent avec la
conclusion de la fiche du 10/08 sur l'empoisonnement de mémoire.

## Branches ouvertes (banquées, pas creusées maintenant)

- **MCP Tool Poisoning** : ARIA n'a aujourd'hui AUCUN serveur MCP tiers
  connecté (Kraken CLI, 1inch MCP, Chainbase AgentKey, Orbs SPOT — tous
  restent des candidats de diligence, jamais connectés). Ce vecteur devient
  pertinent le jour où une première connexion MCP tierce est envisagée —
  à ce moment-là, vérifier que les schémas d'outils sont audités/épinglés à
  une version figée plutôt que rechargés dynamiquement à chaque appel.
- **MemMorph (biais du choix d'outil via mémoire)** : redevient pertinent
  seulement si `aria-brain` (`ARIA_BRAIN_ENABLED`, actuellement `false`) est
  un jour réactivé et qu'un chemin de décision commence à lire
  `knowledge_inbox`/`truth_ledger` — même conclusion que la fiche du 10/08,
  le cloisonnement actuel rend le vecteur inatteignable pour l'instant.
- **Claude Code — deux CVE jamais loguées jusqu'ici** (CVE-2025-59536,
  CVSS 8.7, exécution shell arbitraire via `.claude/settings.json`/hooks/MCP
  d'un repo tiers cloné avant confirmation de confiance, corrigée
  28/12/2025 ; CVE-2026-21852, fuite de clé API Anthropic via
  `ANTHROPIC_BASE_URL` détourné, corrigée en 2.0.65) — non vérifiable
  directement dans cette session (commande `claude --version` restreinte
  par le mode d'exécution). Reste un vrai suivi ouvert, voir backlog #304.
- **UniswapV4Router04 drainé de ~42 600 USDC (03/03/2026)** par
  contournement d'autorisation du payeur via calldata à offset non-standard
  — élargit l'audit déjà ouvert #290 (hooks Uniswap v4) au ROUTER officiel
  lui-même, pas seulement aux hooks tiers. Sans action tant qu'ARIA ne route
  pas directement via Uniswap v4 (swap actuel 100% via l'agrégateur 0x du
  SDK CDP).

## Sources

Entrées originales du journal `research-log.md`, sections 2026-08-14 et
2026-08-15 (Microsoft AI Recommendation Poisoning, Zscaler ThreatLabz,
MemMorph, MCP Tool Poisoning/ClawHavoc, CVE-2025-59536, CVE-2026-21852,
UniswapV4Router04) — à recroiser si une action concrète est un jour engagée
sur l'un de ces branches.
