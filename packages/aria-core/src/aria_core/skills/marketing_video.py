"""Vidéos marketing ARIA à partir d'un verdict /vc déjà produit (tâche #23).

Portée V1, volontairement étroite : texte + graphique déjà rendu + portrait ARIA
en incrustation. AUCUNE voix (aucune infra TTS dans ce repo à ce jour, et une voix
de synthèse est justement le tell le plus reconnaissable d'un contenu IA -- écarté
par choix, pas par oubli, cf. plan #23).

Ce module NE PUBLIE JAMAIS RIEN lui-même : il s'arrête à
``approvals.create_approval(action="publish_marketing_video", ...)``. La
publication réelle (TikTok/X) reste un geste opérateur séparé, déjà gaté
ailleurs (``gateway/tiktok.py::is_tiktok_publish_enabled``,
``release_pipeline.is_campaign_armed``) -- ce module ne les appelle jamais.

Réutilise uniquement des données déjà calculées : le graphique vient du
``chart_data_uri`` déjà rendu par ``chart_render.render_scenario_png()`` au moment
du ``/vc`` (jamais régénéré), la thèse/cible/invalidation/scénarios viennent du
``VCResult`` déjà en mémoire, capturé sans recalcul par
``vc_session_context.queue_video_candidate()``.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "yes", "on")

# Résolution verticale pensée TikTok/Shorts/Reels.
_FRAME_W = 1080
_FRAME_H = 1920
_SECONDS_PER_FRAME = 3

_BG = (10, 12, 16)
_FG = (235, 235, 235)
_ACCENT = (212, 175, 55)  # or ZHC, cohérent avec l'identité visuelle existante

# Vera.ttf est déjà vendu par la dépendance reportlab (licence Bitstream Vera,
# permissive) -- réutilisé tel quel, aucune nouvelle dépendance de police.
try:
    import reportlab

    _FONT_DIR = Path(reportlab.__file__).resolve().parent / "fonts"
    _FONT_REGULAR = _FONT_DIR / "Vera.ttf"
    _FONT_BOLD = _FONT_DIR / "VeraBd.ttf"
except Exception:  # noqa: BLE001 -- reportlab absent en environnement de test minimal
    _FONT_REGULAR = None
    _FONT_BOLD = None

# Marqueurs "tell IA" à retirer avant tout texte burned-in dans la vidéo -- aucun
# utilitaire de ce type n'existait ailleurs dans le repo (confirmé en exploration).
_EM_DASH_RE = re.compile(r"[—–]")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "]+",
    flags=re.UNICODE,
)


def strip_ai_trace(text: str) -> str:
    """Retire em-dash et emojis d'un texte destiné à être incrusté dans la vidéo.

    Ne touche à rien d'autre (pas de reformulation) -- un simple filtre
    déterministe, pas une réécriture LLM."""
    if not text:
        return ""
    cleaned = _EM_DASH_RE.sub("-", text)
    cleaned = _EMOJI_RE.sub("", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def marketing_video_enabled() -> bool:
    """Gate OFF par défaut -- même patron que avatar_style_refresh._enabled() :
    env var en priorité, sinon settings, défaut False (jamais actif sans un
    geste opérateur explicite)."""
    env = os.environ.get("ARIA_MARKETING_VIDEO_ENABLED", "").strip().lower()
    if env:
        return env in _TRUTHY
    try:
        from aria_core.runtime import settings

        return bool(getattr(settings, "aria_marketing_video_enabled", False))
    except Exception:  # noqa: BLE001
        return False


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _FONT_BOLD if bold and _FONT_BOLD else _FONT_REGULAR
    if path and path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), candidate, font=font)
        if box[2] - box[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _base_frame() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (_FRAME_W, _FRAME_H), _BG)
    return img, ImageDraw.Draw(img)


def _draw_avatar_badge(img: Image.Image) -> None:
    """Incruste le portrait ARIA canonique en coin, s'il existe."""
    try:
        from aria_core.avatar import current_avatar_path

        path = current_avatar_path()
        if not path.exists():
            return
        badge = Image.open(path).convert("RGB").resize((140, 140))
        img.paste(badge, (_FRAME_W - 140 - 40, _FRAME_H - 140 - 60))
    except Exception as exc:  # noqa: BLE001 -- l'incrustation est cosmétique, jamais bloquante
        logger.warning("marketing_video: incrustation avatar échouée: %s", exc)


def _card_title(contract: str, symbol: str) -> Image.Image:
    img, draw = _base_frame()
    label = strip_ai_trace(symbol or contract[:10])
    font = _font(96, bold=True)
    box = draw.textbbox((0, 0), label, font=font)
    draw.text(((_FRAME_W - (box[2] - box[0])) / 2, _FRAME_H / 2 - 200), label, font=font, fill=_ACCENT)
    sub = _font(40)
    draw.text((80, _FRAME_H / 2 - 40), strip_ai_trace(contract), font=sub, fill=_FG)
    _draw_avatar_badge(img)
    return img


def _card_text(heading: str, body: str) -> Image.Image:
    img, draw = _base_frame()
    head_font = _font(64, bold=True)
    draw.text((80, 140), strip_ai_trace(heading), font=head_font, fill=_ACCENT)
    body_font = _font(46)
    lines = _wrap_text(draw, strip_ai_trace(body), body_font, _FRAME_W - 160)
    y = 280
    for line in lines[:14]:
        draw.text((80, y), line, font=body_font, fill=_FG)
        y += 62
    _draw_avatar_badge(img)
    return img


def _card_chart(chart_data_uri: str) -> Image.Image | None:
    """Décode le PNG scénario déjà rendu par chart_render.render_scenario_png()
    -- jamais régénéré ici."""
    if not chart_data_uri or "," not in chart_data_uri:
        return None
    try:
        payload = chart_data_uri.split(",", 1)[1]
        raw = base64.b64decode(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("marketing_video: décodage chart_data_uri échoué: %s", exc)
        return None
    img, _ = _base_frame()
    chart = Image.open(io.BytesIO(raw)).convert("RGB")
    scale = min((_FRAME_W - 120) / chart.width, (_FRAME_H * 0.55) / chart.height)
    new_size = (int(chart.width * scale), int(chart.height * scale))
    chart = chart.resize(new_size)
    img.paste(chart, ((_FRAME_W - new_size[0]) // 2, (_FRAME_H - new_size[1]) // 2))
    _draw_avatar_badge(img)
    return img


def render_video_frames(snapshot: dict, *, out_dir: Path) -> list[Path]:
    """Construit les images fixes de la vidéo à partir d'un snapshot déjà capturé
    (aucun appel réseau, aucun LLM, aucune donnée recalculée)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Image.Image] = [
        _card_title(snapshot.get("contract", ""), snapshot.get("symbol", "")),
        _card_text("These", snapshot.get("these", "")),
    ]
    chart_frame = _card_chart(snapshot.get("chart_data_uri", ""))
    if chart_frame is not None:
        frames.append(chart_frame)
    frames.append(
        _card_text(
            "Cible et invalidation",
            f"Cible: {snapshot.get('cible', '')}\nInvalidation: {snapshot.get('invalidation', '')}",
        )
    )
    for scenario in (snapshot.get("scenarios") or [])[:3]:
        nom = strip_ai_trace(str(scenario.get("nom", "")))
        cible = strip_ai_trace(str(scenario.get("cible", "")))
        proba = scenario.get("probabilite", "")
        frames.append(_card_text(f"Scenario {nom}", f"Cible: {cible}\nProbabilite: {proba}"))

    paths: list[Path] = []
    for i, frame in enumerate(frames):
        path = out_dir / f"frame_{i:03d}.png"
        frame.save(path, format="PNG")
        paths.append(path)
    return paths


def assemble_video(frames: list[Path], *, out_path: Path) -> Path:
    """Assemble les frames (PNG générés en interne par render_video_frames --
    JAMAIS un chemin fourni par une donnée externe) en MP4 via le binaire système
    ffmpeg. Arguments toujours passés en liste (jamais shell=True, jamais de
    chaîne interpolée depuis une donnée on-chain/utilisateur/LLM)."""
    if not frames:
        raise ValueError("aucune frame à assembler")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_file = out_path.parent / f"{out_path.stem}_concat.txt"
    lines = []
    for frame in frames:
        lines.append(f"file '{frame.resolve()}'")
        lines.append(f"duration {_SECONDS_PER_FRAME}")
    # ffmpeg exige que la dernière image soit répétée sans "duration" additionnel.
    lines.append(f"file '{frames[-1].resolve()}'")
    concat_file.write_text("\n".join(lines), encoding="utf-8")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-vf", f"scale={_FRAME_W}:{_FRAME_H}:force_original_aspect_ratio=decrease,"
               f"pad={_FRAME_W}:{_FRAME_H}:(ow-iw)/2:(oh-ih)/2",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    concat_file.unlink(missing_ok=True)
    return out_path


async def run_marketing_video_cycle(*, notifier=None) -> dict:
    """Un tour heartbeat : consomme UN candidat 'pending' déjà capturé (jamais de
    recalcul), génère la vidéo, met en file d'approbation opérateur -- ne publie
    jamais rien elle-même."""
    if not marketing_video_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core.paths import aria_marketing_video_dir
    from aria_core.skills.vc_session_context import (
        load_next_video_candidate,
        mark_video_candidate_done,
    )

    snapshot = await load_next_video_candidate()
    if snapshot is None:
        return {"outcome": "nothing_new"}

    candidate_id = snapshot["id"]
    video_dir = aria_marketing_video_dir() / f"candidate_{candidate_id}"
    try:
        frames = render_video_frames(snapshot, out_dir=video_dir)
        out_path = assemble_video(frames, out_path=video_dir / "verdict.mp4")
    except Exception as exc:  # noqa: BLE001 -- un rendu en échec ne casse jamais le heartbeat
        await mark_video_candidate_done(candidate_id, status="error")
        return {"outcome": "error", "error": str(exc)[:300], "id": candidate_id}

    from aria_core import approvals

    description = (
        f"Video marketing generee pour {snapshot.get('symbol') or snapshot.get('contract', '')} "
        f"-- {out_path}. Revue humaine requise avant toute publication (TikTok/X)."
    )
    req = await approvals.create_approval(
        "publish_marketing_video",
        strip_ai_trace(description),
        payload=str(out_path),
        requested_by="aria",
    )
    await mark_video_candidate_done(candidate_id, status="ready_for_review")

    if notifier:
        try:
            await notifier(f"Video marketing prete pour revue (approbation #{req.id}) -- {out_path}")
        except Exception:  # noqa: BLE001
            pass

    return {"outcome": "ok", "id": candidate_id, "video_path": str(out_path), "approval_id": req.id}
