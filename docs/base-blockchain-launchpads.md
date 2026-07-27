# Base blockchain — launchpads de tokens (fiche de référence)

> Fiche de référence VIVANTE (pas un instantané daté comme `docs/aria-learning-inbox/`) — à
> mettre à jour au fil du temps quand l'un de ces launchpads évolue. Contexte : diligence
> lancée le 27/07/2026 en vue d'une **future** tokenisation d'ARIA (aucune action engagée à
> ce jour) — déclenchée par l'incident de compromission du compte X de Bankr.bot. Diligence
> initiale complète et sourcée : `docs/aria-learning-inbox/2026-07-27-diligence-launchpads-tokenisation-aria.md`.
> Chaque section ci-dessous porte le point faible principal identifié, sous forme de question
> à se reposer avant tout engagement réel ou à chaque reprise de ce fichier.

## Bankr

**Diligence** : construit sur le protocole Doppler (hooks Uniswap V4), frais de swap 0,7%.
100 milliards de tokens à l'émission, 85% amorce la liquidité, 15% vest au créateur sur 2 ans
(cliff 30 jours) — mécanisme correct en soi. Mais deux incidents de sécurité en 2 mois en
2026 : (1) mai — hack de wallet via injection de prompt ciblant Grok↔Bankrbot, >440 000$ de
dégâts, 14 wallets compromis ; (2) juillet — prise de contrôle du compte X officiel. Le
mécanisme tokenomics n'est pas en cause, c'est la confiance opérationnelle de la plateforme.
**Statut : écarté.**

**Question sur son point faible** : Bankr a-t-il eu un nouvel incident de sécurité depuis
juillet 2026, ou a-t-il démontré une vraie amélioration de sa posture de sécurité (audit
indépendant publié, changement d'infrastructure, communication de transparence) qui
justifierait de reconsidérer la plateforme ?

---

## Virtuals Protocol

**Diligence** : le plus mature de l'écosystème "AI agent tokens" — fair launch strict (1B
tokens fixes/agent), liquidité verrouillée **10 ans**, frais 1%. ARIA déjà dans cet
écosystème (compute.virtuals.io). Mais déclin structurel confirmé (pas juste une baisse de
prix cyclique) : revenus -95% depuis janvier 2025 (3,5M$/mois → <200K$/mois), utilisateurs
actifs quotidiens 30 000 → <12 000, volume 24h actuel seulement 174 000$ sur 382M$ de mcap.
Le développement technique continue activement (migration Chainlink CCIP, intégration
Robinhood Chain) sans que ça inverse l'exode utilisateur. Un vrai fonds d'accélération
existe (Virtuals Ventures, virtuals.vc) orienté IA au sens large (pas que robotique),
étendu à la finance — mais aucun montant/critère public trouvé. **Statut : écarté pour le
lancement de token, piste Virtuals Ventures à explorer séparément.**

**Question sur son point faible** : le déclin des revenus/utilisateurs de Virtuals s'est-il
stabilisé ou inversé depuis juillet 2026, ou continue-t-il de se dégrader malgré les
nouveaux partenariats (Robinhood Chain, Eastworld Labs, ACP v2.0) ? Et Virtuals Ventures
a-t-il publié des montants/critères concrets, ou reste-t-il une boîte noire ?

---

## Clanker

**Diligence** : frais 1% Uniswap V3 — créateur reçoit 100% des frais LP initiaux via
l'interface clanker.world (80% via le bot Farcaster). Liquidité verrouillée **jusqu'en
2100** (le mécanisme anti-dump le plus solide de toute la diligence). Croissance actuelle
réelle : frais quotidiens +37% en un mois (65K$→89K$), 100 000+ traders cumulés dès le 1er
mois, 486 000+ détenteurs au total. Audit de sécurité réel (0xMacro, juin-juillet 2025).
Racheté par Farcaster (oct. 2025) puis Neynar (janv. 2026) — légitimité renforcée. Incident
clarifié : le développeur "_proxystudio" a démissionné immédiatement en mai 2025 après avoir
été exposé pour un vol de fonds chez un **autre** projet (Velodrome) avant de rejoindre
Clanker — fonds rendus, aucun lien avec les fonds Clanker. Zones d'ombre restantes : support
Discord non testé empiriquement, portée exacte de l'exigence $CLANKFUN (coût négligeable
~3-5$) non confirmée, pas de gouvernance/staking natif pour les tokens tiers. **Statut :
candidat recommandé.**

**Question sur son point faible** : le support Discord de Clanker répond-il réellement dans
un délai raisonnable à une vraie question technique (test à faire soi-même) ? Et l'exigence
$CLANKFUN s'applique-t-elle à toutes les voies de déploiement (API/SDK inclus) ou seulement
à l'interface web clanker.world ?

---

## Flaunch

**Diligence** : "Fixed Price Fair Launch" de 30 minutes (anti-bot/anti-sniping, CAPTCHA +
plafond par wallet), 100% des frais redistribués (créateur choisit son %), hook Uniswap v4
"Progressive Bid Wall" (soutien de prix mécanique). Mais TVL de seulement 2,0M$, revenu
annuel 2,8M$ (DeFiLlama) — ~200x plus petit que Virtuals à son pic. Durée de verrouillage de
liquidité introuvable malgré deux recherches ciblées. Fondateurs non identifiés. **Statut :
écarté pour l'instant (trop petit).**

**Question sur son point faible** : le TVL de Flaunch a-t-il significativement grossi depuis
juillet 2026 (rapprochement de Clanker/Virtuals) ? A-t-on pu identifier une équipe/des
fondateurs vérifiables, et la durée réelle de verrouillage de liquidité a-t-elle été
clarifiée ?

---

## Robinhood Chain (Noxa / PONS / RobinPad / hood.fun)

**Diligence** : mainnet lancé le 1er juillet 2026 — écosystème de 3 semaines au moment de
cette diligence. Noxa, le launchpad principal (60 000 tokens lancés, memecoin phare
CASHCAT), a déjà fermé en moins de 2 semaines : ~12M$ de frais générés (11-14 juillet), puis
annonce de redistribution de 100% des revenus aux créateurs et fermeture, invoquant "des
tokens de faible qualité qui inondaient la plateforme" — pas un vol, mais un vrai échec de
gouvernance. CASHCAT -33% en 24h suite à l'annonce. **Statut : écarté (trop jeune, déjà
instable).**

**Question sur son point faible** : un nouveau launchpad stable a-t-il émergé sur Robinhood
Chain depuis la fermeture de Noxa (PONS ? un autre ?), avec un historique d'au moins
plusieurs mois sans incident majeur, avant de reconsidérer cet écosystème ?

---

## Coinbase / Base (écosystème général)

**Diligence** : pas de launchpad "officiellement soutenu" par Coinbase — le Base Ecosystem
Fund (Coinbase Ventures) investit dans l'écosystème Base au sens large, pas fléché vers un
launchpad de tokens précis. Aucune preuve d'investissement direct de Coinbase Ventures dans
Clanker ou Virtuals. Jesse Pollak n'a recommandé aucun launchpad officiel. Fait le plus
pertinent : pivot stratégique annoncé le 15/07/2026 vers la tokenisation/trading/paiements/
agents IA, après l'échec reconnu du pari "creator coin"/social onchain — timing macro
favorable pour ARIA, indépendant du choix de launchpad. **Statut : pas un candidat en soi,
signal macro à surveiller.**

**Question sur son point faible** : Coinbase a-t-il fini par lancer un token natif Base ou
désigné un launchpad officiel/préféré depuis juillet 2026 — ce qui changerait la donne
stratégique pour le choix final ?

---

## Synthèse / avis maintenu

Clanker reste le candidat recommandé (voir la diligence datée pour le détail complet des
sources). Avant tout engagement réel : tester le support Discord de Clanker, clarifier la
portée de l'exigence $CLANKFUN, et repasser sur ce fichier périodiquement pour vérifier si
les réponses aux questions ci-dessus ont changé la donne (notamment un éventuel rebond de
Virtuals, ou une stabilisation de l'écosystème Robinhood Chain).
