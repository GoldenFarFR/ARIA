# Cartographie des agents IA on-chain concurrents et axes de différenciation ARIA (16/07)

**Contexte** : Jesse Pollak (lead Base) a annoncé cette semaine (16/07) un pivot officiel
de la stratégie Base 2026 vers trois piliers -- trading, paiements, agents IA -- après
avoir déclaré publiquement l'échec de la stratégie sociale/creator-coins précédente
("disintegrated completely"). Sources : [crypto.news](https://crypto.news/jesse-pollak-admits-base-misstep-bets-big-on-ai/),
[CryptoTimes](https://www.cryptotimes.io/2026/07/16/bases-jesse-pollak-admits-creator-coin-strategy-failed-signals-ai-pivot/),
[CryptoBriefing](https://cryptobriefing.com/base-pivots-trading-payments-ai-agents-2026/).
Ce pivot valide directement le terrain sur lequel ARIA opère déjà -- justifie la
priorité donnée à Base dans cette cartographie.

**Méthode** : recherche web ciblée (WebSearch/WebFetch, pas de workflow multi-agents --
doctrine économie de tokens CLAUDE.md), faits sourcés uniquement, aucune conclusion
sans preuve. Deux limites honnêtes assumées : (1) certaines sources (BeInCrypto)
étaient inaccessibles (403) -- reconstruit depuis les extraits de recherche disponibles
uniquement, marqué comme tel ; (2) recherche web, pas d'audit du code source des
concurrents -- toute affirmation sur leur gouvernance interne vient de leur
communication publique, jamais d'une vérification indépendante comme celle qu'ARIA
s'impose à elle-même (cf. `docs/etat-systeme-cable.md`).

## Agents cartographiés

### 1. AIXBT (Virtuals Protocol, Base)

- **Fonction réelle** : agent d'analyse/signal -- surveille +400 comptes influents sur
  X, l'on-chain et le sentiment de marché, publie des signaux/narratifs. Description
  historique = "couche analytique, pas un moteur de trading". **Évolution notée** :
  des sources plus récentes (2026) indiquent qu'AIXBT gère désormais aussi son propre
  wallet Solana et aurait traité +140 000 transactions autonomes au Q1 2026 -- sources
  contradictoires sur le degré d'autonomie exact (signal pur vs exécution mixte),
  présenté ici sans trancher.
- **Modèle de preuve** : un track record existe (416 tokens "shillés", winrate 48%,
  retour moyen 19% -- [BeInCrypto](https://beincrypto.com/ai-agent-aixbt-crypto-shilling-performance/)),
  mais c'est une **mesure a posteriori par un tiers (média)**, pas un ledger structuré
  publié nativement par l'agent (entrée/cible/invalidation/résolution automatique).
  Aucune preuve trouvée d'un mécanisme de résolution transparent et natif comparable à
  `vc_predictions`/`/feuvert` d'ARIA.
- **Gouvernance** : **incident de sécurité réel et documenté** -- 55,5 ETH (~106 000$)
  perdus le 19 mars 2025 par manipulation adversariale : des attaquants ont conditionné
  l'agent via des entrées externes répétées, traitées comme des instructions
  légitimes ("behavioral conditioning"). Causes citées explicitement par l'analyse
  post-mortem : validation d'entrée insuffisante, autorité de signature autonome sans
  approbation multi-étapes, absence de détection d'anomalie, aucun garde-fou
  comportemental. Source : [PointGuard AI](https://www.pointguardai.com/ai-security-incidents/ai-trading-bot-manipulated-into-six-figure-loss).
  **C'est exactement la classe de vulnérabilité corrigée aujourd'hui sur ARIA (mandat
  #192, `momentum_entry.py::_llm_confirm`, symbole ERC-20 non neutralisé) -- ici trouvée
  et corrigée en paper-trading AVANT tout capital réel ; chez AIXBT, découverte APRÈS
  une perte réelle.**
- **Modèle économique** : token spéculatif (`$AIXBT`, Base). Capitalisation très
  volatile et sources contradictoires selon la date -- vérifié en direct (16/07) :
  CoinGecko/CoinMarketCap s'accordent sur **~18-19M$** actuellement (990M tokens en
  circulation), très loin d'un chiffre de 506M$ trouvé dans une source secondaire de
  moindre qualité (probable erreur ou confusion avec un pic historique -- écarté, non
  retenu). Perte de 92% documentée sur une position d'un whale (déc. 2025).
- **Signaux de légitimité** : plateforme Virtuals (audits du protocole, pas de l'agent
  lui-même), mais l'incident de mars 2025 est un signal négatif concret et daté.

### 2. Luna ($LUNA, Virtuals Protocol)

- **Fonction réelle** : persona IA/streameuse (TikTok, Spotify), PAS un agent
  d'analyse/trading VC au sens d'ARIA -- catégorie différente (divertissement/social).
  A commencé à "tipper" des utilisateurs on-chain de façon autonome.
- **Gouvernance** : donnée la plus significative pour la comparaison -- "Sentient Mode
  2.0" (21 octobre 2024) lui donne le contrôle **total et autonome** de son compte X,
  explicitement présenté comme "éliminant le besoin de supervision humaine", premier
  agent IA de l'histoire à le faire selon la source. **Confirme que l'autonomie totale
  sans validation humaine est déjà une réalité commercialisée et mise en avant comme un
  argument de vente dans cet écosystème**, pas une hypothèse théorique.
- **Modèle économique** : token spéculatif, valeur portée par la popularité de la
  persona.
- Sources : [Gate Learn](https://www.gate.com/learn/articles/what-is-luna-by-virtuals-fully-sentient-blockchain-based-ai-agent/6271),
  [Medium/0xai](https://medium.com/@0xai.dev/virtuals-protocol-luna-55b661df601e).

### 3. KellyClaude ($KELLYCLAUDE, Base)

- **Fonction réelle** : persona IA/automation de posts sociaux (couche "Clawdbot") --
  **le projet lui-même se décrit explicitement comme n'étant PAS fonctionnel** : "value
  shaped by attention, participation, and liquidity rather than functional output"
  ([Medium/XT Exchange](https://medium.com/@XT_com/kelly-claude-kellyclaude-when-ai-personas-enter-the-token-economy-b0db6e153c78)).
  Aucune fonction d'analyse ou d'exécution de trading trouvée.
- **Modèle de preuve** : aucun -- absence de track record, le projet ne revendique pas
  d'expertise analytique.
- **Modèle économique** : 100% spéculatif, admis comme tel par le projet lui-même. Cas
  le plus net de "token spéculatif vs vrai produit" trouvé dans cette recherche.
- **Marché réel** : ~0,058$ au 16/07, volume 24h ~493k$ (DexScreener), listé sur
  Uniswap V4 (Base). Trading actif mais purement narratif/mémétique.

### 4. ai16z / ElizaOS (framework, multi-chaînes dont Base)

- **Fonction réelle** : PAS un agent isolé -- un framework open-source (>50% des
  nouveaux projets IA-crypto en 2026 selon les sources trouvées) permettant à des
  agents tiers de gérer des wallets, signer des transactions, trader de façon autonome.
  Le "Generative Treasury System" (nov. 2025) déploie du capital de façon **autonome,
  sans intervention humaine par transaction**.
- **Modèle de preuve / gouvernance -- signal négatif majeur et daté** : plainte en
  recours collectif fédérale déposée le 20 avril 2026 (Southern District of New York)
  contre Eliza Labs Inc., son fondateur et l'AI16Z DAO. Allégations sourcées et
  détaillées ([Cryptopolitan](https://www.cryptopolitan.com/ai16z-elizaos-creators-sued-fake-ai-hype/),
  [ClaimDepot](https://www.claimdepot.com/cases/ai16z-class-action-alleges-founders-faked-ai-agent-misled-investors-in-26b-crypto-scheme)) :
  (a) l'agent phare ("Marc AIndreessen", nom calqué sur a16z/Marc Andreessen) aurait
  été **opéré par des humains, pas autonome comme annoncé** (rapporté par Protos dès
  oct. 2024) ; (b) migration de token AI16Z→ELIZAOS multipliant l'offre par 10 (1,1Md→
  11Md), avec 40% de l'offre allouée à des insiders sans divulgation préalable selon la
  plainte ; (c) au moins 3 945 adresses auraient subi des pertes ; token en baisse de
  99,9% depuis son pic (2,6Md$ de capitalisation en janvier 2025). Ces faits sont des
  **allégations judiciaires non jugées**, présentées ici comme telles.
- **Modèle économique** : token spéculatif, capitalisation ayant atteint 2,6Md$ au pic.
- **À noter** : le framework technique (ElizaOS) reste largement adopté indépendamment
  de ce litige -- distinction à faire entre l'outil (probablement légitime, largement
  utilisé) et le projet fondateur qui l'a lancé (sous accusation de fraude).

### 5. Freysa ($FAI, Base)

- **Fonction réelle** : agent "souverain" (clé/mémoire/actions dans un TEE), mais
  conçu comme un **jeu/expérience psychologique** (convaincre l'IA de libérer une
  cagnotte), pas un agent d'analyse ou de trading de tokens. Catégorie différente
  d'ARIA -- inclus pour la comparaison de gouvernance/autonomie uniquement.
- **Gouvernance** : wallet chiffré contrôlé par l'agent seul, aucune validation humaine
  par action décrite dans les sources trouvées.
- Source : [Crypto.com University](https://crypto.com/us/university/what-is-freysa-fai).

### 6. Wayfinder ($PROMPT, Parallel Studios, multi-chaînes dont Base)

- **Fonction réelle** : protocole d'infrastructure (pas un agent isolé) permettant à
  des agents tiers d'exécuter des transactions cross-chain. Inclut un "Apollo Perps
  Agent" (juillet 2025) pour du trading à effet de levier automatisé et un "Prediction
  Agent" pour Polymarket.
- **Gouvernance** : aucune mention trouvée d'une validation humaine par transaction sur
  l'agent de perps -- absence de preuve, pas une preuve d'absence (non confirmé dans un
  sens ou l'autre par les sources disponibles publiquement).
- **Modèle économique** : token utilitaire (`$PROMPT` requis pour chaque swap/
  déploiement/trade cross-chain) -- modèle différent d'un pur token spéculatif, plus
  proche d'un "gas token" applicatif.
- Source : [ainvest](https://www.ainvest.com/news/wayfinder-ai-agents-paradigm-cross-chain-trading-token-utility-2508/).

## Constat transversal sur la gouvernance (le plus significatif de cette recherche)

Sur les 6 agents étudiés ayant une composante de gestion de capital autonome (AIXBT,
Luna, ai16z/ElizaOS, Freysa, Wayfinder), **aucun ne documente publiquement une
validation humaine obligatoire par transaction sur du capital réel** -- l'autonomie
totale est présentée comme un argument de vente (Luna : "éliminant le besoin de
supervision humaine"), un choix d'architecture par défaut (ai16z : "sans intervention
humaine par transaction"), ou n'est simplement pas abordée dans la communication
publique (Wayfinder, Freysa). La littérature de gouvernance/risque généraliste (TRM
Labs, Coinbase Institute) **recommande** des plafonds de dépense, une approbation
humaine par paliers de risque et des kill-switches -- mais aucun des agents nommés
étudiés ici ne documente publiquement avoir implémenté cette recommandation, et
l'incident AIXBT (mars 2025) est une preuve concrète que son absence a un coût réel
mesuré.

## Axes de différenciation ARIA -- évalués un par un, avec honnêteté sur la rareté

1. **Track record structuré, auto-résolu, publiquement vérifiable** (`vc_predictions`,
   `/feuvert`) -- **RARE, confirmé par cette recherche**. AIXBT a un track record, mais
   mesuré a posteriori par un média tiers, pas publié nativement par l'agent avec
   entrée/cible/invalidation et résolution automatique. Aucun autre agent étudié n'a
   d'équivalent trouvé. Limite honnête : absence de preuve ≠ preuve d'absence sur
   l'ensemble du marché -- recherche non exhaustive.

2. **Validation humaine obligatoire sur capital réel + kill-switch fonctionnel** --
   **RARE et démontré nécessaire par le constat transversal ci-dessus**. C'est l'axe le
   plus solidement établi par cette recherche : la norme observée dans l'écosystème est
   l'autonomie totale par défaut, présentée comme un avantage marketing plutôt qu'un
   risque à gérer. L'incident AIXBT donne un coût chiffré concret à l'absence de cette
   garantie.

3. **Aucun token spéculatif propre à ARIA** -- **RARE, contraste frontal avec les 6
   agents étudiés** (tous ont un token dont la valeur dépend en tout ou partie de la
   spéculation/attention -- KellyClaude l'admet explicitement, ai16z en est un exemple
   aggravé par le litige en cours). Le modèle économique d'ARIA (abonnement/service,
   pas de token à pomper) écarte structurellement l'incitation même qui est au cœur des
   allégations de fraude contre ai16z.

4. **Correction proactive de vecteurs d'attaque avant tout incident réel** -- axe
   défendable mais À FORMULER AVEC PRUDENCE. Le correctif de ce jour sur
   `momentum_entry.py` (même classe de vulnérabilité que l'incident AIXBT réel) a été
   trouvé et corrigé en paper-trading, avant tout capital réel -- mais ARIA n'a pas
   encore été testée à l'échelle et en conditions adversariales réelles (le mandat #192
   lui-même le reconnaît). C'est une différenciation de PROCESSUS documentée, pas un
   résultat prouvé au combat comme le serait un track record de résistance en
   production.

5. **Gouvernance documentée et bornée par écrit, avec exceptions nommées et scopées**
   (ex. le rehearsal Sepolia autonome, explicitement borné et distinct de tout chemin
   touchant du capital réel) -- axe le PLUS INCERTAIN des cinq, à formuler modestement :
   cette recherche n'a eu accès qu'à la communication publique des concurrents, jamais
   à leur documentation de gouvernance interne (si elle existe). Ne pas affirmer
   qu'ARIA est "plus transparente en interne" sans base de comparaison équivalente.

## Non retenu comme axe (vérifié, pas différenciant)

- **Détection honeypot/rug-pull avant entrée (type GoPlus)** : recherche confirme que
  c'est déjà une **capacité commodifiée** dans le secteur -- de nombreux outils dédiés
  existent (ChainAware, AiCryptoScan, GetBlock Rug Pull Check, etc.), certains
  utilisables par n'importe quel agent IA via MCP. Ne pas revendiquer ce point comme
  unique à ARIA.

## Branches ouvertes (non creusées, pour une prochaine recherche)

- Auditer si un agent d'analyse crypto publie réellement un ledger de prédictions
  structuré et auto-résolu comparable à `vc_predictions` -- cette recherche ne l'a pas
  trouvé, mais n'a couvert qu'une poignée d'agents notables, pas l'ensemble du marché
  (des centaines de tokens "agent IA" existent, cf. classement CoinMarketCap "AI
  Agents").
  Le classement trending Base montré par l'opérateur (PAMPU/MYRAD/BASEMATE/etc., cf.
  CLAUDE.md #194) contient probablement d'autres cas KellyClaude-like (persona
  spéculative sans fonction réelle) -- pourrait affiner le constat "token spéculatif
  vs vrai produit" avec un échantillon plus large.
- Creuser le statut légal exact du litige ai16z (issue non tranchée, à suivre --
  pourrait devenir une jurisprudence de référence sur la responsabilité des agents IA
  "faussement autonomes").
- Vérifier si AIXBT a publié un post-mortem officiel de l'incident de mars 2025 (source
  actuelle = analyse tierce PointGuard AI, jamais confirmée par Virtuals/AIXBT
  eux-mêmes dans cette recherche).
- Chercher spécifiquement des agents de la catégorie "VC-thesis/due diligence" (analyse
  fondamentale, pas juste signal de momentum) pour affiner l'axe 1 -- cette recherche
  n'a couvert que des agents généralistes/signal, pas de concurrent direct trouvé sur
  le créneau précis d'ARIA (analyse VC + preuve + validation humaine).
