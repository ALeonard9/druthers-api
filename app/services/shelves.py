"""
The four tracked domains, described once.

Movies, TV, Books and Games have identical tracker shapes: ``on_rankings`` /
``on_watchlist`` / ``rank`` against a catalog row carrying a title, year and
poster. Endpoints that work across all four — the public profile and the home
summary — read this registry instead of repeating the tuple, so the shelf list
stays in one place.
"""

from typing import NamedTuple, Tuple, Type

from app.db.models_sandbox import (
    DbBook,
    DbMovie,
    DbTVShow,
    DbUserBook,
    DbUserMovie,
    DbUserTVShow,
    DbUserVideoGame,
    DbVideoGame,
)


class Shelf(NamedTuple):
    """One tracked domain and the bits generic queries need to reach it."""

    # URL/JSON slug ('movies'). Stable — clients key off this.
    category: str
    # Human label ('Video Games'), as it appears on profiles and share cards.
    label: str
    # Attribute on DbUser holding this shelf's visibility tier.
    visibility_tier: str
    # Attribute on DbUser holding this shelf's *watchlist* visibility tier
    # (#236) — only takes effect when visibility_tier admits the viewer too.
    watchlist_visibility_tier: str
    tracker_model: Type
    catalog_model: Type
    # Tracker column joining to ``catalog_model.pk``.
    join_col: str


SHELVES: Tuple[Shelf, ...] = (
    Shelf(
        'movies',
        'Movies',
        'visibility_movies',
        'visibility_watchlist_movies',
        DbUserMovie,
        DbMovie,
        'movie_id',
    ),
    Shelf(
        'tv',
        'TV',
        'visibility_tv',
        'visibility_watchlist_tv',
        DbUserTVShow,
        DbTVShow,
        'tv_show_id',
    ),
    Shelf(
        'books',
        'Books',
        'visibility_books',
        'visibility_watchlist_books',
        DbUserBook,
        DbBook,
        'book_id',
    ),
    Shelf(
        'games',
        'Video Games',
        'visibility_games',
        'visibility_watchlist_games',
        DbUserVideoGame,
        DbVideoGame,
        'game_id',
    ),
)


def shelf_tier_fields() -> Tuple[Tuple[str, str], ...]:
    """
    Every shelf tier column on ``DbUser`` paired with a human label.

    Ordered ranked-list then watchlist, shelf by shelf, so a validation error
    names shelves in the order the settings page lists them. Derived from
    :data:`SHELVES` so adding a domain never means editing a second list.
    """
    return tuple(
        pair
        for shelf in SHELVES
        for pair in (
            (shelf.visibility_tier, shelf.label),
            (shelf.watchlist_visibility_tier, f'{shelf.label} watchlist'),
        )
    )
