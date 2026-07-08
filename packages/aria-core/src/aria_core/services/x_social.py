"""Écoute sociale X (Twitter) — service de sourcing pour le Radar (Voûte 4).

Récolte les MENTIONS de contrats de token sur X et en tire un signal de **bruit**
(combien de mentions, combien d'auteurs distincts). Ce bruit sert uniquement à
**sourcer/réveiller** des candidats : il ne décide JAMAIS d'acheter ou de vendre.

DÔME (règle de fer) : la donnée sociale est **non fiable** et **hostile par défaut**
(fermes de bots, shills payés, faux consensus). Elle est **sanitisée** ici (jamais
interprétée comme une instruction), et en aval c'est l'analyse **on-chain** qui
tranche (``token_absorber``). Le social FILTRE/RÉVEILLE, l'on-chain ARBITRE.

Le fetch réseau est **injectable** → testable hors-ligne. En prod, le défaut
dégrade gracieusement (liste vide) tant que l'API X n'est pas configurée : aucune
exception, aucun blocage. Lecture seule, aucune signature.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Adresse EVM : 0x + 40 hexa. On borne par des non-hexa pour éviter d'attraper
# un préfixe d'une chaîne plus longue (ex. hash de 64). Insensible à la casse.
_CONTRACT_RE = re.compile(r"(?<![0-9a-fA-Fx])(0x[0-9a-fA-F]{40})(?![0-9a-fA-F])")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HANDLE_MAX = 32


def _sanitize(text: object, max_len: int = 280) -> str:
    """Neutralise une donnée sociale (jamais une instruction) : contrôle + chevrons.

    Même doctrine que ``vc_analysis._sanitize`` : on retire les caractères de
    contrôle et on neutralise ``<`` ``>`` pour qu'un post hostile ne puisse pas
    forger une balise et s'échapper d'une zone non fiable en aval.
    """
    s = _CONTROL_CHARS_RE.sub("", str(text or ""))
    s = s.replace("<", "‹").replace(">", "›")
    return s[:max_len]


def extract_contracts(text: str) -> list[str]:
    """Adresses de contrat (0x + 40 hexa) trouvées dans un texte, en minuscules, dédupliquées."""
    seen: dict[str, None] = {}
    for m in _CONTRACT_RE.findall(text or ""):
        addr = m.lower()
        seen.setdefault(addr, None)
    return list(seen.keys())


@dataclass(frozen=True)
class SocialSignal:
    """Bruit social autour d'un contrat : combien de mentions, combien d'auteurs.

    C'est un signal de SOURCING, jamais un déclencheur. ``distinct_authors`` filtre
    l'astroturf grossier (un seul auteur qui spamme n'est pas un consensus).
    """

    contract: str
    mentions: int = 0
    distinct_authors: int = 0
    sample_handles: list[str] = field(default_factory=list)


class XSocialClient:
    """Client d'écoute X (lecture seule). ``fetch`` injectable pour les tests offline.

    En prod, ``fetch`` interroge l'API X/Twitter (recherche récente). Non configuré →
    défaut ``_fetch_stub`` qui renvoie ``[]`` (dégradation gracieuse, jamais bloquant).
    """

    def __init__(self, fetch=None) -> None:
        self._fetch = fetch or _fetch_stub

    async def scan_mentions(
        self, query: str = "base token 0x", *, limit: int = 100
    ) -> list[SocialSignal]:
        """Récolte des posts et agrège le bruit PAR contrat mentionné.

        ``fetch(query, limit)`` doit renvoyer une liste de posts
        ``{"text": str, "author": str}``. Toute forme inattendue est ignorée
        silencieusement (jamais d'exception : la donnée sociale est hostile).
        """
        try:
            posts = await self._fetch(query, limit)
        except Exception as exc:  # noqa: BLE001 — jamais bloquant
            logger.info("x_social: fetch échoué (%s) — radar vide ce tour", exc)
            return []

        agg: dict[str, dict] = {}
        for post in posts or []:
            if not isinstance(post, dict):
                continue
            text = _sanitize(post.get("text", ""))
            author = _sanitize(post.get("author", ""), max_len=_HANDLE_MAX)
            for contract in extract_contracts(text):
                slot = agg.setdefault(
                    contract, {"mentions": 0, "authors": set(), "handles": []}
                )
                slot["mentions"] += 1
                if author:
                    slot["authors"].add(author)
                    if author not in slot["handles"] and len(slot["handles"]) < 5:
                        slot["handles"].append(author)

        signals = [
            SocialSignal(
                contract=contract,
                mentions=slot["mentions"],
                distinct_authors=len(slot["authors"]),
                sample_handles=slot["handles"],
            )
            for contract, slot in agg.items()
        ]
        # Le plus bruyant d'abord (mentions puis auteurs distincts).
        signals.sort(key=lambda s: (s.mentions, s.distinct_authors), reverse=True)
        return signals


async def _fetch_stub(query: str, limit: int) -> list[dict]:
    """Défaut hors-ligne / non configuré : aucune donnée sociale (jamais d'erreur)."""
    logger.info("x_social: aucune source configurée — radar en veille (fetch stub)")
    return []


# Singleton pratique (fetch par défaut = stub tant que l'API X n'est pas branchée).
x_social_client = XSocialClient()
