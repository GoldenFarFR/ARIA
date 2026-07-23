"""Confirmation de stabilité temporelle sur la liquidité — crible VC.

Point faible du stress-test (Codex Partie 11, item de priorité #3) : un scan
`safety_screen` lit la liquidité/volume de façon INSTANTANÉE. Une manipulation
temporaire synchronisée sur la fenêtre de scan (liquidité gonflée juste avant
le scan, retirée juste après) passerait le crible sans que rien ne le détecte
— une lecture unique ne peut, par construction, jamais prouver une stabilité
dans le temps.

Design retenu, honnête sur ses limites : contrairement au wash-trading momentum
(état en mémoire process, confirmé sur des scans répétés en boucle continue),
le crible VC n'est pas un cycle continu — un contrat peut n'être scanné qu'une
seule fois. La confirmation ne peut donc s'appliquer QUE si ce même contrat a
déjà été vu par un scan précédent, dans une fenêtre récente (persistée en base,
survit aux redémarrages, contrairement à un simple dict process). Sur un
PREMIER scan (aucun antécédent), le résultat est `None` (indéterminé) — jamais
un rejet sur une absence de donnée, même doctrine fail-open que le reste du
projet quand l'information manque pour juger.
"""
from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Chute de liquidité entre deux scans au-delà de laquelle on suspecte un
# retrait (pas juste la volatilité normale d'un marché fin) -- soft-fail,
# jamais un mécanisme confirmé dans le contrat (comportement de marché).
DEFAULT_MAX_DROP_PCT = 40.0
# Fenêtre dans laquelle un scan précédent est considéré pertinent pour la
# comparaison -- au-delà, un vrai mouvement de marché légitime (pas une
# manipulation synchronisée sur UN scan) devient plus probable.
DEFAULT_WINDOW_MINUTES = 60


@dataclass(frozen=True)
class LiquidityStabilityResult:
    confirmed: bool | None  # True=stable, False=chute suspecte, None=pas d'antécédent
    previous_liquidity_usd: float | None = None
    previous_recorded_at: str | None = None


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS vc_liquidity_snapshots (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                liquidity_usd REAL NOT NULL,
                volume_24h_usd REAL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        await db.commit()


async def record_and_check_liquidity_stability(
    contract: str,
    chain: str,
    liquidity_usd: float,
    volume_24h_usd: float | None = None,
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    max_drop_pct: float = DEFAULT_MAX_DROP_PCT,
) -> LiquidityStabilityResult:
    """Compare la liquidité actuelle au dernier snapshot connu (s'il existe et
    reste dans la fenêtre), PUIS enregistre le snapshot courant (upsert -- un
    seul snapshot gardé par contrat, le plus récent, pas un historique complet).

    Toujours enregistre le nouveau snapshot, même si aucune comparaison n'a été
    possible -- sans ça, un contrat jamais revu ne bénéficierait jamais de la
    protection au scan suivant."""
    contract_l = (contract or "").strip().lower()
    chain_l = (chain or "").strip().lower()
    if not contract_l or not chain_l or liquidity_usd is None or liquidity_usd < 0:
        return LiquidityStabilityResult(confirmed=None)

    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT liquidity_usd, recorded_at FROM vc_liquidity_snapshots "
                "WHERE contract = ? AND chain = ? "
                "AND recorded_at >= datetime('now', ?)",
                (contract_l, chain_l, f"-{window_minutes} minutes"),
            )
        ).fetchone()

        await db.execute(
            "INSERT INTO vc_liquidity_snapshots (contract, chain, liquidity_usd, volume_24h_usd, recorded_at) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(contract, chain) DO UPDATE SET "
            "liquidity_usd = excluded.liquidity_usd, volume_24h_usd = excluded.volume_24h_usd, "
            "recorded_at = excluded.recorded_at",
            (contract_l, chain_l, liquidity_usd, volume_24h_usd),
        )
        await db.commit()

    if row is None:
        return LiquidityStabilityResult(confirmed=None)

    previous = float(row["liquidity_usd"])
    if previous <= 0:
        return LiquidityStabilityResult(confirmed=None, previous_liquidity_usd=previous, previous_recorded_at=row["recorded_at"])

    drop_pct = 100.0 * (previous - liquidity_usd) / previous
    confirmed = drop_pct < max_drop_pct
    return LiquidityStabilityResult(
        confirmed=confirmed, previous_liquidity_usd=previous, previous_recorded_at=row["recorded_at"],
    )
