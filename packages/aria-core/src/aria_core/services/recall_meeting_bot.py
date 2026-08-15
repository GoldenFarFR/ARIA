"""Recall.ai meeting-bot client -- lets ARIA join a live video call (Google
Meet/Zoom/Teams) as a participant. Google exposes no official API for
real-time audio/video access to a Meet call (verified via WebSearch, 15/08:
"Google does not provide an official API for accessing real-time audio or
video streams from Google Meet") -- Recall.ai is a third-party bot that
joins via the meeting link and streams media in/out over a webpage it
controls (their "Output Media" feature).

Seam only, per docs/architecture-extensibilite.md: this module owns the
Recall.ai REST surface (create/inspect/stop a bot, configure output media).
It does NOT own the conversation loop (STT -> Haiku -> TTS/avatar) -- that
is a future orchestration layer consuming this client's webhook events, not
built yet (2026-08-15, operator-requested prototype, no real account/key
created yet -- see docs/HANDOFF_TELEGRAM.md's voice/avatar note). Nothing in
this module is wired into the heartbeat; it stays inert (no network call
possible) until a real key/base URL are configured, and a feature gate will
be declared in test_coherence.py's known-gates registry the day an actual
caller wires one in -- never named in advance of that wiring.

Sourced (docs.recall.ai, verified via WebFetch, 15/08):
- POST /api/v1/bot/ -- create a bot. Body: {"meeting_url", "bot_name",
  "recording_config": {"realtime_endpoints": [{"type": "webhook", "url",
  "events": [...]}]}, "output_media": {"camera": {"kind": "webpage",
  "config": {"url": ...}}}}. Returns 201 with {"id", "meeting_url",
  "bot_name", "status_changes": [...]}.
- POST /api/v1/bot/{id}/output_media/ -- (re)configure output media on an
  already-running bot. Same `output_media` shape as above. 300 req/min per
  workspace.
- GET /api/v1/bot/{id}/ -- current bot status.
- POST /api/v1/bot/{id}/leave_call/ -- stop the bot.
- Auth: `Authorization: Bearer <key>` (header name NOT reconfirmed against
  a real account yet -- Recall.ai's docs use a bare token scheme in some
  places; VERIFY against a real key before first live call, per the "every
  new external API client tested against a REAL live call" process norm).
- Region-scoped base URL (Recall.ai hosts several regions, e.g.
  us-east-1.recall.ai) -- deliberately NOT hardcoded to a guessed region;
  `RECALL_AI_API_BASE` must be set from the real workspace's dashboard.
- Realtime transcript events arrive on the configured webhook (never
  polled -- "Polling for bot status changes is an anti-pattern" per their
  own docs) as `transcript.data` / `transcript.partial_data`.

No key created, no account exists yet -- `is_recall_configured()` gates every
network call so this module is fully inert (returns unavailable results)
until real credentials are provided, same doctrine as `services/firecrawl.py`.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0

UNAVAILABLE = "donnée Recall.ai indisponible"


def recall_api_key() -> str:
    """Recall.ai key from the env ONLY (never hardcoded, never logged)."""
    return os.environ.get("RECALL_AI_API_KEY", "").strip()


def recall_api_base() -> str:
    """Region-scoped base URL, e.g. https://us-east-1.recall.ai/api/v1 --
    must be set explicitly (no guessed default, see module docstring)."""
    return os.environ.get("RECALL_AI_API_BASE", "").strip().rstrip("/")


def is_recall_configured() -> bool:
    return bool(recall_api_key()) and bool(recall_api_base())


@dataclass
class MeetingBot:
    """A Recall.ai bot's known state -- only fields this module actually
    reads are typed here; the raw API response carries more (recordings,
    status_changes history) that a future caller can add on demand."""

    bot_id: str
    meeting_url: str
    status: str = "unknown"
    available: bool = False
    error: str | None = None


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {recall_api_key()}",
        "Content-Type": "application/json",
    }


class RecallMeetingBotClient:
    """Async HTTP client, thin wrapper -- never raises to the caller (same
    degradation doctrine as every other services/*.py client: a network
    failure returns an `available=False` MeetingBot, never an exception)."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    async def create_bot(
        self,
        *,
        meeting_url: str,
        bot_name: str = "ARIA",
        webhook_url: str | None = None,
        output_webpage_url: str | None = None,
    ) -> MeetingBot:
        """Join `meeting_url` as a bot. `webhook_url` (if set) receives
        real-time transcript events; `output_webpage_url` (if set) is the
        page the bot streams into the call as its camera/mic (the future
        conversation-loop frontend -- not built yet)."""
        if not is_recall_configured():
            return MeetingBot(
                bot_id="", meeting_url=meeting_url, available=False,
                error=f"{UNAVAILABLE} (RECALL_AI_API_KEY/RECALL_AI_API_BASE non configurés)",
            )
        if not (meeting_url or "").strip():
            return MeetingBot(bot_id="", meeting_url=meeting_url, available=False, error="meeting_url manquant")

        body: dict = {"meeting_url": meeting_url, "bot_name": bot_name}
        recording_config: dict = {}
        if webhook_url:
            recording_config["realtime_endpoints"] = [{
                "type": "webhook",
                "url": webhook_url,
                "events": ["transcript.data", "transcript.partial_data"],
            }]
        if recording_config:
            body["recording_config"] = recording_config
        if output_webpage_url:
            body["output_media"] = {"camera": {"kind": "webpage", "config": {"url": output_webpage_url}}}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{recall_api_base()}/bot/", headers=_headers(), json=body)
            if resp.status_code not in (200, 201):
                return MeetingBot(
                    bot_id="", meeting_url=meeting_url, available=False,
                    error=f"{UNAVAILABLE} (HTTP {resp.status_code})",
                )
            data = resp.json()
            return MeetingBot(
                bot_id=str(data.get("id", "")), meeting_url=meeting_url,
                status=str(data.get("status", "created")), available=True,
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("recall_meeting_bot: create_bot failed: %r", exc)
            return MeetingBot(bot_id="", meeting_url=meeting_url, available=False, error=f"{UNAVAILABLE}: {exc}")

    async def get_bot_status(self, bot_id: str) -> MeetingBot:
        if not is_recall_configured() or not (bot_id or "").strip():
            return MeetingBot(bot_id=bot_id, meeting_url="", available=False, error=UNAVAILABLE)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{recall_api_base()}/bot/{bot_id}/", headers=_headers())
            if resp.status_code != 200:
                return MeetingBot(
                    bot_id=bot_id, meeting_url="", available=False,
                    error=f"{UNAVAILABLE} (HTTP {resp.status_code})",
                )
            data = resp.json()
            return MeetingBot(
                bot_id=bot_id, meeting_url=str(data.get("meeting_url", "")),
                status=str(data.get("status", "unknown")), available=True,
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("recall_meeting_bot: get_bot_status failed: %r", exc)
            return MeetingBot(bot_id=bot_id, meeting_url="", available=False, error=f"{UNAVAILABLE}: {exc}")

    async def leave_call(self, bot_id: str) -> bool:
        """Best-effort -- a failure here never blocks the caller, same
        degradation doctrine as the rest of this client."""
        if not is_recall_configured() or not (bot_id or "").strip():
            return False
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{recall_api_base()}/bot/{bot_id}/leave_call/", headers=_headers())
            return resp.status_code in (200, 204)
        except httpx.HTTPError as exc:
            logger.warning("recall_meeting_bot: leave_call failed: %r", exc)
            return False
