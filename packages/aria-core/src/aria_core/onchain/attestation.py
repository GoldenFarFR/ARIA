"""Merkle tree of verdicts — PURE, deterministic primitives, no dependency.

Each verdict is hashed into a leaf, a Merkle tree is built, and the ROOT
summarizes the whole set in 32 bytes. Anchoring this root on Base (timestamp) makes the
track record tamper-proof: modifying/backdating a single verdict changes the root, thereby
breaking the proof of membership.

Technical choices:
- SHA-256 (stdlib): level A = we anchor the root, verification happens OFF-chain
  (we rehash). No keccak dependency. (Switch to keccak256 only if we ever want to
  verify a proof INSIDE the contract.)
- Domain separation: 0x00 prefix for leaves, 0x01 for internal nodes
  (prevents a leaf/node second-preimage attack).
- Odd number of leaves: the last one is duplicated (classic scheme).
- Stable JSON canonicalization (sorted keys, fixed separators): two machines produce
  the same root for the same set.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

_LEAF = b"\x00"
_NODE = b"\x01"


def canonical_record(record: dict[str, Any]) -> bytes:
    """STABLE canonical serialization of a verdict (sorted keys, compact, UTF-8)."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _leaf_hash(record: dict[str, Any]) -> bytes:
    return hashlib.sha256(_LEAF + canonical_record(record)).digest()


def _node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(_NODE + left + right).digest()


def _levels(leaves: list[bytes]) -> list[list[bytes]]:
    """Builds all levels of the tree, from the bottom (leaves) to the top (root)."""
    if not leaves:
        # Root of an empty set = conventional (deterministic) hash.
        return [[hashlib.sha256(_NODE).digest()]]
    levels = [leaves]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt: list[bytes] = []
        for i in range(0, len(cur), 2):
            left = cur[i]
            right = cur[i + 1] if i + 1 < len(cur) else cur[i]  # odd -> duplicate
            nxt.append(_node_hash(left, right))
        levels.append(nxt)
    return levels


def merkle_root(records: list[dict[str, Any]]) -> str:
    """Merkle root (hex 0x…) of the ordered set of verdicts."""
    leaves = [_leaf_hash(r) for r in records]
    return "0x" + _levels(leaves)[-1][0].hex()


def merkle_proof(records: list[dict[str, Any]], index: int) -> list[tuple[str, bool]]:
    """Proof of membership for verdict `index`: list of (sibling_hex, sibling_is_right)."""
    if not 0 <= index < len(records):
        raise IndexError("verdict index out of bounds")
    leaves = [_leaf_hash(r) for r in records]
    levels = _levels(leaves)
    proof: list[tuple[str, bool]] = []
    idx = index
    for level in levels[:-1]:  # up to the level below the root
        pair = idx ^ 1  # sibling index
        sibling = level[pair] if pair < len(level) else level[idx]  # odd -> itself
        proof.append(("0x" + sibling.hex(), pair > idx))
        idx //= 2
    return proof


def verify_proof(record: dict[str, Any], proof: list[tuple[str, bool]], root: str) -> bool:
    """True if `record` really belongs to the set summarized by `root`, via `proof`."""
    h = _leaf_hash(record)
    for sibling_hex, sibling_is_right in proof:
        try:
            sibling = bytes.fromhex(sibling_hex.removeprefix("0x"))
        except ValueError:
            return False
        h = _node_hash(h, sibling) if sibling_is_right else _node_hash(sibling, h)
    return ("0x" + h.hex()) == (root or "").lower()
