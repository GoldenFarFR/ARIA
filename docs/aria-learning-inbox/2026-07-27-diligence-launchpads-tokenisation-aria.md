# Diligence launchpads pour une future tokenisation d'ARIA — 27/07/2026

> Note brute, recherche web réelle (WebSearch/WebFetch depuis la session cloud).
> Déclaratif — à vérifier/recouper avant intégration dans `knowledge/*.yaml`/
> `canonical_facts.yaml`, comme toute note de cet inbox. Contexte : l'opérateur envisage de
> **tokeniser ARIA elle-même plus tard** (pas maintenant — aucune action engagée), et cherche
> un launchpad "d'avenir" capable de propulser durablement à 10M$+ de mcap, pas un pump-and-dump.
> Déclencheur initial : l'incident de compromission du compte X de Bankr.bot (juillet 2026).

## Méthode

Diligence multi-rounds (pas un seul passage) : chaque candidat évalué sur tokenomics/anti-dump,
sécurité/légitimité, volume/traction réelle, équipe/support, et — sur demande explicite de
l'opérateur — la possibilité d'un investissement direct du launchpad dans le projet. Chaque
"bruit"/controverse trouvé a été creusé jusqu'à la source primaire avant conclusion (norme
"vérifier avant d'affirmer" du projet).

---

## Bankr — ÉCARTÉ

**Tokenomics** : construit sur le protocole Doppler (hooks Uniswap V4), frais de swap 0,7%.
100 milliards de tokens à l'émission, 85% amorce la liquidité, 15% vest au créateur sur 2 ans
(cliff 30 jours). Mécanisme correct en soi (cf. `docs/aria-learning-inbox/2026-07-11-bankr-diligence-approfondie.md`
pour le détail déjà établi le 11/07).

**Pourquoi écarté** : deux incidents de sécurité en 2 mois en 2026 —
1. Mai 2026 : hack de wallet via injection de prompt ciblant les interactions Grok↔Bankrbot,
   >440 000$ de dégâts, au moins 14 wallets compromis.
2. Juillet 2026 : prise de contrôle du compte X officiel de Bankr.bot (l'incident qui a
   déclenché cette diligence).

Le mécanisme tokenomics n'est pas en cause — c'est la confiance opérationnelle de la
plateforme (sécurité récurrente) qui pose problème. Associer le lancement d'un token ARIA à
une plateforme qui vient de se faire pirater son compte X publiquement est un risque de
perception réel, indépendant du protocole lui-même.

Sources : [Coinpaper — Bankr suspends transactions](https://coinpaper.com/17151/bankr-suspends-transactions-after-major-crypto-wallet-exploit), [The Block — Bankr resumes activity](https://www.theblock.co/post/374119/bankr-back-live-x), [KuCoin — Bankr hacked through Grok](https://www.kucoin.com/news/community/BTC/6a0e4fb68f4e600007edb4a0), [Brinztech — Bankr X account compromised](https://www.brinztech.com/breach-alerts/brinztech-alert-decentralized-finance-protocol-bankr-bot-x-account-compromised-amid-wave-of-high-profile-social-media-takeovers)

---

## Virtuals Protocol — MATURE MAIS EN DÉCLIN STRUCTUREL CONFIRMÉ

**Position historique** : le plus gros/mature de l'écosystème "AI agent tokens" — VIRTUAL
mcap ~382-485M$ selon la source, fair launch strict (1B tokens fixes par agent), **liquidité
verrouillée 10 ans**, frais 1% trading. 17 000+ agents créés, ARIA déjà dans cet écosystème
(compute.virtuals.io, provider LLM historique).

**Signal de déclin réel, vérifié en profondeur (pas une simple baisse de prix cyclique)** :
- Revenus effondrés de **95% depuis janvier 2025** (3,5M$/mois → moins de 200K$/mois en juin 2025)
- Utilisateurs actifs quotidiens : 30 000 → moins de 12 000 (vrai exode)
- Volume de trading actuel (24h, fin juillet 2026) : seulement **174 000$** — ratio volume/mcap
  ridiculement faible (~0,05%) sur un token à 382M$ de mcap
- Cause citée explicitement par la presse : "demande en baisse pour les tokens d'agents IA,
  revenus en chute, engagement utilisateur en recul"
- Cause connexe : drain de tokens — soldes d'échange +22% en 30 jours, avoirs "whale" -5%,
  avoirs investisseurs avertis -46%

**Nuance importante** : le développement technique/produit continue activement (migration
$7,2Md d'actifs de LayerZero vers Chainlink CCIP le 10/07, intégration Robinhood Chain dès le
02/07, +20% suite à cette intégration) — mais ça n'a pas empêché ni inversé l'exode
utilisateur/trading. L'équipe construit, le marché ne suit plus.

**Découverte qui nuance la conclusion — Virtuals Ventures (virtuals.vc)** : un vrai fonds
d'écosystème dédié existe, décrit comme "le premier accélérateur d'agents IA onchain",
conçu pour accélérer des projets IA **et** Robotique dès le stade zéro (pas limité à la
robotique). Virtuals s'étend activement dans la finance (agents autonomes sur actions
tokenisées/prêt/stablecoins via Robinhood Chain) — un agent de trading comme ARIA rentrerait
dans leur scope théorique. **Limite honnête** : aucun montant, critère de sélection ou
exemple de projet financé trouvé publiquement — juste un formulaire de candidature Google
Forms, impossible de vérifier la substance réelle sans candidater directement.

Sources : [crypto.news — Virtuals Protocol token drain, revenue plummets](https://crypto.news/virtuals-protocol-token-drain-revenue-plummets/), [CoinGecko — VIRTUAL price](https://www.coingecko.com/en/coins/virtual-protocol), [CoinMarketCap — Robinhood Chain integration](https://coinmarketcap.com/top-stories/6a5456001fc65c3af1aaa106/), [virtuals.vc](https://virtuals.vc/), [Base Batches — Virtuals Robotics Track](https://batches.base.org/)

---

## Clanker — CANDIDAT RECOMMANDÉ

**Tokenomics du lancement** : frais 1% sur chaque transaction Uniswap V3. Répartition selon
le canal de déploiement — **via l'interface clanker.world : créateur reçoit 100% des frais
LP initiaux** ; via le bot @clanker sur Farcaster : créateur reçoit 80%. Frais Clanker fixés
à 20% des frais LP (v4 récente : 0,2% max, payable en WETH uniquement).

**Anti-dump — le mécanisme le plus solide trouvé dans toute cette diligence** : le NFT
représentant la position Uniswap V3 est envoyé à un contrat "LP Locker" **verrouillé
jusqu'en l'an 2100** — aucune méthode de retrait codée, ni pour le créateur, ni pour
l'équipe Clanker, ni pour l'interface utilisée. Un changement récent (~13 novembre 2026,
annoncé) donne aux créateurs un **contrôle permanent sur les frais**.

**Traction/croissance actuelle réelle** : frais quotidiens moyens en hausse, 65 000$ (juin
2026) → 89 000$ (juillet 2026), **+37% en un mois**. 4ème version du protocole déployée
mi-juin 2026, déjà 7 819 tokens déployés avec cette seule version. Pic historique en février
2026 : 8M$ de frais hebdomadaires, volumes quotidiens jusqu'à 300M$ sur les tokens déployés.
100 000+ traders cumulés dès le premier mois de lancement. 486 000+ détenteurs de tokens au
total (oct. 2025).

**Sécurité/légitimité** :
- Audit de sécurité réel et daté, effectué par Macro (0xMacro), du 27 juin au 7 juillet 2025,
  documenté publiquement.
- **Racheté par Farcaster** (octobre 2025), puis transitionné vers **Neynar** (janvier 2026,
  principal fournisseur d'infrastructure de Farcaster, backé par Paradigm et a16z crypto) —
  renforcement de légitimité réel, Clanker n'est plus une petite startup isolée.
- Incident clarifié : mai 2025, le développeur "_proxystudio" a démissionné immédiatement
  après avoir été exposé publiquement comme ayant volé ~350 000$ de fonds d'équipe d'un
  **autre** projet (Velodrome Finance), sous une identité antérieure ("Gabagool.eth"). Fonds
  rendus. Le fondateur Jack Dishman a confirmé que ça concernait des faits antérieurs à son
  arrivée chez Clanker, sans lien avec les fonds Clanker eux-mêmes. Signal de vigilance sur
  le recrutement passé, mais géré proprement.
- Aucune autre controverse/scam/exploit spécifique trouvé en 2026.

**Investissement direct — pas de programme identifié** : le "Clanker Ecosystem Fund" (CEF)
existe (8M$ déjà déployés) mais reste orienté subventions communautaires et rachat du token
natif $CLANKER — pas d'investissement en capital direct dans un projet tiers précis comme
ARIA.

**Zones d'ombre restantes, à lever avant tout engagement réel** :
- Support officiel non confirmé empiriquement — un serveur Discord "Clankers" existe
  (discord.gg/wegMpTSFzH) mais sa réactivité/qualité n'a pas été testée en conditions
  réelles (recommandé avant de s'engager).
- Une exigence de détenir 1 000 000 de tokens "$CLANKFUN" (token tiers distinct, coût
  négligeable ~3-5$ au prix actuel) semble s'appliquer à une méthode précise de déploiement
  multi-chaînes — portée exacte (toutes voies ou juste clanker.world web) non confirmée.
- Aucune fonction de gouvernance/staking native pour les tokens tiers lancés dessus — à
  construire séparément si voulu.

Sources : [Clanker Documentation (GitBook)](https://clanker.gitbook.io/documentation), [0xMacro — Clanker audit](https://0xmacro.com/library/audits/clanker-3), [Gate — Clanker Proxystudio incident](https://www.gate.com/post/status/11405666), [ainvest — Clanker fee surge](https://www.ainvest.com/news/clanker-fee-surge-record-300m-volume-event-2602/), [crypto.news — Clanker Ecosystem Fund](https://crypto.news/clanker-launches-ecosystem-fund-to-recycle-fees-into-creators-and-community/), [CryptoBriefing — Neynar acquires Farcaster](https://cryptobriefing.com/neynar-farcaster-acquisition/)

---

## Flaunch — ÉCARTÉ (trop petit aujourd'hui)

**Mécanisme** : "Fixed Price Fair Launch" de 30 minutes (anti-bot/anti-sniping, protection
CAPTCHA + plafond par wallet), 100% des frais de trading redistribués (créateur choisit son
%, le reste en buyback automatique), hook Uniswap v4 "Progressive Bid Wall" (soutien de prix
mécanique).

**Pourquoi écarté pour l'instant** : TVL de seulement **2,0M$**, revenu **annuel** de 2,8M$
(DeFiLlama) — environ 200x plus petit que Virtuals à son pic. Trop petit pour absorber une
croissance vers 10M$+ sans risque de slippage/instabilité. Durée exacte de verrouillage de
liquidité non trouvée malgré deux recherches ciblées — vraie zone d'ombre. Communauté
Discord/Telegram active mais pas de fondateurs identifiables trouvés facilement.

Sources : [Flaunch Docs](https://docs.flaunch.gg/), [DeFiLlama — Flaunch](https://defillama.com/protocol/flaunch)

---

## Robinhood Chain (Noxa/PONS/RobinPad/hood.fun) — ÉCARTÉ (trop jeune, déjà instable)

Mainnet lancé le 1er juillet 2026 — 3 semaines d'existence au moment de cette diligence.
**Noxa**, le launchpad principal de la chaîne (60 000 tokens lancés dont le memecoin phare
CASHCAT), a déjà fermé en moins de 2 semaines : ~12M$ de frais générés (11-14 juillet), puis
annonce de rediriger 100% des revenus aux créateurs et fermeture du site, invoquant "des
tokens de faible qualité qui inondaient la plateforme". Pas un vol, mais un vrai échec de
gouvernance — conséquence concrète : CASHCAT -33% en 24h. Une source prévient explicitement
qu'aucune plateforme de cette chaîne ne doit être traitée comme mature juste parce qu'elle
porte la marque Robinhood.

Sources : [Coindesk — Noxa gave away all its revenue](https://www.coindesk.com/business/2026/07/15/the-launchpad-that-fueled-robinhood-chain-s-memecoin-boom-just-gave-away-all-its-revenue), [Bitcoin Foundation — Best Robinhood Chain Launchpads](https://bitcoinfoundation.org/news/blockchain-news/best-robinhood-chain-launchpads-2026/)

---

## Coinbase/Base — pas de launchpad officiel, mais timing macro favorable

Pas de launchpad "officiellement soutenu" par Coinbase — le **Base Ecosystem Fund** (mené
par Coinbase Ventures) investit dans l'écosystème Base au sens large, pas fléché vers un
launchpad de tokens précis. Aucune preuve trouvée d'un investissement direct de Coinbase
Ventures dans Clanker ou Virtuals spécifiquement. Jesse Pollak n'a recommandé aucun
launchpad officiel.

**Fait le plus pertinent pour ARIA** : Jesse Pollak a annoncé le 15 juillet 2026 (12 jours
avant cette diligence) que la stratégie 2026 de Base **pivote vers la tokenisation, le
trading, les paiements et les agents IA**, après avoir reconnu l'échec de leur pari
précédent (applications sociales onchain + "creator coin"). Signal macro favorable
indépendant du choix de launchpad — Base cherche activement des projets comme ARIA en ce
moment.

Sources : [CryptoRank — Base Ecosystem Fund](https://cryptorank.io/funds/base-ecosystem-fund), [Hokanews — Jesse Pollak unveils Base's 2026 strategy](https://www.hokanews.com/2026/07/jesse-pollak-unveils-bases-new-2026.html)

---

## Avis final

**Clanker reste mon candidat recommandé**, seul à cocher tous les critères en même temps :
mécanisme anti-dump le plus solide trouvé (verrouillage jusqu'en 2100), croissance actuelle
réelle et récente (+37% de frais en un mois, contrairement à Virtuals en déclin structurel
confirmé), audit de sécurité réel et daté, rachat par Farcaster/Neynar (renforcement de
légitimité), aucun incident de sécurité propre à la plateforme (contrairement à Bankr),
suffisamment mature (contrairement à Flaunch/Robinhood Chain).

Ce que je recommande de faire avant tout engagement réel (aucune urgence, l'opérateur a été
clair que ce n'est pas pour maintenant) :
1. Tester réellement le Discord Clanker (poser une vraie question, mesurer la réactivité) —
   seul point de cette diligence resté non vérifié empiriquement.
2. Clarifier la portée exacte de l'exigence $CLANKFUN (toutes voies de déploiement ou
   seulement clanker.world web).
3. Envisager de candidater en parallèle à Virtuals Ventures (virtuals.vc) — rien n'empêche
   de lancer le token sur Clanker tout en explorant un investissement/accélération via
   Virtuals, les deux ne sont pas mutuellement exclusifs.

## Limite de cette note

Recherche web (WebSearch/WebFetch), aucun test API réel, aucune vérification on-chain directe
des contrats mentionnés (LP Locker Clanker, Doppler Bankr). Deux pièges d'homonymie rencontrés
et corrigés en cours de route (une entreprise EdTech "Flaunch" sans lien avec le launchpad
crypto ; un produit "Clanker Support" générique sans lien avec le support du launchpad) — à
garder en tête pour toute future recherche sur ces noms. À recouper avant intégration dans
`knowledge/*.yaml`, comme toute note de cet inbox.
