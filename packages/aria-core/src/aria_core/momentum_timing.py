"""Time-confirmation constants shared by the momentum pipeline (07/20).

Extracted from ``paper_trader.HIGH_WATER_CONFIRMATION_SECONDS`` and
``momentum_entry._WASH_TRADING_CONFIRMATION_SECONDS`` -- these two constants
used to be independent copies of the same value (75s), deliberately not linked
by a direct import to avoid a cycle (``paper_trader.py`` already imports from
``momentum_entry.py``). An external cross-review correctly flagged that this
duplication is real maintenance debt: nothing prevents changing one without
thinking of the other. This neutral module (no dependency on either) is now the
SOLE source of truth -- both files import it, never a hand-copied value.
"""
from __future__ import annotations

MOMENTUM_CONFIRMATION_SECONDS = 75.0
