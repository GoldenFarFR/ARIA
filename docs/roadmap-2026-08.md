# Roadmap ARIA — Août 2026

> Instantané daté (02/08/2026), pas une fiche vivante à maintenir indéfiniment — à
> reprendre/réviser lors du bilan hebdomadaire ou si le contexte change fortement.
> Construite à partir de faits vérifiés en base (`/opt/aria-data/aria.db`), du code réel,
> de CLAUDE.md et de la mémoire persistante — cross-checkée par deux tours de workflow de
> revue adversariale (cohérence + complétude, doctrine du projet) avant validation
> opérateur. Volontairement **pas réduite à un seul axe financier** (bénéfices, intérêt
> composé, sources de revenu) — décision opérateur explicite du 02/08 : la roadmap doit
> couvrir toute la largeur du projet.

**Cadre chapeau, valable pour les 7 axes** : doctrine « ARIA d'abord, token ensuite » —
performance réelle → utilité → identité → communauté → token. Aucun axe secondaire ne
doit inverser cet ordre (ex. précipiter une tokenisation ou une notoriété externe avant
que la performance et l'utilité soient prouvées).

---

## Vue d'ensemble — 7 axes en parallèle

1. **Performance** — le test hebdomadaire +10%, répété jusqu'à validation fiable ; la
   qualité démontrée par chaque poche conditionne directement la taille du capital réel
   qu'elle recevra ensuite (voir axe 1).
2. **Identité & présence** — voix, avatar, mémoire libre (aria-brain).
3. **Sécurité & robustesse** — angle mort adversarial, garde-fous de gouvernance,
   prérequis avant tout capital réel.
4. **Écosystème & réseau** — Base, x402/Bazaar, tokenisation potentielle, visibilité
   externe.
5. **Infrastructure & autonomie technique** — CDP, LLM, dépendance API, wallet-scoring.
6. **Gouvernance & hygiène** — backlog, HANDOFF, mécanismes de revue adversariale
   (actuellement en partie cassés — voir axe 6), cadence de déploiement.
7. **Sources de revenu** — une dimension parmi les six autres, pas la seule boussole.

**Override permanent, prime sur toute cette séquence** : un correctif de sécurité (faille,
secret exposé, garde-fou cassé) se déploie immédiatement, jamais mis en attente d'un
créneau de roadmap (CLAUDE.md, « Cadence de déploiement »).

---

## Axe 1 — Performance (test hebdomadaire +10%)

**État réel au 02/08** : aucune semaine `validated=1` sur `paper_weekly_cycle` à ce jour
(3 lignes seulement — swing, vc, scalping_v6 ; les autres poches n'ont encore jamais
bouclé un cycle complet). Dernier cycle complet : swing -0.51% (9 trades, 22% winrate),
scalping_v6 +2.20% (18 trades, 78% winrate). **Jugement opérateur explicite (02/08) : pour
l'instant, décevant côté poches.**

**Lien direct avec le capital réel à venir** : le cap du pilote réel n'est plus figé à
10-15$ — la direction envisagée est **3 Smart Wallets réels distincts (scalping / swing /
vc), ~50$ chacun**, mais leur taille finale dépendra de la **qualité démontrée par chaque
poche papier respective**, pas d'un montant décidé à l'avance. Une poche qui continue de
décevoir reçoit moins (voire rien) ; une poche qui prouve sa discipline peut recevoir
plus. C'est donc l'axe 1 — pas une décision de calendrier — qui fixe le rythme réel de
cet axe 5/7.

**Ce soir (02/08, ~21h50Z)** : les 4 poches (scalping v1-v6, swing, vc, megacap) sourcent
désormais toutes en parallèle — `ARIA_SCALPING_ONLY_SOURCING_ENABLED` désactivé,
`ARIA_VC_POCKET_SOURCING_ENABLED` activé, 3 correctifs déployés (entry_atr_pct, wash-
trading scalping-only, angle mort liquidité DexScreener/B20, throttle CoinMarketCap, race
`_execute_trigger`). Vérification programmée à 22h51Z.

**Semaine 1 (03-09/08)** — point structurel à garder en tête : le reset de swing/vc/
scalping_v6 tombe le **08/08**, en plein milieu de la fenêtre. Le bilan du 09/08 jugera un
cycle vieux d'à peine ~1 jour sous pipeline corrigé — ne pas le lire comme une semaine
pleine. Priorités immédiates :
- Revérifier v2/v4/v5 dès maintenant (déjà 28h à zéro trade) plutôt qu'attendre 24-48h (#22).
- Diagnostiquer le blocage réel du wallet-scoring (oscillation sur 3-4 wallets, logs
  02/08 21h02-21h25) — pas une simple lenteur, possible récidive du bug corrigé le 23/07 (#32).

**Semaines 2-4** : cycle diagnostic → correction → observation (doctrine du 18/07,
inchangée) — chaque semaine jugée sur elle-même, aucun seuil de semaines consécutives
n'est fixé à ce jour.

**Jalon daté dans le mois — premier utilisateur externe (~13/08)** : plan opérateur
d'onboarder un premier utilisateur externe qui copie les trades d'ARIA (~50$), bloqué par
la règle absolue « aucun encaissement avant validation avocat ». À statuer AVANT
l'échéance, pas après (#54).

**Fin de mois** : si le +10% est validé de façon répétée, rouvrir la discussion sur le
critère de passage à plus de capital réel — aucun chiffre n'est tranché aujourd'hui.
Prérequis à traiter avant toute extension : désactiver Solana, statuer sur les
coupe-circuits de risque actuellement désactivés en paper, reprendre le backlog de
durcissement agent-wallet (#49).

---

## Axe 2 — Identité & présence

Vision opérateur du 15/07, jamais construite mais jamais oubliée : au-delà d'une
investisseuse, ARIA doit devenir à terme une présence reconnaissable — voix, avatar,
présence X — avec une frontière de goût déjà gravée (jamais suggestif/dénudé/sexualisé,
10/07).

- **Ce mois-ci, diligence seule, pas de construction** : stack TTS réaliste (coût,
  latence, qualité) + **avatar parlant (HeyGen)** + **cadence de publication X gated par
  revue humaine + kill-switch étendu** (#45/#53 — les trois volets de la vision banquée du
  22/07, pas seulement la voix). Décider après diligence si un prototype se justifie.
- **aria-brain** (mémoire libre, une page/jour) reste actif — doctrine 99% réel / 1%
  spéculation marquée explicitement inchangée, aucune action requise ce mois-ci sauf
  incident.
- **`knowledge/dna.yaml`** : tension architecturale jamais tranchée depuis le 21/07
  (multi-ancrage identité/mémoire suggéré par la recherche externe vs fusion en un seul
  fichier voulue par l'opérateur) — à trancher avant tout futur refactor (#50).

---

## Axe 3 — Sécurité & robustesse

Mandat permanent VPS Research (15/07) : catalogue et vérifie que les atouts propres à une
IA-trader sont VRAIMENT exploités, et que les points faibles propres à une IA sont trouvés
puis comblés — jusqu'à ce que l'opérateur juge ARIA prête.

- **Vulnérabilité adversariale/prompt-injection on-chain** — un projet malveillant qui
  façonnerait son nom/site/métadonnées pour biaiser le jugement LLM d'ARIA. Testé
  seulement à n=2 prompts le 17/07 (#117). Ce mois-ci : élargir l'échantillon,
  documenter, combler tout point faible réel trouvé (#44).
- **Backlog de durcissement agent-wallet #215-#230, jamais repris** — directement
  pertinent maintenant que 3 Smart Wallets réels sont envisagés (axe 1). Prioriser #224
  (allowance ERC-20 jamais illimitée + simulation pré-signature avant tout swap réel) et
  #221 (audit que rien ne peut élargir le périmètre swap-only du pilote) (#49).
- **Prérequis avant capital réel étendu** : coupe-circuits de risque paper actuellement
  désactivés (le commit lui-même dit « MUST be revisited before any real-capital
  transition ») ; Solana à désactiver avant toute extension du pipeline vers une
  exécution réelle au-delà du pilote agent-wallet déjà Base-only.

---

## Axe 4 — Écosystème & réseau

- **Veille Base/Jesse Pollak** (permanente depuis le 16/07) : décision #199 (quelle
  ressource x402 payer en premier — Cybercentry, 0,02$/appel) toujours en attente d'un
  tranchage opérateur (#36).
- **Diligence tokenisation ARIA, approfondir Clanker** (diligence de surface du 27/07) :
  creuser la mécanique exacte du lock LP et la gouvernance réelle avant toute décision (#41).
- **Visibilité/reconnaissance dans l'écosystème AI-agent crypto — ambition générale, pas
  de cible nommée** (clarifié par l'opérateur le 02/08 : « ai16z » n'était qu'une image,
  pas un objectif littéral). Contexte factuel utile gardé en tête, vérifié le 02/08:
  « ai16z » n'existe plus sous ce nom depuis janvier 2025 (rebrandé **ElizaOS**, à la
  demande d'a16z le vrai VC) et fait l'objet d'une class-action active depuis le
  22/04/2026 (allégation de fraude, 2,6Md$) — un acteur du paysage à connaître, pas une
  cible à viser en ce moment précis vu la turbulence. Direction concrète : construire la
  reconnaissance par la preuve (track record public, performance, présence — axes 1/2),
  pas par un rapprochement calculé avec un acteur précis.
- **Monad** (chaîne candidate) — EVM/GoPlus/DexScreener OK, mais Blockscout non-officiel
  reste un vrai bloquant. À revérifier périodiquement (#52).

---

## Axe 5 — Infrastructure & autonomie technique

- **Migration LLM vers Claude (Haiku 4.5 + Sonnet 5)** — direction actée, gate désormais
  séparé par rôle (`ARIA_LLM_ANTHROPIC_ROUTING_ENABLED` / `..._TRADING_ENABLED`, commit du
  02/08). Séquence avant tout flip réel : compte OpenRouter dédié pour DeepSeek, vérifier
  `ANTHROPIC_API_KEY` en prod, gate général d'abord (observer), gate trading en dernier (#48).
- **Smart Account CDP (Spend Permissions + Paymaster)** — direction actée, ~10 jours de
  conception déjà faits. Prochaine étape (wrapper `eth_account.BaseAccount`) nécessite des
  sessions hardware-in-the-loop avec le Tangem physique de l'opérateur — à planifier
  explicitement (#35). Devient plus concret maintenant que 3 Smart Wallets réels sont
  envisagés (axe 1).
- **unified_entry.py** (#194 amendé, crible VC/Swing unifié) — CODE, dormant depuis le
  22/07, à moitié fait. Décider ce mois-ci : reprendre, ou geler explicitement (#33).
- **Wallet-scoring vers le seuil ~500** — 9 wallets uniques / 775 lignes au 02/08, très en
  dessous. Lié au diagnostic de blocage de l'axe 1 (#40).
- **Réduction de dépendance API** — identifier ce mois-ci UN candidat concret (#42).

---

## Axe 6 — Gouvernance & hygiène

**Urgent, trouvé aujourd'hui** : le mécanisme Avocat du Diable (`scripts/devils-advocate-
review.sh`, revue de code post-push) est **cassé depuis le 26/07** (compte OpenRouter à
sec, HTTP 402) — et le même compte partagé alimente aussi le juge adversarial trading
(`trade_devils_advocate.py`/`trade_loss_batch_review.py`). Les deux garde-fous de
gouvernance sont hors service silencieusement depuis une semaine. Action opérateur requise
en premier (recharger le compte), puis migration déjà décidée mais jamais tracée vers
Gemini pour le hook code (#47).

- Backlog remis à 10-15 items pending le 02/08 (norme du 09/07) — à réalimenter dès qu'il
  redescend.
- HANDOFF par composant : pratique active, vérifier qu'aucun nouveau composant ne reste
  sans fichier dédié.
- Cadence de déploiement direct vs batch : doctrine du 18/07 déjà appliquée sans
  incident ce mois-ci (3 correctifs groupés dans un même déploiement le 02/08).

---

## Axe 7 — Sources de revenu (une dimension parmi les six autres)

- **x402 seller** (`/api/x402/walletscore`) — code complet, dormant. Seule étape
  restante : le test d'auto-paiement testnet de l'opérateur lui-même.
- **Élargir le catalogue vendable** — recherche/scoping seul ce mois-ci. **Blocage
  contractuel réel trouvé** : aucun des fournisseurs utilisés (GoPlus, Blockscout,
  CabalSpy, TwitterAPI.io) n'autorise explicitement la revente de données dérivées —
  GoPlus et CabalSpy sont carrément restrictifs. Écrire pour obtenir une permission écrite
  avant tout élargissement (#51).
- **Mindshare Rewards** — le split de revenu est **déjà décidé** (5-8% Mindshare, 4-7%
  buybacks/burn préféré, reste → trésorerie/dev/compute/voix/avatar/infra, plafond dur
  15% de redistribution) — ce qui reste ouvert n'est QUE le mécanisme de paiement sortant
  automatisé multi-destinataires (qui valide, plafond, anti-abus) (#43).

---

## Branches ouvertes — brainstorm génératif (02/08)

Sur demande opérateur explicite ("plus d'imagination"), workflow dédié à la pure
génération d'idées (pas une vérification factuelle) — deux angles distincts, mêmes
frontières que la doctrine « multiplier les branches » du 10/07 (jamais rien qui
toucherait `wallet_guard`/`permission_mode`/capital réel/secrets).

**6 pistes à fort potentiel, coût de premier pas quasi nul** (toutes construites sur une
brique déjà existante et vérifiée dans le code, jamais un chantier from scratch) :

1. **Registre public des refus** — exposer (délai 1 semaine) les candidats rejetés + leur
   contrefactuel déjà calculé (`/counterfactual`). Matérialise « preuve avant promesse ».
   Zéro concurrent observé (aixbt et les autres agents ne montrent que leurs bons appels) (#55).
2. **Index différentiel ARIA vs marché** — contextualise le bilan hebdomadaire (axe 1)
   contre un benchmark simple, au lieu d'un chiffre isolé (#56).
3. **Desks en compétition** — narrer scalping/swing/vc/megacap comme des équipes
   distinctes dans le rapport hebdo, réutilise une segmentation déjà en base (#57).
4. **`x402_trust_score.py` comme 2e produit vendable** — moteur complet et testé, jamais
   branché en prod ; calcul propriétaire (pas une revente de donnée tierce) donc
   **débloque un produit x402 sans attendre la permission GoPlus/CabalSpy** (#51) (#58).
5. **`pump_dump_autopsy.py` → aria-brain** — texte déjà produit, jamais poussé vers la
   mémoire libre. Coût le plus bas de toute la liste (#59).
6. **« Wallet Passport » public** — combine 4 briques d'identité/réputation qui ne se
   parlent jamais (Farcaster, Basenames, CabalSpy, smart_money) en une fiche narrative,
   zéro coût API nouveau, teaser naturel pour walletscore x402 (#60).

**Autres pistes banquées, pas encore scopées** (coût plus élevé ou dépendance externe,
gardées pour une future itération) : Trade Cards générées automatiquement (réutilise
`chart_render.render_scenario_png`), Mode Replay d'une décision passée (`thesis_journal`/
`truth_ledger`), terminal gated abonnés pour interroger ARIA sur un token Base précis,
watchlist smart-money comme avantage abonné visible, client Kalshi (`blockrun_kalshi.py`,
construit, zéro appelant) comme 2e marché de prédiction, `insider_wallets.py`/
`deployer_history.py` comme produit x402 distinct, élargissement de `arena_signal.py`
au-delà du seul BTC, `liquidity_rotation.py` pour prioriser l'ordre d'évaluation des
candidats (jamais les seuils de décision), clustering Sybil par graphe complet (recherche
déjà faite le 15/07, jamais implémentée — le plus coûteux de la liste).

---

## Backlog actif (24 items pending au 02/08, TaskList #22/#32-#60)

| # | Sujet | Axe |
|---|---|---|
| #22 | Revérifier v2/v4/v5, supprimer si toujours inactives | 1 |
| #32 | Diagnostiquer wallet_scan_queue bloqué | 1/5 |
| #33 | Trancher le sort de unified_entry.py | 5 |
| #34 | Restaurer cadence Polymarket paper | 6 |
| #35 | Planifier session dédiée Smart Account CDP | 1/5 |
| #36 | Trancher veille Base #199 (ressource x402) | 4 |
| #37 | Rappel : désactiver Solana avant capital réel | 3 |
| #38 | Décider du sort des coupe-circuits désactivés | 3 |
| #39 | Suivre le reset hebdo du 08/08 (cycle tronqué) | 1 |
| #40 | Revérifier avancement wallet-scoring vers 500 | 5 |
| #41 | Diligence approfondie Clanker | 4 |
| #42 | Identifier un candidat de réduction dépendance API | 5 |
| #43 | Trancher mécanisme de paiement Mindshare Rewards | 7 |
| #44 | Creuser vulnérabilité adversariale on-chain | 3 |
| #45 | Diligence stack voix pour ARIA | 2 |
| #46 | Élargir le catalogue x402 vendable | 7 |
| #47 | Recharger le compte OpenRouter (Avocat du Diable cassé) | 6 |
| #48 | Séquencer le flip du routing LLM Anthropic | 5 |
| #49 | Reprendre le backlog agent-wallet #215-#230 | 1/3 |
| #50 | Trancher la tension architecturale dna.yaml | 2 |
| #51 | Permission de revente x402 (GoPlus/Blockscout/CabalSpy) | 7 |
| #52 | Revisiter Monad comme chaîne candidate | 4 |
| #53 | Scoper avatar HeyGen + cadence X gated | 2 |
| #54 | Décider du portail de custody, premier utilisateur externe | 1 |
| #55 | Scoper le registre public des refus | 2/4 |
| #56 | Scoper l'index différentiel ARIA vs marché | 1 |
| #57 | Scoper la narration "desks en compétition" | 2/4 |
| #58 | Brancher x402_trust_score.py comme 2e produit vendable | 7 |
| #59 | Pousser pump_dump_autopsy.py vers aria-brain | 2 |
| #60 | Scoper le "Wallet Passport" public | 2/4/7 |
