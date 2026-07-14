[VPS Research]

# Positionnement & go-to-market — suite de la veille #79 (2026-07-13)

Reprise de la tâche #13, mise à jour de la veille concurrentielle #79
(2026-07-12). Objectif : nouveaux entrants depuis le dernier scan, angles
de différenciation encore inexploités. Méthode : WebSearch ciblé, sourcé,
pas de workflow fan-out. Le scan #79 comparait ARIA à trois acteurs
directs (AIXBT, ai16z, Manfred/ClawBank) sur l'axe "validation humaine +
vérifiabilité on-chain". Cette passe élargit à trois catégories de
nouveaux entrants que #79 n'avait pas couvertes, et remet en question la
solidité de l'angle de différenciation retenu à la lumière de ce qui est
apparu depuis.

---

## 1. Nouveaux entrants — plateformes mainstream d'agents on-chain

**Constat majeur, à traiter en priorité pour #13** : entre le 8 juin et le
2 juillet 2026 (donc avant #79, mais non couvert par cette veille, qui
portait sur des agents-concurrents et pas sur l'infrastructure de
plateforme), une vague de lancements a changé le paysage : MetaMask a
livré un wallet self-custodial dédié aux agents IA, Coinbase a lancé une
plateforme permettant à des agents de trader et payer de façon autonome,
OKX a ouvert un marketplace où des agents IA peuvent "s'embaucher" et se
payer entre eux, BNB Chain a sorti un studio "one-prompt" avec paiements
intégrés, et Robinhood a ajouté du trading agentique. Robinhood Chain a
traité 17 millions de transactions sur près de 350 000 wallets dans sa
première semaine (depuis le 1er juillet 2026).

**Ce qui compte pour le positionnement d'ARIA** : Robinhood permet
explicitement à ses utilisateurs de connecter un agent IA tiers pour
trader "dans des limites définies par l'utilisateur" — un modèle de
gouvernance par limites préétablies, conceptuellement proche (pas
identique) de la doctrine du dôme d'ARIA. **Ce n'est plus un
différenciateur qu'ARIA seule revendique** : c'est en train de devenir un
pattern par défaut chez les plateformes grand public elles-mêmes, pas
seulement chez des agents-concurrents isolés.

### 2. Nouveaux entrants — infrastructure de gouvernance/policy engine (le plus significatif pour #78/#13)

**Microsoft Agent Governance Toolkit** (livré le 2 avril 2026, open-source,
licence MIT, `github.com/microsoft/agent-governance-toolkit`) : premier
framework à couvrir les 10 risques de l'OWASP Agentic AI Top 10 (publié
décembre 2025), moteur de policy déterministe, "fail-closed", évaluant
chaque appel d'outil en moins de 0,1ms (p99), politiques en YAML/OPA
Rego/Cedar, module de conformité mappé RGPD/EU AI Act/HIPAA/SOC2.

**Ledger — feuille de route sécurité IA 2026** : roadmap en phases —
Skills/Agent Identity/Ledger CLI (Q2), Agent Intents and Policies (Q3),
"Proof of Human" (Q4). Philosophie explicite : l'IA propose, l'humain
signe physiquement sur un appareil de confiance — "l'IA ne prend jamais
la garde ni la signature finale." Un exemple de production déjà cité
(Moonpay + signature Ledger dans un wallet d'agent IA).

**Pourquoi c'est le signal le plus important de cette passe** : le scan
#79 avait identifié "validation humaine systématique + vérifiabilité
on-chain du track-record" comme l'angle de différenciation le plus
défendable d'ARIA face à AIXBT/ai16z/Manfred. **Cet angle est en train de
devenir un standard d'infrastructure généraliste** (Microsoft, acteur du
poids le plus lourd possible, l'a open-sourcé gratuitement ; Ledger, le
fabricant de hardware wallet dominant, en fait sa feuille de route
officielle 2026) — pas quelque chose qu'ARIA invente ou revendique seule.
**Nuance honnête à apporter à #78/#13** : la validation humaine et
l'auditabilité ne sont plus un moat de conception, elles deviennent une
attente de base du marché. Le moat réel, s'il existe encore, doit se
déplacer vers *ce qu'ARIA fait de cette gouvernance* (décisions
d'investissement réelles, ancrées, pour un principal donné) plutôt que
vers *le fait d'avoir une gouvernance* en soi.

### 3. Nouveaux entrants — analytics/intelligence on-chain (contexte, pas concurrents directs)

**Arkham Intelligence** (moteur "Ultra", 300M+ labels d'adresses, 150 000+
pages d'entités) et son écosystème concurrentiel déjà établi (Nansen pour
le retail crypto-natif, Chainalysis/TRM pour la conformité réglementée,
Bubblemaps pour l'investigation visuelle virale) : à noter pour contexte,
mais ce sont des outils d'analyse de tiers, pas des agents autonomes
concurrents d'ARIA sur le positionnement — cohérent avec la conclusion de
#79 (ChainAware.ai) : pertinents comme fournisseurs de données
potentiels, pas comme concurrents de positionnement.

---

## Angles de différenciation encore inexploités (pour #13)

Après cette mise à jour, aucun des acteurs recensés (AIXBT, ai16z, Manfred,
OKX/Coinbase/Robinhood/MetaMask, Microsoft AGT, Ledger, Arkham/Nansen) ne
combine les trois traits suivants à la fois — chacun n'en a qu'un ou deux :

1. **Agent à principal unique, pas infrastructure partagée.** AIXBT vend
   un accès à un flux de signaux partagé ; ai16z gère un fonds DAO
   collectif ; OKX/Coinbase/Robinhood sont des plateformes où l'agent
   appartient à l'utilisateur mais l'infrastructure de garde/exécution
   appartient à la plateforme. Aucun ne se positionne comme "un agent
   économique dédié à une seule personne/entité, dont l'identité et le
   track-record lui appartiennent en propre" — c'est plus proche de ce
   qu'ARIA est réellement (un agent, un opérateur, un dôme). Angle non
   revendiqué ailleurs à ce jour.
2. **Gouvernance ET décision d'investissement réunies dans le même
   produit.** Microsoft AGT et Ledger fournissent l'infrastructure de
   gouvernance (policy engine, signature humaine) mais ne prennent aucune
   décision d'investissement eux-mêmes — ce sont des couches de sécurité
   génériques, agnostiques du métier. AIXBT/ai16z prennent des décisions
   mais sans la même rigueur de gouvernance formalisée qu'un policy engine
   dédié. Aucun acteur trouvé ne bundle les deux dans un seul produit
   destiné à un principal unique — c'est l'angle le plus concret et le
   plus vérifiable dans le code d'ARIA elle-même (dôme + `anchor.py` +
   décisions réelles), à condition de ne pas sur-vendre l'état
   d'activation réel (cf. réserve déjà notée dans #79 sur
   `ARIA_ONCHAIN_ANCHOR_ENABLED`).
3. **Empreinte "un seul VPS", pas de dépendance à une plateforme
   tierce.** Tous les entrants de la catégorie 1 (OKX/Coinbase/Robinhood/
   MetaMask) nécessitent que l'agent tourne à l'intérieur de leur
   infrastructure/marketplace — c'est un lock-in structurel. ARIA tourne
   sur une machine que l'opérateur contrôle entièrement (déploiement
   documenté dans `deploy.sh`, cf. veille rollback de ce même tour) — un
   argument de souveraineté/portabilité qu'aucune des plateformes
   mainstream ne peut revendiquer par construction (leur modèle
   économique dépend justement du lock-in).

**Recommandation pour #13** : la phrase de positionnement à affiner n'est
plus seulement "validation humaine + vérifiable on-chain" (en train de
devenir un standard du marché), mais plutôt quelque chose comme *"un agent
économique dédié, souverain (auto-hébergé), dont les décisions
d'investissement réelles sont à la fois gouvernées par policy et
vérifiables après coup — pas une infrastructure partagée, pas un simple
flux de signaux, pas juste une couche de sécurité générique."* Question
ouverte laissée pour #13 : est-ce que ce positionnement parle à un
segment de marché identifiable (family office crypto-natif ? opérateur
individuel high-net-worth ? développeur voulant un agent perso plutôt
qu'un abonnement à une plateforme ?) — pas tranché ici, à décider par le
commandement.

## Sources

- [Crypto AI Agents in 2026 — Coincub](https://coincub.com/blog/crypto-ai-agents/)
- [OKX wants AI agents to hire and pay each other — TechCrunch](https://techcrunch.com/2026/06/30/crypto-exchange-okx-wants-ai-agents-to-hire-and-pay-each-other/)
- [Robinhood hands AI agents your crypto trades — crypto.news](https://crypto.news/robinhood-hands-ai-agents-your-crypto-trades-in-shift/)
- [AI Agents on Blockchain — thirdweb](https://blog.thirdweb.com/ai-agents-on-blockchain-how-crypto-became-the-payment-layer-for-autonomous-ai/)
- [Introducing the Agent Governance Toolkit — Microsoft Open Source Blog](https://opensource.microsoft.com/blog/2026/04/02/introducing-the-agent-governance-toolkit-open-source-runtime-security-for-ai-agents/)
- [microsoft/agent-governance-toolkit (GitHub)](https://github.com/microsoft/agent-governance-toolkit)
- [Ledger's 2026 AI Security Roadmap](https://www.ledger.com/blog-2026-ai-security-roadmap)
- [Ledger unveils AI security roadmap — The Block](https://www.theblock.co/post/397284/crypto-ledger-ai-security-roadmap-agentic-economy-human-loop)
- [What Is Arkham Intelligence — CoinGecko](https://www.coingecko.com/learn/what-is-arkham-intelligence-crypto)
- [How Arkham Insights is redefining onchain alpha — Blockworks](https://blockworks.co/news/arkham-insights-onchain-alpha)
- Note de référence interne : `docs/aria-learning-inbox/2026-07-12-veille-concurrentielle-positioning.md`
  (#79, base de comparaison pour cette mise à jour)

## Frontières confirmées respectées

Aucun code touché. Recherche et références uniquement. Décision de
positionnement final pour #13/#78 laissée au commandement.
