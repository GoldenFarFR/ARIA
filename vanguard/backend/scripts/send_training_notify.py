import asyncio

import httpx

from app.config import settings


async def main() -> None:
    admin = settings.admin_ids[0] if settings.admin_ids else None
    token = settings.telegram_bot_token.strip()
    if not admin or not token:
        print("telegram_skip")
        return
    msg = (
        "ARIA — Entrainement execute pour toi\n\n"
        "Portefeuille fictif: 947 USD\n"
        "(-80 depenses, +27 ventes simulees)\n\n"
        "Signal Brief #0 produit:\n"
        "ZINC 71 | JAMESON 70 | LOA 67 | FARM 66.5 | PHT 57\n"
        "Verdict: tous NEUTRAL\n\n"
        "Fichiers:\n"
        "data/memory/training_portfolio.md\n"
        "data/memory/signal_brief_00.md"
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, json={"chat_id": admin, "text": msg})
        print("ok" if r.json().get("ok") else r.text[:200])


if __name__ == "__main__":
    asyncio.run(main())