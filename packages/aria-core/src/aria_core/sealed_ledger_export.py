"""Export du Sealed Ledger vers un fichier JSONL public (19/07, #214) -- voir
``sealed_ledger.py`` pour le design complet. Module SÉPARÉ et volontairement dépourvu de
tout appel git/subprocess : ce fichier ne fait QUE de l'I/O fichier + la garde fail-fast de
continuité de chaîne, ce qui le rend testable sans mocker git. Le commit+push réel est un
script fin (``scripts/sealed_ledger_seed_demo.py`` pour la preuve v0) qui appelle
``export_snapshot_to_jsonl`` puis fait le ``git add``/``commit``/``push`` lui-même.

Garde fail-fast (spec figée dans la conversation ARIA/opérateur du 19/07) : si le fichier
JSONL existe déjà et contient au moins une ligne, on lit le hash de sa DERNIÈRE ligne et on
le compare au ``prev_hash`` du premier événement qu'on s'apprête à ajouter. Un mismatch
signifie que le fichier distant a divergé de ce que l'exporteur croyait -- on n'écrit RIEN,
on lève bruyamment. L'intégrité du registre repose sur cette continuité vérifiée à chaque
export, pas sur la protection de branche GitHub (acté explicitement dans la conversation
de design)."""
from __future__ import annotations

import json
from pathlib import Path

from aria_core.sealed_ledger import GENESIS_HASH


class ExportIntegrityError(RuntimeError):
    """Le fichier JSONL existant ne s'enchaîne pas avec les nouveaux événements --
    jamais rattrapée pour écrire quand même, c'est exactement le scénario que cette
    garde existe pour bloquer."""


def read_jsonl_events(jsonl_path: Path) -> list[dict]:
    """Relit un export existant tel quel -- utilisé à la fois par la garde fail-fast
    interne ET par la re-vérification tierce indépendante (qui doit pouvoir relire un
    export SANS toucher à la base de données locale)."""
    if not jsonl_path.exists():
        return []
    events = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def export_snapshot_to_jsonl(*, jsonl_path: Path, new_events: list[dict]) -> dict:
    """Ajoute ``new_events`` (triés par ``sequence`` par cette fonction, jamais supposé
    déjà trié) à la fin du fichier JSONL, une ligne JSON canonique par événement. Vérifie
    la continuité de chaîne avant d'écrire quoi que ce soit.

    Retourne {"appended": N, "tail_hash": "<hash du dernier événement écrit>"}.
    Lève ``ExportIntegrityError`` sans rien écrire si la continuité est rompue.
    Ne fait AUCUN appel git -- c'est la responsabilité de l'appelant."""
    if not new_events:
        return {"appended": 0, "tail_hash": None}

    ordered = sorted(new_events, key=lambda e: e["sequence"])

    existing = read_jsonl_events(jsonl_path)
    expected_prev_hash = existing[-1]["hash"] if existing else GENESIS_HASH

    if ordered[0]["prev_hash"] != expected_prev_hash:
        raise ExportIntegrityError(
            f"Rupture de continuité : le premier événement à exporter "
            f"(event_id={ordered[0]['event_id']}) a prev_hash={ordered[0]['prev_hash']!r}, "
            f"mais le fichier {jsonl_path} se termine sur hash={expected_prev_hash!r}. "
            f"Rien n'a été écrit."
        )

    # Continuité interne du lot lui-même, avant d'écrire quoi que ce soit.
    for i in range(1, len(ordered)):
        if ordered[i]["prev_hash"] != ordered[i - 1]["hash"]:
            raise ExportIntegrityError(
                f"Rupture de continuité DANS le lot à exporter, à l'index {i} "
                f"(event_id={ordered[i]['event_id']}). Rien n'a été écrit."
            )

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as fh:
        for ev in ordered:
            fh.write(json.dumps(ev, sort_keys=True, separators=(",", ":")))
            fh.write("\n")

    return {"appended": len(ordered), "tail_hash": ordered[-1]["hash"]}
