"""Fixed watchlist -- the "megacap" pocket's candidate source (02/08, operator
request: "trouver des paires avec 100 million de mc minimum pour qu'il y ait
beaucoup de volume tous les jours" -- an A/B comparison arm against the 6
existing scan-large scalping pockets, never a replacement for them).

Deliberately NOT a reuse of ``manual_candidates.py``: that module's whole
purpose (per its own docstring) is a TRANSIENT discovery-priming queue --
TTL 7 days, opportunistic purge on every read, self-drains once bought. This
table's semantics are the OPPOSITE -- a PERMANENT list of 10 operator-curated
contracts, no TTL, no purge, never drained by a buy (the megacap pocket keeps
evaluating the same 10 tokens cycle after cycle). Bolting "never expire" onto
``manual_candidate_queue`` would have broken its dedup/cap assumptions
(``MAX_MANUAL_CANDIDATES_PER_CYCLE`` throttling) for no benefit -- a small
dedicated table is cleaner.

The 10 contracts (all Base -- DEFAULT_CHAINS=("base",) confirmed, no Solana
support in this pipeline today) were vetted this session: verified >=100M$
market cap (CoinGecko), real daily volume, GoPlus honeypot-checked. AAVE and
VIRTUAL carry owner_change_balance=True/is_mintable=True/hidden_owner=True --
see momentum_entry.py's _ESTABLISHED_TOKEN_SECURITY_ALLOWLIST_BASE for the
Basescan diligence (mint/burn gated to the canonical Base bridge) and the
operator's explicit go-ahead on that specific guardrail exception."""
from __future__ import annotations

import logging

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Recommended activation sequence (ronde 5 of this pocket's design review):
# start with only the 8 tokens that carry no security-guardrail exception,
# observe a few cycles, THEN add AAVE/VIRTUAL as a 2-line follow-up commit --
# isolates the one non-reversible risk (a global blacklist write, see
# momentum_entry.py) from the rest of this pocket's rollout. Both are
# included here now (implementation-complete per the approved plan); trimming
# to 8 for the first deploy, if desired, is an operator call at deploy time.
_DEFAULT_WATCHLIST: tuple[tuple[str, str, str], ...] = (
    ("LINK", "0x88fb150bdc53a65fe94dea0c9ba0a6daf8c6e196", "base"),
    ("KAITO", "0x98d0baa52b2d063e780de12f615f963fe8537553", "base"),
    ("ICP", "0x00f3c42833c3170159af4e92dbb451fb3f708917", "base"),
    ("ENA", "0x58538e6a46e07434d7e7375bc268d3cb839c0133", "base"),
    ("AAVE", "0x63706e401c06ac8513145b7687a14804d17f814b", "base"),
    ("VIRTUAL", "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b", "base"),
    ("WETH", "0x4200000000000000000000000000000000000006", "base"),
    ("CBBTC", "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf", "base"),
    ("WBTC", "0x0555e30da8f98308edb960aa94c0db47230d2b9c", "base"),
    ("CBETH", "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22", "base"),
)


def _normalize_contract(contract: str, chain: str) -> str:
    """Same case-handling as manual_candidates.py/momentum_blacklist.py
    (Base/EVM tolerates lowercase, Solana base58 does not) -- duplicated
    rather than imported, same anti-circular-import doctrine already
    documented there."""
    contract = (contract or "").strip()
    if (chain or "").strip().lower() != "solana":
        contract = contract.lower()
    return contract


async def _ensure_table() -> None:
    """Idempotent create + seed -- runs on every call (cheap, no separate
    migration script to remember), same self-seeding idiom as paper_state's
    swing/vc rows. INSERT OR IGNORE never resets an already-present row's
    added_at, and never touches rows added later via add_watchlist_candidate
    (extensibility hook, not currently exercised by any caller)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS fixed_watchlist (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                symbol TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        for symbol, contract, chain in _DEFAULT_WATCHLIST:
            normalized = _normalize_contract(contract, chain)
            await db.execute(
                "INSERT OR IGNORE INTO fixed_watchlist (contract, chain, symbol, added_at) "
                "VALUES (?, ?, ?, datetime('now'))",
                (normalized, chain, symbol),
            )
        await db.commit()


async def add_watchlist_candidate(symbol: str, contract: str, chain: str = "base") -> bool:
    """Extensibility hook -- not currently called anywhere, kept for a future
    operator-driven addition to the fixed list without a code change to
    _DEFAULT_WATCHLIST. INSERT OR IGNORE, same dedup semantics as the seed."""
    await _ensure_table()
    chain = (chain or "base").strip().lower()
    contract = _normalize_contract(contract, chain)
    if not contract or not chain:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO fixed_watchlist (contract, chain, symbol, added_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (contract, chain, symbol),
        )
        await db.commit()
    return True


async def list_watchlist_candidates() -> list[dict]:
    """Every contract on the fixed watchlist -- called once per discovery
    cycle (heartbeat) and once per WebSocket drain pass. Deliberately NEVER
    purges/expires anything (contrast with manual_candidates.py's TTL) --
    this is a permanent list, not a discovery-priming queue."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute("SELECT * FROM fixed_watchlist ORDER BY added_at ASC")
        ).fetchall()
    return [dict(r) for r in rows]
