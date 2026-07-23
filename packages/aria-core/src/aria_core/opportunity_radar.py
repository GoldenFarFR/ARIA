"""Opportunity radar — mines conversations (posts + COMMENTS) around Base and
elsewhere to extract ideas to merge into ARIA.

The gold is often in the replies (« this could enable... », « we'd need... »,
« someone should build... »). This module detects this opportunity language, spots
technical hooks (x402, MCP, agent, onchain...), scores, deduplicates and ranks — then
surfaces it to the operator (never autonomous action; read-only).

Decoupled from the SOURCE: `mine_threads`/`extract_opportunities` take TEXT. Fetching
X threads (read radar) is a seam to wire up once the X read API is active; in the
meantime, the radar can be fed pasted text (operator) or other feeds.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Opportunity language (FR + EN) — signals that a message proposes/calls for an idea.
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

# Technical hooks relevant to ARIA (Base ecosystem + agentic).
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
    idea: str                       # the sentence/segment carrying the opportunity
    source: str                     # where the text comes from (e.g. "x:@base/reply", "manual")
    score: float
    signals: list[str] = field(default_factory=list)   # opportunity phrasings detected
    tech_hooks: list[str] = field(default_factory=list) # technical hooks spotted

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
    """Extracts opportunity-carrying segments from a text (post OR comment)."""
    out: list[OpportunityCandidate] = []
    for seg in _segments(text):
        if len(seg) < _MIN_LEN:
            continue
        signals = sorted({m.group(0).lower() for m in _OPP_RE.finditer(seg)})
        hooks = sorted({m.group(1).lower() for m in _HOOK_RE.finditer(seg)})
        if not signals and not hooks:
            continue
        # Score: opportunity language weighs more (3) than hooks (2); small substance
        # bonus. A segment with no opportunity signal (hook only) stays below threshold.
        score = 3.0 * len(signals) + 2.0 * len(hooks) + min(len(seg), 200) / 100.0
        if score >= threshold and signals:
            out.append(OpportunityCandidate(
                idea=seg[:300], source=source, score=score, signals=signals, tech_hooks=hooks,
            ))
    return out


def opportunity_radar_enabled() -> bool:
    """Gate for the operator DIGEST (task #52) -- OFF by default, outward-facing (Telegram push)."""
    return os.environ.get("ARIA_OPPORTUNITY_RADAR_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def mine_curiosity_items(
    items: Iterable[dict[str, Any]],
    opportunity_handles: Iterable[str],
    *,
    threshold: float = _DEFAULT_THRESHOLD,
) -> list[OpportunityCandidate]:
    """Filters `fetch_curiosity_feed` items to "opportunity" accounts (e.g. @base,
    @Whale_AI_net -- cf. `x_watchlist.opportunity_watch_handles`), then mines them.

    Root-only for now: the current X read client (`gateway/x_twitter.py`) has no
    replies/search endpoint -- only the post text is mineable, not the comments
    (despite the module's name, cf. the top docstring). Reuses the fetch already done by
    the existing curiosity cycle -- no extra X call."""
    handles = {h.lstrip("@").lower() for h in opportunity_handles}
    if not handles:
        return []
    cands: list[OpportunityCandidate] = []
    for item in items:
        topic = str(item.get("topic") or "").lstrip("@").lower()
        if topic not in handles:
            continue
        text = str(item.get("text") or "")
        cands += extract_opportunities(text, source=f"x:@{topic}", threshold=threshold)
    return cands


def mine_threads(
    threads: Iterable[dict[str, Any]], *, threshold: float = _DEFAULT_THRESHOLD,
) -> list[OpportunityCandidate]:
    """Mines a list of threads. A thread = {handle, text, replies:[{handle,text}, ...]}.

    Mines the post AND especially the COMMENTS (often the richest in ideas)."""
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
    """Sorts by descending score and deduplicates near-identical ideas."""
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
    """Human-readable digest for the operator (Telegram). Surfaces ideas, decides nothing."""
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
    """CLI: feeds the radar with pasted text (one line = one comment), or a JSON
    of threads on --threads. Usage: python -m aria_core.opportunity_radar < comments.txt"""
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
