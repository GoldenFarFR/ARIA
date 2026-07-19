"""Client de lecture seule pour un CANAL/GROUPE TIERS déclaré par un projet (19/07,
retour opérateur : "telegram c'est possible ?") -- sans rapport avec le bot
Telegram d'ARIA elle-même (``gateway/telegram_bot.py``), aucun token ne peut/ne doit
être réutilisé ici.

`t.me/s/<canal>` (la page d'aperçu public HTML de Telegram, vérifiée en direct,
19/07) ne nécessite AUCUN bot token -- Telegram l'expose pour l'indexation web de
tout canal PUBLIC. Un canal inexistant ou sans historique public redirige vers
`t.me/<canal>` (sans le `/s/`) -- signal fiable, vérifié empiriquement (redirection
confirmée, code 200 sur la page finale sans le préfixe `/s/`). Fragile par nature
(dépend du HTML de Telegram, aucune garantie de stabilité contractuelle contrairement
à une vraie API) -- dégradation honnête systématique si le format attendu n'est pas
retrouvé, jamais un chiffre inventé."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_HANDLE_RE = re.compile(r"t\.me/(?:s/)?([\w\-]+)", re.IGNORECASE)
_SUBSCRIBER_RE = re.compile(
    r'counter_value">([\d.,]+[KMB]?)</span>\s*<span class="counter_type">subscribers',
)
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')
_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class TelegramChannelVerification:
    available: bool
    exists: bool | None = None  # None = jamais résolu, False = pas de canal public/historique
    subscriber_count_display: str | None = None  # texte brut Telegram ("11.6M") -- jamais reparsé en int, formats ambigus
    days_since_last_post: int | None = None
    error: str | None = None


def _parse_handle(url: str) -> str | None:
    m = _HANDLE_RE.search(url or "")
    if not m:
        return None
    handle = m.group(1).strip("/")
    return handle or None


async def verify_channel(url: str) -> TelegramChannelVerification:
    """Vérifie un canal Telegram déclaré. Jamais une exception qui remonte."""
    handle = _parse_handle(url)
    if not handle:
        return TelegramChannelVerification(available=False, error="URL Telegram illisible")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=True) as client:
            res = await client.get(f"https://t.me/s/{handle}")
    except Exception as exc:  # noqa: BLE001
        logger.info("telegram_channel_verify: requête échouée pour %s (%s)", handle, exc)
        return TelegramChannelVerification(available=False, error=f"requête échouée ({exc})")

    if res.status_code != 200:
        return TelegramChannelVerification(available=False, error=f"HTTP {res.status_code}")

    # Redirection vers la page sans `/s/` -- pas de canal public avec historique
    # (n'existe pas, est privé, ou n'a jamais posté publiquement).
    if "/s/" not in str(res.url):
        return TelegramChannelVerification(available=True, exists=False)

    text = res.text
    sub_match = _SUBSCRIBER_RE.search(text)
    subscriber_display = sub_match.group(1) if sub_match else None

    time_matches = _TIME_RE.findall(text)
    days_since_last_post = None
    if time_matches:
        try:
            last_dt = datetime.fromisoformat(time_matches[-1])
            days_since_last_post = max(0, (datetime.now(timezone.utc) - last_dt).days)
        except ValueError:
            pass

    return TelegramChannelVerification(
        available=True, exists=True,
        subscriber_count_display=subscriber_display,
        days_since_last_post=days_since_last_post,
    )


def format_channel_verification(v: TelegramChannelVerification) -> str:
    if not v.available:
        return "vérification indisponible"
    if v.exists is False:
        return "canal introuvable ou sans historique public (lien mort ou privé -- signal négatif)"
    parts = []
    if v.subscriber_count_display:
        parts.append(f"{v.subscriber_count_display} abonnés")
    if v.days_since_last_post is not None:
        parts.append(f"dernier message il y a {v.days_since_last_post}j")
    return ", ".join(parts) if parts else "canal trouvé, détails indisponibles"
