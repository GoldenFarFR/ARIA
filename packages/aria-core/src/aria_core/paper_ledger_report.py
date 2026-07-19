"""Registre du paper-trading 1 M$ — dissection thèse d'entrée/sortie + score de winrate.

Lecture pure : réutilise `paper_trader.get_open_positions()`/`get_closed_positions()`/
`portfolio_summary()` tels quels (aucune requête SQL dupliquée, même patron que
`dossier.py` et la route `/diagnostics/paper-ledger`). Importé par `scripts/
paper_ledger_report.py` (CLI/VPS) ET par `gateway/telegram_bot.py` (commande `/ledger`,
17/07) -- un seul endroit qui sait dissequer une position, jamais deux versions qui
divergent.
"""
from __future__ import annotations

from datetime import datetime, timezone

from aria_core import paper_trader
from aria_core.services.dexscreener import token_url


def _fmt_price(v) -> str:
    return f"{v:.6g}" if isinstance(v, (int, float)) else "?"


def _fmt_money(v) -> str:
    return f"{v:+,.0f} $" if isinstance(v, (int, float)) else "?"


def _rr_ratio(entry, target, invalidation) -> str:
    """Ratio risque/récompense visé à l'entrée : (cible-entrée)/(entrée-invalidation)."""
    if not all(isinstance(v, (int, float)) for v in (entry, target, invalidation)):
        return "?"
    risk = entry - invalidation
    reward = target - entry
    if risk <= 0:
        return "?"
    return f"{reward / risk:.2f}"


def _duration(opened_at: str | None, closed_at: str | None) -> str:
    if not opened_at or not closed_at:
        return "?"
    try:
        a = datetime.fromisoformat(opened_at)
        b = datetime.fromisoformat(closed_at)
        hours = (b - a).total_seconds() / 3600
        if hours < 24:
            return f"{hours:.1f}h"
        return f"{hours / 24:.1f}j"
    except ValueError:
        return "?"


def _render_open(p: dict) -> str:
    lines = [
        f"  {p.get('symbol') or p.get('contract', '?')} ({p.get('chain', '?')}) — OUVERTE",
        f"    Entrée {_fmt_price(p.get('entry_price'))} le {p.get('opened_at', '?')}"
        f" · coût {p.get('cost_usd', 0):,.0f} $",
        f"    Cible {_fmt_price(p.get('target_price'))} · Invalidation"
        f" {_fmt_price(p.get('invalidation_price'))} · R:R visé"
        f" {_rr_ratio(p.get('entry_price'), p.get('target_price'), p.get('invalidation_price'))}",
    ]
    if p.get("high_water_price"):
        lines.append(f"    Plus haut atteint {_fmt_price(p['high_water_price'])}")
    if p.get("realized_pnl_partial"):
        lines.append(f"    P&L réalisé (prises de profit partielles) {_fmt_money(p['realized_pnl_partial'])}")
    thesis = (p.get("thesis") or "").strip()
    lines.append(f"    Thèse : {thesis}" if thesis else "    Thèse : (aucune — position pré-#197 ou non renseignée)")
    close_notes = (p.get("close_notes") or "").strip()
    if close_notes:
        lines.append(f"    Dernière prise de profit partielle : {close_notes}")
    if p.get("contract"):
        lines.append(f"    DexScreener : {token_url(p['contract'], chain=p.get('chain') or 'base')}")
    return "\n".join(lines)


def _render_closed(p: dict) -> str:
    pnl = p.get("pnl_usd") or 0.0
    verdict = "GAGNANTE" if pnl > 0 else ("PERDANTE" if pnl < 0 else "NEUTRE")
    lines = [
        f"  {p.get('symbol') or p.get('contract', '?')} ({p.get('chain', '?')}) — CLÔTURÉE {verdict}",
        f"    Entrée {_fmt_price(p.get('entry_price'))} le {p.get('opened_at', '?')}"
        f" → Sortie {_fmt_price(p.get('exit_price'))} le {p.get('closed_at', '?')}"
        f" (détenue {_duration(p.get('opened_at'), p.get('closed_at'))})",
        f"    Cible {_fmt_price(p.get('target_price'))} · Invalidation {_fmt_price(p.get('invalidation_price'))}"
        f" · Raison de sortie : {p.get('close_reason') or '?'}",
        f"    P&L {_fmt_money(pnl)} ({p.get('pnl_pct', 0):+.1f} %)",
    ]
    thesis = (p.get("thesis") or "").strip()
    lines.append(f"    Thèse : {thesis}" if thesis else "    Thèse : (aucune — position pré-#197 ou non renseignée)")
    close_notes = (p.get("close_notes") or "").strip()
    lines.append(
        f"    Pourquoi cette sortie : {close_notes}" if close_notes
        else "    Pourquoi cette sortie : (aucune note — clôture pré-17/07 ou non renseignée)"
    )
    if p.get("contract"):
        lines.append(f"    DexScreener : {token_url(p['contract'], chain=p.get('chain') or 'base')}")
    return "\n".join(lines)


async def build_report(closed_limit: int = 500) -> tuple[str, dict]:
    """Renvoie (texte lisible, dict JSON-able). ``closed_limit`` borne le nombre de
    positions clôturées incluses (le plus récent d'abord, cf. get_closed_positions)."""
    starting = await paper_trader.starting_capital()
    opens = await paper_trader.get_open_positions()
    closed = await paper_trader.get_closed_positions(limit=closed_limit)
    summary = await paper_trader.portfolio_summary()

    wins = [p for p in closed if (p.get("pnl_usd") or 0.0) > 0]
    losses = [p for p in closed if (p.get("pnl_usd") or 0.0) < 0]
    avg_win = sum(p["pnl_usd"] for p in wins) / len(wins) if wins else None
    avg_loss = sum(p["pnl_usd"] for p in losses) / len(losses) if losses else None
    expectancy = sum((p.get("pnl_usd") or 0.0) for p in closed) / len(closed) if closed else None

    now = datetime.now(timezone.utc).isoformat()
    header = [
        f"=== Registre paper-trading ARIA — {now} ===",
        f"Capital de départ {starting:,.0f} $ · Équité (au coût, sans prix live) {summary['equity']:,.0f} $"
        f" ({summary['return_pct']:+.2f} %)",
        f"Cash {summary['cash']:,.0f} $ · P&L réalisé {_fmt_money(summary['realized_pnl'])}"
        f" · P&L latent {_fmt_money(summary['unrealized_pnl'])}",
        "",
        "--- Score de winrate (trades clôturés uniquement) ---",
        f"{len(closed)} trade(s) clôturé(s) · {len(wins)} gagnant(s) · {len(losses)} perdant(s)"
        + (f" · winrate {summary['win_rate']:.1f} %" if summary["win_rate"] is not None else " · winrate: n/a (0 clôture)"),
        f"Gain moyen {_fmt_money(avg_win) if avg_win is not None else 'n/a'}"
        f" · Perte moyenne {_fmt_money(avg_loss) if avg_loss is not None else 'n/a'}"
        f" · Espérance/trade {_fmt_money(expectancy) if expectancy is not None else 'n/a'}",
    ]
    open_section = [f"--- Positions ouvertes ({len(opens)}) ---"] + (
        [_render_open(p) for p in opens] or ["  (aucune)"]
    )
    closed_section = [f"--- Positions clôturées ({len(closed)}) ---"] + (
        [_render_closed(p) for p in closed] or ["  (aucune)"]
    )

    text = "\n".join(header + [""] + open_section + [""] + closed_section)

    machine = {
        "generated_at": now,
        "starting_capital": starting,
        "summary": summary,
        "winrate_stats": {
            "closed_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": summary["win_rate"],
            "avg_win_usd": avg_win,
            "avg_loss_usd": avg_loss,
            "expectancy_usd": expectancy,
        },
        "open_positions": opens,
        "closed_positions": closed,
    }
    return text, machine


async def build_positions_detail_block(*, closed_limit: int = 5) -> str:
    """Bloc « détail des positions » seul (ouvertes + N dernières clôturées, avec
    URL DexScreener/thèse/R:R) -- SANS le header agrégé (départ/équité/winrate) de
    ``build_report``, pour un appelant qui calcule déjà ses propres chiffres agrégés
    ailleurs et veut juste ajouter le détail sans le dupliquer.

    19/07, demande opérateur explicite : ``/feedback`` ne montrait qu'un bilan agrégé
    (départ/PnL/résultat), jamais le détail par position -- l'opérateur veut voir
    « toutes les positions en cours, l'URL et tout aussi » directement sous cette
    commande, pas seulement sous ``/ledger``. Réutilise ``_render_open``/
    ``_render_closed`` tels quels (même rendu que ``/ledger``, jamais un 2e format qui
    pourrait diverger)."""
    opens = await paper_trader.get_open_positions()
    closed = await paper_trader.get_closed_positions(limit=closed_limit)
    open_section = [f"--- Positions ouvertes ({len(opens)}) ---"] + (
        [_render_open(p) for p in opens] or ["  (aucune)"]
    )
    closed_section = [f"--- Positions clôturées récentes ({len(closed)}) ---"] + (
        [_render_closed(p) for p in closed] or ["  (aucune)"]
    )
    return "\n".join(open_section + [""] + closed_section)


async def build_trade_status_context() -> str:
    """Bloc de contexte compact pour injection dans un prompt LLM (``brain.py``,
    ``_try_trade_status_response``, 17/07) -- réutilise ``build_report`` tel quel
    (aucune requête dupliquée), borné à 5 positions clôturées pour rester lisible
    dans un contexte LLM déjà contraint en tokens. Préfixé pour qu'un LLM comprenne
    immédiatement qu'il s'agit de données RÉELLES, pas d'un exemple à broder."""
    text, _machine = await build_report(closed_limit=5)
    return (
        "# Registre paper-trading RÉEL (portefeuille papier 1 M$, aucun argent réel) "
        "-- utilise ces vrais chiffres pour répondre, n'en invente aucun autre\n"
        f"{text}"
    )
