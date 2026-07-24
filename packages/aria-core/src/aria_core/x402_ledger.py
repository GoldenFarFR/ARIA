"""x402 consolidated ledger (07/24, direct operator request: "livre des
recettes -- sortie et entrée x402 de tout les wallet cumulé"). Combines
``x402_budget.py`` (what ARIA spends, already in production for months) and
``x402_revenue_ledger.py`` (what ARIA earns, new -- the seller path is still
dormant, so this starts at zero and grows once the FastAPI route is live)
into a single net view.

Only ONE real wallet does x402 payments today (``aria-wallet-X402-EVM``,
confirmed: the very same address both spends on Cybercentry/Otto AI/
BlockRun AND receives ARIA's own sales) -- ``by_wallet`` below is still
computed generically (never hardcoded to that one name) so a future second
x402-paying wallet is aggregated correctly without a rewrite."""
from __future__ import annotations

from datetime import datetime


async def consolidated_summary(since: datetime | None = None) -> dict:
    """Net x402 position: total spent, total earned, net (earned - spent),
    broken down by wallet where the underlying data allows it.

    Spend-side wallet attribution: ``x402_spend_log`` has no payer-wallet
    column (every spend today implicitly comes from the same single CDP
    wallet) -- spends are reported under ``_UNKNOWN_SPEND_WALLET`` rather
    than guessing a name, never silently mislabeled as the seller's
    receiving wallet."""
    from aria_core import x402_budget
    from aria_core.x402_revenue_ledger import DEFAULT_RECEIVING_WALLET, list_sales

    spends = await x402_budget.list_spends(limit=100_000)
    sales = await list_sales(limit=100_000)

    if since is not None:
        cutoff = since.isoformat()
        spends = [s for s in spends if s.get("created_at", "") >= cutoff]
        sales = [s for s in sales if s.get("created_at", "") >= cutoff]

    total_spent = sum(float(s.get("amount_usd") or 0.0) for s in spends if s.get("status") == "ok")
    total_revenue = sum(float(s.get("amount_usd") or 0.0) for s in sales if s.get("status") == "ok")

    by_wallet: dict[str, dict] = {}

    def _bucket(wallet: str) -> dict:
        return by_wallet.setdefault(wallet, {"spent_usd": 0.0, "revenue_usd": 0.0})

    _UNKNOWN_SPEND_WALLET = "wallet_payeur_non_trace_x402_spend_log"
    for s in spends:
        if s.get("status") != "ok":
            continue
        _bucket(_UNKNOWN_SPEND_WALLET)["spent_usd"] += float(s.get("amount_usd") or 0.0)

    for s in sales:
        if s.get("status") != "ok":
            continue
        wallet = s.get("wallet") or DEFAULT_RECEIVING_WALLET
        _bucket(wallet)["revenue_usd"] += float(s.get("amount_usd") or 0.0)

    for bucket in by_wallet.values():
        bucket["net_usd"] = round(bucket["revenue_usd"] - bucket["spent_usd"], 6)
        bucket["spent_usd"] = round(bucket["spent_usd"], 6)
        bucket["revenue_usd"] = round(bucket["revenue_usd"], 6)

    return {
        "total_spent_usd": round(total_spent, 6),
        "total_revenue_usd": round(total_revenue, 6),
        "net_usd": round(total_revenue - total_spent, 6),
        "by_wallet": by_wallet,
    }


def format_consolidated_summary(summary: dict) -> str:
    """Telegram-friendly rendering."""
    lines = [
        "📒 Livre de recettes x402 (cumulé)",
        f"Dépenses totales : ${summary['total_spent_usd']:.4f}",
        f"Recettes totales : ${summary['total_revenue_usd']:.4f}",
        f"Net : ${summary['net_usd']:.4f}",
    ]
    if summary["by_wallet"]:
        lines.append("")
        lines.append("Par wallet :")
        for wallet, data in summary["by_wallet"].items():
            lines.append(
                f"  {wallet} -- dépensé ${data['spent_usd']:.4f}, "
                f"gagné ${data['revenue_usd']:.4f}, net ${data['net_usd']:.4f}"
            )
    return "\n".join(lines)
