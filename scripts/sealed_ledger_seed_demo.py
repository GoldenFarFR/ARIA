"""Preuve v0 du Sealed Ledger (19/07, #214) : remplit 4 trades FICTIFS à la main dans
sealed_ledger.py (aucun ne vient d'ARIA ou du paper-trading réel -- c'est explicite dans
chaque thèse et chaque token_address ci-dessous), exerçant les 4 branches de la machine
d'état :

  1. Trade gagnant, sortie simple (1 fill entrée + 1 fill sortie FINAL)
  2. Trade perdant, sortie simple (stop-loss)
  3. Sortie fragmentée (2 EXIT_FILL, VWAP pondéré, statut CLOSED via somme des fills)
  4. Liquidité disparue en sortie -- EXIT_ABANDONED, reliquat jamais valorisé

Puis exporte la chaîne complète vers un fichier JSONL public, ET relit CE FICHIER (pas
la base SQLite locale) dans un contexte totalement frais pour lancer verify_chain() --
c'est la preuve réelle de re-vérification tierce, pas une affirmation.

Usage : python scripts/sealed_ledger_seed_demo.py
Écrit dans : sealed-ledger-v0-proof/trades.jsonl (racine du repo)
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aria_core import sealed_ledger as sl
from aria_core import sealed_ledger_export as sle

REPO_ROOT = Path(__file__).resolve().parent.parent
JSONL_PATH = REPO_ROOT / "sealed-ledger-v0-proof" / "trades.jsonl"

_PROOF_PREFIX = "PROOF-v0-hand-filled-not-a-real-ARIA-decision"


async def _trade_1_winning_simple() -> None:
    entry = await sl.record_entry_decision(
        trade_id="demo-trade-1", token_address="0xPROOF0000000000000000000000000000000001",
        chain="base", decision_price_usd=1.00, target_size_usd=1000.0,
        thesis=f"{_PROOF_PREFIX} : trade gagnant, sortie simple.",
        conviction=70, pipeline="momentum", source_price="dexscreener:demo-pool-1",
    )
    await sl.record_entry_fill(
        trade_id="demo-trade-1", entry_decision_hash=entry.hash,
        execution_price_usd=1.00, filled_quantity=1000.0, fill_status="FINAL",
    )
    exit_dec = await sl.record_exit_decision(
        trade_id="demo-trade-1", entry_decision_hash=entry.hash,
        decision_price_usd=1.50, target_quantity=1000.0, exit_reason="take-profit",
    )
    await sl.record_exit_fill(
        trade_id="demo-trade-1", exit_decision_hash=exit_dec.hash, sequence_index=1,
        execution_price_usd=1.50, filled_quantity=1000.0, fill_status="FINAL",
    )


async def _trade_2_losing_simple() -> None:
    entry = await sl.record_entry_decision(
        trade_id="demo-trade-2", token_address="0xPROOF0000000000000000000000000000000002",
        chain="base", decision_price_usd=2.00, target_size_usd=500.0,
        thesis=f"{_PROOF_PREFIX} : trade perdant, stop-loss.",
        conviction=55, pipeline="momentum", source_price="dexscreener:demo-pool-2",
    )
    await sl.record_entry_fill(
        trade_id="demo-trade-2", entry_decision_hash=entry.hash,
        execution_price_usd=2.00, filled_quantity=250.0, fill_status="FINAL",
    )
    exit_dec = await sl.record_exit_decision(
        trade_id="demo-trade-2", entry_decision_hash=entry.hash,
        decision_price_usd=1.70, target_quantity=250.0, exit_reason="stop-loss",
    )
    await sl.record_exit_fill(
        trade_id="demo-trade-2", exit_decision_hash=exit_dec.hash, sequence_index=1,
        execution_price_usd=1.70, filled_quantity=250.0, fill_status="FINAL",
    )


async def _trade_3_fragmented_exit() -> None:
    entry = await sl.record_entry_decision(
        trade_id="demo-trade-3", token_address="0xPROOF0000000000000000000000000000000003",
        chain="solana", decision_price_usd=0.05, target_size_usd=800.0,
        thesis=f"{_PROOF_PREFIX} : sortie fragmentée en 2 fills, teste le VWAP.",
        conviction=65, pipeline="momentum", source_price="dexscreener:demo-pool-3",
    )
    await sl.record_entry_fill(
        trade_id="demo-trade-3", entry_decision_hash=entry.hash,
        execution_price_usd=0.05, filled_quantity=16000.0, fill_status="FINAL",
    )
    exit_dec = await sl.record_exit_decision(
        trade_id="demo-trade-3", entry_decision_hash=entry.hash,
        decision_price_usd=0.08, target_quantity=16000.0, exit_reason="take-profit",
    )
    # Liquidité insuffisante pour un seul fill -- deux exécutions à des prix différents.
    await sl.record_exit_fill(
        trade_id="demo-trade-3", exit_decision_hash=exit_dec.hash, sequence_index=1,
        execution_price_usd=0.082, filled_quantity=10000.0, fill_status="PARTIAL",
    )
    await sl.record_exit_fill(
        trade_id="demo-trade-3", exit_decision_hash=exit_dec.hash, sequence_index=2,
        execution_price_usd=0.076, filled_quantity=6000.0, fill_status="FINAL",
    )


async def _trade_4_exit_abandoned() -> None:
    entry = await sl.record_entry_decision(
        trade_id="demo-trade-4", token_address="0xPROOF0000000000000000000000000000000004",
        chain="base", decision_price_usd=3.00, target_size_usd=300.0,
        thesis=f"{_PROOF_PREFIX} : liquidité disparaît avant la clôture -- EXIT_ABANDONED.",
        conviction=60, pipeline="momentum", source_price="dexscreener:demo-pool-4",
    )
    await sl.record_entry_fill(
        trade_id="demo-trade-4", entry_decision_hash=entry.hash,
        execution_price_usd=3.00, filled_quantity=100.0, fill_status="FINAL",
    )
    exit_dec = await sl.record_exit_decision(
        trade_id="demo-trade-4", entry_decision_hash=entry.hash,
        decision_price_usd=1.20, target_quantity=100.0, exit_reason="stop-loss",
    )
    await sl.record_exit_fill(
        trade_id="demo-trade-4", exit_decision_hash=exit_dec.hash, sequence_index=1,
        execution_price_usd=1.10, filled_quantity=35.0, fill_status="PARTIAL",
    )
    await sl.record_exit_abandoned(
        trade_id="demo-trade-4", exit_decision_hash=exit_dec.hash,
        remaining_quantity=65.0, reason=f"{_PROOF_PREFIX} : pool vidée, simulation.",
    )


async def main() -> None:
    print("=== Sealed Ledger v0 -- preuve isolée (4 trades fictifs, remplis à la main) ===\n")

    await _trade_1_winning_simple()
    await _trade_2_losing_simple()
    await _trade_3_fragmented_exit()
    await _trade_4_exit_abandoned()

    all_events = await sl.list_events()
    print(f"Événements écrits dans la chaîne locale : {len(all_events)}")

    for trade_id in ("demo-trade-1", "demo-trade-2", "demo-trade-3", "demo-trade-4"):
        pnl = await sl.compute_trade_pnl(trade_id)
        print(f"  {trade_id} : status={pnl['status']}", end="")
        if "pnl_usd" in pnl:
            print(f", pnl_usd={pnl['pnl_usd']:.2f}, pnl_pct={pnl['pnl_pct']:.2f}%", end="")
        if pnl["status"] == "ABANDONED":
            print(f", reliquat_non_valorise={pnl['abandoned_quantity']}", end="")
        print()

    print("\n--- Étape 1 : vérification LOCALE (juste après écriture) ---")
    ok_local, reason_local = sl.verify_chain(all_events)
    print(f"verify_chain() sur les événements en mémoire : ok={ok_local} reason={reason_local}")
    assert ok_local, "La chaîne locale devrait être intacte juste après écriture"

    print("\n--- Étape 2 : export JSONL (fail-fast si divergence) ---")
    result = sle.export_snapshot_to_jsonl(jsonl_path=JSONL_PATH, new_events=all_events)
    print(f"Exporté : {result['appended']} événements -> {JSONL_PATH}")
    print(f"Hash de fin de chaîne : {result['tail_hash']}")

    print("\n--- Étape 3 : RE-VÉRIFICATION TIERCE INDÉPENDANTE ---")
    print("(relit UNIQUEMENT le fichier JSONL fraîchement exporté, zéro accès à la base SQLite)")
    reread_events = sle.read_jsonl_events(JSONL_PATH)
    ok_tiers, reason_tiers = sl.verify_chain(reread_events)
    print(f"verify_chain() sur le fichier ré-importé : ok={ok_tiers} reason={reason_tiers}")
    assert ok_tiers, "La ré-importation depuis le fichier exporté devrait rester intacte"
    assert len(reread_events) == len(all_events), "Le fichier exporté doit contenir tous les événements"

    print("\n--- Étape 4 : test de détection de falsification (preuve négative) ---")
    tampered = json.loads(json.dumps(reread_events))  # copie profonde
    tampered[0]["payload"]["decision_price_usd"] = 999999.0
    ok_tampered, reason_tampered = sl.verify_chain(tampered)
    print(f"verify_chain() sur une copie ALTÉRÉE : ok={ok_tampered} reason={reason_tampered}")
    assert not ok_tampered, "Une ligne altérée doit être détectée, pas passer silencieusement"

    print("\n=== PREUVE COMPLÈTE : le sceau tient, l'export tient, la falsification est détectée. ===")


if __name__ == "__main__":
    asyncio.run(main())
