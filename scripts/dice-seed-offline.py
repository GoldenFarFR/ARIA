#!/usr/bin/env python3
"""Turn physical dice rolls into a BIP39 24-word seed -- OFFLINE ONLY.

Operator decision 21/08: the recovery key's entropy must come from the
operator's own hand (dice), never from a software RNG. This script does NOT
generate randomness -- it only converts the dice rolls it is given. Garbage in,
garbage out: fewer than 99 honest rolls means a weaker key, and the script
refuses rather than pads.

Usage, on a machine DISCONNECTED from any network:
    python3 dice-seed-offline.py

  1. It asks for 99 dice rolls (digits 1-6), typed in groups as you go.
  2. It prints 24 words. Write them on PAPER, twice. Never photograph them,
     never type them into anything online.
  3. Power the machine off afterwards; nothing is written to disk.

Standard BIP39 English wordlist required next to this file as
`bip39-english.txt` (2048 words, one per line -- from the reference
repository, checksum printed for verification).

Why 99 rolls: each roll carries log2(6) ~ 2.585 bits, 99 rolls ~ 256 bits,
the full strength of a 24-word seed.
"""

import hashlib
import hmac
import sys
import unicodedata
from pathlib import Path

# --- Solana address derivation, pure stdlib ---------------------------------
# Same path Phantom uses (BIP44 m/44'/501'/0'/0', SLIP-0010 ed25519), so the
# address printed here is EXACTLY the one Phantom would show for these words.
# Ed25519 public-key math is the RFC 8032 reference construction; both layers
# are pinned by tests on the repo side (SLIP-0010 official vector + solders).

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_BX = 15112221349535400772501151409588531511454012693041857206046113283949847762202
_BY = 46316835694926478169428394003475163141307993866256225615783033603165251855960


def _edwards_add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    aa = (y1 - x1) * (y2 - x2) % _P
    bb = (y1 + x1) * (y2 + x2) % _P
    cc = 2 * t1 * t2 * _D % _P
    dd = 2 * z1 * z2 % _P
    e, f, g, h = bb - aa, dd - cc, dd + cc, bb + aa
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _scalar_mult_base(k):
    q = (0, 1, 1, 0)
    pnt = (_BX % _P, _BY % _P, 1, _BX * _BY % _P)
    while k:
        if k & 1:
            q = _edwards_add(q, pnt)
        pnt = _edwards_add(pnt, pnt)
        k >>= 1
    x, y, z, _ = q
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _ed25519_pubkey(seed32: bytes) -> bytes:
    h = hashlib.sha512(seed32).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return _scalar_mult_base(a)


def _slip10_master(seed: bytes):
    h = hmac.new(b"ed25519 seed", seed, hashlib.sha512).digest()
    return h[:32], h[32:]


def _slip10_child(key, chain, index):
    data = b"\x00" + key + (index | 0x80000000).to_bytes(4, "big")
    h = hmac.new(chain, data, hashlib.sha512).digest()
    return h[:32], h[32:]


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + out


def solana_address(mnemonic: str) -> str:
    norm = unicodedata.normalize("NFKD", mnemonic)
    seed = hashlib.pbkdf2_hmac("sha512", norm.encode(), b"mnemonic", 2048)
    key, chain = _slip10_master(seed)
    for idx in (44, 501, 0, 0):  # m/44'/501'/0'/0'
        key, chain = _slip10_child(key, chain, idx)
    return _base58(_ed25519_pubkey(key))

ROLLS_NEEDED = 99


def main() -> None:
    wl_path = Path(__file__).parent / "bip39-english.txt"
    if not wl_path.exists():
        sys.exit("bip39-english.txt missing next to this script -- copy it there first.")
    words = wl_path.read_text(encoding="utf-8").split()
    if len(words) != 2048:
        sys.exit(f"wordlist has {len(words)} entries, expected 2048 -- wrong file.")
    digest = hashlib.sha256(
        unicodedata.normalize("NFKD", "\n".join(words)).encode()).hexdigest()
    print(f"wordlist sha256: {digest[:16]}...  (compare with the reference)")

    print(f"\nEnter your {ROLLS_NEEDED} dice rolls (digits 1-6, spaces/newlines ignored).")
    rolls: list[int] = []
    while len(rolls) < ROLLS_NEEDED:
        chunk = input(f"[{len(rolls)}/{ROLLS_NEEDED}] > ").strip()
        for ch in chunk:
            if ch in "123456":
                rolls.append(int(ch))
            elif ch not in " \t":
                print(f"  ignored '{ch}' (only 1-6 count)")
        if len(rolls) > ROLLS_NEEDED:
            rolls = rolls[:ROLLS_NEEDED]

    # Rolls -> integer (base 6) -> 256 bits. Modulo bias is negligible here:
    # 6^99 / 2^256 leaves a bias far below any practical attack, and hashing
    # would hide the operator's own entropy behind a machine step -- exactly
    # what this procedure exists to avoid.
    n = 0
    for r in rolls:
        n = n * 6 + (r - 1)
    entropy = (n % (1 << 256)).to_bytes(32, "big")

    checksum = hashlib.sha256(entropy).digest()
    bits = bin(int.from_bytes(entropy, "big"))[2:].zfill(256) + \
        bin(checksum[0])[2:].zfill(8)
    seed_words = [words[int(bits[i:i + 11], 2)] for i in range(0, 264, 11)]

    print("\n=== YOUR 24 WORDS -- PAPER ONLY, TWO COPIES, TWO PLACES ===\n")
    for i, w in enumerate(seed_words, 1):
        print(f"  {i:2d}. {w}")
    address = solana_address(" ".join(seed_words))
    print(f"\nSolana address (PUBLIC -- this one you can share): {address}")
    print("\nVerify you copied the words correctly, then power this machine off.")


if __name__ == "__main__":
    main()
