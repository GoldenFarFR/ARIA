"""Vision KART — analyse d'images locales pour l'ouvrier Letta."""
from __future__ import annotations

import base64
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import requests

from aria_config import bridge_api_keys
from ouvrier_trace import trace, trace_block

IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
_PATH_RE = re.compile(
    r'(?i)(?:[A-Za-z]:)?(?:\\|/)[^\s"\'<>|]+\.(?:png|jpe?g|webp|gif|bmp)'
)
_IMAGE_REF_RE = re.compile(
    r"(?i)\b(?:"
    r"l['']?image|la photo|l['']?écran|l['']?ecran|"
    r"capture|screenshot|screenpresso|"
    r"tu as pu lire|tu a pu lire|as[- ]tu lu|peux[- ]tu lire"
    r")\b"
)


def extract_image_paths(message: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _PATH_RE.finditer(message or ""):
        raw = match.group(0).strip().strip('"\'')
        key = raw.lower()
        if key not in seen:
            seen.add(key)
            out.append(raw)
    return out


def resolve_image_path(message: str, session: dict[str, Any] | None = None) -> Path | None:
    """Chemin image explicite dans le message, ou dernière image mémorisée."""
    for raw in extract_image_paths(message):
        path = Path(raw)
        if path.is_file():
            return path.resolve()
    session = session or {}
    prev = (session.get("last_image_path") or "").strip()
    if prev:
        path = Path(prev)
        if path.is_file():
            return path.resolve()
    if _IMAGE_REF_RE.search(message or ""):
        return None
    return None


def wants_image_context(message: str, session: dict[str, Any] | None = None) -> bool:
    if extract_image_paths(message):
        return True
    if _IMAGE_REF_RE.search(message or ""):
        return bool((session or {}).get("last_image_path"))
    return False


def image_path_from_message(message: str) -> str | None:
    path = resolve_image_path(message)
    return str(path) if path else None


def _to_jpeg_bytes(path: Path) -> bytes:
    from PIL import Image

    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _vault_key(*names: str) -> str:
    vault = Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "vault"
    for fname in ("local.env", "production.env"):
        path = vault / fname
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            for name in names:
                if line.strip().startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"')
    return ""


def _vision_post(
    url: str,
    api_key: str,
    model: str,
    instruction: str,
    image_jpeg: bytes,
    *,
    extra_headers: dict[str, str] | None = None,
) -> str | None:
    b64 = base64.b64encode(image_jpeg).decode("ascii")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    if extra_headers:
        headers.update(extra_headers)
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ],
        "temperature": 0.2,
        "max_tokens": 900,
    }
    try:
        response = requests.post(url, headers=headers, json=body, timeout=120)
        if response.status_code != 200:
            trace("fallback", f"vision {model} HTTP {response.status_code}")
            return None
        data = response.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return str(content).strip() or None
    except Exception as exc:
        trace("fallback", f"vision {model} erreur: {exc}")
        return None


def _ollama_vision(image_jpeg: bytes, instruction: str) -> str | None:
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("ARIA_VISION_MODEL", "llama3.2-vision")
    b64 = base64.b64encode(image_jpeg).decode("ascii")
    try:
        response = requests.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": instruction,
                        "images": [b64],
                    }
                ],
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=180,
        )
        if response.status_code != 200:
            trace("fallback", f"ollama vision HTTP {response.status_code}")
            return None
        content = (response.json().get("message") or {}).get("content") or ""
        return str(content).strip() or None
    except Exception as exc:
        trace("fallback", f"ollama vision erreur: {exc}")
        return None


def analyze_image(path: Path, *, question: str = "") -> tuple[str | None, str]:
    """Analyse une image — retourne (description, provider_utilisé)."""
    bridge_api_keys()
    instruction = (
        question.strip()
        or "Décris cette image en détail (texte visible, interface, erreurs, contexte). Réponds en français."
    )
    if not instruction.lower().startswith("décris") and "?" not in instruction:
        instruction = (
            f"{instruction}\n\nDécris aussi le contenu visuel de l'image (texte, UI, erreurs)."
        )

    try:
        jpeg = _to_jpeg_bytes(path)
    except Exception as exc:
        return None, f"lecture fichier: {exc}"

    xai = (
        os.environ.get("XAI_API_KEY")
        or os.environ.get("GROK_API_KEY")
        or os.environ.get("IMAGE_API_KEY")
        or _vault_key("XAI_API_KEY", "GROK_API_KEY", "IMAGE_API_KEY")
    )
    if xai and len(xai) >= 20:
        for model in ("grok-4.3", "grok-2-vision-1212", "grok-2-vision-latest"):
            trace("moteur", f"Vision → {model}")
            text = _vision_post(
                "https://api.x.ai/v1/chat/completions",
                xai,
                model,
                instruction,
                jpeg,
            )
            if text:
                return text, model

    groq = os.environ.get("GROQ_API_KEY") or _vault_key("GROQ_API_KEY", "LLM_API_KEY")
    if groq and len(groq) >= 20:
        trace("moteur", "Vision → groq/llama-4-scout")
        text = _vision_post(
            "https://api.groq.com/openai/v1/chat/completions",
            groq,
            "meta-llama/llama-4-scout-17b-16e-instruct",
            instruction,
            jpeg,
        )
        if text:
            return text, "groq-llama-4-scout"

    trace("moteur", "Vision → ollama (fallback)")
    text = _ollama_vision(jpeg, instruction)
    if text:
        return text, "ollama-vision"

    return None, "aucun"


_IMAGE_READ_RE = re.compile(
    r"(?i)\b(?:tu a pu lire|tu as pu lire|as[- ]tu lu|peux[- ]tu lire|"
    r"lis l['']?image|lire l['']?image|décris l['']?image|decris l['']?image)\b"
)


def build_image_context(
    message: str, session: dict[str, Any] | None = None
) -> tuple[str, str | None, str | None]:
    """
    Prépare un bloc contexte vision pour le prompt ouvrier.
    Retourne (bloc_texte, chemin_image_résolu, analyse_brute).
    """
    session = session or {}
    if not wants_image_context(message, session):
        return "", None, None

    path = resolve_image_path(message, session)
    if not path:
        if _IMAGE_REF_RE.search(message or ""):
            return (
                "IMAGE — Sylvain fait référence à une image mais aucun fichier n'est en session. "
                "Demande-lui de coller le chemin complet (ex. C:\\Users\\...\\capture.png).",
                None,
                None,
            )
        return "", None, None

    trace("preflight", f"vision — {path.name}")
    analysis, provider = analyze_image(path, question=message)
    if not analysis:
        return (
            f"IMAGE — fichier `{path}` détecté mais vision indisponible ({provider}).\n"
            "Sylvain peut installer `ollama pull llama3.2-vision` ou réessayer quand Groq/xAI répond.",
            str(path),
            None,
        )

    trace_block("preflight", "vision", analysis, max_lines=8)
    block = (
        f"ANALYSE IMAGE (déjà lue — ne dis pas que tu n'as pas accès aux fichiers)\n"
        f"Fichier : {path}\n"
        f"Moteur vision : {provider}\n"
        f"Contenu :\n{analysis}\n\n"
        "Réponds à Sylvain en t'appuyant sur cette analyse."
    )
    return block, str(path), analysis


def direct_image_reply(message: str, analysis: str, image_path: str) -> str | None:
    """Réponse courte quand la vision a déjà lu l'image."""
    if not analysis or not _IMAGE_READ_RE.search(message or ""):
        return None
    short = analysis.strip()
    if len(short) > 1200:
        short = short[:1197] + "…"
    return (
        f"Oui — j'ai lu l'image `{Path(image_path).name}`.\n\n"
        f"{short}"
    )