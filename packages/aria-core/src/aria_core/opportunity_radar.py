"""Radar d'opportunités — mine les conversations (posts + COMMENTAIRES) autour de Base et
d'ailleurs pour en extraire des idées à fusionner dans ARIA.

L'or est souvent dans les réponses (« ça pourrait permettre de… », « il faudrait… »,
« someone should build… »). Ce module détecte ce langage d'opportunité, repère les accroches
techniques (x402, MCP, agent, onchain…), score, déduplique et classe — puis surface à
l'opérateur (jamais d'action autonome ; lecture seule).

Découplé de la SOURCE : `mine_threads`/`extract_opportunities` prennent du TEXTE. La récupération
des fils X (radar lecture) est un seam à brancher quand l'API X lecture est active ; en
attendant, on peut alimenter le radar avec des textes collés (opérateur) ou d'autres flux.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Langage d'opportunité (FR + EN) — signaux qu'un message propose/appelle une idée.
_OPPORTUNITY_PATTERNS = [
    r"could (?:enable|allow|power|unlock|let|help)",
    r"would be (?:great|amazing|nice|cool|huge|perfect|sick|dope)\b",
    r"someone should (?:build|make|create|ship)",
    r"imagine (?:if|a|being)",
    r"there'?s (?:an|a huge|a real) opportunity",
    r"\bgap in\b|\bmissing\b|\bwe need\b|\bneeds? an?\b",
    r"wish (?:there was|we had|someone)",
    r"why (?:isn'?t there|doesn'?t|is there no)",
    r"what if\b|this unlocks|perfect for|killer (?:app|feature|use ?case)",
    r"would love (?:to see|a|an)\b",
    r"pourrait (?:permettre|servir|aider|d[eé]bloquer)",
    r"il (?:faudrait|manque)\b|ce serait (?:bien|g[eé]nial|top|parfait)",
    r"on pourrait\b|quelqu'?un devrait\b|il y a une opportunit[eé]",
    r"manque (?:de|un|une|d')\b|j'?aimerais\b|besoin d'?un?e?\b|et si\b",
]
_OPP_RE = re.compile("|".join(_OPPORTUNITY_PATTERNS), re.IGNORECASE)

# Accroches techniques pertinentes pour ARIA (écosystème Base + agentique).
_TECH_HOOKS = [
    "x402", "mcp", "agent kit", "agentkit", "onchain", "on-chain", "smart account",
    "paymaster", "minikit", "base app", "attestation", "eas", "sdk", "oracle",
    "farcaster", "frame", "webhook", "api", "grant", "batches", "defi", "wallet",
    "agent", "verifiable", "proof", "signal", "scan", "honeypot", "token",
]
_HOOK_RE = re.compile(r"\b(" + "|".join(re.escape(h) for h in _TECH_HOOKS) + r")\b", re.IGNORECASE)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…\n])\s+")
_MIN_LEN = 20
_DEFAULT_THRESHOLD = 3.0


@dataclass
class OpportunityCandidate:
    idea: str                       # la phrase/segment porteur d'opportunité
    source: str                     # d'où vient le texte (ex. "x:@base/reply", "manual")
    score: float
    signals: list[str] = field(default_factory=list)   # tournures d'opportunité détectées
    tech_hooks: list[str] = field(default_factory=list) # accroches techniques repérées

    def as_dict(self) -> dict[str, Any]:
        return {
            "idea": self.idea, "source": self.source, "score": round(self.score, 1),
            "signals": self.signals, "tech_hooks": self.tech_hooks,
        }


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _segments(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split((text or "").strip()) if s.strip()]


def extract_opportunities(
    text: str, *, source: str = "manual", threshold: float = _DEFAULT_THRESHOLD,
) -> list[OpportunityCandidate]:
    """Extrait les segments porteurs d'opportunité d'un texte (post OU commentaire)."""
    out: list[OpportunityCandidate] = []
    for seg in _segments(text):
        if len(seg) < _MIN_LEN:
            continue
        signals = sorted({m.group(0).lower() for m in _OPP_RE.finditer(seg)})
        hooks = sorted({m.group(1).lower() for m in _HOOK_RE.finditer(seg)})
        if not signals and not hooks:
            continue
        # Score : le langage d'opportunité pèse plus (3) que les accroches (2) ; petit bonus
        # de substance. Un segment sans signal d'opportunité (accroche seule) reste sous le seuil.
        score = 3.0 * len(signals) + 2.0 * len(hooks) + min(len(seg), 200) / 100.0
        if score >= threshold and signals:
            out.append(OpportunityCandidate(
                idea=seg[:300], source=source, score=score, signals=signals, tech_hooks=hooks,
            ))
    return out


def mine_threads(
    threads: Iterable[dict[str, Any]], *, threshold: float = _DEFAULT_THRESHOLD,
) -> list[OpportunityCandidate]:
    """Mine une liste de fils. Un fil = {handle, text, replies:[{handle,text}, ...]}.

    On mine le post ET surtout les COMMENTAIRES (souvent les plus riches en idées)."""
    cands: list[OpportunityCandidate] = []
    for thread in threads:
        handle = str(thread.get("handle") or "?").lstrip("@")
        root = str(thread.get("text") or "")
        cands += extract_opportunities(root, source=f"x:@{handle}", threshold=threshold)
        for reply in thread.get("replies") or []:
            rh = str(reply.get("handle") or "?").lstrip("@")
            rt = str(reply.get("text") or "")
            cands += extract_opportunities(rt, source=f"x:@{handle}/reply:@{rh}", threshold=threshold)
    return cands


def rank_opportunities(
    candidates: list[OpportunityCandidate], *, top: int | None = None,
) -> list[OpportunityCandidate]:
    """Trie par score décroissant et déduplique les idées quasi identiques."""
    seen: set[str] = set()
    ranked: list[OpportunityCandidate] = []
    for c in sorted(candidates, key=lambda x: x.score, reverse=True):
        key = _norm(c.idea)[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        ranked.append(c)
    return ranked[:top] if top else ranked


def format_operator_digest(candidates: list[OpportunityCandidate], *, lang: str = "fr", top: int = 10) -> str:
    """Digest lisible pour l'opérateur (Telegram). Surface les idées, ne décide rien."""
    ranked = rank_opportunities(candidates, top=top)
    if not ranked:
        return ("Aucune opportunité détectée dans les conversations analysées."
                if lang == "fr" else "No opportunities detected in the analyzed conversations.")
    head = ("Opportunites Base a evaluer (surface, decision operateur) :"
            if lang == "fr" else "Base opportunities to review (operator decides):")
    lines = [head, ""]
    for i, c in enumerate(ranked, 1):
        hooks = f" [{', '.join(c.tech_hooks)}]" if c.tech_hooks else ""
        lines.append(f"{i}. {c.idea}{hooks}")
        lines.append(f"   source: {c.source}")
    return "\n".join(lines)


def _main() -> None:
    """CLI : alimente le radar avec du texte collé (une ligne = un commentaire), ou un JSON
    de fils sur --threads. Usage : python -m aria_core.opportunity_radar < commentaires.txt"""
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--threads":
        threads = json.load(sys.stdin)
        cands = mine_threads(threads)
    else:
        cands = []
        for line in sys.stdin:
            cands += extract_opportunities(line, source="manual")
    print(format_operator_digest(cands, lang="fr"))


if __name__ == "__main__":
    _main()
