"""Arbre de Merkle des verdicts — primitives PURES, déterministes, sans dépendance.

On hache chaque verdict en une feuille, on construit un arbre de Merkle, et la RACINE
résume tout l'ensemble en 32 octets. Ancrer cette racine sur Base (horodatage) rend le
track-record inviolable : modifier/backdater un seul verdict change la racine, donc casse
la preuve d'appartenance.

Choix techniques :
- SHA-256 (stdlib) : niveau A = on ancre la racine, la vérification se fait HORS chaîne
  (on rehash). Pas de dépendance keccak. (Passer à keccak256 seulement si un jour on veut
  vérifier une preuve DANS le contrat.)
- Séparation de domaine : préfixe 0x00 pour les feuilles, 0x01 pour les nœuds internes
  (empêche une attaque de second pré-image feuille/nœud).
- Nombre impair de feuilles : on duplique la dernière (schéma classique).
- Canonicalisation JSON stable (clés triées, séparateurs fixes) : deux machines produisent
  la même racine pour le même ensemble.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

_LEAF = b"\x00"
_NODE = b"\x01"


def canonical_record(record: dict[str, Any]) -> bytes:
    """Sérialisation canonique STABLE d'un verdict (clés triées, compact, UTF-8)."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _leaf_hash(record: dict[str, Any]) -> bytes:
    return hashlib.sha256(_LEAF + canonical_record(record)).digest()


def _node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(_NODE + left + right).digest()


def _levels(leaves: list[bytes]) -> list[list[bytes]]:
    """Construit tous les niveaux de l'arbre, du bas (feuilles) au sommet (racine)."""
    if not leaves:
        # Racine d'un ensemble vide = hash conventionnel (déterministe).
        return [[hashlib.sha256(_NODE).digest()]]
    levels = [leaves]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt: list[bytes] = []
        for i in range(0, len(cur), 2):
            left = cur[i]
            right = cur[i + 1] if i + 1 < len(cur) else cur[i]  # impair -> duplique
            nxt.append(_node_hash(left, right))
        levels.append(nxt)
    return levels


def merkle_root(records: list[dict[str, Any]]) -> str:
    """Racine de Merkle (hex 0x…) de l'ensemble ordonné de verdicts."""
    leaves = [_leaf_hash(r) for r in records]
    return "0x" + _levels(leaves)[-1][0].hex()


def merkle_proof(records: list[dict[str, Any]], index: int) -> list[tuple[str, bool]]:
    """Preuve d'appartenance du verdict `index` : liste de (sibling_hex, sibling_is_right)."""
    if not 0 <= index < len(records):
        raise IndexError("index de verdict hors bornes")
    leaves = [_leaf_hash(r) for r in records]
    levels = _levels(leaves)
    proof: list[tuple[str, bool]] = []
    idx = index
    for level in levels[:-1]:  # jusqu'au niveau sous la racine
        pair = idx ^ 1  # index du frère
        sibling = level[pair] if pair < len(level) else level[idx]  # impair -> soi-même
        proof.append(("0x" + sibling.hex(), pair > idx))
        idx //= 2
    return proof


def verify_proof(record: dict[str, Any], proof: list[tuple[str, bool]], root: str) -> bool:
    """Vrai si `record` appartient bien à l'ensemble résumé par `root`, via `proof`."""
    h = _leaf_hash(record)
    for sibling_hex, sibling_is_right in proof:
        try:
            sibling = bytes.fromhex(sibling_hex.removeprefix("0x"))
        except ValueError:
            return False
        h = _node_hash(h, sibling) if sibling_is_right else _node_hash(sibling, h)
    return ("0x" + h.hex()) == (root or "").lower()
