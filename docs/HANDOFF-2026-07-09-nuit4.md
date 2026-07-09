# HANDOFF — 2026-07-09 nuit (suite 4) — Décision pilote Virtuals Arena, due diligence approfondie, prompts drafts

Suite directe de `docs/HANDOFF-2026-07-09-nuit3.md` (même journée, segment encore plus
tardif). Lire les quatre HANDOFF du 09/07 + `CLAUDE.md` + `docs/etat-systeme-cable.md`.

**Aucun commit aria-core ce segment** — tout ce qui suit est de la recherche externe
(Virtuals/GAME/Arena), une décision opérateur actée, et des brouillons de prompt
(fichiers scratchpad, pas dans le repo). Le seul artefact permanent est ce HANDOFF.

## Contexte réseau — accès élargi de la session cloud (changement d'environnement, pas de code)
L'opérateur a ajouté un accès réseau **Personnalisé** à l'environnement Claude Code
(web) : `*.virtuals.io`, `degen.virtuals.io`, `whitepaper.virtuals.io`,
`docs.game.virtuals.io`, `basescan.org`/`api.basescan.org`, `sepolia.base.org`,
`*.youtube.com` — en plus de la liste par défaut (npm/PyPI/GitHub...). Ça change la
donne pour les prochaines sessions cloud : **on peut désormais interroger l'API
Virtuals en direct** (ex. `GET https://degen.virtuals.io/api/leaderboard`) sans passer
par un relais VPS/opérateur. Persistant tant que l'environnement "Par défaut" n'est
pas reconfiguré.

## Décision opérateur actée — ARIA participe à l'Arena comme "prototype à échelle réelle"
Décision explicite et répétée : *"je confirme que aria dans l'arena sera notre
prototype à échelle réelle pas de problème"*. Contexte donné avant la décision, à
reconfirmer si une session future en doute :
- Le mécanisme d'exécution (`dgclaw-skill`, cf. plus bas) est **100% autonome par
  conception** — signé directement par le wallet de l'agent, sans étape de
  confirmation, sans limite de risque intégrée. C'est l'infrastructure de Virtuals qui
  exécute, **pas notre code** — `wallet_guard`/le clic Telegram ne voit JAMAIS ces
  trades, et le kill-switch `/stop` ne s'applique pas à ce chemin.
- Ce n'est donc **pas une entorse à la règle absolue côté aria-core** (jamais de trade
  automatique sur capital réel sans validation Telegram) — cette règle continue de
  s'appliquer intégralement à tout ce qui vit dans notre codebase. L'Arena est un
  système tiers séparé, sur lequel l'opérateur choisit d'engager du capital réel
  dédié, en toute connaissance de cause (autonomie totale de ce côté-là, aucun
  garde-fou aria-core ne peut s'y brancher).
- Wallet dédié, isolé du wallet Vanguard ZHC principal (celui de l'incident sécurité de
  ce même 09/07) — jamais le même.
- Objectif énoncé par l'opérateur : *"j'ai encore rien fait"* — **aucune action
  n'a encore été prise côté Virtuals à la fin de ce segment** : ni wallet créé, ni
  compétence installée, ni prompt collé. Tout reste à faire par l'opérateur.

## Deux marchés distincts dans l'Arena — architecture technique très différente
- **HL Perps (Hyperliquid)** : compétence officielle prête à installer,
  `github.com/Virtual-Protocol/dgclaw-skill` — `git clone` + `./dgclaw.sh join` (après
  configuration de `acp-cli` et dépôt d'USDC). Aucune logique de risque intégrée (pas
  de limite de taille/levier) — tout doit venir du prompt/Goal de l'agent.
- **Jetons d'agent (Agent Tokens)** : trading des tokens Virtuals en courbe de
  bonding, AVANT graduation — via `github.com/Virtual-Protocol/bondv5-trader`.
  **Ce n'est PAS une compétence packagée** (contrairement à dgclaw) — une simple
  librairie npm (5 fonctions : `balanceOf`, `ensureApproval`, `usdcToVirtualSwap`,
  `virtualToUsdcSwap`, `bondingV5Trade`) à intégrer soi-même dans une fonction custom
  GAME SDK. Vrai développement à faire, pas encore commencé.
  **Faille de sécurité confirmée dans la librairie** : `minOutWei` par défaut = `1`
  (aucune protection anti-slippage sauf calcul explicite d'un devis frais avant chaque
  trade) — à ne jamais utiliser tel quel.
  Ce marché correspond exactement à la niche déjà construite (#10,
  `services/virtuals.py`, détection pré-bonding) — c'est pour ça qu'il a été choisi
  comme axe principal malgré le développement supplémentaire : joue sur la vraie
  force analytique d'ARIA plutôt que d'improviser un jugement macro/technique sur des
  perpétuels à effet de levier (domaine où ARIA n'a aucun track record).

**Décision finale opérateur (fin de segment)** : *"les deux, je veux apprendre et
régler tous les détails pour améliorer ARIA et pas forcément gagner"* — les deux
marchés seront tentés, dans un esprit d'apprentissage explicite (pas de pression de
rentabilité). Séquencement proposé : démarrer par HL Perps (installation rapide, déjà
prête) pendant que le développement Jetons d'agent (plus lourd) est construit en
parallèle.

## Due diligence Virtuals — éléments vérifiés ce segment (au-delà de nuit3)
- **Endpoint public confirmé et exploité** : `GET degen.virtuals.io/api/leaderboard`
  (déjà câblé en Phase 0, `services/virtuals_arena.py`, voir nuit3). Ce segment a en
  plus confirmé `GET degen.virtuals.io/api/agents/{id}` (détail par agent, y compris
  `copyTradeSelections` = historique de sélection par le Conseil IA) et
  `GET degen.virtuals.io/api/forums` (liste des fils de discussion par agent).
- **Les threads "SIGNALS/Alphas" sont gated (401 sans abonnement)** pour la quasi
  totalité des agents testés (Calculated Opportunity id 1173, Monyet id 176 tous deux
  confirmés `isGated: true` côté API) — **mais l'opérateur voit certains posts de
  Monyet gratuitement dans son navigateur**, contradiction non résolue (peut-être un
  mécanisme de preview propre au front-end, ou un abonnement déjà actif côté
  opérateur — à él ucider si besoin, sans impact sur la suite).
- **Mécanique de sélection du Conseil IA confirmée** : si un agent est sélectionné
  (badge "TOP 10"), 50% des profits réalisés sont reversés à l'opérateur de l'agent,
  les pertes étant absorbées par Virtuals — asymétrie qui pousse les agents en haut du
  classement vers plus de risque une fois sélectionnés. **Ne pas calibrer notre risque
  sur cette asymétrie tant qu'on n'est pas sélectionnés** (100% du risque reste sur
  notre dépôt).
- **Second marché "Jetons d'agent"** découvert via captures opérateur (toggle
  "Marché : HL Perps / Jetons d'agent" sur la page Arène) — classement et agents
  totalement différents de celui HL Perps, PnL dénommé en $VIRTUAL.

## Enseignements tirés de posts publics réels (jamais copiés, style/pratiques absorbés)
Posts librement lus (screenshots opérateur) de Monyet (#1 lifetime), Zen, Agent Toto,
BTCtrade — tous des agents distincts, styles différents :
- Sorties échelonnées par tranches plutôt que tout-ou-rien (Agent Toto).
- Critère d'invalidation écrit AVANT l'entrée, pas reconstruit après coup (Zen).
- Stop-loss placé avec marge de sécurité AU-DESSUS du prix de liquidation, jamais
  collé dessus (risque de cascade de liquidation) (Zen).
- Vérifier la liquidité disponible AVANT d'entrer (position liquidable en quelques
  secondes vs le volume) — même logique que notre `liquidity_depth.py` (Zen).
- Confluence de plusieurs signaux indépendants exigée avant d'agir, jamais un seul
  indice isolé (Zen, BTCtrade — modèle de risque nommé "Satoshi" chez ce dernier,
  dominance BTC comme filtre de régime + gestion du bêta de portefeuille global).
- **Point de vigilance, PAS à imiter** : Zen a mis 99% de sa marge sur un seul trade
  ("configuration la plus convaincante de l'univers ce jour-là") — trop concentré
  pour un agent sans historique comme le nôtre. Nos brouillons plafonnent à 5% du
  capital par position, délibérément plus conservateur.

## Brouillons de prompt/Goal — évolution v1 → v3 (fichiers scratchpad, PAS dans le repo)
Trois versions successives, affinées au fil des découvertes ci-dessus :
- **v1** : générique HL Perps, majors uniquement, levier max 3x, 2 positions max.
- **v2** : HL Perps + actifs HIP-3 (actions US/coréennes, ETF, matières premières,
  indices, FX via trade.xyz, préfixe `xyz:` obligatoire sinon le trade ne compte pas
  pour l'évaluation du Conseil IA) — levier assoupli (3x défaut, jusqu'à 8x sur setup
  liquide/majeur), 4 positions max, style d'entrée/sortie inspiré de Monyet (liquidité
  + maturité du narratif, sortir sans ego quand la thèse s'invalide).
- **v3** : pivot complet vers Jetons d'agent — remplace le jugement macro/technique par
  une barre d'entrée à 4 critères vérifiables réutilisant la doctrine ARIA existante
  (mint/ownership safety, santé de liquidité, progression de bonding réelle vs
  fabriquée, comportement builder vs extraction) ; jamais d'achat si un seul des 4
  n'est pas clair ("donnée insuffisante" plutôt qu'une supposition. Intègre aussi les
  enseignements généraux ci-dessus (sorties échelonnées, invalidation écrite,
  slippage réel obligatoire vu la faille `bondv5-trader`).

**Décision finale : poursuivre les deux marchés → il faudra fusionner ou juxtaposer
v2 et v3 en un seul document Goal cohérent** (pas encore fait — prochaine étape
concrète si l'opérateur confirme avoir créé le wallet).

## Étapes d'installation données à l'opérateur (HL Perps, prêt à exécuter)
1. `app.virtuals.io/acp/new` → nouvel agent → wallet dédié Base (isolé de Vanguard ZHC).
2. Dépôt USDC (montant au choix de l'opérateur).
3. `git clone github.com/Virtual-Protocol/acp-cli` → `acp configure` → `acp agent use
   <id>` → `acp agent add-signer`.
4. `git clone github.com/Virtual-Protocol/dgclaw-skill` → `./dgclaw.sh
   activer-compte-unifié` → `npx ts-node scripts/deposit.ts <montant>` → `./dgclaw.sh
   join`.
5. Coller le prompt/Goal (v2) dans la config Virtuals.

**Statut fin de segment : aucune étape effectuée par l'opérateur.**

## Ce qui reste en attente (priorité pour la prochaine session)
1. **Confirmer si le wallet dédié Arena a été créé** — dernière info connue : non fait.
2. Si oui : fusionner v2/v3 en un Goal unique, guider l'installation `dgclaw-skill`
   (déjà documentée ci-dessus), puis démarrer le vrai développement du wrapper GAME
   custom pour `bondv5-trader` (Jetons d'agent) — pas encore commencé, nécessite de
   décider où ce code vit (nouveau composant TypeScript, probablement hébergé sur le
   VPS pour un accès réseau réel en continu, distinct d'aria-core Python).
3. Objets non résolus reportés depuis nuit3 (toujours valables) : JWT non vérifié
   dans `skills/core/memory/ACP VIRTUAL PROTOCOL/20260628_1139_source.md:211`,
   vérification Basescan de la fenêtre d'exposition de l'ancienne clé (probablement
   faite entre-temps par l'opérateur, à confirmer).
4. Contradiction non résolue (mineure, pas bloquante) : pourquoi les posts de Monyet
   étaient visibles gratuitement pour l'opérateur alors que l'API confirme le thread
   gated — à éclaircir si ça revient.
5. Backlog sans blocage : #11, #17, #19-23, #29, #32, #34, #56, #57, #59.

## Auto-critique honnête
Ce segment est presque entièrement de la recherche/décision/design — aucun code
aria-core produit, ce qui est cohérent avec la consigne explicite de l'opérateur
("priorise le potentiel plutôt que d'intégrer tout et n'importe quoi") : mieux valait
comprendre en profondeur (deux marchés, deux mécaniques d'exécution très différentes,
une vraie faille de sécurité dans l'outil tiers) avant d'écrire une ligne de code
engageant du capital réel. Le vrai risque à surveiller la prochaine fois : ne pas
laisser le chantier Jetons d'agent (plus lourd) traîner indéfiniment pendant que le
chemin rapide (HL Perps) avance seul — l'opérateur a été clair sur vouloir les deux.
