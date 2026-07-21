"""Sélection des candidats pour l'extraction de holders (21/07, demande opérateur
explicite) -- remplace la source précédente (``screened_token``, le pool VC-thesis)
par le flux de découverte DÉJÀ construit pour le pipeline momentum
(``momentum_entry.discover_momentum_candidates``, DexScreener boosts/profils +
GeckoTerminal Base, continu, sans filtre de sécurité en amont).

Flux exact demandé par l'opérateur :
  1. Découverte brute (DexScreener + GeckoTerminal, ``discover_momentum_candidates``).
  2. Filtre : GoPlus (honeypot, ``momentum_entry.check_honeypot`` -- même garde-fou dur
     fail-closed que le pipeline de trading, jamais une version allégée) + liquidité
     ≥50 000$ + volume 24h ≥1 000$ (les MÊMES seuils que le pipeline momentum --
     "ça bouge beaucoup à cette faible liquidité", zone volatile délibérément ciblée).
  3. OK → éligible pour l'extraction Blockscout x402.
  4. Pas OK (honeypot confirmé) → liste noire PERMANENTE (``token_candidate_blacklist``,
     même doctrine que ``momentum_blacklist.py``/``smart_money_rejected_wallets`` --
     aucune fonction symétrique de retrait, jamais retesté).
     Liquidité/volume insuffisants → PAS blacklisté (peut grossir et redevenir
     éligible plus tard) -- seul un signal de sécurité CONFIRMÉ (honeypot) est
     permanent, jamais un simple manque de traction actuel.
  5. Déjà extrait (``token_holder_intel``) → ignoré, jamais recompté ni reblacklisté.

Distinct de ``momentum_blacklist.py`` (contrats bannis du pipeline de TRADING pour
wash-trading confirmé) : ici on bannit des candidats pour l'EXTRACTION de holders --
même risque (honeypot), contexte différent, table séparée pour ne jamais confondre
les deux mécanismes en le relisant plus tard."""
from __future__ import annotations

import logging
import os

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Chaînes couvertes par ce screening -- Base uniquement pour l'instant, cohérent
# avec token_holder_extraction_cycle.py (Blockscout x402 vérifié sur Base
# uniquement à ce jour).
_CHAIN = "base"


def _normalize_contract(contract: str) -> str:
    return (contract or "").strip().lower()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS token_candidate_blacklist (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                reason TEXT NOT NULL,
                blacklisted_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        await db.commit()


async def is_candidate_blacklisted(contract: str, chain: str = _CHAIN) -> bool:
    await _ensure_table()
    contract = _normalize_contract(contract)
    chain = (chain or "").strip().lower()
    if not contract:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM token_candidate_blacklist WHERE contract = ? AND chain = ?",
                (contract, chain),
            )
        ).fetchone()
    return row is not None


async def _blacklist_candidate(contract: str, chain: str, reason: str) -> None:
    """Permanent -- même doctrine que ``momentum_blacklist.py`` (contrats de trading)
    et ``smart_money_leaderboard.mark_rejected`` (wallets) : aucune fonction
    symétrique de retrait, un candidat confirmé dangereux le reste."""
    await _ensure_table()
    contract = _normalize_contract(contract)
    chain = (chain or "").strip().lower()
    if not contract or not chain:
        return
    from datetime import datetime, timezone

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO token_candidate_blacklist (contract, chain, reason, blacklisted_at) "
            "VALUES (?, ?, ?, ?)",
            (contract, chain, reason, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def list_blacklisted_candidates(limit: int = 100) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT contract, chain, reason, blacklisted_at FROM token_candidate_blacklist "
                "ORDER BY blacklisted_at DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def screen_and_select_candidates(limit: int) -> list[tuple[str, str]]:
    """Découvre (DexScreener/GeckoTerminal), filtre (GoPlus + liquidité/volume),
    blackliste les échecs de sécurité confirmés, et retourne jusqu'à ``limit``
    candidats prêts pour l'extraction de holders -- ``[(contract, symbol), ...]``,
    même forme que l'ancienne source ``_select_next_tokens``/``screened_token``
    pour un branchement direct (drop-in) dans ``token_holder_extraction_cycle.py``.

    Best-effort à chaque étape (une panne de découverte/filtre sur UN candidat ne
    bloque jamais les autres, cf. dôme standard du reste du pipeline momentum)."""
    from aria_core import token_holder_intel
    from aria_core.momentum_entry import (
        _MIN_LIQUIDITY_USD,
        _MIN_VOLUME_24H_USD,
        check_honeypot,
        discover_momentum_candidates,
    )
    from aria_core.services.dexscreener import fetch_tokens_batch

    try:
        raw = await discover_momentum_candidates(chains=(_CHAIN,))
    except Exception as exc:  # noqa: BLE001
        logger.info("token_candidate_screening: découverte échouée (%s)", exc)
        return []
    if not raw:
        return []

    already_extracted = {
        c["contract"].lower() for c in await token_holder_intel.list_extracted_contracts(_CHAIN)
    }

    candidates: list[dict] = []
    for c in raw:
        addr = c["contract"].lower()
        if addr in already_extracted:
            continue
        if await is_candidate_blacklisted(addr, _CHAIN):
            continue
        candidates.append(c)
    if not candidates:
        return []

    # Enrichissement par lot (DexScreener, gratuit, 30 adresses/appel) -- liquidité
    # + volume réels pour le filtre, jamais devinés depuis le pré-filtre de découverte
    # (qui ne checke QUE la liquidité, jamais le volume).
    pairs_by_contract: dict[str, object] = {}
    for i in range(0, len(candidates), 30):
        chunk = [c["contract"] for c in candidates[i : i + 30]]
        try:
            pairs = await fetch_tokens_batch(chunk, chain=_CHAIN)
        except Exception as exc:  # noqa: BLE001
            logger.info("token_candidate_screening: enrichissement DexScreener échoué (%s)", exc)
            continue
        for p in pairs:
            addr = (p.base_address or "").lower()
            if not addr:
                continue
            existing = pairs_by_contract.get(addr)
            if existing is None or p.liquidity_usd > existing.liquidity_usd:
                pairs_by_contract[addr] = p

    selected: list[tuple[str, str]] = []
    for c in candidates:
        if len(selected) >= limit:
            break
        addr = c["contract"].lower()
        pair = pairs_by_contract.get(addr)
        if pair is None:
            continue  # pas de paire résolue -- jamais un candidat retenu sans donnée réelle
        if pair.liquidity_usd < _MIN_LIQUIDITY_USD or pair.volume_24h_usd < _MIN_VOLUME_24H_USD:
            continue  # pas assez de traction pour l'instant -- PAS blacklisté, peut grossir

        try:
            clear, reason, code = await check_honeypot(addr, _CHAIN)
        except Exception as exc:  # noqa: BLE001
            logger.info("token_candidate_screening: honeypot check échoué pour %s (%s)", addr, exc)
            continue  # panne technique -- ni retenu ni blacklisté, retenté au prochain cycle

        if not clear:
            if code == "honeypot_rejected":
                await _blacklist_candidate(addr, _CHAIN, reason)
            continue  # panne d'infra (unavailable/chain_not_covered) -- jamais blacklisté

        selected.append((c["contract"], pair.base_symbol or ""))

    return selected
