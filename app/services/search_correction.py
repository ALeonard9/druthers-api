"""
Spelling fallback for the search proxies.

The external providers do exact-ish matching, so a typo
like "jurrasic" returns nothing. When a search comes back empty, the routers
retry once with a spell-corrected query. Correction is per-word against an
offline English dictionary; words it can't improve pass through unchanged,
so proper nouns degrade gracefully to the original behavior.
"""

from typing import Optional

from spellchecker import SpellChecker

_spell: Optional[SpellChecker] = None


def correct_query(query: str) -> Optional[str]:
    """
    Best-effort respelling of ``query``. Returns the corrected string, or
    None when correction wouldn't change anything (so callers can skip the
    retry).
    """
    global _spell  # pylint: disable=global-statement
    if _spell is None:
        # Lazy: loading the dictionary costs ~0.3s; only pay it on the first
        # empty search result, not at import time.
        _spell = SpellChecker()

    words = query.split()
    if not words:
        return None
    corrected = [_spell.correction(word) or word for word in words]
    result = ' '.join(corrected)
    if result.lower() == query.lower():
        return None
    return result
