# Agent wallet Solana en reel -- etat verifie et ecart restant

> Redige le 21/08 a la demande de l'operateur ("prepare le terrain sur l'agent
> wallet sur solana... commencer a tester en reel d'ici 24h"). Rien n'est
> active par ce document. Chaque affirmation ci-dessous a ete verifiee dans le
> code ou en direct le jour meme, jamais citee de memoire.

## Principe directeur, pose par l'operateur

**"Il faut exactement les memes outils que la poche bonding."**

Consequence d'architecture, non negociable : le reel ne REECRIT pas la
strategie, il remplace UNIQUEMENT l'execution. Sourcing, filtres d'entree,
regle de sortie et lecture de prix restent le code partage deja calibre. Deux
copies divergeraient en silence et rendraient sans valeur toute la calibration
accumulee -- exactement le defaut que `evaluate_exit` partage entre les poches
existe deja pour empecher.

## Ce qui est PROUVE aujourd'hui (devnet, valeur nulle)

- Multisig Squads v4 cree, `SpendingLimit` actif (0.003 SOL).
- Transfert delegue REUSSI on-chain, et depassement REJETE par le contrat
  lui-meme (`6026: Spending limit exceeded`) -- le plafond est applique
  ON-CHAIN, pas seulement par notre Python.
- Passage par le vrai wrapper garde-fou (`homemade_agent_wallet.attempt_transfer`,
  `wallet_product=homemade_agent_wallet_solana`), journalise en base de prod.
- Module de signature committe et teste (`onchain/squads_solana_signer.py`),
  attente de `finalized` (une vraie condition de course trouvee et corrigee).

## L'ECART PRINCIPAL : transferer n'est pas acheter

`spending_limit_use` DEPLACE des lamports du coffre vers une destination. Il ne
swappe pas. Trader un memecoin exige SOL -> token puis token -> SOL, et aucun
chemin de swap n'existait dans le repo (verifie : zero occurrence de
jupiter/raydium/swap dans les modules Solana).

**Fait le 21/08** : `services/jupiter.py`, client de DEVIS uniquement --
aucune cle, aucune signature, aucun acces a l'endpoint de construction de
transaction (verrouille par un test AST, pas par un scan de texte : le scan de
texte se declenche sur la docstring du module lui-meme, piege deja paye sur
`safe_robinhood_simulation`). Slippage plafonne a 10% comme partout ailleurs.
Endpoint `lite-api.jup.ag` -- l'ancien `quote-api.jup.ag/v6` a ete teste en
direct le meme jour et ne repond plus, ecarte plutot que laisse en repli mort.
Debit : backoff reactif seulement, la limite du palier gratuit n'est pas
publiee et ce dome n'invente jamais un throttle chiffre.

**Benefice immediat, avant tout capital reel** : un devis Jupiter est ce
qu'une execution rendrait VRAIMENT maintenant. Les poches shadow valorisent
depuis les reserves de courbe avec un impact de prix MODELISE. Les deux
peuvent enfin etre compares -- si les remplissages simules sont optimistes,
cela se voit ici plutot qu'au premier trade reel. C'est aussi la reponse a la
seule inconnue majeure de la configuration deployee ce jour (la friction de
vente : a 1% elle laisse +5.1% de PnL hors outliers, a 5% elle l'annule).

## Ce qui reste, dans l'ordre

1. **Comparer devis Jupiter et prix simules** sur les positions shadow en
   cours. Zero risque, aucun capital, et cela valide ou invalide la friction
   de 1% retenue dans toute la calibration du jour.
2. **Rendre l'execution injectable dans la poche existante.** `consider_candidate`
   ecrit aujourd'hui directement un INSERT simule. Il lui faut un `execute_fn`
   optionnel, exactement comme `snapshot_fn`/`resolve_curves_fn` le sont deja,
   pour que le reel soit une INJECTION et non une copie du module.
3. **Signature du swap** (construction de transaction Jupiter + signature par
   la cle deleguee) -- premiere etape qui ecrit vraiment, donc etape a valider
   separement.
4. **Multisig + SpendingLimit sur MAINNET.** Exige du vrai SOL et donc une
   action de l'operateur. Le cap applicatif actuel (0.003 SOL) est l'ordre de
   grandeur devnet, pas la cible 200$ : le relever est une decision distincte.
5. **Cycle complet en mainnet a montant minimal** avant tout volume.

## Dimensionnement du portefeuille de test -- mesure, pas estime

Pic de positions ouvertes SIMULTANEMENT, mesure sur 407 positions reelles de
la configuration courante (6 heures) :

| | positions | capital immobilise | dont cautions |
|---|---|---|---|
| mediane | 5 | 1.45$ | 0.95$ |
| 90e centile | 29 | 8.41$ | 5.51$ |
| **pic observe** | **35** | **10.15$** | 6.65$ |
| plafond configure | 60 | 17.40$ | 11.40$ |

**Les 5$ evoques ne suffisent pas.** Ils couvrent largement la mediane mais
saturent des que l'activite monte -- et l'activite monte precisement quand il
y a des opportunites. Deux sorties possibles : financer ~10$ pour absorber le
pic observe, ou abaisser `MAX_CONCURRENT_TRACKED` pour tenir dans 5$ (environ
17 positions). Decision operateur, pas technique.

## Les ordres declenches Jupiter : la vraie sortie au depassement, a partir de 5$

Mesure du 21/08 sur 84 clotures au stop fixe -5% : la perte moyenne est de
11.4 points, decomposee en 5.0 de seuil, 5.8 de DEPASSEMENT avant observation
et 0.6 de friction. Mais la distribution est trompeuse -- depassement median
+1.8 point seulement, et 15 clotures (18%) portent 70% du total. Sur ces
15-la, la chute traverse le seuil en une seule transaction : il n'existe
AUCUN instant ou le prix vaut -5%, donc aucune vitesse de lecture ne peut les
intercepter. Sur une courbe a 7000$, une vente de ~700$ suffit a faire -20%
d'un coup.

**Le remede n'est donc pas la vitesse, c'est de ne plus avoir a reagir.**
L'API Trigger de Jupiter (limites, take-profit, stop-loss, stop suiveur
depuis 07/2026, ordres stockes hors chaine et prives -- ce qui ferme au
passage le vecteur MEV de l'ordre visible) executerait dans la meme
transaction que la vente qui fait chuter le prix.

**BLOQUANT VERIFIE EN DIRECT LE 21/08** : `createOrder` refuse tout ordre
sous 5 USD (`Order size must be at least 5 USD`). Consequences :
  - sous 5$/position, on est structurellement condamne a reagir et a subir
    le depassement -- ce n'est pas un defaut de notre code ;
  - a partir de 5$/position, la mecanique de sortie CHANGE : les paliers
    deviennent des take-profit poses d'avance, le stop fixe un vrai
    stop-loss on-chain, le delai de reaction disparait ;
  - **un test a 0.1$ ne prefigure donc PAS le fonctionnement a 5$** : ce ne
    sont pas les memes outils. Prevoir une SECONDE phase de validation au
    passage a 5$, distincte de la validation de plomberie.

## OBLIGATION de la chaine de vente : fermer le compte du token

Verifie le 21/08 sur l'endpoint `swap-instructions` de Jupiter : il fournit
bien une `cleanupInstruction`, mais elle ne couvre que le SOL temporairement
emballe pendant le swap. **Le compte du token lui-meme reste OUVERT apres une
vente totale, avec sa caution dedans.**

Sur Solana, detenir un token exige un compte dedie, et un compte doit
contenir un minimum de SOL pour exister (2 039 280 lamports, ~0.19$ au cours
du jour). C'est une caution, pas un frais : le reseau la rend integralement a
la fermeture du compte. Mais elle n'est PAS rendue automatiquement.

Consequence chiffree, au portefeuille de test de 5$ evoque par l'operateur :
15 positions tradees sans fermeture = 2.85$ immobilises definitivement, soit
plus de la moitie du capital rendue inutilisable sans qu'une seule mauvaise
transaction ait eu lieu.

**Donc la vente n'est pas terminee quand le swap passe : elle est terminee
quand le compte est ferme.** A traiter comme une etape obligatoire de la
chaine, jamais comme une optimisation ulterieure -- exactement la meme
discipline que "un deploiement n'est pas fini quand le script rend la main,
mais quand le commit reellement servi est verifie".

## Points ouverts qui appartiennent a l'operateur, pas a moi

- **La regle Solana : TRANCHEE le 21/08.** L'operateur l'a retiree lui-meme
  ("supprime cette regle en integrale, se sera plus simple qu'un gate on ou
  off") -- sa premisse, qu'il ne finance pas de wallet SOL, est devenue fausse
  au moment ou il a decide de financer le test a 5$. La borne est desormais le
  gate plus le solde du wallet, pas une interdiction de chaine. Plus rien a
  reconfirmer ici.
- **L'ecart d'audit AllowanceModule v0.1.1** (jambe EVM) reste ouvert.
- **Le plafond cible** (0.003 SOL aujourd'hui vs 200$ vise) est une decision
  d'operateur, pas un reglage technique.
- **L'architecture du portefeuille de test** (operateur, 21/08 : "brancher un
  semblant de portefeuille avec 5$ et de reels trades a 0.1$") : wallet simple
  a cle dediee, ou multisig Squads avec plafond on-chain ? Pour 5$ le wallet
  simple est defendable -- le plafond, c'est le solde -- et c'est deja le
  choix fait pour le pilote CDP. Mais la doctrine ecrite dit "smart wallets,
  pas EOA". Question posee, non tranchee par moi.
- **La taille de position pour la phase de test** : l'operateur vise 0.1$, la
  mesure du jour montre que 1$ donne le meme risque pratique avec des chiffres
  exploitables (a 0.1$ les frais fixes coutent 1.3 point de PnL et faussent la
  comparaison avec le shadow).

## Faisabilite des 24h -- avis honnete

Les etapes 1 et 2 sont faisables aujourd'hui sans aucun risque. L'etape 3
(signature d'un swap) est un vrai chantier : Squads v4 n'a pas de SDK Python
officiel et chaque instruction a du etre construite a la main depuis la source
Rust. L'etape 4 depend d'un financement mainnet par l'operateur. 24h est
tenable pour un PREMIER swap reel de montant symbolique si les etapes 3 et 4
se passent sans surprise -- mais la journee a montre trois "constantes mappees
jamais lues" et deux correctifs qui s'annulaient : sur du capital reel, ce
type de surprise coute de l'argent, pas une mesure faussee.
