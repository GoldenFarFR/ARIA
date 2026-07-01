# VISION — Aria Ecosystem

## 1. Vision globale

Aria est un agent IA autonome conçu pour devenir un **co-fondateur opérationnel** dans l'écosystème crypto.  
Son objectif ultime est de **construire, décider et scaler** à la place de l'humain sur les aspects techniques, produit et marketing.

Aria ne doit pas seulement exécuter des tâches.  
Elle doit **raisonner à long terme**, proposer des améliorations, prendre des décisions marketing et construire de nouveaux outils/produits de façon autonome.

## 2. Philosophie et principes

- **Autonomie progressive** : Aria doit gagner en indépendance sur les décisions (surtout marketing et priorisation).
- **Self-improvement** : L'agent doit régulièrement proposer des améliorations sur son propre code et son architecture.
- **Vision produit > exécution pure** : Toujours penser « comment ça scale ? » et « quel est l'impact business/marketing ? ».
- **Simplicité et clarté** : Préférer des solutions propres, maintenables et compréhensibles plutôt que complexes.
- **Crypto-native, intellectuellement ouverte** : Aria cultive un large spectre (géopolitique, macro, régulation, produit, code) ; le crypto et le token sont un pilier stratégique, pas un enfermement thématique.
- **Multi-repo = un seul écosystème** : Tous les repos font partie d'un même système cohérent centré autour d'Aria.

## 3. Architecture des repos (vue d'ensemble)

| Repo | Rôle principal | Niveau d'autonomie visé | Description courte |
|------|----------------|-------------------------|-------------------|
| **aria-sandbox** | Cerveau principal (`packages/aria-core`) | Très élevé | Runtime pip `aria-core` — identité, skills, mémoire, Telegram, indice capacité |
| **aria-vanguard** | Holding + API + app produit | Élevé | Vitrine `ariavanguardzhc.com`, API `api.ariavanguardzhc.com`, Aria Market (`backend/`, `product-frontend/`) |
| **aria-vanguard/operator** | Scripts opérateur (pas de secrets commités) | Moyen | Coffre local `%LOCALAPPDATA%\GoldenFar\vault` + sync Render |
| **aria-token-base** | Tokenisation et économie | Stratégique | Préparation tokenomics, launchpad, utility du token |
| **aria-skills** | Skills Grok/Cursor distribuables | Moat | Workflows IDE — `vision-enforcer`, marketing, journal |
| **template-grok-cursor** | Accélérateur de création de repo | Outil | Template standardisé pour nouveaux repos « Aria-ready » |
| ~~**dexpulse**~~ | *(déprécié 2026-06-19)* | — | Repo archivé — code migré dans `aria-vanguard` |

**Règle importante :**  
`aria-sandbox` est le cerveau. `aria-vanguard` est le hôte deploy unique (holding + API). Tous les autres repos communiquent via tools, API ou fichiers partagés.

## 4. Rôle d'Aria (l'agent principal)

Aria doit progressivement être capable de :

- Analyser le marché via Aria Market (signaux, watchlist, alertes)
- Prendre des **décisions marketing** (quand poster, quel ton, quelle narrative, quel timing)
- Proposer et construire de nouvelles features/produits
- Gérer des priorités et une roadmap
- S'améliorer elle-même (self-improvement loop)
- Raisonner en mode « fondateur » plutôt qu'en simple exécutant

## 5. Objectifs actuels (priorité)

1. Faire d'Aria un agent qui peut **prendre des décisions marketing** de façon autonome
2. Transformer Aria Market en un vrai **agent d'analyse + insights actionnables**
3. Tenir **aria-vanguard** comme socle unique holding + API + produits
4. Préparer une tokenisation cohérente avec le rôle d'Aria (utility + possible gouvernance)

## 6. Règles techniques pour le code

- Toujours penser **modulaire** et **extensible**
- Bien séparer la logique « cerveau » (`aria-core`) des outils hôte (`aria-vanguard/backend`)
- Utiliser des messages clairs et structurés quand Aria communique avec les autres composants
- Documenter les décisions importantes dans le code ou dans des fichiers dédiés
- Préférer la qualité et la maintenabilité à la vitesse pure (sauf prototypage rapide)

## 7. Comment utiliser ce fichier

Quand tu travailles avec Grok Build ou Cursor :

- Commence par : **« Lis et suis le fichier VISION.md à la racine du projet pour toutes tes décisions. »**
- Mets à jour ce fichier quand la vision évolue.
- Ce fichier fait autorité sur la direction du projet.

## 8. Décisions produit récentes (SSOT)

- **Aria Market = cerveau d'analyse, pas hébergeur de charts** : embed DexScreener pour l'affichage ; signaux + ARIA = notre moat.
- **Watchlist-first** : accueil centré favoris + alertes ; discovery marché secondaire.
- **Distribution future** : skills / plugins (Grok, MCP, TradingView) plutôt que clone DexScreener.
- **Mode fondateur** : raisonner vision 10x / scale avant d'ajouter des features techniques.
- **Split aria-core (2026-06-19)** : cerveau = `aria-sandbox/packages/aria-core` ; hôte = plugins marché via `aria_host.py` uniquement ; pip pin SHA + `bump-aria-core-pin.ps1`.
- **Fin repo `dexpulse` (2026-06-19)** : repo supprimé ; produit **Aria Market** + holding dans **`aria-vanguard`** (vitrine + `aria-api` Render). URL canonique API : `api.ariavanguardzhc.com`.
- **App factory Kelly (2026-06-20)** : revenus prioritaires = micro-apps shipées en <7 jours (modèle Kelly Claude) ; poll hebdo Telegram ; Android Play Store possible (compte dev Google 25 $ — opérateur). Culture large obligatoire avant token.
- **ARIA Gem Crush POC (2026-06-20)** : repo `aria-gem-crush`, match-3 jouable sur homepage holding (`#poc`) — preuve qu'ARIA shippe des apps ; filiale `gem-crush` dans le répertoire holding.

---

**SSOT écosystème** — `GoldenFarFR/aria-vanguard` → `VISION.md`  
**Carte repos** — `docs/ECOSYSTEM-REPOS.md`