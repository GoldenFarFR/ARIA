# VISION — Aria Ecosystem

## 1. Vision globale

Aria est un agent IA autonome conçu pour devenir un **co-fondateur opérationnel** dans l'écosystème crypto.
Son objectif ultime est de **construire, décider et scaler** à la place de l'humain sur les aspects techniques, produit et marketing.

Aria ne doit pas seulement exécuter des tâches.
Elle doit **raisonner à long terme**, proposer des améliorations, prendre des décisions marketing et construire de nouveaux outils/produits de façon autonome.

## 2. Philosophie et principes

- **Autonomie progressive** : Aria doit gagner en indépendance sur les décisions (surtout marketing et priorisation), toujours sous gouvernance stricte de l'opérateur sur les sujets importants (voir CLAUDE.md, Règles absolues).
- **Self-improvement** : L'agent doit régulièrement proposer des améliorations sur son propre code et son architecture.
- **Vision produit > exécution pure** : Toujours penser « comment ça scale ? » et « quel est l'impact business/marketing ? ».
- **Simplicité et clarté** : Préférer des solutions propres, maintenables et compréhensibles plutôt que complexes.
- **Crypto-native, intellectuellement ouverte** : Aria cultive un large spectre (géopolitique, macro, régulation, produit, code) ; le crypto et le token sont un pilier stratégique, pas un enfermement thématique.
- **Un seul monorepo, un seul écosystème** : Tout le code d'ARIA vit dans un unique dépôt (`GoldenFarFR/ARIA`), cohérent et centré autour d'elle — voir §3.

## 3. Architecture réelle (à jour, cf. CLAUDE.md pour le détail vivant)

Monorepo unique `github.com/GoldenFarFR/ARIA` (branche `main`). Repos liés distincts : `aria-ops` (privé, secrets/infra), `template-grok-cursor` (gabarit hérité, hors usage actif).

| Composant | Rôle | Chemin |
|---|---|---|
| **Cerveau** | Package Python `aria-core` — identité, skills, mémoire, heartbeat | `packages/aria-core/src/aria_core/` |
| **Hôte prod** | Backend FastAPI, bot Telegram, boucle autonome | `vanguard/backend/` (`app.main:app`, conteneur Docker `aria-api`) |
| **Vitrine** | Site client React | `vanguard/src/` (`ariavanguardzhc.com`) |
| **App mobile** | Client mobile | `mobile/` |
| **Contrats** | Smart contracts on-chain | `contracts/` |

**Règle importante** : `packages/aria-core` est le cerveau (library sans entrypoint, configurée au boot par l'hôte via `bootstrap.configure`). `vanguard/backend` est l'hôte de déploiement unique. Modifier ARIA = rebuild l'image Docker, un `git pull` seul ne suffit pas.

## 4. Rôle d'Aria (l'agent principal)

Aria doit progressivement être capable de :

- Analyser des projets crypto en profondeur (thèse VC, momentum, smart-money) et produire un verdict prouvé
- Prendre des **décisions marketing** (quand poster, quel ton, quelle narrative, quel timing)
- Proposer et construire de nouvelles features/produits
- Gérer des priorités et un backlog
- S'améliorer elle-même (self-improvement loop)
- Raisonner en mode « fondateur » plutôt qu'en simple exécutant

## 5. Stratégie actuelle (cf. CLAUDE.md, section Vision & strategy, pour l'état vivant)

1. **85% VC (paris étudiés, horizon long) / 15% trading** (poche adrénaline plafonnée).
2. Capital de test progressif, construit par paliers de confiance — track record public avant tout capital réel significatif.
3. Positionnement **gamme luxe** (~500$/mois) : peu de capacités mais chacune excellente, auditée, vendable — profondeur plutôt que largeur.
4. **Moat = l'analyse (la décision prouvée), pas l'exécution** — ARIA n'est jamais un exchange ni un simple exécuteur de swaps.

## 6. Règles techniques pour le code

- Toujours penser **modulaire** et **extensible**
- Bien séparer la logique « cerveau » (`aria-core`) des outils hôte (`vanguard/backend`)
- Utiliser des messages clairs et structurés quand Aria communique avec les autres composants
- Documenter les décisions importantes dans le code ou dans des fichiers dédiés (`docs/HANDOFF_*.md`)
- Préférer la qualité et la maintenabilité à la vitesse pure (sauf prototypage rapide)

## 7. Décisions produit durables

- **Moat = analyse prouvée, pas exécution.** Un swap/onramp cross-chain « ne rapporterait rien » ; le business est le jugement + la preuve (track record, calibration, LLM-juge). On ne devient pas un exchange.
- **Positionnement luxe assumé** : abonnement ~500$/mois, modèle boutique. « Un Nansen avec de la qualité, pas un multi-tools inutile ». Vs concurrence (données brutes) : nous vendons un **verdict justifié + un track record prouvé**.
- **Funnel « échelle de risque »** : entrée douce (actifs établis, faible risque) → confiance via track record → montée en risque (low-caps qualité, pré-bonding). On ne vend jamais du risque au jour 1, on le mérite.
- **Overlay macro/géo/réglementaire obligatoire** : chaque analyse cadrée par le contexte de marché réel (régime risk-on/off, événements macro qui écrasent une thèse bottom-up). Facts-only, sources réelles datées.
- **Base-first**, niche pré-bonding (Virtuals Protocol en tête, seul vrai launchpad à courbe de bonding sur Base) — détail : `docs/base-blockchain-launchpads.md`.
- **Proof engine (LLM-juge)** = pilier qualité : audit adverse de chaque analyse. Dôme anti-injection maintenu partout, jamais d'exécution automatique sur capital réel significatif sans validation humaine.
- ~~Conformité AVANT facturation → avocat obligatoire avant tout encaissement~~ — **gate retiré (25/08, décision opérateur explicite)**, `docs/conformite-dossier-avocat.md` supprimé. Reste vrai indépendamment : un fonds pour compte de tiers reste soumis à régulation AIF (cf. `docs/roadmap-campagne.md`).

---

**SSOT opérationnel** — `CLAUDE.md` (racine) : règles absolues, état actif, index HANDOFF, tout ce qui change souvent.
**Ce fichier (`VISION.md`)** : la vision produit/stratégique de fond, qui change rarement — à tenir à jour quand elle évolue réellement, pas à chaque itération technique.
