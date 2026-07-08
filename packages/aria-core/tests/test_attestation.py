"""Attestation Merkle — la preuve d'inviolabilité doit tenir sur tous les cas."""
import pytest

from aria_core.onchain.attestation import (
    canonical_record,
    merkle_proof,
    merkle_root,
    verify_proof,
)


def _verdicts(n: int) -> list[dict]:
    return [
        {"id": f"v{i}", "token": f"0x{i:040x}", "verdict": "AVOID" if i % 2 else "WATCH",
         "confidence": 0.5 + i / 100, "at": f"2026-07-08T00:{i:02d}:00Z"}
        for i in range(n)
    ]


def test_root_is_deterministic_and_order_sensitive():
    a = _verdicts(5)
    assert merkle_root(a) == merkle_root(list(a))          # même ensemble -> même racine
    assert merkle_root(a) != merkle_root(list(reversed(a)))  # l'ordre compte (position prouvée)


def test_canonical_stable_regardless_of_key_order():
    r1 = {"b": 2, "a": 1, "token": "0xabc"}
    r2 = {"token": "0xabc", "a": 1, "b": 2}
    assert canonical_record(r1) == canonical_record(r2)


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8, 9, 17, 100])
def test_proof_roundtrip_every_index(n):
    records = _verdicts(n)
    root = merkle_root(records)
    for i in range(n):
        proof = merkle_proof(records, i)
        assert verify_proof(records[i], proof, root) is True


def test_tamper_breaks_proof():
    """Modifier un verdict (même d'un chiffre) doit invalider sa preuve — c'est LA promesse."""
    records = _verdicts(6)
    root = merkle_root(records)
    proof = merkle_proof(records, 3)

    tampered = dict(records[3])
    tampered["verdict"] = "BUY"   # on maquille le verdict a posteriori
    assert verify_proof(tampered, proof, root) is False

    tampered2 = dict(records[3])
    tampered2["confidence"] = 0.99
    assert verify_proof(tampered2, proof, root) is False


def test_proof_from_other_set_fails():
    root_a = merkle_root(_verdicts(6))
    other = _verdicts(6)
    other[0]["id"] = "injected"
    proof = merkle_proof(other, 0)
    assert verify_proof(other[0], proof, root_a) is False  # pas dans l'ensemble ancré


def test_backdating_detected():
    """Ajouter un verdict après coup change la racine -> l'ancrage passé ne le couvre pas."""
    before = merkle_root(_verdicts(5))
    after = merkle_root(_verdicts(6))
    assert before != after


def test_out_of_range_index_raises():
    with pytest.raises(IndexError):
        merkle_proof(_verdicts(3), 5)


def test_empty_set_has_stable_root():
    assert merkle_root([]) == merkle_root([])
    assert isinstance(merkle_root([]), str) and merkle_root([]).startswith("0x")
