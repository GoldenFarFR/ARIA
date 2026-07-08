"""Preuve onchain d'ARIA — track-record inviolable ancré sur Base.

Le moat : chaque verdict est réduit à une empreinte dans un arbre de Merkle ; on ancre la
RACINE sur Base (horodatage inviolable). Quiconque peut ensuite prouver qu'un verdict donné
faisait partie de l'ensemble à cette date — sans que nous (ni personne) puissions le
backdater ou le réécrire. « Preuve avant promesse », rendue vérifiable.

- attestation.py : primitives Merkle PURES (stdlib, déterministes) — build racine, preuve,
  vérification. Aucune clé, aucun réseau.
- Le contrat `contracts/AriaLedger.sol` (racine du repo) stocke les racines ancrées.
- Le dernier maillon (signer/soumettre sur Base) est GATÉ : il exige une clé, donc la
  décision explicite de l'opérateur (cf. garde-fous clé privée).
"""
from .attestation import (  # noqa: F401
    canonical_record,
    merkle_proof,
    merkle_root,
    verify_proof,
)
