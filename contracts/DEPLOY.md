# Déploiement AriaLedger sur Base — runbook opérateur (signature LOCALE)

> `AriaLedger.sol` ancre la racine Merkle du track-record d'ARIA sur Base : `anchor(bytes32 root)`
> `onlyOwner`, **aucun transfert de valeur**. Le serveur ne détient AUCUNE clé : il PRÉPARE la
> demande (`aria_core/onchain/anchor.py`), tu **signes et diffuses en LOCAL**. Le tir est ton geste.

## Prérequis (sur TA machine, jamais le serveur)
- Un wallet de déploiement avec un peu d'**ETH sur Base** (le gas d'un `anchor` est minime).
- `foundry` (`forge`, `cast`) ou `hardhat`. Exemples ci-dessous en foundry.
- **Recommandé (archi option 2)** : une **clé d'attestation dédiée**, à faible privilège, sans
  fonds au repos, réservée aux ancrages. Jamais la clé du wallet principal.

## 1. Déployer le contrat (une fois)
```bash
# testnet d'abord (Base Sepolia, chain 84532) pour valider a moindre cout
forge create contracts/AriaLedger.sol:AriaLedger \
  --rpc-url https://sepolia.base.org \
  --private-key $LOCAL_DEPLOYER_KEY

# puis mainnet Base (chain 8453) quand c'est validé
forge create contracts/AriaLedger.sol:AriaLedger \
  --rpc-url https://mainnet.base.org \
  --private-key $LOCAL_DEPLOYER_KEY
```
Note l'**adresse déployée** (`Deployed to: 0x…`).

## 2. Configurer ARIA (côté hôte)
```bash
ARIA_ONCHAIN_ANCHOR_ENABLED=1
ARIA_LEDGER_ADDRESS=0x…            # adresse du contrat déployé
# ARIA_ONCHAIN_CHAIN_ID=84532      # optionnel : rester sur testnet un temps
```
Le serveur peut désormais PRÉPARER des demandes d'ancrage. Il ne signe toujours rien.

## 3. Ancrer une racine (à chaque jalon du track-record)
ARIA fournit la demande (`build_anchor_request` → racine + instruction). Toi, en local :
```bash
cast send $ARIA_LEDGER_ADDRESS "anchor(bytes32)" $ROOT \
  --rpc-url https://mainnet.base.org \
  --private-key $LOCAL_ATTESTATION_KEY
```
`$ROOT` = la racine `0x…` (bytes32) donnée par ARIA. La transaction émet `Anchored(root, ...)`.

## Garde-fous (ne pas contourner)
- **Clé jamais sur le serveur.** La signature se fait ici, en local (ou via un relais qui garde
  la clé hors serveur). `anchor.py` est incapable de signer ou d'émettre une transaction.
- **Aucun transfert de valeur** dans le contrat : `anchor` ne bouge aucun fonds.
- **Gaté** : sans `ARIA_ONCHAIN_ANCHOR_ENABLED` + adresse, rien ne se prépare (fail-closed).
