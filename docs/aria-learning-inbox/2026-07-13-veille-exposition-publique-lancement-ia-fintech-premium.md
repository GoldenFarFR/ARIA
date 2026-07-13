[VPS Research]

# Exposition publique / lancement — précédents IA-fintech premium comparables

## Contexte et cadrage (contraintes déjà actées, non remises en question)

Veille préparatoire pour #13 (go-to-market, décision toujours en attente
côté opérateur). Décision opérateur explicite du 13/07 : pas de décision
à prendre maintenant, mais anticiper la question "comment exposer ARIA
publiquement le jour où elle sera prête" — après track-record complet,
cf. `docs/protocole-argent-reel.md` (8 cases à cocher, ≥80 verdicts
résolus sur ≥6 mois, calibration prouvée, benchmark battu, robustesse
anti-chance, avant tout argent réel). Contraintes déjà tranchées et
respectées dans cette veille :
- Gamme luxe (~500$/mois), preuve avant promesse — donc "exposer" ne veut
  pas dire "vendre" tant que le protocole `docs/protocole-argent-reel.md`
  n'est pas complété.
- Zéro trace IA sur les surfaces client (pas d'em-dash/emoji, voix
  humaine) — aucune stratégie de communication citée ici ne suppose le
  contraire.
- Positionnement déjà retenu : agent économique souverain, un seul
  principal (cf. `docs/aria-learning-inbox/2026-07-13-veille-gouvernance-auto-modification-confiance-graduee.md`
  et la note positionnement du même jour) — non remis en question.

Aucune recommandation tranchée ci-dessous, aucun plan de lancement défini
— uniquement des précédents sourcés et les enseignements transférables.

---

## 1. Formats d'exposition de la capacité réelle sans sur-promettre

**Track record vérifiable en direct plutôt que discours marketing** :
le précédent le plus directement transposable à ARIA est **Numerai**
(hedge fund crowdsourcé, cofondé par Richard Craib) — son modèle repose
sur un "meta-model" agrégé à partir de plus de 1 200 modèles stakés
chaque semaine par plus de 30 000 participants, avec une performance
publique annoncée (ex. "29% de surperformance vs hedge funds
market-neutral") mais une divulgation détaillée volontairement restreinte
au public — **preuve de résultat agrégé publique, détail du mécanisme
propriétaire non exposé**. Numerai a levé une validation institutionnelle
majeure (500M$ engagés par JPMorgan Asset Management, doublant son AUM de
450M$ à ~1Md$) **après** avoir démontré une alpha persistante mesurée sur
plusieurs années, pas avant. C'est le schéma le plus proche du protocole
ARIA (`protocole-argent-reel.md`) : preuve d'abord, échelle ensuite.

**Whitepaper/documentation technique comme format de crédibilité
initiale** : plusieurs lancements agentiques premium 2026 (Vestmark
Pulse, WealthAi Client File, Flanks) ont utilisé la crédibilité d'un
historique d'entreprise déjà établi comme caution — Vestmark a mis en
avant "plus de deux décennies" et "plus de 2 000 milliards de dollars"
d'actifs déjà gérés par sa plateforme sous-jacente avant de lancer sa
couche IA, plutôt que de faire reposer la crédibilité sur l'IA seule.
**Transposition à ARIA, avec réserve honnête** : ARIA n'a pas cet
historique d'entreprise — c'est justement ce que le protocole argent réel
doit construire en interne avant toute exposition, pas un raccourci
disponible ici.

**Sandbox/mode démonstration à données réelles, sans risque financier
réel** : AI Brokers (courtier conversationnel) a lancé sa version Alpha
"entièrement en mode sandbox, avec des données réelles dans un
environnement fermé" — permettant aux testeurs d'explorer les
fonctionnalités et de donner du feedback sans risque financier. Format
directement compatible avec le statut actuel d'ARIA (portefeuille suivi
en paper, valorisé aux vrais prix on-chain).

---

## 2. Séquencement — cercle restreint d'abord, ou public large d'abord ?

**Consensus net dans les précédents trouvés : cercle restreint d'abord,
systématiquement.** Aucun contre-exemple de lancement public large en
premier n'a été trouvé dans ce secteur pour un produit premium/à enjeu
financier :

- **Waton Financial (MoTA)** : dévoilé d'abord en "closed beta" (mai
  2026), puis alpha, avec un passage en bêta publique explicitement
  planifié seulement pour le T3 2026 — trois paliers séquentiels avant
  le grand public.
- **Robinhood (trading agentique)** : lancé en bêta, ciblé
  délibérément sur "un sous-ensemble d'utilisateurs particulièrement
  technophiles" en premier, la société voulant explicitement "encourager
  les early adopters à apporter leurs propres outils et apprendre de
  cette audience" avant l'ouverture large — un choix de séquencement
  assumé, pas un hasard de calendrier.
- **FUTR Agent App** : progression documentée de "bêta fermée" vers des
  "canaux de distribution ciblés" avant un déploiement commercial plus
  large au T2 2026.
- **Numerai** : des années de compétition/validation communautaire
  fermée (tournois, staking) avant la validation institutionnelle
  publique majeure (JPMorgan) — le "cercle restreint" y a duré des
  années, pas des semaines.

**Aucun précédent trouvé de séquencement inverse** (exposition publique
large d'abord, cercle restreint ensuite) pour ce type de produit — cela
ne veut pas dire que ça n'existe nulle part, seulement qu'aucune source
trouvée dans cette veille ne documente ce sens inverse comme un choix
délibéré et réussi.

---

## 3. Erreurs de lancement documentées — sur-promesse et décrédibilisation

**Précédent le plus directement pertinent et le plus sévère trouvé : SEC
contre Titan Global Capital Management (robo-advisor, 2023)** — Titan a
publié sur son site des projections de performance "hypothétiques"
extrapolées à partir de ses trois premières semaines d'activité pour
en déduire un rendement annualisé affiché jusqu'à **2 700%** de
performance. Sanction : 850 000$ d'amende civile + 192 454$ de
restitution + blâme, pour violation de la règle marketing de la SEC sur
les performances hypothétiques, plus des défaillances de conformité
connexes (divulgations contradictoires sur la garde des actifs crypto).
**Enseignement directement transposable à ARIA, qui recoupe exactement
la logique déjà actée dans `protocole-argent-reel.md`** : une performance
mesurée sur une fenêtre trop courte, extrapolée comme si elle allait se
maintenir, est le type précis d'erreur que le barème des "8 cases"
(≥80 verdicts, ≥6 mois, robustesse anti-chance en retirant les 2
meilleurs coups) est conçu pour empêcher structurellement — ce précédent
confirme, avec une sanction réglementaire réelle à l'appui, que la
discipline déjà choisie par l'opérateur est la bonne, pas une prudence
excessive.

**Motif structurel du côté produit (pas seulement réglementaire)** :
plusieurs analyses 2026 sur les échecs de lancement IA-fintech
convergent sur un même diagnostic — sur-promesse des capacités,
manque de tests/audit de biais, gouvernance insuffisante — comme causes
principales de décrédibilisation publique et de retour de bâton
réglementaire, plutôt que l'échec technique du modèle lui-même. Cohérent
avec le motif déjà documenté dans la veille gouvernance auto-modification
de ce même jour (les incidents graves viennent de lacunes de conception/
processus, pas d'un excès ponctuel de confiance accordée sciemment).

**Attrition communautaire documentée comme échec de canal (pas de
produit)** : plus de 68% des projets crypto dépendant principalement d'un
lancement porté par des influenceurs (KOL) ont vu leur communauté
s'effondrer de plus de 70% en 90 jours — alors que les projets ayant
investi dans des canaux propres, du SEO et du contenu éducatif ont
retenu trois fois plus d'utilisateurs actifs. Signal transférable :
un lancement porté principalement par de l'influence achetée est fragile
structurellement, indépendamment de la qualité du produit exposé.

---

## 4. Canaux à rapport effort/impact documenté (pas une liste générique)

**Presse spécialisée comme brique de crédibilité tierce, avec effet
mesuré au-delà de la simple visibilité** : la couverture presse sur des
médias spécialisés (Cointelegraph, CoinDesk cités comme exemples) "bâtit
une crédibilité tierce, alimente les résultats de recherche Google, et
entraîne les modèles d'IA à citer le projet dans leurs réponses futures"
— un effet en aval (citation par des IA génératives tierces) rarement
mentionné dans les analyses marketing plus anciennes, propre au contexte
2026.

**KOL (community leaders/influenceurs spécialisés) : ROI le plus élevé
mesuré, mais fragilité documentée en parallèle** — cité comme le canal
au ROI le plus constant (engagement moyen 5,2%, largement supérieur aux
autres catégories de contenu), **mais** c'est précisément le canal
associé au risque d'attrition de 68%+ en 90 jours cité en section 3 —
donc un canal à fort impact court terme, documenté comme fragile en
tant que fondation unique, pas comme premier choix isolé.

**Combinaison mesurée communauté + PR, avec chiffres concrets** : une
campagne combinant construction de communauté et PR a démontré +36% de
croissance de TVL et +400% d'augmentation du nombre de détenteurs de
token dans un contexte DeFi — un des rares chiffres concrets et
attribuables trouvés dans cette recherche (à prendre comme ordre de
grandeur indicatif d'un secteur comparable, pas comme une promesse de
résultat transposable telle quelle à ARIA).

**Attribution on-chain comme discipline de mesure, pas juste un canal** :
72% des projets Web3 utilisant une attribution on-chain de leurs
campagnes ont rapporté un meilleur ROI sur deux trimestres — signal que
la discipline de mesure (attribuer chaque conversion à un canal via des
données on-chain vérifiables) est elle-même un facteur de succès
documenté, indépendamment du canal choisi — cohérent avec la culture de
preuve déjà en place chez ARIA (`protocole-argent-reel.md`, ancrage
on-chain des verdicts).

---

## Synthèse (pas une recommandation tranchée — pour nourrir #13)

Aucun signal contredisant les contraintes déjà actées par l'opérateur —
au contraire, le précédent Titan (sanction réglementaire réelle pour
extrapolation d'une performance trop courte) renforce directement la
logique déjà choisie ("preuve avant promesse", 8 cases du protocole
argent réel). Trois motifs reviennent de façon cohérente à travers tous
les précédents trouvés :

1. **Cercle restreint d'abord, systématiquement** — aucun contre-exemple
   trouvé de séquencement inverse réussi dans ce secteur.
2. **La preuve publique (track record, whitepaper technique, sandbox à
   données réelles) précède toujours la promesse commerciale** — jamais
   l'inverse dans les précédents identifiés.
3. **Les canaux à fort ROI court terme (KOL) sont aussi les plus
   fragiles sans fondation de contenu/communauté propre** — un
   déséquilibre documenté chiffré (68% d'attrition vs rétention x3 avec
   canaux propres), pertinent si #13 doit un jour arbitrer entre
   plusieurs canaux.

Question ouverte, non tranchée ici, laissée au commandement pour #13 :
une fois le protocole argent réel complété, quel sera le "cercle
restreint" concret pour ARIA (investisseurs identifiés ? communauté
crypto-native déjà engagée ? presse spécialisée en avant-première sous
embargo ?) — aucun élément de cette veille ne permet de trancher, le
choix dépend de contraintes (légales, relationnelles) hors du périmètre
de cette recherche externe.

## Sources

- [SEC Charges FinTech Investment Adviser Titan for Misrepresenting Hypothetical Performance — SEC.gov](https://www.sec.gov/newsroom/press-releases/2023-153)
- [SEC hands fintech investment advisor Titan $850,000 penalty — FinTech Futures](https://www.fintechfutures.com/regulatory-actions/sec-hands-fintech-investment-advisor-titan-850-000-penalty-for-misleading-clients)
- [SEC action against Titan highlights dangers of hypothetical-performance ads — InvestmentNews](https://www.investmentnews.com/industry-news/news/titan-hit-with-1-million-penalty-in-perfect-compliance-cocktail-241263)
- [Numerai — Grokipedia](https://grokipedia.com/page/Numerai)
- [Numerai and Numeraire: How This Crowdsourced Hedge Fund is Revolutionizing AI-Driven Finance — OKX](https://www.okx.com/en-us/learn/numerai-numeraire-crowdsourced-hedge-fund)
- [In conversation with Richard Craib, Founder, Numerai — Matt Turck](https://www.mattturck.com/numerai)
- [Vestmark launches AI wealth management tool Pulse — fintech.global](https://fintech.global/2026/05/12/vestmark-launches-ai-wealth-management-tool-pulse/)
- [Flanks launches AI financial advisor for wealth managers — fintech.global](https://fintech.global/2026/03/25/flanks-launches-ai-financial-advisor-for-wealth-managers/)
- [Arca Raises $64 Million to Build AI Wealth Management Platform — PR Fintech](https://prfintech.com/arca-raises-64-million-to-build-ai-wealth-management-platform/)
- [Waton Financial Launches MoTA Alpha — Yahoo Finance](https://finance.yahoo.com/technology/ai/articles/waton-financial-launches-mota-alpha-023200467.html)
- [Robinhood launches agentic trading and agentic credit card payments — Fortune](https://fortune.com/2026/05/27/robinhood-ai-agents/)
- [Robinhood now lets your AI agents trade stocks — TechCrunch](https://techcrunch.com/2026/05/27/robinhood-now-lets-your-ai-agents-trade-stocks/)
- [AI Brokers, The World First Chat Based Stock Investment Brokers, Launches Alpha Version for Early Testers — Yahoo Finance](https://finance.yahoo.com/news/ai-brokers-world-first-chat-133600040.html)
- [Crypto Marketing Services That Drive Real Results in 2026 — Coinpedia](https://coinpedia.org/information/crypto-marketing-services-that-drive-real-results-in-2026-a-complete-breakdown/)
- [Crypto Marketing: How to Promote Your Web3 Project Successfully (2026 Guide) — ChainAware.ai](https://chainaware.ai/blog/web3-marketing-guide/)
- Note de référence interne : `docs/protocole-argent-reel.md`
  (protocole "feu vert argent réel", lu directement pour cette veille, 2026-07-13)
- Note de référence interne : `docs/aria-learning-inbox/2026-07-13-veille-gouvernance-auto-modification-confiance-graduee.md`
  (motif "défaillance de conception, pas excès de confiance" déjà documenté ce jour)

## Frontières confirmées respectées

Aucun code touché, aucune stratégie de lancement tranchée, aucun canal
recommandé de façon définitive. Recherche et références externes
uniquement — les contraintes déjà actées par l'opérateur (gamme luxe,
preuve avant promesse, zéro trace IA, positionnement souverain) n'ont
pas été remises en question, seulement utilisées comme filtre de
pertinence pour la recherche. Décision de séquencement/canaux pour #13
laissée entièrement au commandement, une fois le protocole argent réel
complété.
