[VPS Research]

# Diligence — skills.sh (registre/marketplace de "skills" pour agents IA, par Vercel)

## Contexte

Diligence standard demandée par l'opérateur, même méthode que
Clanker/GoPlus/Webacy/Arkham. Contrainte annoncée : l'accès web de
l'opérateur depuis le cloud est bloqué (403, probablement anti-bot) —
contournement confirmé ce soir via l'accès réseau direct du VPS
(`curl` brut, sans blocage rencontré).

**Verdict en une phrase** : skills.sh n'est pas une startup obscure — c'est
un produit **officiel de Vercel** (la plateforme d'hébergement/déploiement,
société connue et bien financée), un registre open-source de "skills"
(fichiers d'instructions `SKILL.md`) pour agents IA. Légitime et à forte
traction, mais **l'API de recherche programmatique est verrouillée
derrière l'infrastructure d'authentification Vercel elle-même** (pas une
simple clé API à obtenir), et le modèle "instructions tierces exécutées
par l'agent sans revue formelle" est un vrai vecteur de risque à traiter
avec prudence, pas juste une curiosité technique.

---

## 1. Qu'est-ce que c'est exactement, qui l'opère

**Produit et opérateur confirmés** : `skills.sh` est un registre + tableau
de classement ("directory and leaderboard for skill packages"), lancé
officiellement par **Vercel** le **21 janvier 2026** — annonce officielle
sur le changelog Vercel (`vercel.com/changelog/introducing-skills-the-open-
agent-skills-ecosystem`), reprise par la presse tech spécialisée (InfoQ).
Le CLI open-source associé (`npx skills`) vit sur GitHub sous
`vercel-labs/skills`, licence **MIT**.

**Légitimité de l'opérateur** : Vercel est une société d'infrastructure web
connue (plateforme d'hébergement/déploiement Next.js), **financement
cumulé >563M$** selon les sources trouvées — pas un projet anonyme ou
de complaisance, à l'opposé de plusieurs launchpads déjà diligenciés
(BullX, gg.xyz). Ce n'est pas un token, pas un produit crypto — un outil
de développement pur pour agents IA en général (Claude Code, Cursor,
Codex, etc.), sans lien avec la finance ou le trading en soi.

## 2. Ce que ça propose concrètement

**Mécanisme** : une "skill" est un dépôt GitHub public contenant un
fichier `SKILL.md` (frontmatter YAML + `name`/`description` + instructions
en langage naturel que l'agent doit suivre quand la skill est activée) —
exactement le même format que les skills déjà utilisées dans cette
session (`docs.zora.co`, deep-research, etc.). N'importe quel dépôt public
GitHub avec un `SKILL.md` à la racine est une source de skill valide.

**CLI (`npx skills`)** — commandes confirmées : `add` (installer depuis un
dépôt), `list` (lister les skills installées), `remove`, `update`, plus
`use` (générer le prompt sans installer), `find` (chercher une skill),
`init` (créer un nouveau modèle de skill). Sources acceptées : raccourci
GitHub (`owner/repo`), URL complète, GitLab, URL git SSH, chemin local.
**Supporte 70+ agents/outils** (OpenCode, Claude Code, Cursor, Cline,
Continue, GitHub Copilot, Codex, Windsurf, Devin, Goose, etc.) — installe
en créant des liens symboliques depuis une copie canonique unique vers
chaque agent, pour une seule source de vérité facilement mise à jour.

**Traction confirmée** : **91 000+ installations totales**, **87 000+
skills uniques indexées** depuis le lancement (chiffres de janvier à
avril 2026) — croissance rapide et réelle, pas un projet mort.

**Lien direct avec du contexte déjà rencontré cette session** : la commande
`npx skills add coinbase/agentic-wallet-skills` déjà notée dans une
diligence précédente (Coinbase Agentic Wallets) **utilise très
probablement ce même registre skills.sh/CLI** — confirme que l'écosystème
est déjà utilisé de facto par au moins un acteur crypto sérieux (Coinbase),
sans que ce lien ait été identifié explicitement à l'époque.

## 3. Tarification

**Le CLI et l'installation de skills sont gratuits et open-source** (MIT,
aucun paiement requis pour `npx skills add/list/find`). **L'API de
recherche programmatique** (voir §4) n'a pas de grille tarifaire publique
trouvée — son modèle d'accès n'est de toute façon pas une clé API
classique payante mais une contrainte d'infrastructure (voir ci-dessous),
donc la question "combien ça coûte" ne se pose pas de la même façon que
pour GoPlus/Webacy/Arkham.

## 4. API/accès programmatique — RÉSERVE IMPORTANTE, vérifiée par test réel

**Test réel effectué** (contournement du blocage 403 signalé par
l'opérateur, via `curl` direct depuis le VPS — aucun blocage anti-bot
rencontré ici) :
```
curl https://skills.sh/api/v1/skills?per_page=5
→ HTTP 401 {"error":"authentication_required","message":"This endpoint
  requires authentication. Pass a Vercel OIDC token
  (Authorization: Bearer <VERCEL_OIDC_TOKEN>) — voir
  https://skills.sh/docs/api#authentication."}
```

**Ce que ça signifie concrètement, vérifié par la doc officielle** :
l'API (documentée comme donnant accès à "plus de 600 000 skills à travers
l'écosystème open-source", recherche, détails par skill, audits de
sécurité) **ne s'obtient pas via une simple clé API à s'inscrire** — elle
exige un **jeton Vercel OIDC** (`@vercel/oidc`), un jeton de courte durée
**scopé à une équipe et un projet Vercel spécifiques**, avec rotation
automatique. Rate limit documenté : **600 requêtes/minute par équipe et
projet**. **Implication directe pour ARIA** : ARIA ne tourne pas sur
l'infrastructure Vercel — utiliser cette API programmatiquement
nécessiterait de déployer quelque chose sur Vercel pour obtenir ce jeton,
une contrainte d'intégration réelle et non triviale, **différente** des
autres blocages rencontrés ce soir (placeholder de clé Dune, 401 Webacy
attendant juste une inscription) : ici, ce n'est structurellement **pas
un simple compte à créer**.

**Ce qui reste accessible sans cette contrainte** : le CLI `npx skills`
lui-même (installation/recherche locale) et la navigation du site web
`skills.sh` — l'écosystème n'est donc pas fermé dans l'absolu, seule
l'API de recherche programmatique à grande échelle l'est.

## 5. Sécurité — angle mort réel identifié, à traiter avec prudence

**Confirmé par lecture du README/doc du CLI** : **aucune mention de
processus de revue de sécurité, de scan de vulnérabilité, ou de workflow
d'approbation pour les skills tierces avant installation**. Une "skill"
est en pratique un fichier d'instructions en langage naturel qu'un agent
va suivre — **un vecteur d'injection de prompt/chaîne d'approvisionnement
non négligeable** si des skills sont installées automatiquement depuis des
dépôts non vérifiés, exactement le type de risque qu'AGENTS.md/CLAUDE.md
d'ARIA cherche à éviter par la doctrine « jamais d'auto-modification, tout
changement de comportement passe par une revue humaine ». **Ce n'est pas
une raison de rejeter skills.sh en bloc** (le registre lui-même est
légitime, Vercel est une entité sérieuse), **mais toute skill individuelle
tierce installée pour ARIA devrait être lue intégralement par un humain
avant activation** — exactement la même discipline déjà appliquée aux
skills utilisées dans cette session cloud (lecture manuelle avant usage,
jamais d'installation automatique aveugle).

## 6. En quoi ça pourrait apporter un plus concret à ARIA

- **Pas une source de données on-chain** (contrairement à
  Clanker/GoPlus/Webacy/Arkham) — c'est un outil **méta**, orienté
  capacités de développement pour l'agent lui-même, pas un flux
  d'information trading.
- **Piste concrète réelle** : le répertoire skills.sh (consultable via le
  site, sans avoir besoin de l'API gated) pourrait être une source de
  découverte de skills déjà écrites par la communauté pour des tâches
  utiles à ARIA (analyse on-chain, intégrations API crypto, scoring,
  etc.) — à parcourir manuellement (navigation web, pas d'automatisation)
  plutôt qu'à intégrer en programmatique vu le blocage OIDC.
- **Risque à documenter avant tout usage** : toute skill candidate devrait
  être lue en entier par un humain avant adoption (§5) — pas d'installation
  automatique depuis ce registre pour ARIA, cohérent avec les garde-fous
  existants contre l'auto-modification.
- **Verdict global : intéressant à connaître, pas une priorité
  d'intégration** — utile surtout comme référence/inspiration
  ponctuelle (parcourir manuellement pour des idées), pas comme brique
  technique à câbler dans le code ARIA à court terme.

---

## Branches adjacentes repérées, banquées non creusées (doctrine "multiplier les branches")

- **`vercel-labs/skills`** (le CLI lui-même, GitHub) — pourrait valoir une
  lecture de code directe (pas juste la doc) si ARIA envisage un jour de
  consommer des skills programmatiquement en dehors de Vercel — non fait
  ce soir.
- **`antfu/skills-cli`** — un fork/outil alternatif du même concept
  (repéré dans les résultats de recherche), auteur `antfu` (connu dans
  l'écosystème JS/Vite) — relation exacte avec `vercel-labs/skills`
  (fork, concurrent, ou contribution amont) non vérifiée ce soir.
- **`nextlevelbuilder/skillx` (SkillX.sh)** — concurrent direct revendiqué
  avec recherche sémantique, classement, notation, CLI — non diligencé.
- **SkillsMP (`skillsmp.com`)** — un autre marketplace concurrent
  ("Agent Skills Marketplace | Claude & Codex Skills") — non diligencé.
- **SkillHub (`skill-marketplace.com`)** — encore un autre concurrent
  listé dans les résultats — non diligencé.
- **"Claude Code Templates"** — mentionné comme un troisième lieu de
  distribution de skills dans un thread X cité en source — pas le même
  type de produit qu'un registre, à clarifier si creusé un jour.
- **Section "audits de sécurité"** mentionnée dans la doc de l'API
  skills.sh elle-même ("reviewing security audits") — suggère que Vercel
  a peut-être mis en place un mécanisme de scan/audit pour certaines
  skills malgré l'absence de processus de revue formel évoquée au §5 —
  contradiction apparente non résolue ce soir, à vérifier si l'accès API
  devient un jour pertinent.

## Sources

- [Vercel Changelog — Introducing skills, the open agent skills ecosystem](https://vercel.com/changelog/introducing-skills-the-open-agent-skills-ecosystem)
- [Vercel Changelog — The skills.sh API is now available](https://vercel.com/changelog/the-skills-sh-api-is-now-available)
- [Vercel Docs — Agent Skills](https://vercel.com/docs/agent-resources/skills)
- [Vercel KB — Agent Skills: Creating, Installing, and Sharing Reusable Agent Context](https://vercel.com/kb/guide/agent-skills-creating-installing-and-sharing-reusable-agent-context)
- [GitHub — vercel-labs/skills](https://github.com/vercel-labs/skills)
- [GitHub — vercel-labs/skills — README.md](https://github.com/vercel-labs/skills/blob/main/README.md)
- [InfoQ — Vercel Introduces Skills.sh, an Open Ecosystem for Agent Commands](https://www.infoq.com/news/2026/02/vercel-agent-skills/)
- [AI @ Sulat.com — Vercel just launched skills.sh, and it already has 20K installs](https://ai.sulat.com/vercel-just-launched-skills-sh-and-it-already-has-20k-installs-c07e6da7e29e)
- [GitHub — antfu/skills-cli](https://github.com/antfu/skills-cli) (branche adjacente, non diligenciée)
- [GitHub — nextlevelbuilder/skillx (SkillX.sh)](https://github.com/nextlevelbuilder/skillx) (branche adjacente, non diligenciée)
- [SkillsMP](https://skillsmp.com/) (branche adjacente, non diligenciée)
- [SkillHub](https://www.skill-marketplace.com/) (branche adjacente, non diligenciée)
- Test réel `curl` ce soir : `skills.sh` (200, contenu SPA Next.js confirmé),
  `skills.sh/api/v1/skills?per_page=5` (401, message d'authentification
  Vercel OIDC exact reçu), `skills.sh/docs/api` (200)

## Frontières confirmées respectées

Aucun compte créé (ni Vercel, ni skills.sh). Aucune clé/jeton
d'authentification obtenu ou utilisé — le test API s'est arrêté au 401,
aucune tentative de contournement de l'authentification. Aucun code ARIA
modifié, aucune skill tierce installée. Recherche externe + un test `curl`
en lecture seule sur des endpoints publics. Aucune approche de
`wallet_guard`/`permission_mode`/`config.toml`/auto-modification/capital
réel — au contraire, ce rapport documente explicitement pourquoi
l'auto-installation de skills tierces serait en tension avec la doctrine
anti-auto-modification d'ARIA (§5).
