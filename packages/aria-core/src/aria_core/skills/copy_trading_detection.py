"""Détection copy-trading / bot — un wallet qui suit systématiquement un AUTRE
wallet déjà scoré plutôt que de décider seul.

Design validé avec l'opérateur (22/07, après vérification indépendante d'une
proposition externe attribuée à "Grok" v2) : ``composite_percentile``
(`smart_money.py`) reste PUREMENT une mesure de PERFORMANCE — jamais mélangé à
ce signal (Option 1, retenue explicitement). La détection de copy-trading est
un flag SÉPARÉ, purement consultatif, jamais une correction du score composite
lui-même (2 wallets à gros score dominent toujours 10 wallets à score faible,
cf. `analyze_smart_money`/CLAUDE.md 22/07 — ce flag ne change rien à ça).

Mécanique — GRATUIT, pas de collecte dédiée : chaque scan de wallet
(`smart_money._analyze_wallet_multi_token`) enregistre l'horodatage de sa
première entrée sur chaque token qu'il analyse déjà pour calculer le critère
"early entry" — `record_entry()` est un simple sous-produit de ce calcul
existant, zéro appel réseau supplémentaire. Une requête de corrélation (jointure
de la table sur elle-même) détecte ensuite un wallet qui entre systématiquement
dans une fenêtre courte (5-15 min) APRÈS un autre wallet déjà scoré, sur
PLUSIEURS tokens distincts — signe de copie/bot plutôt que de conviction
indépendante. Un chevauchement isolé sur un seul token n'est jamais suspect
(deux wallets indépendants peuvent légitimement réagir à la même annonce
publique) ; le seuil de tokens distincts élimine ce bruit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Fenêtre après l'entrée d'un autre wallet dans laquelle une entrée est
# considérée comme une COPIE possible. Repris tel quel du design validé
# opérateur (22/07) — 5 min : trop court serait indiscernable d'un carnet
# d'ordres réactif normal ; 15 min : au-delà, la corrélation temporelle
# s'affaiblit trop pour rester un signal fiable.
_COPY_WINDOW_MIN_SECONDS = 5 * 60
_COPY_WINDOW_MAX_SECONDS = 15 * 60
# Nombre de tokens DISTINCTS sur lesquels le pattern doit se répéter avant de
# suspecter un copy-trading systématique — jamais sur un seul token, une
# coïncidence isolée n'est pas un pattern.
_MIN_DISTINCT_TOKENS_FOR_SUSPICION = 3


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_entry_timestamps (
                wallet TEXT NOT NULL,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                entry_ts TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (wallet, contract, chain)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_wallet_entry_contract_chain "
            "ON wallet_entry_timestamps (contract, chain, entry_ts)"
        )
        await db.commit()


async def record_entry(wallet: str, contract: str, chain: str, entry_ts: datetime) -> None:
    """Enregistre l'horodatage de première entrée d'un wallet sur un token —
    idempotent (upsert), une paire wallet/contract/chain n'est jamais dupliquée.
    Défensif par construction : l'appelant (`smart_money.py`) avale déjà toute
    exception, mais une entrée mal formée est ignorée silencieusement ici aussi
    plutôt que de risquer une écriture corrompue."""
    wallet_l = (wallet or "").strip().lower()
    contract_l = (contract or "").strip().lower()
    chain_l = (chain or "").strip().lower()
    if not wallet_l or not contract_l or not chain_l or entry_ts is None:
        return
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_entry_timestamps (wallet, contract, chain, entry_ts, recorded_at) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(wallet, contract, chain) DO UPDATE SET entry_ts = excluded.entry_ts, "
            "recorded_at = excluded.recorded_at",
            (wallet_l, contract_l, chain_l, entry_ts.isoformat()),
        )
        await db.commit()


@dataclass(frozen=True)
class CopyTradingFacts:
    distinct_tokens_followed: int = 0
    followed_wallets: list[str] = field(default_factory=list)
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class CopyTradingVerdict:
    flag: str  # copy_trading_suspected / independent / unknown
    points: list[str] = field(default_factory=list)


def judge_copy_trading(facts: CopyTradingFacts) -> CopyTradingVerdict:
    """Jugement pur et déterministe — même doctrine que dev_wallet.py/
    insider_wallets.py : jamais un rejet automatique, un flag consultatif de plus."""
    if not facts.available:
        return CopyTradingVerdict(flag="unknown", points=[facts.error or "historique d'entrées non analysable"])
    if facts.distinct_tokens_followed < _MIN_DISTINCT_TOKENS_FOR_SUSPICION:
        return CopyTradingVerdict(
            flag="independent",
            points=[f"entrées corrélées sur {facts.distinct_tokens_followed} token(s), sous le seuil de suspicion"],
        )
    return CopyTradingVerdict(
        flag="copy_trading_suspected",
        points=[
            f"entre systématiquement {_COPY_WINDOW_MIN_SECONDS // 60}-{_COPY_WINDOW_MAX_SECONDS // 60} min après "
            f"{len(facts.followed_wallets)} wallet(s) déjà scoré(s), sur {facts.distinct_tokens_followed} tokens "
            "distincts -- possible copy-trading/bot plutôt que conviction indépendante"
        ],
    )


async def gather_copy_trading_facts(wallet: str, chain: str = "base") -> CopyTradingFacts:
    """Corrèle les entrées de ``wallet`` contre celles de TOUS les autres wallets
    déjà enregistrés sur la même chaîne — une seule requête (jointure de la table
    sur elle-même), pas de N+1. Toute indisponibilité -> ``available=False``,
    jamais un flag déduit d'une corrélation partielle/incertaine."""
    wallet_l = (wallet or "").strip().lower()
    chain_l = (chain or "").strip().lower()
    if not wallet_l or not chain_l:
        return CopyTradingFacts(available=False, error="wallet ou chaîne manquant")
    await _ensure_table()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT a.contract AS contract, b.wallet AS followed_wallet
                    FROM wallet_entry_timestamps a
                    JOIN wallet_entry_timestamps b
                      ON a.contract = b.contract AND a.chain = b.chain AND a.wallet != b.wallet
                    WHERE a.wallet = ? AND a.chain = ?
                      AND (julianday(a.entry_ts) - julianday(b.entry_ts)) * 86400 BETWEEN ? AND ?
                    """,
                    (wallet_l, chain_l, _COPY_WINDOW_MIN_SECONDS, _COPY_WINDOW_MAX_SECONDS),
                )
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return CopyTradingFacts(available=False, error=f"corrélation indisponible ({exc})")

    distinct_tokens = {r["contract"] for r in rows}
    followed = sorted({r["followed_wallet"] for r in rows})
    return CopyTradingFacts(
        distinct_tokens_followed=len(distinct_tokens), followed_wallets=followed, available=True,
    )
