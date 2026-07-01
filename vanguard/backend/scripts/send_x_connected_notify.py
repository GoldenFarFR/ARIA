import asyncio

import httpx

from app.config import settings


async def main() -> None:
    admin = settings.admin_ids[0] if settings.admin_ids else None
    token = settings.telegram_bot_token.strip()
    if not admin or not token:
        return

    from aria_core.gateway.x_twitter import is_x_post_configured, is_x_read_configured, verify_x_connection

    post_ok = is_x_post_configured()
    read_ok = is_x_read_configured()
    verify_msg = "OAuth keys manquantes"
    if post_ok:
        _, verify_msg = await verify_x_connection()

    msg = (
        "ARIA — X status\n\n"
        f"Compte: @Aria_ZHC\n"
        f"Lecture (Bearer): {'oui' if read_ok else 'non'}\n"
        f"Publication (OAuth): {'oui' if post_ok else 'non'}\n"
        f"Vérification: {verify_msg}\n\n"
        "Test: /x status"
    )
    if post_ok:
        msg += "\nPremier tweet: /x post ARIA ZHC online — Aria Vanguard ZHC holding."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(url, json={"chat_id": admin, "text": msg})


if __name__ == "__main__":
    asyncio.run(main())