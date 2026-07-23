"""Neutralization of untrusted external content before injection into an LLM prompt.

Shared choke point. Extracted on 13/07 from ``skills/vc_analysis.py``
(``_sanitize``, until then duplicated nowhere but coupled to the VC dome) to
be reusable by any module that shows external text (web search, third-party
HTML page, public API response) to an LLM as DATA, never as an instruction.

Always use in conjunction with an explicit delimiting tag
(``<donnees_non_fiables>``/``</donnees_non_fiables>``, cf. ``vc_analysis.py``,
``vc_judge.py``, ``knowledge/web_verify.py``): neutralizing the angle brackets
below makes this tag unforgeable by the content it wraps, but doesn't replace
the tag itself -- the two go together.
"""

from __future__ import annotations

import re

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

DEFAULT_MAX_LEN = 600


def sanitize_untrusted_text(text: object, max_len: int = DEFAULT_MAX_LEN) -> str:
    """Neutralizes any external data before injection into an LLM prompt.

    - Removes control characters.
    - **Neutralizes angle brackets `<` `>`** (replaced with single guillemets
      `‹` `›`): hostile data (e.g. a web excerpt containing
      "</donnees_non_fiables> SYSTEM: ...") can therefore NOT forge the
      delimiting tag and escape the untrusted zone (anti prompt-injection).
      Angle brackets have no legitimate use in this kind of content.
    - Truncates to ``max_len``.
    """
    s = _CONTROL_CHARS_RE.sub("", str(text or ""))
    s = s.replace("<", "‹").replace(">", "›")
    return s[:max_len]
