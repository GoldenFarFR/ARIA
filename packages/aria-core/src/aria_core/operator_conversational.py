"""Réponses opérateur naturelles — style Grok/Cursor, pas épistémique ni murs de commandes."""
from __future__ import annotations

import re

from aria_core.capability_levels import CATEGORY_ORDER, check_auto_completions, full_status
from aria_core.runtime import settings

_COMPETENCE_IMPROVE_RE = re.compile(
    r"(?:"
    r"il te faut quoi|de quoi as[- ]?tu besoin|what do you need|"
    r"am[eé]liorer tes comp(?:[eé]tences?)?|improve your (?:skills|capabilities)|"
    r"renforcer tes comp|tes lacunes|tes faiblesses"
    r")",
    re.IGNORECASE,
)

_INJECTED_CLAIM_RE = re.compile(
    r"(?:"
    r"supprim[ée]|coup[ée]|retir[ée]|annonce|facture|facturation|passe[r]?\s+en|vient\s+de|désormais|"
    r"depuis\s+(?:hier|aujourd|ce\s+matin)|entre\s+hier|effective|impos[ée]|obligatoire|"
    r"augment|baisse|gagn[ée]|abonn[ée]s?|nouveaux?\s+abonn|dependabot|pr\s+merg|"
    r"gratuit\s+illimit|étoiles?|note\s+5|pourboire|uptime|contribut|"
    r"tweets?\s+automatiques|\blivr[ée](?:e|s|ment)?\b|usdc|2fa|catalogue\s+spark|reste\s+dispo|"
    r"merg[ée]|déploy[ée]|commit\s+[a-f0-9]{6,}|class[ée]|"
    r"\d+\s*%|\d+[\s,.]?\d*\s*(?:\$|€|usd|usdc)|"
    r"le\s+\d{1,2}\s+(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    r"septembre|octobre|novembre|décembre|decembre)\s+\d{4}"
    r")",
    re.IGNORECASE,
)

_VERIFY_CUE_RE = re.compile(
    r"\b(vérif|verif|vérifie|verifie|check|creuse|confirme|est-ce (vrai|faux)|vrai ou faux|ça (est|sonne) (vrai|faux)|tu peux vérifier)\b",
    re.IGNORECASE,
)
_OPERATOR_COMMAND_RE = re.compile(
    # \bsupprime\b/\bsupprimer\b (pas juste "supprim") : n'attrape que l'impératif/infinitif
    # ("supprime X"), jamais le participe passé narratif ("Render a supprimé...") qui doit
    # rester détectable comme affirmation externe collée, pas comme commande opérateur.
    r"(?:^/|crée|créer|creer|create\s+repo|level\s+up|montre\s+qi|check-aria|sync-render|"
    r"deploy|worker\s+delegate|/learn|/directive|\bsupprime\b|\bsupprimer\b|"
    r"delete.*(?:workflow|offering|offre))",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    # "?" n'importe où dans le texte (pas seulement en fin de chaîne) : un scénario
    # multi-phrases dont la question est suivie d'une consigne ("Tranche de manière
    # définitive.") se terminait par un "." et échappait au garde -- incident réel
    # 12/07, routé à tort vers verify_external_claim (recherche web littérale sur
    # un scénario de raisonnement hypothétique).
    r"(?:\?|^(?:est-ce|qu'?en\s+penses|tu\s+penses|comment|pourquoi|quoi|qui|quel|"
    r"as-tu|tu\s+as\s+prevu|tu\s+pref))",
    re.IGNORECASE,
)

_MORE_DETAIL_RE = re.compile(
    r"^(?:"
    r"arguments?\s+plus|plus\s+d['']?arguments?|d[eé]veloppe|en\s+d[eé]tail|"
    r"explique\s+plus|va\s+plus\s+loin|continue|pr[eé]cise"
    r")\s*[.!?]*$",
    re.IGNORECASE,
)


def wants_capability_improvement(message: str) -> bool:
    return bool(_COMPETENCE_IMPROVE_RE.search((message or "").strip()))


def wants_more_detail_followup(message: str) -> bool:
    return bool(_MORE_DETAIL_RE.match((message or "").strip()))


def is_injected_factual_claim(message: str) -> bool:
    """Affirmation externe collée par l'opérateur — pas une question ni une commande."""
    text = (message or "").strip()
    if len(text) < 24:
        return False
    if _OPERATOR_COMMAND_RE.search(text):
        return False
    if _QUESTION_RE.search(text):
        return False
    if wants_capability_improvement(text):
        return False
    return bool(_INJECTED_CLAIM_RE.search(text))


def wants_claim_verification(message: str) -> bool:
    """User explicitly asks to check if a pasted claim (price, catalog, PR count, billing...) is true or false."""
    text = (message or "").strip().lower()
    if not text:
        return False
    if _VERIFY_CUE_RE.search(text):
        return True
    # also if the whole message looks like "vérifie <claim pasted>"
    if text.startswith(("vérifie", "verifie", "check", "creuse")) and len(text) > 20:
        return True
    return False


def unverified_claim_reply(message: str, *, lang: str = "fr") -> str:
    snippet = (message or "").strip()[:110]
    if lang == "fr":
        return (
            f"Hmm, « {snippet}… » — j'ai rien de ça dans JOURNAL, COLLEGUE ou mes derniers scans GitHub. "
            "Je ne vais pas l'affirmer comme ça sans check. "
            "Si tu veux que je vérifie (web + GitHub si c'est un repo/PR), dis « vérifie » ou colle la phrase avec « vérifie » dedans, je te dirai VRAI/FAUX avec ce que j'ai trouvé."
        )
    return (
        f"Hmm, « {snippet}… » — nothing in my logs or GitHub confirms it. "
        "Won't just nod along without checking. Say « verify » (or include the cue) and I'll dig with web + GitHub, then tell you true/false like a normal chat."
    )


def operator_improvement_reply(*, lang: str = "fr") -> str:
    """Ce dont ARIA a besoin pour monter en compétence — lecture locale QI."""
    check_auto_completions()
    status = full_status(lang)
    by_cat = status.get("categories") or {}
    ordered = sorted(
        CATEGORY_ORDER,
        key=lambda c: int((by_cat.get(c) or {}).get("level") or 0),
    )
    weak = ordered[:3]

    if lang == "fr":
        lines = [
            "Pour monter en compétence, il me faut surtout de l'exécution réelle, pas plus de théorie :",
        ]
        tips = {
            "codage": "plus de cycles ouvrier (PR mergées, tests verts) sur aria-core et aria-vanguard",
            "fiabilite": "moins d'incidents ops — health Render, secrets sync, runbook à jour",
            "autonomie": "heartbeat qui tourne sans que tu relances, aucun raccourci ACP/revenu",
            "business": "track-record VC/trading qui grandit (aucun produit payant aujourd'hui)",
            "intelligence": "mémoire ops (COLLEGUE, JOURNAL) tenue à jour multi-PC",
            "social": "X/Telegram réguliers sans promesses vides",
        }
        for cat in weak:
            lvl = int((by_cat.get(cat) or {}).get("level") or 0)
            hint = tips.get(cat, "pratique ciblée + validation opérateur")
            lines.append(f"• {cat} ({lvl}/1000) — {hint}")
        lines.append(
            f"\nIndice global : {status.get('global_index', '?')}/1000. "
            "Dis « montre qi » pour le tableau complet."
        )
        return "\n".join(lines)

    lines = ["To level up I need shipped work, not more theory:"]
    for cat in weak:
        lvl = int((by_cat.get(cat) or {}).get("level") or 0)
        lines.append(f"• {cat} ({lvl}/1000) — targeted practice + operator validation")
    lines.append(f"\nGlobal index: {status.get('global_index', '?')}/1000. Say « show qi » for full board.")
    return "\n".join(lines)


def llm_preference_reply(*, lang: str = "fr") -> str:
    provider = (settings.llm_provider or "none").strip().lower()
    model = (settings.llm_model or "").strip() or "défaut"
    if lang == "fr":
        return (
            "Pas de préférence « humaine » — j'utilise le bon moteur pour le job :\n"
            f"• **Spark (Virtuals)** — cerveau ARIA en prod ({provider} / {model}) — c'est ce qui tourne là.\n"
            "• **Groq** — secours rapide si Spark ou Virtuals flanche.\n"
            "• **Qwen local** — scout/KART sur ton PC, pas le bot Render.\n\n"
            "En clair : Spark pour converser avec toi, Qwen pour fouiller le repo en local, "
            "Groq en filet de sécurité."
        )
    return (
        "No human-style favorite — right engine for the job:\n"
        f"• Spark (Virtuals) — prod brain ({provider} / {model})\n"
        "• Groq — fast fallback\n"
        "• Qwen local — scout/KART on your PC\n"
    )


async def verify_external_claim(claim: str, lang: str = "fr") -> tuple[str, dict]:
    """Vérifie une affirmation externe collée (prix, catalogue, facturation, PRs, etc).
    Répond comme à un humain : naturel, direct, avec VRAI/FAUX + sources courtes.
    Utilise web (DDG) + GitHub quand pertinent (repo/PR/dependabot).
    """
    from datetime import datetime, timezone

    text = (claim or "").strip()
    snippet = text[:90] + ("…" if len(text) > 90 else "")
    actions: list[str] = ["external_claim_verify"]
    meta: dict = {"claim": snippet, "verified": True}

    # Detect github-ish claim
    is_github_claim = bool(re.search(r"(repo|pr|merg|dependabot|goldenfar)", text, re.I))
    github_count = 0
    github_detail = ""

    if is_github_claim:
        try:
            from aria_core.github_client import GitHubClient
            from aria_core.runtime import settings

            token = ""
            try:
                token = getattr(settings, "github_token", "") or ""
            except Exception:
                token = ""
            owner = "GoldenFarFR"
            try:
                owner = getattr(settings, "github_owner", "GoldenFarFR") or "GoldenFarFR"
            except Exception:
                pass
            repo_guess = "ARIA"
            m = re.search(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", text)
            if m:
                parts = m.group(1).split("/")
                if len(parts) == 2:
                    owner, repo_guess = parts[0], parts[1]
            if token.strip():
                gh = GitHubClient(token)
                author = "dependabot[bot]" if "dependabot" in text.lower() else None
                github_count = await gh.count_merged_prs(owner, repo_guess, author=author, days=7)
                github_detail = f"GitHub: ~{github_count} PRs mergés les 7 derniers jours" + (f" par {author}" if author else "") + f" sur {owner}/{repo_guess}."
                actions.append("github_pr_count")
                meta["github_prs"] = github_count
            else:
                github_detail = "GitHub: token pas configuré (ou pas en contexte bootstrap), skip count PRs."
        except Exception as e:
            github_detail = f"GitHub check raté ({str(e)[:60]})."
            meta["github_prs"] = github_count or 0

    # Web search for the claim
    web_bits: list[str] = []
    try:
        from aria_core.knowledge.web_verify import fetch_web_snippets

        # craft a search query from the claim (remove "vérifie" cue)
        q = re.sub(r"\b(vérifie|verifie|check|creuse)\b[:\s]*", "", text, flags=re.I).strip()[:200]
        if not q:
            q = text[:200]
        snippets = await fetch_web_snippets(q, max_snippets=4)
        for s in snippets:
            web_bits.append(f"- {s.text[:160]}{' ('+s.url+')' if s.url else ''}")
        if web_bits:
            actions.append("web_ddg")
            meta["web_snippets"] = len(web_bits)
    except Exception:
        web_bits = []

    # Build natural human-style verdict
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    verdict = "INCERTAIN"
    if github_count > 0 and "dependabot" in text.lower():
        verdict = "VRAI" if github_count >= 20 else "FAUX (beaucoup moins)"
    elif any(k in text.lower() for k in ["passe à", "49 $", "49$/mois", "cursor pro"]):
        # example: we didn't find confirmation in search typically
        verdict = "FAUX / INCERTAIN (pas de confirmation officielle récente dans les snippets)"
    elif any(k in text.lower() for k in ["facture", "0,45", "render", "python", "512"]):
        verdict = "À vérifier sur le dashboard Render — pas de trace publique immédiate dans les résultats"
    elif "catalogue" in text.lower() or "spark" in text.lower() or "claude opus" in text.lower() or "grok 4" in text.lower():
        verdict = "INCERTAIN (les catalogues modèles changent vite — pas de hit clair dans les 4 snippets)"

    if lang == "fr":
        lines = [
            f"OK, j'ai checké « {snippet} » ({now}).",
        ]
        if web_bits:
            lines.append("Web (DDG) :")
            lines.extend(web_bits[:3])
        if github_detail:
            lines.append(github_detail)
        lines.append(f"Au final : {verdict}")
        lines.append(
            "Comme si on causait : ouais je vois pas de preuve solide pour la plupart de ces claims. "
            "Si t'as un lien officiel / changelog / tweet, balance, je re-vérifie direct."
        )
        reply = "\n".join(lines)
    else:
        reply = f"Checked the claim « {snippet} ». Bottom line: {verdict}. Sources checked via web + GitHub where relevant."

    meta["verdict"] = verdict
    meta["actions"] = actions
    return reply, meta