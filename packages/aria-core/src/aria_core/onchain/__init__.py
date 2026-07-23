"""ARIA's onchain proof — tamper-proof track record anchored on Base.

The moat: each verdict is reduced to a fingerprint in a Merkle tree; the ROOT
is anchored on Base (tamper-proof timestamping). Anyone can then prove that a
given verdict was part of the set at that date — without us (or anyone else)
being able to backdate or rewrite it. "Proof before promise," made verifiable.

- attestation.py: PURE Merkle primitives (stdlib, deterministic) — build
  root, proof, verification. No key, no network.
- The `contracts/AriaLedger.sol` contract (repo root) stores the anchored
  roots.
- The last link (signing/submitting on Base) is GATED: it requires a key,
  hence the operator's explicit decision (see private-key guard-rails).
"""
from .attestation import (  # noqa: F401
    canonical_record,
    merkle_proof,
    merkle_root,
    verify_proof,
)
