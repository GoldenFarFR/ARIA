"""Dossier par token — la mémoire d'ARIA sur UN contrat, en une chronologie.

L'opérateur (et, plus tard, le cockpit) donne un CA (adresse de contrat) et
récupère TOUT ce qu'ARIA a déjà consigné dessus, fusionné en un seul flux daté :

- **analyses VC** (`vc_prediction`) — chaque verdict `/vc`, ouverture + résultat ;
- **carnet de bord** (`journal_entry`) — thèse, décision, faits ;
- **suivi de thèse** (`thesis_checkpoint`) — les re-vérifications dans le temps ;
- **mémoire d'investissement** (`investment_thesis`) — décision → leçon ;
- **paper-trading** (`paper_position`) — achats/ventes FICTIFS 1 M$.

Aucun client externe, aucun réseau, aucune écriture : c'est une **lecture pure**
qui réutilise les fonctions existantes de chaque store (jamais un doublon). La
fusion (`build_events`) est une fonction pure — testable hors-ligne, sans DB.

Facts-only : on n'affiche que ce qui est réellement en base. Un token jamais
analysé renvoie un dossier vide (et non une donnée inventée).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# Adresse EVM canonique : 0x suivi de 40 hexadécimaux.
ADDR_RE = re.compile(r"0x[a-fA-F0-9]{40}")
_STRICT_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def is_contract(text: str) -> bool:
    return bool(_STRICT_ADDR_RE.match((text or "").strip()))


def extract_contract(text: str) -> str | None:
    """Extrait LE contrat d'un message qui n'est *essentiellement* qu'une adresse.

    Renvoie l'adresse normalisée si le message ne contient qu'une seule adresse
    distincte (tolère un copier-coller dupliqué : « 0x…0x… » = même adresse deux
    fois) et quasiment rien d'autre. Sinon ``None`` — on laisse alors la vraie
    conversation au LLM (on n'intercepte pas une phrase qui *cite* une adresse).
    """
    if not text:
        return None
    matches = ADDR_RE.findall(text)
    if not matches:
        return None
    distinct = {m.lower() for m in matches}
    if len(distinct) != 1:
        return None  # plusieurs tokens cités → ce n'est pas une consultation de dossier
    remainder = ADDR_RE.sub("", text)
    remainder = re.sub(r"[\s,;:.·\-–—/]+", "", remainder)
    if len(remainder) > 4:
        return None  # trop de texte autour → vraie phrase, pas une simple adresse
    return matches[0]


def _parse_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _sort_key(ev: dict) -> tuple:
    dt = _parse_dt(ev.get("at"))
    # Événements sans date en dernier (timestamp minimal), tri décroissant ensuite.
    if dt is None:
        return (0, datetime.min.replace(tzinfo=timezone.utc))
    return (1, dt)


def _pct(v) -> str:
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def build_events(
    *,
    predictions: list[dict] | None = None,
    entries: list[dict] | None = None,
    checkpoints: list[dict] | None = None,
    theses: list[dict] | None = None,
    paper: list[dict] | None = None,
) -> list[dict]:
    """Fusionne les lignes brutes de chaque store en un flux d'événements daté.

    Fonction PURE (aucune I/O) : on lui passe les listes déjà chargées, elle rend
    les événements triés du plus récent au plus ancien. Chaque événement :
    ``{at, kind, source, summary, data}``.
    """
    events: list[dict] = []

    for p in predictions or []:
        reco = p.get("recommandation") or "?"
        pot = p.get("potentiel")
        pot_str = f" · potentiel {pot}/10" if pot is not None else ""
        events.append({
            "at": p.get("created_at"),
            "kind": "analyse",
            "source": "vc_prediction",
            "summary": f"Analyse VC : {reco}{pot_str}",
            "data": {
                "id": p.get("id"), "recommandation": reco, "potentiel": pot,
                "risque": p.get("risque"), "security_score": p.get("security_score"),
                "strategy": p.get("strategy"), "entry_price": p.get("entry_price"),
                "target_price": p.get("target_price"),
                "invalidation_price": p.get("invalidation_price"),
                "llm_used": bool(p.get("llm_used")),
            },
        })
        if p.get("status") == "closed" and p.get("closed_at"):
            events.append({
                "at": p.get("closed_at"),
                "kind": "analyse_resultat",
                "source": "vc_prediction",
                "summary": f"Résultat attribué : {_pct(p.get('outcome_pct'))}",
                "data": {"id": p.get("id"), "outcome_pct": p.get("outcome_pct"),
                         "note": p.get("outcome_note")},
            })

    for e in entries or []:
        events.append({
            "at": e.get("created_at"),
            "kind": "these",
            "source": "journal_entry",
            "summary": f"Carnet de bord : {e.get('decision') or '?'}",
            "data": {"symbol": e.get("symbol"), "decision": e.get("decision"),
                     "thesis": e.get("thesis"), "facts": e.get("facts") or [],
                     "entry_price": e.get("entry_price"),
                     "target_price": e.get("target_price"),
                     "invalidation_price": e.get("invalidation_price"),
                     "chart_ref": e.get("chart_ref")},
        })

    for c in checkpoints or []:
        events.append({
            "at": c.get("created_at"),
            "kind": "suivi",
            "source": "thesis_checkpoint",
            "summary": f"Suivi : {c.get('verdict') or '?'} ({_pct(c.get('price_vs_entry_pct'))})",
            "data": {"price": c.get("price"),
                     "price_vs_entry_pct": c.get("price_vs_entry_pct"),
                     "activity_status": c.get("activity_status"),
                     "verdict": c.get("verdict"), "note": c.get("note")},
        })

    for t in theses or []:
        events.append({
            "at": t.get("created_at"),
            "kind": "memoire",
            "source": "investment_thesis",
            "summary": f"Mémoire : {t.get('decision') or '?'}",
            "data": {"symbol": t.get("token_symbol"), "decision": t.get("decision"),
                     "thesis": t.get("thesis"), "status": t.get("status")},
        })
        if t.get("closed_at"):
            events.append({
                "at": t.get("closed_at"),
                "kind": "memoire_resultat",
                "source": "investment_thesis",
                "summary": f"Leçon : {t.get('outcome') or '?'}",
                "data": {"outcome": t.get("outcome"), "lesson": t.get("lesson")},
            })

    for pos in paper or []:
        events.append({
            "at": pos.get("opened_at"),
            "kind": "paper_achat",
            "source": "paper_position",
            "summary": f"Achat FICTIF (paper 1 M$) {pos.get('symbol') or ''}".strip(),
            "data": {"symbol": pos.get("symbol"), "entry_price": pos.get("entry_price"),
                     "cost_usd": pos.get("cost_usd"), "target_price": pos.get("target_price"),
                     "invalidation_price": pos.get("invalidation_price"),
                     "status": pos.get("status")},
        })
        if pos.get("status") == "closed" and pos.get("closed_at"):
            events.append({
                "at": pos.get("closed_at"),
                "kind": "paper_vente",
                "source": "paper_position",
                "summary": (
                    f"Vente FICTIVE {pos.get('symbol') or ''} : "
                    f"{_pct(pos.get('pnl_pct'))} ({pos.get('close_reason') or ''})"
                ).strip(),
                "data": {"exit_price": pos.get("exit_price"), "pnl_usd": pos.get("pnl_usd"),
                         "pnl_pct": pos.get("pnl_pct"), "close_reason": pos.get("close_reason")},
            })

    events.sort(key=_sort_key, reverse=True)
    return events


def _guess_symbol(predictions, entries, theses, paper) -> str | None:
    for src in (entries, theses, paper):
        for row in src or []:
            sym = row.get("symbol") or row.get("token_symbol")
            if sym:
                return sym
    return None


async def build_dossier(contract: str, *, limit: int = 50) -> dict:
    """Charge et fusionne tout l'historique d'ARIA sur un contrat.

    Renvoie ``{contract, valid, symbol, screened_status, counts, events, generated_at}``.
    Un CA invalide renvoie ``valid: False`` (jamais une exception vers l'appelant).
    Lecture seule : aucune écriture, aucun appel réseau externe.
    """
    from aria_core import investment_memory, paper_trader, screened_pool, thesis_journal, vc_predictions

    raw = (contract or "").strip()
    if not is_contract(raw):
        return {"contract": raw, "valid": False,
                "error": "Adresse invalide — attendu 0x suivi de 40 hexadécimaux."}

    predictions = await vc_predictions.list_predictions_for_contract(raw, limit=limit)
    entries = await thesis_journal.list_entries(raw, limit=limit)
    checkpoints = await thesis_journal.list_checkpoints(raw, limit=limit)
    theses = await investment_memory.list_theses_for_token(raw, limit=limit)
    paper = await paper_trader.list_positions_for_contract(raw, limit=limit)
    screened_status = await screened_pool.get_status(raw)

    events = build_events(
        predictions=predictions, entries=entries, checkpoints=checkpoints,
        theses=theses, paper=paper,
    )

    counts = {
        "analyses": len(predictions),
        "carnet": len(entries),
        "suivis": len(checkpoints),
        "memoire": len(theses),
        "paper": len(paper),
        "evenements": len(events),
    }
    return {
        "contract": raw,
        "valid": True,
        "symbol": _guess_symbol(predictions, entries, theses, paper),
        "screened_status": screened_status,
        "counts": counts,
        "events": events,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _fmt_ts(iso: str | None) -> str:
    if not iso or len(iso) < 16:
        return "??"
    return iso[:16].replace("T", " ")


def format_dossier_telegram(dossier: dict, *, limit_events: int = 15) -> str:
    """Rend le dossier en texte Telegram (opérateur). Chronologie récente en tête.

    Dégradation douce : un dossier vide n'est pas un cul-de-sac — on propose la
    suite (/vc pour analyser, /scan pour un contrôle rapide).
    """
    if not dossier.get("valid"):
        return dossier.get("error") or "Adresse invalide."

    contract = dossier["contract"]
    short = f"{contract[:6]}…{contract[-4:]}"
    sym = dossier.get("symbol")
    head = f"Dossier {sym} ({short})" if sym else f"Dossier {short}"

    events = dossier.get("events") or []
    if not events:
        status = dossier.get("screened_status")
        status_line = f"\nStatut pool : {status}." if status else ""
        return (
            f"{head}\n"
            f"Aucune analyse enregistrée sur ce token pour l'instant.{status_line}\n\n"
            f"Pour lancer une analyse complète : /vc {contract}\n"
            f"Pour un contrôle de risque rapide : /scan {contract}"
        )

    c = dossier.get("counts", {})
    lines = [
        head,
        f"{c.get('analyses', 0)} analyse(s) · {c.get('suivis', 0)} suivi(s) · "
        f"{c.get('paper', 0)} position(s) paper",
        "",
        "Chronologie (récent → ancien) :",
    ]
    for ev in events[:limit_events]:
        lines.append(f"[{_fmt_ts(ev.get('at'))}] {ev.get('summary')}")
    if len(events) > limit_events:
        lines.append(f"… (+{len(events) - limit_events} événement(s) plus anciens)")
    return "\n".join(lines)
