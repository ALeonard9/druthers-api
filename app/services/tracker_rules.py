"""
Shared rules for per-user tracker list membership.

The one-home rule (2026-07-18 product decision, druthers-api#145): an item
lives on exactly one list - Watchlist, To-be-ranked, or Ranked. Moving it to
one removes it from the others. "To-be-ranked" and "Ranked" are both
``on_rankings``; the difference is whether ``rank`` is set.
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Naive-free timestamp for rank assignments (#141)."""
    return datetime.now(timezone.utc)


def enforce_single_home(tracker, requested: dict) -> None:
    """
    Resolve any watchlist/rankings overlap after a request's fields have been
    applied to ``tracker``.

    The list the request just asked for wins; when a single request asks for
    both, Rankings wins (it's the stronger claim - "I've seen this").
    Rank clearing and gap closing stay with the domain routers, which own
    their shift math.
    """
    if not (tracker.on_watchlist and tracker.on_rankings):
        return
    if requested.get('on_watchlist') and not requested.get('on_rankings'):
        tracker.on_rankings = False
    else:
        tracker.on_watchlist = False


def default_completed_at(tracker, was_on_rankings: bool) -> None:
    """
    Completed-date rule: entering Rankings means "I finished this", so stamp
    today unless a date is already set (imported history or an explicit value
    in the same request always wins). Editable later on the detail page.
    """
    if tracker.on_rankings and not was_on_rankings and tracker.completed_at is None:
        tracker.completed_at = utc_now().date()
