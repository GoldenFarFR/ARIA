[VPS Research]

# Radar — détection Sybil / clustering d'entité au-delà de la convergence pairwise

## Contexte

Suite directe du marathon wallet-scoring de ce soir : `smart_money.py`
documente explicitement (commentaires autour de la L.434-451 et L.700-718)
que le trou le plus important encore ouvert est l'absence de
**clustering d'entité à l'échelle d'un graphe complet** — le module ne
fait qu'une **convergence pairwise** entre 2-3 wallets soumis ensemble
(`_pairwise_convergence`, source de financement partagée), ce qui laisse
un Sybil bien orchestré (cluster de wallets répartissant ses outliers)
indétectable, et les services commerciaux déjà notés (Nansen/Arkham/
Chainalysis/TRM) sont cités dans le code comme faisant "la même famille"
de clustering, mais à l'échelle industrielle. Cette recherche répond à la
question : **existe-t-il un équivalent gratuit/open-source qu'ARIA
pourrait implémenter elle-même**, plutôt que de dépendre d'un service
payant (Arkham : 149-999$/mois, déjà diligencié).

**Verdict en une phrase** : **oui**, une littérature académique et des
outils open-source réels existent, avec un chemin d'implémentation
concret et gratuit (bibliothèques Python standards + heuristiques
publiées) — pas besoin d'un service commercial pour un premier
niveau de clustering d'entité au-delà du pairwise.

---

## 1. Repère académique fondateur — "Address Clustering Heuristics for Ethereum" (Victor, FC 2020)

**La référence la plus directement exploitable trouvée ce soir.** Article
académique publié à Financial Cryptography and Data Security 2020 (FC20,
conférence évaluée par les pairs, sérieuse dans le domaine
sécurité/finance), qui répond exactement à la question : contrairement
à Bitcoin (modèle UTXO, où l'heuristique "multi-input" classique
fonctionne), **Ethereum est un modèle de compte** — il n'existe pas
d'équivalent direct de l'heuristique multi-input, donc l'auteur propose
trois heuristiques spécifiquement conçues pour ce modèle :

1. **Heuristique d'adresse de dépôt** ("deposit address heuristic") — la
   plus efficace des trois selon l'article : repère les adresses de dépôt
   temporaires (utilisées par les exchanges/services) qui redirigent
   systématiquement leurs fonds vers une même adresse de collecte —
   **c'est cette heuristique seule qui permet de regrouper 17,9% de
   toutes les adresses EOA actives**, révélant plus de **340 000
   entités contrôlant plusieurs adresses**. Chiffre publié, vérifiable,
   pas une estimation vague.
2. **Heuristique de participation multiple à un airdrop** — plusieurs
   adresses réclamant le même airdrop selon un pattern coordonné.
3. **Heuristique d'autorisation de token** (token approval) — des
   adresses distinctes s'autorisant mutuellement des dépenses de token,
   signe de contrôle commun.

**Directement pertinent pour ARIA** : ces heuristiques sont publiées,
gratuites, et implémentables directement en Python à partir des données
déjà utilisées par `smart_money.py` (transferts ERC-20 via Blockscout) —
pas besoin d'un service tiers pour un premier niveau de clustering par
financement partagé **étendu à un graphe complet** plutôt qu'à une simple
paire.

## 2. Outil open-source runnable — TrustaLabs/Airdrop-Sybil-Identification

**Vérifié en détail par appel direct à l'API GitHub** (pas une simple
lecture de README) :
```
curl https://api.github.com/repos/TrustaLabs/Airdrop-Sybil-Identification
→ language: Python, license: GPL-3.0, stars: 57, dernière mise à jour :
  2026-06-02 (actif, pas abandonné)
```
Structure du dépôt confirmée réelle (pas juste de la documentation) :
`src/scripts/` contient trois fichiers Python **réels et exécutables** —
`network_community.py`, `kdtree_dist_cluster.py`,
`components_scripts.py`.

**Méthodologie en deux phases, publiée par Trusta Labs (Medium +
README)** :
- **Phase 1 — détection de communauté sur un graphe de transfert d'actifs
  (ATG)** : construit deux graphes (transferts génériques + réseau de
  "premier apport de gas", un signal fort — qui a payé le gas de départ
  d'un nouveau wallet est presque toujours la même entité qui le
  contrôle). Retire d'abord les adresses d'entités connues (ponts,
  exchanges, contrats). Applique **Louvain** (détection de communauté
  standard, disponible nativement dans `networkx`, licence BSD, 100%
  gratuite) et **K-Core** pour isoler les groupes densément connectés.
  Identifie des patterns de forme (étoile divergente/convergente,
  arborescent, en chaîne) — une classification utile en soi pour
  documenter *comment* un cluster Sybil est structuré.
- **Phase 2 — raffinement K-means** : calcule un centroïde
  multi-dimensionnel du cluster (moyenne pour variables continues, mode
  pour catégorielles), exclut itérativement les adresses trop éloignées
  du centroïde jusqu'à convergence — réduit les faux positifs de la
  phase 1.

**Réserve à documenter avant tout usage direct du code** : licence
**GPL-3.0**, copyleft fort — si ARIA envisageait de réutiliser ce code
littéralement (pas juste s'en inspirer pour ré-implémenter l'algorithme
avec des bibliothèques permissives comme `networkx`/`scikit-learn`,
toutes deux BSD), cela imposerait des obligations de licence à examiner
avant intégration dans un dépôt privé — point de vigilance juridique,
pas un blocage technique.

## 3. Méthodologie prouvée à l'échelle réelle — Arbitrum Foundation (documentation seule, pas de code)

**Vérifié par API GitHub** : `ArbitrumFoundation/sybil-detection` — 271
étoiles (nettement plus de visibilité que TrustaLabs), mais **confirmé
être uniquement de la documentation** (README + images, aucun fichier de
code, 8 commits au total, pas de licence déclarée). **Important à ne pas
confondre avec un outil clé-en-main** — c'est la méthodologie qui compte
ici, appliquée en conditions réelles sur l'un des plus gros airdrops de
l'histoire crypto (Arbitrum, avril 2023), donc un signal de sérieux fort
malgré l'absence de code.

**Features/heuristiques confirmées** :
- Chaque transaction avec `msg.value` = une arête du graphe ; transaction
  de "funding"/"sweep" (financement initial ou rapatriement final vers
  une adresse commune) = arête également — **même famille de signal que
  le "premier apport de gas" de TrustaLabs**, convergence indépendante
  de deux méthodologies vers le même heuristique fort.
- Adresses financées depuis une même source.
- Similarité d'activité entre adresses.
- Partition du graphe en sous-graphes fortement/faiblement connexes.
- **Dépend de 11 sources de données externes** pour la version complète
  du pipeline (dont plusieurs tags Nansen payants) — mais **le principe
  du graphe funder/sweep + composantes connexes ne dépend, lui, d'aucune
  source payante** : implémentable avec les seules données on-chain déjà
  collectées par ARIA.

## 4. Piste plus avancée, non gratuite en pratique — papier ML 2025

Un article académique récent (mai 2025, arXiv 2505.09313) propose une
approche par **propagation et fusion de caractéristiques sur sous-graphe**
(features temporelles : premier transfert, timing d'acquisition du gas,
participation à un airdrop, dernière transaction + montants + structure
réseau), entraînée via **LightGBM** sur un graphe de transaction à deux
couches. Testé sur 193 701 adresses (23 240 Sybils confirmés), revendique
des scores >0,9 en précision/rappel/F1. **Code non confirmé comme
open-sourcé** (non mentionné dans le résumé). **Verdict : plus
sophistiqué que Louvain/K-means, mais nécessiterait un travail de
ré-implémentation ML complet (entraînement supervisé sur des Sybils déjà
labellisés) — hors de portée d'un premier chantier, cohérent avec ce que
`smart_money.py` note déjà lui-même (L.450-451 : "hors de portée").**

## 5. Plateforme open-source généraliste — GraphSense (contexte, pas une solution clé-en-main pour ce cas précis)

**GraphSense** (`graphsense.org`, MIT, projet AIT Autriche/Iknaio) est une
plateforme d'analytique cryptoasset complète et open-source, avec un
concept de "TagPacks" pour l'attribution collaborative d'adresses à des
entités. **Réserve importante trouvée en creusant** : son heuristique de
clustering historique ("co-spend"/multi-input) est nativement conçue pour
le modèle UTXO (Bitcoin) — **elle ne s'applique pas telle quelle à
Ethereum**. La recherche confirme cependant que les heuristiques
spécifiques au modèle de compte (dépôt/airdrop/autorisation, §1
ci-dessus) sont le sujet d'une littérature académique séparée, que
GraphSense peut en principe intégrer via ses adaptateurs Ethereum — mais
**aucune confirmation trouvée ce soir que GraphSense implémente déjà
concrètement les heuristiques du §1 pour Ethereum** — à vérifier avant de
compter dessus comme solution prête à l'emploi. **Verdict : plateforme
généraliste crédible pour du contexte/forensique large, mais pas une
réponse directe "clé en main" à ce besoin précis de clustering Sybil
account-model.**

## 6. Recoupement avec une piste déjà banquée — Webacy KYW

Rappel utile (déjà noté dans `2026-07-15-radar-goplus-clanker-webacy.md`) :
le produit **Webacy KYW** ("Know Your Wallet") revendique explicitement
une détection **spam/sybil** parmi ses signaux de risque d'adresse — un
service payant qui recoupe partiellement ce besoin, mais dont le
mécanisme interne exact n'est pas documenté publiquement (boîte noire
commerciale), contrairement aux heuristiques académiques ci-dessus qui
sont, elles, entièrement publiées et vérifiables.

---

## Synthèse — chemin d'implémentation gratuit concret pour ARIA

| Brique | Coût | Statut | Ce qu'elle apporte |
|---|---|---|---|
| Heuristique "financement partagé" étendue en graphe complet (funder/sweep) | Gratuit | Publiée (Victor FC20 + Arbitrum) | Extension directe et naturelle du `_pairwise_convergence` existant vers N wallets |
| `networkx` (Louvain, composantes connexes, K-Core) | Gratuit, BSD | Bibliothèque Python standard | Implémente la Phase 1 de TrustaLabs sans dépendre de leur code GPL |
| `scikit-learn` (K-means) | Gratuit, BSD | Bibliothèque Python standard | Implémente la Phase 2 de raffinement (réduction faux positifs) |
| TrustaLabs (code de référence) | Gratuit, GPL-3.0 | Runnable, actif (mise à jour juin 2026) | Référence d'implémentation concrète à consulter, pas forcément à copier telle quelle (licence) |
| Méthodologie Arbitrum Foundation | Gratuit | Doc seule, pas de code | Validation à l'échelle réelle du même principe funder/sweep |
| Papier ML 2025 (LightGBM) | Gratuit (papier), pas de code confirmé | Recherche avancée | Hors de portée d'un premier chantier — confirmé par le code ARIA lui-même |
| Arkham Intelligence | 149-999$/mois | Déjà diligencié | Alternative payante si le fait-maison s'avère insuffisant |
| Webacy KYW | Payant, boîte noire | Déjà diligencié | Signal "spam/sybil" packagé, mécanisme non transparent |

**Recommandation concrète** : le chantier dédié déjà différé dans
`smart_money.py` (clustering d'entité au-delà du pairwise) est
réalisable **sans dépenser un centime** — construire un graphe
funder/sweep sur l'historique de transferts déjà collecté (Blockscout),
appliquer Louvain (`networkx`) pour isoler des communautés denses, puis
un raffinement K-means (`scikit-learn`) pour réduire les faux positifs —
exactement le patron en deux phases de TrustaLabs, ré-implémenté avec des
bibliothèques permissives plutôt que copié depuis leur dépôt GPL. Un
signal payant (Arkham/Webacy) resterait une option de secours si le
fait-maison montre ses limites en pratique, pas un point de départ
obligé.

## Branches ouvertes (banquées, pas creusées)

- Vérifier concrètement si un adaptateur GraphSense Ethereum existant
  implémente déjà les heuristiques dépôt/airdrop/autorisation (§5) — pas
  confirmé ce soir, pourrait éviter une ré-implémentation from scratch.
- Threshold/paramètres exacts du K-means de TrustaLabs (non publiés dans
  le README) — nécessiterait de lire le code source ligne à ligne
  (`kdtree_dist_cluster.py`) plutôt que la doc, pas fait ce soir.
- Le papier ML 2025 (LightGBM) pourrait valoir une deuxième passe si le
  clustering heuristique simple s'avère insuffisant en pratique une fois
  construit — pas prioritaire maintenant.
- Vérifier si l'historique de transferts déjà collecté par ARIA
  (Blockscout) capture bien les transactions "premier apport de gas" —
  signal jugé le plus fort par TrustaLabs ET Arbitrum indépendamment —
  ou s'il faudrait étendre la collecte actuelle pour l'exploiter
  pleinement.

## Sources

- [Address Clustering Heuristics for Ethereum — Victor, FC 2020 (PDF)](https://www.ifca.ai/fc20/preproceedings/31.pdf)
- [Address Clustering Heuristics for Ethereum — ResearchGate](https://www.researchgate.net/publication/341078202_Address_Clustering_Heuristics_for_Ethereum)
- [GitHub — TrustaLabs/Airdrop-Sybil-Identification](https://github.com/TrustaLabs/Airdrop-Sybil-Identification)
- [Trusta Labs — Medium: AI/ML Framework for Robust Sybil Resistance](https://medium.com/@trustalabs.ai/trustas-ai-and-machine-learning-framework-for-robust-sybil-resistance-in-airdrops-ba17059ec5b7)
- [GitHub — ArbitrumFoundation/sybil-detection](https://github.com/ArbitrumFoundation/sybil-detection)
- [Detecting Sybil Addresses in Blockchain Airdrops: A Subgraph-based Feature Propagation and Fusion Approach (arXiv 2505.09313)](https://arxiv.org/html/2505.09313v1)
- [GraphSense — Open-Source Cryptoasset Analytics](https://graphsense.org/)
- [GraphSense: A General-Purpose Cryptoasset Analytics Platform (arXiv 2102.13613)](https://ar5iv.labs.arxiv.org/html/2102.13613)
- Test réel `curl` ce soir : `api.github.com/repos/TrustaLabs/Airdrop-Sybil-Identification`
  (licence/langage/étoiles/date confirmés), listing `src/scripts/` confirmé
  réel ; `api.github.com/repos/ArbitrumFoundation/sybil-detection`
  (confirmé documentation seule, pas de code)
- Code local vérifié (grep-avant-proposer) :
  `packages/aria-core/src/aria_core/services/smart_money.py`
  (commentaires L.434-451, L.700-718 sur la limite pairwise déjà
  documentée par l'équipe)
- Contexte session : `docs/aria-learning-inbox/2026-07-15-radar-goplus-clanker-webacy.md`
  (Webacy KYW, recoupement §6), `2026-07-15-radar-webacy-approfondi-arkham-entity-labels.md`
  (Arkham, alternative payante déjà diligenciée)

## Frontières confirmées respectées

Aucun compte créé, aucune clé API activée. Aucun code ARIA modifié — la
recherche s'appuie sur une lecture (grep) des commentaires existants dans
`smart_money.py` pour cadrer précisément le besoin, sans toucher au
fichier. Recherche externe + appels `curl` en lecture seule sur l'API
publique GitHub. Aucune approche de `wallet_guard`/`permission_mode`/
`config.toml`/auto-modification/capital réel.

## Mise à jour 18/07 (promotion veille Research — session commandement)

Nouvelle piste remontée par la veille continue : **Sybil-Defender**, un
service tournant en production sur le réseau **Forta** depuis novembre
2023, labellise déjà des clusters Sybil en temps réel sur 7 chaînes EVM
(Ethereum, Arbitrum, Optimism, Polygon, BSC, Avalanche, Fantom) via
détection de communauté (même famille Louvain/K-Core que TrustaLabs §2
ci-dessus) sur un graphe de transferts + "premier apport de gas" — même
heuristique forte déjà identifiée au §1/§3. **Vérifié avant de le prendre
pour argent comptant** (WebSearch, 18/07) : deux incohérences trouvées,
pas juste une confirmation propre du journal de veille —
1. **Statut gratuit à reconfirmer** : le dépôt GitHub `forkoooor/
   Sybil-Defender` (celui cité par le journal, licence ouverte) semble
   distinct du produit officiel documenté sur le blog Forta lui-même
   ("Sybil Defender: Tackling Identity Challenges via Forta's Latest
   **Premium API Feed**", développé par frwd labs) — même nom, statut
   commercial pas clair (dépôt communautaire librement réutilisable vs.
   produit payant packagé). À vérifier lequel des deux (ou les deux) est
   réellement utilisable gratuitement avant toute intégration.
2. **Base non confirmé** : aucune source trouvée ce soir ne liste Base
   parmi les 7 chaînes couvertes — à revérifier explicitement (documentation
   Forta à jour, ou test direct) avant de compter dessus pour ARIA, dont
   toute la thèse de sourcing est centrée sur Base.
Verdict : reste une piste réelle et sérieuse pour le chantier Sybil
(toujours la limite structurelle #1 de `smart_money.py`, jamais résolue),
mais moins immédiatement "clé en main gratuite" que le titre du journal de
veille le laissait entendre — les deux inconnues ci-dessus doivent être
levées avant toute décision d'intégration. Ne change rien à la
recommandation déjà écrite plus haut (chemin fait-maison Louvain/K-means
sur données Blockscout/Dune déjà collectées reste le point de départ le
plus sûr, ce service reste une option de secours/complément à évaluer).
