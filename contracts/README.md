# AriaLedger — preuve onchain du track-record (Base)

`AriaLedger.sol` stocke la **racine de Merkle** de l'ensemble des verdicts ARIA. On l'ancre
périodiquement (ex. quotidiennement) sur Base : chaque ancrage est horodaté par le bloc, ce
qui rend le track-record **inviolable** (impossible de backdater/réécrire un verdict passé
sans casser sa preuve d'appartenance, vérifiée hors chaîne via `aria_core.onchain.attestation`).

## Ce qui est déjà livré (sans clé, testé)
- Primitives Merkle : `aria_core/onchain/attestation.py` (racine, preuve, vérification).
- Contrat minimal `AriaLedger.sol` (aucun transfert de valeur ; seul l'owner ancre).
- Tests d'inviolabilité : `packages/aria-core/tests/test_attestation.py`.

## Le dernier maillon est GATÉ (décision opérateur requise)
Déployer + ancrer = **signer une transaction sur Base**, donc utiliser une **clé**. Règle
ARIA : « clé privée jamais sur le serveur ». Ancrer un *hash* n'est pas un trade (aucun
transfert de valeur, juste du gas), mais reste une signature. Trois options — à trancher
AVANT tout câblage de signature :

1. **Ancrage manuel/local** — l'opérateur signe depuis son PC, périodiquement. Zéro clé sur
   le VPS. Le plus sûr.
2. **Clé « attestation » dédiée** — minuscule (quelques centimes d'ETH Base), isolée de tout
   trésor, signature gatée. Automatisable.
3. **Relayer tiers** — pas de clé chez nous, dépendance externe.

## Étapes de mise en service (une fois l'option choisie)
1. Compiler/déployer `AriaLedger` sur **Base Sepolia** (testnet, gratuit) pour la phase de
   preuve, puis **Base mainnet**.
2. Brancher un service d'ancrage : construire la racine du jour depuis le ledger de verdicts
   (`attestation.merkle_root`), signer selon l'option retenue, appeler `anchor(root)`.
3. Page de vérification sur la vitrine : coller un verdict + sa preuve → vérifier contre la
   racine ancrée on-chain (public, inviolable).

Rien n'est déployé tant que l'option de clé n'est pas validée.
