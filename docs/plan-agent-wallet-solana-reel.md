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

## Points ouverts qui appartiennent a l'operateur, pas a moi

- **La regle Solana de CLAUDE.md.** Elle dit, mot pour mot : desactiver Solana
  avant tout passage au capital reel, l'operateur ne finançant pas de wallet
  SOL, et re-verifier explicitement au moment de preparer la transition
  papier -> reel. C'est ce moment. Cette architecture Squads est justement
  concue pour Solana : la regle a probablement ete ecrite dans un autre
  contexte (le test papier multi-chaines), mais elle demande une
  reconfirmation explicite, pas une interpretation de ma part.
- **L'ecart d'audit AllowanceModule v0.1.1** (jambe EVM) reste ouvert.
- **Le plafond cible** (0.003 SOL aujourd'hui vs 200$ vise) est une decision
  d'operateur, pas un reglage technique.

## Faisabilite des 24h -- avis honnete

Les etapes 1 et 2 sont faisables aujourd'hui sans aucun risque. L'etape 3
(signature d'un swap) est un vrai chantier : Squads v4 n'a pas de SDK Python
officiel et chaque instruction a du etre construite a la main depuis la source
Rust. L'etape 4 depend d'un financement mainnet par l'operateur. 24h est
tenable pour un PREMIER swap reel de montant symbolique si les etapes 3 et 4
se passent sans surprise -- mais la journee a montre trois "constantes mappees
jamais lues" et deux correctifs qui s'annulaient : sur du capital reel, ce
type de surprise coute de l'argent, pas une mesure faussee.
