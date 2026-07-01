"""Journal épisodique — wrapper fin autour de ``_legacy_journal``."""
from __future__ import annotations

from aria_core.memory._legacy_journal import (
    append_memory,
    count_memory_entries,
    get_doctrine_text,
    get_journal_summary,
    get_launchpad_doctrine_text,
    get_persona_text,
    read_recent_memory,
)
from aria_core.memory.llm_context import build_llm_context

append = append_memory
read_recent = read_recent_memory
count_entries = count_memory_entries