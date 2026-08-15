"""
Search-result ranking: cap each domain's hits and surface the best match
first.

Providers already filter to items relevant to the query, but not
necessarily in an order that puts the *best* match first - searching
"Titanic" can bury the 1997 movie under lesser-known or same-named titles.
We re-rank locally with a simple two-tier heuristic instead of pulling in a
fuzzy-matching library:

    1. exact / near-exact title match (case- and punctuation-insensitive)
       - always wins outright, no ambiguity.
    2. any partial match: title starts with the query OR merely contains
       it. These two used to be separate tiers with "starts with" always
       beating "contains" - but that's an unreliable signal on its own.
       E.g. searching "Zelda" in games: an obscure/low-quality entry
       titled "Zelda 64" *starts with* the query, while "The Legend of
       Zelda: Ocarina of Time" - the game everyone actually means - only
       *contains* it. Prefix position alone shouldn't decide that matchup.

Within the partial-match tier (and as a tiebreaker anywhere else), we sort
by each hit's ``popularity`` field when a provider supplies one - real
popularity is a much stronger signal than where in the title the query
happens to appear. Games carry IGDB's ``total_rating_count`` and movies
carry TMDB's ``popularity`` (gained in #163; OMDb supplied none, so movie
search had no tiebreaker at all before that). TV and books still have no
such field: TVMaze and Open Library don't publish one.

Where ``popularity`` is absent it defaults to 0 for every hit, which makes
the sort a no-op and falls back to the provider's own result order -
Python's ``sorted`` is stable, so that order is preserved automatically.
The ranked list is then capped to ``limit`` per domain so the UI shows a
short, best-first list instead of a long unranked one.
"""

import re
from typing import List

_NORMALIZE_RE = re.compile(r'[^a-z0-9]+')

DEFAULT_DOMAIN_CAP = 5

# Higher is better; see module docstring for what each tier means.
_TIER_EXACT = 2
_TIER_PARTIAL = 1
_TIER_OTHER = 0


def _normalize(text: str) -> str:
    """Lowercase and strip everything but alphanumerics, so 'Spider-Man'
    and 'spider man' (or 'Spider Man!') compare equal."""
    return _NORMALIZE_RE.sub('', (text or '').lower())


def _tier(query: str, title: str) -> int:
    """Score how closely ``title`` matches ``query``; see module docstring."""
    q = _normalize(query)
    t = _normalize(title)
    if not q or not t:
        return _TIER_OTHER
    if q == t:
        return _TIER_EXACT
    if t.startswith(q) or q in t:
        return _TIER_PARTIAL
    return _TIER_OTHER


def rank_and_cap(
    query: str, results: List[dict], limit: int = DEFAULT_DOMAIN_CAP
) -> List[dict]:
    """
    Sort ``results`` best-match-first (tier, then popularity - see module
    docstring) and truncate to ``limit``. Safe to call with an empty list.
    """
    ranked = sorted(
        results,
        key=lambda item: (
            _tier(query, item.get('title') or ''),
            item.get('popularity') or 0,
        ),
        reverse=True,
    )
    return ranked[:limit]
