"""Kill-switch sortant — pause globale de toutes les actions d'ARIA vers le monde.

Couvre tweets, réponses/likes X, dépenses ACP et jobs planifiés (heartbeat). L'état
est **persisté sur disque** (`data_dir()/pause_state.json`) et **relu à chaque
vérification** : il survit donc à un redémarrage du process — aucune variable mémoire
qui se perdrait au reboot.

Ce module ne gèle JAMAIS la messagerie opérateur Telegram (`send_message` /
`notify_admin`) : le canal de contrôle doit rester ouvert pour recevoir la
confirmation du `/stop`, les prompts d'approbation, et permettre le `/start`.

Comportement en cas d'état illisible/corrompu (« le doute »), **asymétrique et voulu** :
  - tweets / réponses / likes / jobs → **fail-open** (``is_paused()``) : ARIA continue.
    Un fichier abîmé ne doit pas la briquer toute seule.
  - dépenses / wallet_guard → **fail-closed** (``is_paused(strict=True)`` /
    ``money_block_reason()``) : dans le doute, on gèle l'argent.

Un fichier **absent** n'est pas un doute : c'est l'état propre « jamais pausée » → tout
passe (sinon ARIA ne pourrait jamais rien faire). Seule la corruption déclenche le
fail-closed côté argent. La corruption est toujours loguée.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)


def _state_path() -> Path:
    return data_dir() / "pause_state.json"


def _read_raw() -> dict[str, Any] | None:
    """Lit l'état brut. Distingue trois cas :
      - ``{}``   → fichier absent : état propre « jamais pausée » (pas un doute).
      - ``dict`` → contenu lu correctement.
      - ``None`` → fichier présent mais illisible/corrompu : état INCONNU (le doute).
    """
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("pause_state illisible/corrompu (%s) — état INCONNU", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("pause_state de forme inattendue (%r) — état INCONNU", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Écriture atomique : tmp puis replace, pour qu'un lecteur ne voie jamais un JSON partiel.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def is_paused(*, strict: bool = False) -> bool:
    """Vrai si le kill-switch est armé. Relit le disque à chaque appel (survit au restart).

    En cas d'état illisible/corrompu :
      - ``strict=False`` (défaut — tweets, réponses, likes, jobs) → **fail-open** : False.
      - ``strict=True`` (dépenses / wallet_guard) → **fail-closed** : True (gel par sécurité).
    Un fichier absent renvoie toujours False (état propre, pas un doute).
    """
    data = _read_raw()
    if data is None:
        if strict:
            logger.warning("État pause illisible — fail-closed (strict) : blocage argent par sécurité")
        return strict
    return bool(data.get("paused"))


def money_block_reason(action: str = "Cette dépense") -> str | None:
    """Chemin argent (wallet_guard). ``None`` → dépense autorisée ; sinon message de blocage.

    **Fail-closed** : bloque si ARIA est en pause OU si l'état est illisible/corrompu (le doute
    profite à la sécurité). Un fichier absent (jamais pausée) laisse passer.
    """
    data = _read_raw()
    if data is None:
        return (
            f"⛔ {action} est bloquée : l'état de pause est illisible/corrompu. "
            "Par sécurité, les dépenses sont gelées dans le doute (fail-closed).\n"
            "Répare/supprime pause_state.json — ou fais /stop puis /start — avant toute dépense."
        )
    if data.get("paused"):
        return blocked_notice(action)
    return None


def pause_status() -> dict[str, Any]:
    """État courant : {paused, since (datetime|None), by, reason, readable}.

    ``readable=False`` signale un fichier corrompu (dépenses gelées, tweets/jobs actifs).
    """
    raw = _read_raw()
    readable = raw is not None
    data = raw or {}
    since: datetime | None = None
    since_raw = data.get("since")
    if isinstance(since_raw, str):
        try:
            since = datetime.fromisoformat(since_raw.replace("Z", "+00:00"))
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        except ValueError:
            since = None
    return {
        "paused": bool(data.get("paused")),
        "since": since,
        "by": data.get("by"),
        "reason": data.get("reason") or "",
        "readable": readable,
    }


def pause(by: int | str | None = None, reason: str = "") -> dict[str, Any]:
    """Arme le kill-switch. Toutes les actions sortantes se bloqueront jusqu'à ``resume``."""
    _write(
        {
            "paused": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "by": by,
            "reason": (reason or "").strip(),
        }
    )
    logger.warning("ARIA en PAUSE (kill-switch sortant armé) — by=%s reason=%s", by, reason)
    return pause_status()


def resume(by: int | str | None = None) -> dict[str, Any]:
    """Lève le kill-switch. Les actions sortantes reprennent."""
    _write(
        {
            "paused": False,
            "since": None,
            "by": by,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.warning("ARIA REPRISE (kill-switch levé) — by=%s", by)
    return pause_status()


def since_label() -> str:
    """« depuis 14:32 UTC (il y a 1h07) » — pour rappeler à l'opérateur depuis quand ça dure."""
    since = pause_status().get("since")
    if not isinstance(since, datetime):
        return "depuis un instant indéterminé"
    elapsed_min = int((datetime.now(timezone.utc) - since).total_seconds() // 60)
    if elapsed_min < 1:
        human = "à l'instant"
    elif elapsed_min < 60:
        human = f"il y a {elapsed_min} min"
    else:
        hours, mins = divmod(elapsed_min, 60)
        human = f"il y a {hours}h{mins:02d}"
    return f"depuis {since.strftime('%H:%M UTC')} ({human})"


def blocked_notice(action: str = "Cette action sortante") -> str:
    """Message de blocage — rappelle que la pause est active ET depuis quand (choix opérateur)."""
    return (
        f"⏸ {action} est bloquée : ARIA est en pause {since_label()}.\n"
        "Envoie /start (ou /resume) pour reprendre les actions sortantes."
    )
